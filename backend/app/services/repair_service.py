"""Find and repair bookkeeping damage in existing campaigns.

``analyze_campaign`` never writes. It returns findings with evidence and a confidence tier:
HIGH findings are safe to apply automatically; PROBABLE and AMBIGUOUS findings are only
applied when the player selects them by id. Every merge preserves facts, aliases,
relationships, memories, portraits, and history.
"""

import hashlib
import re
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ability,
    Branch,
    Campaign,
    CampaignSummary,
    Character,
    CharacterRelationship,
    Checkpoint,
    Item,
    Memory,
    Objective,
    Turn,
)
from app.services import objectives as objective_rules
from app.services import world_time
from app.services.abilities import gain_ability
from app.services.character_store import (
    StoreLog,
    add_alias,
    build_resolver,
    ensure_identity_rows,
    merge_character_values,
    merge_characters,
    sync_fact_mirror,
)
from app.services.constitution import derive_constitution
from app.services.context_builder import history_for_branch
from app.services.entity_resolver import (
    DESCRIPTOR_MENTION,
    REVEAL_NAME,
    resolve_named,
    tails_match,
)
from app.services.identity import (
    STATE_ADJECTIVES,
    descriptive_tail,
    head_noun,
    is_generic_reference,
    is_unknown,
    looks_like_proper_name,
    normalize_reference,
    same_statement,
    strip_qualifier,
)
from app.services.post_turn import enqueue
from app.services.state_service import ABILITY_ATTRIBUTE_KEYS, compute_importance

NOISE = {"first appeared in the story", "appeared in the story"}
CHARACTER_KINDS = {"CREATE_CHARACTER", "UPDATE_CHARACTER", "CHANGE_CHARACTER_STATUS", "MOVE_CHARACTER"}


def _fid(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(part) for part in parts).encode()).hexdigest()[:12]


def _finding(kind: str, confidence: str, summary: str, evidence: list[str], payload: dict, *key: Any) -> dict:
    return {"id": _fid(kind, *key), "type": kind, "confidence": confidence, "summary": summary,
            "evidence": evidence[:6], "payload": payload, "auto": confidence == "HIGH"}


def _numbered(name: str) -> bool:
    base, qualifier = strip_qualifier(normalize_reference(name))
    return bool(qualifier) or any(token.isdigit() or token in {"first", "second", "third", "other", "another"}
                                  for token in base.split())


def _modifiers(name: str) -> set[str]:
    base = strip_qualifier(normalize_reference(name))[0]
    base = re.split(r"\b(?:in|with|from|of|at|near|on|by|who|that|wearing)\b", base, maxsplit=1)[0]
    tokens = base.split()
    return set(tokens[:-1])


async def _reveal_pairs(turns: list[Turn], characters: list[Character]) -> list[tuple[Character, Character, str, int]]:
    """(descriptor person, named person, evidence, turn) from "the woman in the dark coat ... My name is Mara"."""
    named = {normalize_reference(row.name): row for row in characters if looks_like_proper_name(row.name)}
    descriptors = [row for row in characters if descriptive_tail(row.name)]
    pairs = []
    for turn in turns:
        text = turn.gm_response or ""
        for match in REVEAL_NAME.finditer(text):
            name = match.group("name").split()[0]
            person = named.get(normalize_reference(name)) or named.get(normalize_reference(match.group("name")))
            if not person:
                continue
            window = text[max(0, match.start() - 900):match.start()]
            for found in reversed(list(DESCRIPTOR_MENTION.finditer(window))):
                tail = descriptive_tail(found.group("descriptor"))
                matched = [row for row in descriptors if tail and tails_match(descriptive_tail(row.name), tail)]
                if matched:
                    sentence_start = text.rfind(".", 0, match.start()) + 1
                    sentence_end = text.find(".", match.end())
                    evidence = " ".join(text[sentence_start:sentence_end + 1].split())[:240]
                    for row in matched:
                        pairs.append((row, person, f"Turn {turn.turn_index}: \"{found.group(0).strip()}\" … {evidence}", turn.turn_index))
                    break
    return pairs


def _story_order(row: Character) -> tuple:
    """Earliest-seen first. Merges run in this order, so the first spelling the story used survives."""
    return (row.first_seen_turn_index is None, row.first_seen_turn_index or 0, row.name.casefold(), str(row.id))


async def analyze_campaign(session: AsyncSession, campaign: Campaign, branch: Branch) -> dict[str, Any]:
    await ensure_identity_rows(session, branch.id)
    await session.flush()
    # Stable order: the database returns rows in no particular order, and which duplicate is kept,
    # which spelling becomes the alias, and how ties break all depend on it.
    characters = sorted((await session.scalars(select(Character).where(Character.branch_id == branch.id))).all(), key=_story_order)
    player_key = normalize_reference(campaign.protagonist_name)
    people = [row for row in characters if normalize_reference(row.name) != player_key]
    player = next((row for row in characters if normalize_reference(row.name) == player_key), None)
    turns = await history_for_branch(session, branch.head_turn_id, limit=100_000)
    findings: list[dict] = []

    # --- duplicate characters -----------------------------------------------------------
    edges: list[tuple[str, str, str, list[str]]] = []  # (source, target, confidence, evidence)

    def add_edge(source: Character, target: Character, confidence: str, evidence: list[str]) -> None:
        if source.id != target.id:
            edges.append((str(source.id), str(target.id), confidence, evidence))

    by_key: dict[str, list[Character]] = defaultdict(list)
    for row in people:
        by_key[normalize_reference(row.name)].append(row)
    for rows in by_key.values():
        if len(rows) > 1:
            target = max(rows, key=lambda row: (int((row.attributes or {}).get("seen_count") or 0), len(row.name)))
            for row in rows:
                add_edge(row, target, "HIGH", [f"Same name ignoring case and articles: '{row.name}' / '{target.name}'"])
    # Roles as they were recorded over time: a later "unknown" must not hide an earlier title.
    historic_roles: dict[str, set[str]] = defaultdict(set)
    for turn in turns:
        for operation in (turn.state_delta or {}).get("operations", []) or []:
            if isinstance(operation, dict) and isinstance(operation.get("value"), dict):
                role = operation["value"].get("role")
                name = operation.get("name") or operation.get("subject")
                if isinstance(role, str) and not is_unknown(role) and isinstance(name, str):
                    historic_roles[normalize_reference(name)].add(normalize_reference(role))

    def roles_of(row: Character) -> set[str]:
        values = set(historic_roles.get(normalize_reference(row.name), set()))
        if row.role and not is_unknown(row.role):
            values.add(normalize_reference(row.role))
        return values

    named = [row for row in people if looks_like_proper_name(row.name)]
    for row in people:
        if looks_like_proper_name(row.name) or is_generic_reference(row.name):
            continue
        key = normalize_reference(row.name)
        titled = [other for other in named if key in roles_of(other)]
        if len(titled) == 1:
            other = titled[0]
            shared = [fact for fact in (row.attributes or {}).get("known_facts") or []
                      if any(same_statement(fact, theirs, 0.5) for theirs in (other.attributes or {}).get("known_facts") or [])]
            add_edge(row, other, "HIGH", [f"'{row.name}' is the recorded title of {other.name}",
                                          *[f"Shared fact: {fact}" for fact in shared[:2]]])
    for descriptor, person, evidence, _ in await _reveal_pairs(turns, people):
        add_edge(descriptor, person, "HIGH", [evidence])
    heads: dict[str, list[Character]] = defaultdict(list)
    for row in people:
        if not looks_like_proper_name(row.name) and not _numbered(row.name) and not descriptive_tail(row.name):
            heads[head_noun(row.name)].append(row)
    for head, rows in heads.items():
        if len(rows) != 2 or not head:
            if len(rows) > 2:
                findings.append(_finding("AMBIGUOUS_CHARACTERS", "AMBIGUOUS",
                                         f"{len(rows)} people are described as '{head}'; not merged without story evidence.",
                                         [row.name for row in rows], {"character_ids": [str(row.id) for row in rows]}, head))
            continue
        left, right = rows
        left_mods, right_mods = _modifiers(left.name), _modifiers(right.name)
        # "boy" / "street boy" can be one person; "burning man" / "masked man" are two descriptions.
        if not (left_mods <= right_mods or right_mods <= left_mods):
            continue
        if not all(token in STATE_ADJECTIVES for token in left_mods ^ right_mods):
            continue
        place_left = normalize_reference((left.attributes or {}).get("first_meeting_place") or "")
        place_right = normalize_reference((right.attributes or {}).get("first_meeting_place") or "")
        target = max(rows, key=lambda row: int((row.attributes or {}).get("seen_count") or 0))
        source = right if target is left else left
        if place_left and place_left == place_right:
            add_edge(source, target, "HIGH", [f"Only two people described as '{head}', both first met at {left.attributes.get('first_meeting_place')}",
                                              f"'{source.name}' differs only by a descriptive word"])
        else:
            add_edge(source, target, "PROBABLE", [f"Only two people described as '{head}', but first met in different places"])
    for row in people:
        base, qualifier = strip_qualifier(row.name)
        if not qualifier:
            continue
        plain = [other for other in people if other.id != row.id and normalize_reference(other.name) == normalize_reference(base)]
        if len(plain) == 1:
            add_edge(row, plain[0], "PROBABLE", [f"'{row.name}' may be the same person as '{plain[0].name}' ({qualifier})"])

    # Union connected duplicates; the proper-named, most-seen person becomes canonical.
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        while parent.get(value, value) != value:
            value = parent[value]
        return value

    confidence_of: dict[frozenset, tuple[str, list[str]]] = {}
    for source, target, confidence, evidence in edges:
        if confidence != "HIGH":
            continue
        parent[find(source)] = find(target)
        confidence_of[frozenset((source, target))] = (confidence, evidence)
    groups: dict[str, list[str]] = defaultdict(list)
    for row in people:
        groups[find(str(row.id))].append(str(row.id))
    by_id = {str(row.id): row for row in people}
    for members in groups.values():
        if len(members) < 2:
            continue
        rows = [by_id[value] for value in members]
        target = max(rows, key=lambda row: (looks_like_proper_name(row.name), int((row.attributes or {}).get("seen_count") or 0)))
        evidence = [text for (left, right), (_, texts) in ((tuple(key), value) for key, value in confidence_of.items())
                    if left in members for text in texts]
        findings.append(_finding(
            "DUPLICATE_CHARACTER", "HIGH",
            f"{', '.join(repr(row.name) for row in rows if row.id != target.id)} → {target.name}",
            evidence, {"target_id": str(target.id),
                       "source_ids": [str(row.id) for row in sorted(rows, key=_story_order) if row.id != target.id]},
            *sorted(members)))
    for source, target, confidence, evidence in edges:
        if confidence == "PROBABLE" and find(source) != find(target):
            findings.append(_finding("DUPLICATE_CHARACTER", "PROBABLE", f"{by_id[source].name} → {by_id[target].name}?",
                                     evidence, {"target_id": target, "source_ids": [source]}, source, target))

    # --- facts and roles lost to earlier overwrites --------------------------------------
    recoverable = 0
    for turn in turns:
        for operation in (turn.state_delta or {}).get("operations", []) or []:
            if isinstance(operation, dict) and operation.get("kind") in CHARACTER_KINDS and isinstance(operation.get("value"), dict):
                value = operation["value"]
                if value.get("known_facts") or value.get("role") or value.get("status"):
                    recoverable += 1
    recovered_through = int(((branch.current_state or {}).get("repairs") or {}).get("history_recovered_through") or -1)
    head_for_history = turns[-1].turn_index if turns else 0
    if recoverable and recovered_through < head_for_history:
        findings.append(_finding("RECOVER_CHARACTER_HISTORY", "HIGH",
                                 f"Replay {recoverable} recorded character updates to restore facts, roles, and full statuses that later updates overwrote.",
                                 ["Earlier updates replaced known_facts wholesale and could set a known role to 'unknown'."],
                                 {}, "recover", branch.id))

    # --- objectives -------------------------------------------------------------------------
    resolver, _ = await build_resolver(session, campaign, branch.id, branch.current_state or {})
    objectives = list((await session.scalars(select(Objective).where(Objective.branch_id == branch.id))).all())
    items = list((await session.scalars(select(Item).where(Item.branch_id == branch.id))).all())
    player_items = [item for item in items if normalize_reference(item.owner_name) in {player_key, "player"}]
    active = [row for row in objectives if row.status == "active"]
    for index, objective in enumerate(active):
        for other in active[index + 1:]:
            if objective_rules.same_objective(objective.title, list(objective.aliases or []), other.title):
                findings.append(_finding("DUPLICATE_OBJECTIVE", "HIGH", f"'{other.title}' repeats '{objective.title}'",
                                         [f"Same goal: {objective.title} / {other.title}"],
                                         {"keep_id": str(objective.id), "duplicate_id": str(other.id)}, objective.id, other.id))
    characters_by_id = {str(row.id): row for row in characters}
    for objective in active:
        criteria = objective.criteria or objective_rules.infer_criteria(objective.title, resolver)
        probe = Objective(title=objective.title, criteria=criteria, status="active")
        note = objective_rules.check_completion(probe, characters_by_id, player_items, resolver)
        if note:
            findings.append(_finding("STALE_OBJECTIVE", "HIGH", f"'{objective.title}' is already satisfied", [note],
                                     {"objective_id": str(objective.id), "criteria": probe.criteria, "note": note}, objective.id))

    # --- items ----------------------------------------------------------------------------------
    by_owner: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        by_owner[normalize_reference(item.owner_name)].append(item)
    for owner_items in by_owner.values():
        for index, item in enumerate(owner_items):
            rows = [(str(other.id), other.name, list(other.aliases or [])) for other in owner_items[index + 1:]]
            match = resolve_named(item.name, rows)
            if match.status == "RESOLVED" and match.confidence >= 0.95:
                findings.append(_finding("DUPLICATE_ITEM", "HIGH", f"'{item.name}' is listed twice",
                                         [f"Same item name for owner {item.owner_name}"],
                                         {"keep_id": match.character_id, "duplicate_id": str(item.id)}, item.id, match.character_id))

    # --- relationships ------------------------------------------------------------------------
    relations = list((await session.scalars(select(CharacterRelationship).where(CharacterRelationship.branch_id == branch.id))).all())
    noise = []
    for relation in relations:
        dims = relation.dimensions or {}
        meaningful = {key for key in dims if key not in {"history", "last_interaction", "awareness"}}
        history = [entry for entry in dims.get("history", []) or [] if isinstance(entry, dict)]
        if not meaningful and all(str(entry.get("reason", "")).casefold() in NOISE for entry in history) and \
                (relation.summary or "").startswith("Known to"):
            noise.append(str(relation.id))
    if noise:
        findings.append(_finding("NOISE_RELATIONSHIP", "PROBABLE",
                                 f"{len(noise)} relationships record only 'appeared in the story'.",
                                 ["They have no scores, kinship, or real events. Removing them does not lose information."],
                                 {"relationship_ids": noise}, "noise", len(noise)))

    # --- memories ---------------------------------------------------------------------------------
    memories = list((await session.scalars(select(Memory).where(Memory.branch_id == branch.id).order_by(Memory.created_at))).all())
    seen: dict[str, Memory] = {}
    duplicates = []
    for memory in memories:
        key = f"{memory.memory_type}:{normalize_reference(memory.content)}"
        if key in seen:
            duplicates.append(str(memory.id))
        else:
            seen[key] = memory
    if duplicates:
        findings.append(_finding("DUPLICATE_MEMORY", "HIGH", f"{len(duplicates)} memories are exact repeats.", [],
                                 {"memory_ids": duplicates}, "memories", len(duplicates)))

    # --- summary, status, time, abilities, failed attempts --------------------------------------------
    head_index = turns[-1].turn_index if turns else 0
    summary = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch.id, CampaignSummary.summary_type == "campaign"))
    if head_index and (summary is None or head_index - summary.through_turn_index >= 8):
        findings.append(_finding("STALE_SUMMARY", "HIGH",
                                 f"Campaign summary covers turn {summary.through_turn_index if summary else 0} of {head_index}.",
                                 [summary.last_error] if summary and summary.last_error else [], {}, "summary", head_index))
    state = branch.current_state or {}
    if len(str(state.get("player_status") or "")) == 32 or any(len(row.status or "") == 32 for row in characters):
        findings.append(_finding("TRUNCATED_STATUS", "HIGH", "Some statuses were cut off at 32 characters.",
                                 [str(state.get("player_status"))], {}, "status"))
    if str(state.get("world_time") or "").casefold() in {"", "opening"} or not state.get("world_clock"):
        findings.append(_finding("WORLD_TIME", "HIGH", f"World time still reads '{state.get('world_time') or 'unset'}'.",
                                 [f"{int(state.get('elapsed_seconds') or 0)} seconds have passed in play."], {}, "time"))
    if player is not None:
        catalogue = (await session.scalars(select(Ability.id).where(Ability.character_id == player.id).limit(1))).first()
        if catalogue is None:
            derived = derive_constitution(campaign.original_prompt)
            stored = [value for key in ABILITY_ATTRIBUTE_KEYS for value in ((player.attributes or {}).get(key) or [])
                      if isinstance(value, str) and key not in {"abilities", "powers"}]
            if derived.ability_catalogue or stored:
                findings.append(_finding("ABILITY_CATALOGUE", "HIGH", "Build the ability catalogue from the setup and play history.",
                                         [entry["name"] for entry in derived.ability_catalogue] + stored, {}, "abilities"))
    failed_ids = [str(value) for value in (await session.scalars(select(Turn.id).where(
        Turn.branch_id == branch.id, Turn.canonical.is_(False)))).all()]
    orphan = (await session.scalars(select(Checkpoint.id).where(
        Checkpoint.branch_id == branch.id, Checkpoint.turn_id.in_([UUID(value) for value in failed_ids])))).all() if failed_ids else []
    if orphan:
        findings.append(_finding("FAILED_ATTEMPTS", "HIGH",
                                 f"{len(failed_ids)} failed generation attempts are kept for debugging and excluded from the story; "
                                 f"{len(orphan)} of their checkpoints can be removed.", [], {"turn_ids": failed_ids}, "failed", len(failed_ids)))
    return {"campaign_id": str(campaign.id), "branch_id": str(branch.id), "head_turn_index": head_index,
            "findings": findings,
            "counts": {tier: sum(1 for finding in findings if finding["confidence"] == tier) for tier in ("HIGH", "PROBABLE", "AMBIGUOUS")}}


async def apply_repair(session: AsyncSession, campaign: Campaign, branch: Branch, selected: set[str], *,
                       include_high_confidence: bool = True) -> dict[str, Any]:
    report = await analyze_campaign(session, campaign, branch)
    log = StoreLog()
    applied: list[str] = []
    chosen = [finding for finding in report["findings"]
              if finding["id"] in selected or (include_high_confidence and finding["auto"])]
    order = ["DUPLICATE_CHARACTER", "RECOVER_CHARACTER_HISTORY", "ABILITY_CATALOGUE", "DUPLICATE_ITEM", "DUPLICATE_OBJECTIVE",
             "STALE_OBJECTIVE", "NOISE_RELATIONSHIP", "DUPLICATE_MEMORY", "TRUNCATED_STATUS", "WORLD_TIME", "FAILED_ATTEMPTS",
             "STALE_SUMMARY"]
    chosen.sort(key=lambda finding: order.index(finding["type"]) if finding["type"] in order else 99)
    turns = await history_for_branch(session, branch.head_turn_id, limit=100_000)
    head_index = turns[-1].turn_index if turns else 0
    for finding in chosen:
        kind, payload = finding["type"], finding["payload"]
        if kind == "DUPLICATE_CHARACTER":
            target = await session.get(Character, UUID(payload["target_id"]))
            for source_id in payload["source_ids"]:
                source = await session.get(Character, UUID(source_id))
                if source and target and source.branch_id == target.branch_id:
                    target = await merge_characters(session, source, target, turn_index=head_index,
                                                    reason="; ".join(finding["evidence"])[:300] or "Repair", log=log)
        elif kind == "RECOVER_CHARACTER_HISTORY":
            await _recover_history(session, campaign, branch, turns, log)
            state = dict(branch.current_state or {})
            state["repairs"] = {**(state.get("repairs") or {}), "history_recovered_through": head_index}
            branch.current_state = state
        elif kind == "ABILITY_CATALOGUE":
            await _build_catalogue(session, campaign, branch, turns, log)
        elif kind == "DUPLICATE_ITEM":
            keep = await session.get(Item, UUID(payload["keep_id"]))
            duplicate = await session.get(Item, UUID(payload["duplicate_id"]))
            if keep and duplicate:
                keep.quantity = max(keep.quantity, duplicate.quantity)
                keep.aliases = list(dict.fromkeys([*(keep.aliases or []), duplicate.name, *(duplicate.aliases or [])]))[:12]
                await session.delete(duplicate)
        elif kind == "DUPLICATE_OBJECTIVE":
            keep = await session.get(Objective, UUID(payload["keep_id"]))
            duplicate = await session.get(Objective, UUID(payload["duplicate_id"]))
            if keep and duplicate:
                keep.aliases = list(dict.fromkeys([*(keep.aliases or []), duplicate.title, *(duplicate.aliases or [])]))[:12]
                if len(duplicate.description or "") > len(keep.description or ""):
                    keep.description = duplicate.description
                duplicate.status = "superseded"
                duplicate.resolution_note = f"Same goal as '{keep.title}'."
        elif kind == "STALE_OBJECTIVE":
            objective = await session.get(Objective, UUID(payload["objective_id"]))
            if objective and objective.status == "active":
                objective.criteria = payload.get("criteria") or objective.criteria
                objective.status = "completed"
                objective.completed_turn_index = head_index
                objective.resolution_note = payload.get("note", "")
        elif kind == "NOISE_RELATIONSHIP":
            await session.execute(delete(CharacterRelationship).where(
                CharacterRelationship.id.in_([UUID(value) for value in payload["relationship_ids"]])))
        elif kind == "DUPLICATE_MEMORY":
            await session.execute(delete(Memory).where(Memory.id.in_([UUID(value) for value in payload["memory_ids"]])))
        elif kind == "TRUNCATED_STATUS":
            await _restore_statuses(session, campaign, branch, turns)
        elif kind == "WORLD_TIME":
            state = dict(branch.current_state or {})
            clock = world_time.initial_clock()
            for turn in turns:
                seconds = int((turn.state_delta or {}).get("time_elapsed_seconds") or 0)
                clock = world_time.advance({"world_clock": clock}, seconds, turn.gm_response or "")
            state["world_clock"] = clock
            state["world_time"] = clock["label"]
            state["elapsed_seconds"] = clock["elapsed_seconds"]
            branch.current_state = state
        elif kind == "FAILED_ATTEMPTS":
            await session.execute(delete(Checkpoint).where(
                Checkpoint.branch_id == branch.id, Checkpoint.turn_id.in_([UUID(value) for value in payload["turn_ids"]])))
        elif kind == "STALE_SUMMARY":
            enqueue(session, campaign_id=campaign.id, branch_id=branch.id, kind="SUMMARY_UPDATE", payload={"force": True})
        applied.append(finding["id"])
    await session.flush()
    await _refresh_derived(session, campaign, branch)
    if branch.head_turn_id and applied:
        from app.services.state_service import capture_snapshot
        head = await session.get(Turn, branch.head_turn_id)
        if head:
            await session.execute(delete(Checkpoint).where(Checkpoint.branch_id == branch.id, Checkpoint.turn_id == head.id,
                                                           Checkpoint.turn_index == head.turn_index))
            session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=head.id,
                                   turn_index=head.turn_index, state_snapshot=await capture_snapshot(session, branch)))
    return {"applied": applied, "log": log.as_dict()}


async def _recover_history(session: AsyncSession, campaign: Campaign, branch: Branch, turns: list[Turn], log: StoreLog) -> None:
    resolver, characters = await build_resolver(session, campaign, branch.id, branch.current_state or {})
    for turn in turns:
        for operation in (turn.state_delta or {}).get("operations", []) or []:
            if not isinstance(operation, dict) or operation.get("kind") not in CHARACTER_KINDS:
                continue
            value = operation.get("value")
            if not isinstance(value, dict):
                continue
            reference = str(operation.get("resolved_name") or operation.get("name") or operation.get("subject") or "")
            result = resolver.resolve(reference, id_hint=operation.get("character_id"))
            if result.status not in {"RESOLVED", "PLAYER"} or not result.character_id or result.confidence < 0.85:
                continue
            character = characters.get(str(result.character_id))
            if character is None:
                continue
            payload = {key: value[key] for key in ("known_facts", "role", "gender", "first_meeting_place", "appearance")
                       if key in value}
            if character.name != campaign.protagonist_name or "known_facts" in payload:
                await merge_character_values(session, character, payload, provenance="DIRECT_OBSERVATION",
                                             certainty=str(operation.get("certainty") or "CONFIRMED"), visibility="PLAYER_KNOWN",
                                             turn_id=turn.id, turn_index=turn.turn_index,
                                             is_player=character.name == campaign.protagonist_name, resolver=resolver, log=log)
            if not character.first_seen_turn_index or turn.turn_index < character.first_seen_turn_index:
                character.first_seen_turn_index = turn.turn_index
            character.last_seen_turn_index = max(character.last_seen_turn_index or 0, turn.turn_index)
            if result.method not in {"id", "canonical_name", "canonical_name_normalized"} and not is_unknown(reference) \
                    and result.confidence >= 0.9:
                await add_alias(session, character, reference, turn_index=turn.turn_index, source="repair", resolver=resolver, log=log)


async def _restore_statuses(session: AsyncSession, campaign: Campaign, branch: Branch, turns: list[Turn]) -> None:
    resolver, characters = await build_resolver(session, campaign, branch.id, branch.current_state or {})
    state = dict(branch.current_state or {})
    for turn in turns:
        for operation in (turn.state_delta or {}).get("operations", []) or []:
            if not isinstance(operation, dict) or not isinstance(operation.get("value"), dict):
                continue
            status = operation["value"].get("status")
            if not isinstance(status, str) or not status.strip():
                continue
            result = resolver.resolve(str(operation.get("name") or operation.get("subject") or ""))
            if result.status == "PLAYER":
                if str(state.get("player_status") or "") and status.startswith(str(state.get("player_status"))):
                    state["player_status"] = status[:240]
                    state["player_condition"] = {**(state.get("player_condition") or {}), "physical_status": status[:240]}
                player = characters.get(str(result.character_id)) if result.character_id else None
                if player and player.status and status.startswith(player.status) and len(player.status) == 32:
                    player.status = status[:160]
            elif result.status == "RESOLVED" and result.character_id in characters:
                character = characters[result.character_id]
                if character.status and len(character.status) == 32 and status.startswith(character.status):
                    character.status = status[:160]
    branch.current_state = state


async def _build_catalogue(session: AsyncSession, campaign: Campaign, branch: Branch, turns: list[Turn], log: StoreLog) -> None:
    player = await session.scalar(select(Character).where(Character.branch_id == branch.id,
                                                          Character.name.ilike(campaign.protagonist_name)))
    if player is None:
        return
    derived = derive_constitution(campaign.original_prompt)
    constitution = dict(campaign.constitution or {})
    if not constitution.get("ability_catalogue"):
        constitution["ability_catalogue"] = derived.ability_catalogue
        constitution["rules"] = [*constitution.get("rules", []), *[rule for rule in derived.rules if rule["type"] == "ABILITY"]]
        campaign.constitution = constitution
    for entry in constitution.get("ability_catalogue") or []:
        await gain_ability(session, player, entry, provenance="PLAYER_EXPLICIT", turn_index=0, log=log,
                           source_default="campaign_setup")
    for turn in turns:
        for operation in (turn.state_delta or {}).get("operations", []) or []:
            if not isinstance(operation, dict) or not isinstance(operation.get("value"), dict):
                continue
            if normalize_reference(operation.get("name") or operation.get("subject") or "") not in {
                    normalize_reference(player.name), "player"}:
                continue
            for key in ABILITY_ATTRIBUTE_KEYS - {"abilities", "powers"}:
                entries = operation["value"].get(key)
                for entry in entries if isinstance(entries, list) else [entries] if isinstance(entries, str) else []:
                    if isinstance(entry, str) and entry.strip():
                        await gain_ability(session, player, {"name": entry.strip()[:120], "source": "stolen in play"},
                                           provenance="DIRECT_OBSERVATION", turn_index=turn.turn_index, log=log)


async def _refresh_derived(session: AsyncSession, campaign: Campaign, branch: Branch) -> None:
    characters = list((await session.scalars(select(Character).where(Character.branch_id == branch.id))).all())
    objectives = list((await session.scalars(select(Objective).where(Objective.branch_id == branch.id))).all())
    linked = {value for objective in objectives for value in (objective.related_character_ids or [])}
    for objective in objectives:
        character_id = (objective.criteria or {}).get("character_id")
        if character_id:
            linked.add(character_id)
    relations = (await session.scalars(select(CharacterRelationship).where(CharacterRelationship.branch_id == branch.id))).all()
    strength: dict[str, float] = {}
    for relation in relations:
        dims = relation.dimensions or {}
        value = max([abs(float(dims[axis]) - (50 if axis in {"trust", "respect"} else 0))
                     for axis in ("trust", "respect", "fear", "hostility") if isinstance(dims.get(axis), (int, float))] or [0])
        for character_id in (str(relation.from_character_id), str(relation.to_character_id)):
            strength[character_id] = max(strength.get(character_id, 0), value)
    for character in characters:
        await sync_fact_mirror(session, character)
        character.importance = compute_importance(
            character, relationship_strength=strength.get(str(character.id), 0), objective_linked=str(character.id) in linked,
            fact_count=len((character.attributes or {}).get("known_facts") or []),
            is_player=normalize_reference(character.name) == normalize_reference(campaign.protagonist_name))
    await session.flush()


async def repair_campaign_cli(campaign_id: str, *, apply: bool, include: list[str]) -> dict[str, Any]:
    from app.db.session import SessionLocal

    async with SessionLocal() as session:
        campaign = await session.get(Campaign, UUID(campaign_id))
        if not campaign:
            raise SystemExit(f"Campaign {campaign_id} not found.")
        branch = await session.get(Branch, campaign.active_branch_id)
        if not apply:
            report = await analyze_campaign(session, campaign, branch)
            await session.rollback()
            return report
        result = await apply_repair(session, campaign, branch, set(include))
        await session.commit()
        return result
