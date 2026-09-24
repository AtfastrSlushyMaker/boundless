"""Canonical state persistence and the deterministic reconciliation pipeline.

Pipeline for one turn (all inside the canonical turn transaction):

    LLM state proposal -> schema validation -> entity resolution -> canon validation
    -> merge-safe application -> deterministic reconciliation (reveals, scene, objectives,
    importance, memory) -> caller writes the checkpoint and commits.

The model proposes; nothing here trusts it with database identity.
"""

import re
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import DateTime, Uuid

from app.db.base import Base
from app.db.models import (
    Ability,
    Branch,
    Campaign,
    CampaignSummary,
    CanonRule,
    Character,
    CharacterAlias,
    CharacterFact,
    CharacterRelationship,
    Event,
    Faction,
    FactionRelationship,
    Item,
    Location,
    Memory,
    Objective,
    RelationshipEvent,
    Secret,
    Turn,
)
from app.schemas import StateInterpretation
from app.services import objectives as objective_rules
from app.services import world_time
from app.services.abilities import gain_ability, lose_ability
from app.services.canon_guard import (
    CanonViolation,
    is_dead,
    player_death_stated,
    resurrection_allowed,
    validate_general_operation,
    validate_state_operation,
)
from app.services.character_store import (
    StoreLog,
    add_alias,
    add_fact,
    build_resolver,
    candidate_for,
    ensure_identity_rows,
    merge_character_values,
    merge_characters,
    rename_character,
    sync_fact_mirror,
)
from app.services.constitution import _explicit_identity
from app.services.entity_resolver import (
    AUTO_ACCEPT,
    FORMER_ROLE,
    CharacterCandidate,
    EntityResolver,
    Resolution,
    detect_identity_reveals,
    resolve_named,
)
from app.services.identity import (
    GENERIC_PERSON_NOUNS,
    is_generic_reference,
    is_mentioned,
    is_unknown,
    looks_like_proper_name,
    normalize_reference,
    provenance_for_certainty,
    same_statement,
)
from app.services.memory_service import add_memory
from app.services.mood import read_mood
from app.services.relationships import apply_relationship_change

SNAPSHOT_MODELS = [
    CanonRule, Faction, Character, CharacterAlias, CharacterFact, Ability, Location, Item, FactionRelationship,
    CharacterRelationship, RelationshipEvent, Event, Memory, Secret, Objective, CampaignSummary,
]
SNAPSHOT_SKIP_COLUMNS = {"embedding"}
ABILITY_ATTRIBUTE_KEYS = {"abilities", "stolen_magic", "stolen_spells", "known_spells", "spells", "powers", "skills",
                          "copied_spells", "stolen_abilities"}
PLAYER_STATUS_LIMIT = 240
QUOTED = re.compile(r"[\"“][^\"”]*[\"”]")
NUMBER_WORDS = {"two", "three", "four", "five", "six", "several", "many", "some", "few"}


def player_says_alive(action: str, name: str) -> bool:
    return bool(re.search(
        rf"\b(?:I am|I'm|{re.escape(name)} is)\s+(?:not dead(?: yet)?|alive)\b",
        action, re.IGNORECASE))


async def capture_snapshot(session: AsyncSession, branch: Branch) -> dict[str, Any]:
    result: dict[str, Any] = {"current_state": deepcopy(branch.current_state or {})}
    for model in SNAPSHOT_MODELS:
        rows = (await session.scalars(select(model).where(model.branch_id == branch.id))).all()
        result[model.__tablename__] = [jsonable_encoder({column.name: getattr(row, column.name)
                                                         for column in model.__table__.columns
                                                         if column.name not in SNAPSHOT_SKIP_COLUMNS}) for row in rows]
    return result


def _coerce_row(model: type[Base], values: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for column in model.__table__.columns:
        if column.name not in values or column.name in SNAPSHOT_SKIP_COLUMNS:
            continue
        value = values[column.name]
        if isinstance(column.type, Uuid) and isinstance(value, str):
            value = UUID(value) if value else None
        elif isinstance(column.type, DateTime) and isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        result[column.name] = value
    return result


async def restore_snapshot(session: AsyncSession, branch: Branch, snapshot: dict[str, Any]) -> None:
    branch.current_state = deepcopy(snapshot.get("current_state", {}))
    # Remove dependent rows before their referenced entities.
    for model in reversed(SNAPSHOT_MODELS):
        await session.execute(delete(model).where(model.branch_id == branch.id))
    await session.flush()
    character_ids: set[str] = set()
    relationship_ids: set[str] = set()
    for model in SNAPSHOT_MODELS:
        for payload in snapshot.get(model.__tablename__, []):
            row_data = _coerce_row(model, payload)
            row_data["branch_id"] = branch.id
            # Older or edited snapshots can reference rows that no longer exist; skip those children.
            if "character_id" in row_data and model is not Memory and str(row_data["character_id"]) not in character_ids:
                continue
            if model is RelationshipEvent and str(row_data.get("relationship_id")) not in relationship_ids:
                continue
            if model is CharacterRelationship and not {str(row_data.get("from_character_id")),
                                                       str(row_data.get("to_character_id"))} <= character_ids:
                continue
            session.add(model(**row_data))
            if model is Character:
                character_ids.add(str(row_data.get("id")))
            if model is CharacterRelationship:
                relationship_ids.add(str(row_data.get("id")))
        await session.flush()
    await ensure_identity_rows(session, branch.id)


def _is_group(name: str) -> bool:
    tokens = normalize_reference(name).split()
    if not tokens:
        return False
    plural = tokens[-1].endswith("s") and tokens[-1][:-1] in GENERIC_PERSON_NOUNS
    return plural or tokens[0] in NUMBER_WORDS


def compute_importance(character: Character, *, relationship_strength: float, objective_linked: bool,
                       fact_count: int, is_player: bool) -> str:
    if is_player:
        return "MAJOR"
    attributes = character.attributes or {}
    explicit = str(attributes.get("importance_override") or "").upper()
    if explicit in {"BACKGROUND", "MINOR", "RECURRING", "MAJOR", "COMPANION"}:
        return explicit
    # Only the player's companions: "armed companion of the masked man" is someone else's.
    if attributes.get("companion") is True or re.search(r"\b(?:your|player'?s?) (?:companion|ally)\b|\bparty member\b",
                                                         character.role or "", re.IGNORECASE):
        return "COMPANION"
    if _is_group(character.name):
        return "BACKGROUND"
    seen = attributes.get("seen_count") if isinstance(attributes.get("seen_count"), int) else 0
    named = looks_like_proper_name(character.name)
    leader = bool(re.search(r"\b(?:leader|high priest|high priestess|king|queen|lord|captain|master)\b", character.role or "", re.IGNORECASE))
    score = seen + (4 if named else 0) + (4 if objective_linked else 0) + (4 if relationship_strength >= 30 else 0) \
        + (3 if leader else 0) + min(fact_count, 10) / 2
    if score >= 18 or (named and seen >= 10):
        return "MAJOR"
    if score >= 8 or seen >= 3:
        return "RECURRING"
    if named or seen >= 1 or fact_count:
        return "MINOR"
    return "BACKGROUND"


def narration_without_speech(text: str) -> str:
    return QUOTED.sub(" ", text or "")


class StateApplier:
    def __init__(self, session: AsyncSession, campaign: Campaign, branch: Branch, turn: Turn, log: StoreLog) -> None:
        self.session = session
        self.campaign = campaign
        self.branch = branch
        self.turn = turn
        self.log = log
        self.narration = turn.gm_response or ""
        self.state = dict(branch.current_state or {})
        self.applied: list[dict[str, Any]] = []
        self.changes: list[dict[str, str]] = []
        self.touched: set[str] = set()
        self.present: set[str] = set()
        self.memories: list[Memory] = []
        self.revealed: set[str] = set()
        self.created: set[str] = set()
        self.mentions: dict[str, str] = {}

    # --- setup ---------------------------------------------------------------------------
    async def setup(self) -> None:
        rows = await self.session.scalars(select(CanonRule).where(
            CanonRule.campaign_id == self.campaign.id,
            (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == self.branch.id))))
        self.rules = [{"rule_type": row.rule_type, "statement": row.statement, "strength": row.strength,
                       "exceptions": row.exceptions} for row in rows.all()]
        self.resurrection = resurrection_allowed(self.campaign.constitution or {}, self.rules)
        self.resolver, self.characters = await build_resolver(self.session, self.campaign, self.branch.id, self.state)
        player_id = next((candidate.id for candidate in self.resolver.candidates if candidate.is_player), None)
        if player_id is None:
            player = Character(campaign_id=self.campaign.id, branch_id=self.branch.id, name=self.campaign.protagonist_name,
                               role="Player character", attributes={}, visibility="PLAYER_KNOWN", importance="MAJOR")
            self.session.add(player)
            await self.session.flush()
            await add_alias(self.session, player, player.name, "CANONICAL_NAME", source="system", confidence=1.0)
            self.characters[str(player.id)] = player
            self.resolver.add(candidate_for(player, [], self.campaign.protagonist_name, set()))
            player_id = str(player.id)
        self.player = self.characters[player_id]
        self.recent_ids = {candidate.id for candidate in self.resolver.candidates
                           if candidate.last_seen is not None and self.turn.turn_index - candidate.last_seen <= 3}
        self.reveals = {reveal.name.casefold(): reveal for reveal in
                        detect_identity_reveals(self.narration, self.resolver, self.recent_ids)}

    # --- helpers -------------------------------------------------------------------------
    def change(self, kind: str, text: str) -> None:
        if len(self.changes) < 60 and not any(entry["text"] == text for entry in self.changes):
            self.changes.append({"type": kind, "text": text[:240]})

    def resolve(self, reference: str, id_hint: str | None = None) -> Resolution:
        if id_hint and id_hint not in self.resolver.by_id:
            # Small models copy example ids; an unknown id must never become a new person's name.
            if self.resolver.is_player_reference(id_hint) and not reference:
                reference = id_hint
            id_hint = None
        result = self.resolver.resolve(reference, id_hint=id_hint)
        if result.status != "RESOLVED" or result.method not in {"id", "canonical_name"}:
            self.log.resolutions.append(result.as_dict())
        if result.status == "RESOLVED" and result.method not in {"id", "canonical_name", "canonical_name_normalized"}:
            self.log.metrics["aliases_resolved"] += 1
        if result.status == "AMBIGUOUS":
            self.log.metrics["ambiguous_references"] += 1
        return result

    async def create_character(self, name: str, *, visibility: str, role: str = "") -> Character:
        name = " ".join(name.split())[:120]
        character = Character(campaign_id=self.campaign.id, branch_id=self.branch.id, name=name, role="",
                              attributes={}, visibility=visibility, first_seen_turn_index=self.turn.turn_index,
                              importance="BACKGROUND" if is_generic_reference(name) else "MINOR")
        self.session.add(character)
        await self.session.flush()
        await add_alias(self.session, character, name, "CANONICAL_NAME", turn_index=self.turn.turn_index,
                        confidence=1.0, log=None)
        self.characters[str(character.id)] = character
        self.resolver.add(CharacterCandidate(id=str(character.id), name=name, aliases=[(name, "CANONICAL_NAME")],
                                             last_seen=self.turn.turn_index))
        self.created.add(str(character.id))
        self.log.metrics["characters_created"] += 1
        self.change("character", f"New character: {name}")
        return character

    async def person(self, reference: str, id_hint: str | None, *, create: bool, visibility: str = "PLAYER_KNOWN",
                     operation: dict | None = None) -> Character | None:
        if id_hint and id_hint in self.mentions:
            # A temporary id we offered for a person who is not saved yet.
            reference = reference or self.mentions[id_hint]
            self.log.metrics["new_person_ids_used"] += 1
            id_hint = None
        if id_hint and id_hint not in self.resolver.by_id and not self.resolver.is_player_reference(id_hint):
            if not reference:
                self.log.reject(operation or {"kind": "REFERENCE", "name": id_hint}, "unknown_id")
                self.log.metrics["invented_ids"] += 1
                return None
            id_hint = None
        result = self.resolve(reference, id_hint)
        if result.status == "PLAYER":
            return self.player
        if result.status == "RESOLVED" and result.confidence >= AUTO_ACCEPT:
            character = self.characters.get(str(result.character_id))
            if character and result.confidence >= 0.85 and result.method in {"title", "descriptor", "alias", "partial_name"} \
                    and not is_unknown(reference) and normalize_reference(reference) != normalize_reference(character.name):
                await add_alias(self.session, character, reference, turn_index=self.turn.turn_index,
                                resolver=self.resolver, log=self.log)
            return character
        if result.status == "AMBIGUOUS":
            self.log.reject(operation or {"kind": "REFERENCE", "name": reference},
                            f"ambiguous_reference:{','.join(result.candidates[:4])}")
            return None
        reveal = self.reveals.get(reference.casefold()) if reference else None
        if reveal and reveal.character_id in self.characters:
            character = self.characters[reveal.character_id]
            await self.reveal(character, reference, role=reveal.former_role, evidence=reveal.evidence)
            return character
        if not create or not reference or is_unknown(reference):
            if reference:
                self.log.reject(operation or {"kind": "REFERENCE", "name": reference}, "unknown_reference")
            return None
        return await self.create_character(reference, visibility=visibility)

    async def reveal(self, character: Character, new_name: str, *, role: str = "", evidence: str = "",
                     aliases: list[str] | None = None) -> Character:
        new_name = " ".join(str(new_name).split())[:120]
        if not new_name or is_unknown(new_name):
            return character
        other = self.resolver.resolve(new_name)
        target = character
        if other.status == "RESOLVED" and other.character_id != str(character.id) and \
                other.method in {"canonical_name", "canonical_name_normalized", "alias"}:
            existing = self.characters.get(str(other.character_id))
            if existing and existing.id != self.player.id:
                target = await merge_characters(self.session, character, existing, turn_index=self.turn.turn_index,
                                                reason=f"Identity revealed: {evidence or new_name}", log=self.log,
                                                resolver=self.resolver)
                self.characters.pop(str(character.id), None)
        else:
            previous = character.name
            await rename_character(self.session, character, new_name, turn_index=self.turn.turn_index,
                                   resolver=self.resolver, log=self.log)
            if previous != character.name:
                self.change("identity", f"{previous} is {character.name}")
        if role and not is_unknown(role):
            await merge_character_values(self.session, target, {"role": role}, provenance="DIRECT_OBSERVATION",
                                         certainty="CONFIRMED", visibility="PLAYER_KNOWN", turn_id=self.turn.id,
                                         turn_index=self.turn.turn_index, is_player=False, resolver=self.resolver,
                                         log=self.log)
            await add_alias(self.session, target, role, "TITLE", turn_index=self.turn.turn_index,
                            resolver=self.resolver, log=self.log)
        for alias in aliases or []:
            await add_alias(self.session, target, alias, turn_index=self.turn.turn_index, resolver=self.resolver, log=self.log)
        if evidence:
            await add_fact(self.session, target, evidence if len(evidence) < 300 else evidence[:297] + "…",
                           fact_type="identity", turn_id=self.turn.id, turn_index=self.turn.turn_index, log=self.log)
        self.revealed.add(str(target.id))
        self.touched.add(str(target.id))
        self.present.add(str(target.id))
        return target

    def item_rows(self, owner: str | None = None) -> list[tuple[str, str, list[str]]]:
        return [(str(item.id), item.name, list(item.aliases or [])) for item in self.items
                if owner is None or normalize_reference(item.owner_name) == normalize_reference(owner)
                or owner == self.player.name and normalize_reference(item.owner_name) in {"player", "you"}]

    # --- operations ----------------------------------------------------------------------
    async def run(self, interpretation: StateInterpretation) -> None:
        self.mentions = dict(interpretation.mention_ids or {})
        self.items = list((await self.session.scalars(select(Item).where(Item.branch_id == self.branch.id))).all())
        self.objectives = list((await self.session.scalars(select(Objective).where(Objective.branch_id == self.branch.id))).all())
        self.locations = list((await self.session.scalars(select(Location).where(Location.branch_id == self.branch.id))).all())
        self.pre_mark_presence()
        await self.title_reveals()
        for operation_model in interpretation.state_changes[:80]:
            operation = operation_model.model_dump(mode="json")
            try:
                await self.apply_operation(operation)
            except CanonViolation as exc:
                self.log.canon.append({"kind": operation.get("kind"), "violation": str(exc)})
                self.log.reject(operation, "canon_violation")
        for reveal in list(self.reveals.values()):
            if reveal.character_id in self.characters and reveal.character_id not in self.revealed:
                await self.reveal(self.characters[reveal.character_id], reveal.name, role=reveal.former_role,
                                  evidence=reveal.evidence)
                self.log.note("reveal_detected_in_narration", name=reveal.name)
        for change in interpretation.relationship_changes[:30]:
            await self.relationship(change)
        await self.knowledge(interpretation.knowledge_changes[:40])
        for event in interpretation.events[:30]:
            await self.event(event)
        for memory in interpretation.new_memories[:20]:
            await self.memory(memory)
        await self.finish(interpretation)

    async def apply_operation(self, operation: dict[str, Any]) -> None:
        kind = operation["kind"]
        value = operation.get("value") or {}
        if kind in {"UPDATE_CHARACTER", "CHANGE_CHARACTER_STATUS"}:
            person = str(operation.get("name") or operation.get("subject") or "")
            status = str(value.get("status", "")).casefold()
            if self.resolver.is_player_reference(person) and status in {"dead", "deceased"} \
                    and not player_death_stated(self.narration, self.campaign.protagonist_name):
                self.log.reject(operation, "player_death_not_in_narration")
                return
        validate_state_operation(operation, self.campaign.constitution or {}, self.rules, self.state)
        handler = {
            "CREATE_CHARACTER": self.character_op, "UPDATE_CHARACTER": self.character_op,
            "MOVE_CHARACTER": self.character_op, "CHANGE_CHARACTER_STATUS": self.character_op,
            "ADD_CHARACTER_ALIAS": self.character_op, "ADD_CHARACTER_FACT": self.character_op,
            "REVEAL_CHARACTER_IDENTITY": self.reveal_op, "MERGE_CHARACTERS": self.merge_op,
            "ADD_ITEM": self.item_op, "REMOVE_ITEM": self.item_op, "TRANSFER_ITEM": self.item_op, "UPDATE_ITEM": self.item_op,
            "CREATE_LOCATION": self.location_op, "UPDATE_LOCATION": self.location_op,
            "CREATE_EVENT": self.event_op, "CREATE_MEMORY": self.memory_op,
            "CHANGE_RELATIONSHIP": self.relationship_op, "CREATE_FACTION": self.faction_op,
            "CHANGE_FACTION_RELATIONSHIP": self.faction_relationship_op, "ADVANCE_WORLD_TIME": self.time_op,
            "CREATE_SECRET": self.secret_op, "REVEAL_SECRET": self.secret_op,
            "CREATE_OBJECTIVE": self.objective_op, "UPDATE_OBJECTIVE": self.objective_op,
            "COMPLETE_OBJECTIVE": self.objective_op, "FAIL_OBJECTIVE": self.objective_op,
            "UPDATE_MONEY": self.money_op, "ADD_CANON_RULE": self.canon_rule_op, "MODIFY_CANON_RULE": self.canon_rule_op,
            "GAIN_ABILITY": self.ability_op, "LOSE_ABILITY": self.ability_op,
        }[kind]
        if await handler(operation) is not False:
            self.applied.append(operation)

    async def title_reveals(self) -> None:
        """'I was the High Archivist before this one', said by a present, named person, sets their role."""
        for match in FORMER_ROLE.finditer(self.narration):
            title = f"former {match.group('title')}"
            before = self.narration[max(0, match.start() - 400):match.start()]
            speaker = None
            best = -1
            for candidate_id in self.present | {reveal.character_id for reveal in self.reveals.values()}:
                character = self.characters.get(candidate_id)
                if character is None or character.id == self.player.id:
                    continue
                names = [character.name] + [reveal.name for reveal in self.reveals.values() if reveal.character_id == candidate_id]
                for name in names:
                    position = before.rfind(name)
                    if position > best:
                        best, speaker = position, character
            if speaker is None:
                continue
            await merge_character_values(self.session, speaker, {"role": title}, provenance="DIRECT_OBSERVATION",
                                         certainty="CONFIRMED", visibility="PLAYER_KNOWN", turn_id=self.turn.id,
                                         turn_index=self.turn.turn_index, is_player=False, resolver=self.resolver, log=self.log)
            if str(speaker.id) in self.resolver.by_id:
                self.resolver.by_id[str(speaker.id)].role = speaker.role
            await add_alias(self.session, speaker, title, "TITLE", turn_index=self.turn.turn_index,
                            resolver=self.resolver, log=self.log)
            self.touched.add(str(speaker.id))
            self.log.note("title_revealed", character=speaker.name, title=title)

    def mentioned_outside_speech(self, character: Character, reference: str) -> bool:
        """Created people count as met unless the narration only mentions them inside dialogue."""
        names = [value for value in {reference, character.name} if value and len(value) >= 3]
        full = [value for value in names if is_mentioned(self.narration, [value])]
        if not full:
            return True
        return is_mentioned(narration_without_speech(self.narration), full)

    def pre_mark_presence(self) -> None:
        text = narration_without_speech(self.narration)
        scene = set((self.state.get("scene") or {}).get("character_ids") or [])
        for candidate in self.resolver.candidates:
            if candidate.is_player:
                continue
            for value in [candidate.name, *(alias for alias, _ in candidate.aliases)]:
                if len(value) < 3 or is_unknown(value):
                    continue
                generic = is_generic_reference(value)
                if generic and candidate.id not in scene:
                    continue
                if is_mentioned(text, [value]):
                    self.present.add(candidate.id)
                    break

    async def character_op(self, operation: dict[str, Any]) -> bool:
        kind = operation["kind"]
        value = dict(operation.get("value") or {})
        reference = str(operation.get("name") or operation.get("subject") or value.get("name") or "").strip()
        if kind == "MOVE_CHARACTER" and operation.get("subject") and not self.resolver.is_player_reference(reference):
            reference = str(operation.get("subject"))
        visibility = operation.get("visibility", "PLAYER_KNOWN")
        character = await self.person(reference, operation.get("character_id"),
                                      create=kind in {"CREATE_CHARACTER", "UPDATE_CHARACTER"}, visibility=visibility,
                                      operation=operation)
        if character is None:
            return False
        existed = str(character.id) not in self.created
        if kind == "CREATE_CHARACTER" and existed:
            self.log.metrics["duplicate_prevented"] += 1
            self.log.note("create_resolved_to_existing", reference=reference, character=character.name)
        if isinstance(value.get("canonical_name"), str) and looks_like_proper_name(value["canonical_name"]) and \
                character.id != self.player.id and normalize_reference(value["canonical_name"]) != normalize_reference(character.name):
            character = await self.reveal(character, value["canonical_name"], evidence="")
        is_player = character.id == self.player.id
        provenance = provenance_for_certainty(operation.get("certainty"))
        if kind == "ADD_CHARACTER_ALIAS":
            alias = str(value.get("alias") or value.get("name") or "")
            await add_alias(self.session, character, alias, value.get("alias_type"), turn_index=self.turn.turn_index,
                            resolver=self.resolver, log=self.log)
        if kind == "ADD_CHARACTER_FACT":
            value = {"known_facts": [value.get("fact") or value.get("content") or ""]}
        if is_player:
            await self.player_changes(value, kind)
        status_value = value.get("status") if isinstance(value.get("status"), str) else None
        if status_value and not is_player:
            reason = validate_general_operation(operation, current_status=character.status,
                                                resurrection=self.resurrection, player_name=self.campaign.protagonist_name)
            if reason:
                self.log.reject(operation, reason)
                self.log.canon.append({"kind": kind, "violation": reason, "character": character.name})
                value.pop("status", None)
            elif not is_unknown(status_value):
                if normalize_reference(status_value) != normalize_reference(character.status):
                    if is_dead(status_value):
                        self.change("status", f"{character.name} is dead")
                character.status = status_value.strip()[:160]
        abilities = {key: value.pop(key) for key in list(value) if key in ABILITY_ATTRIBUTE_KEYS}
        await merge_character_values(self.session, character, {key: entry for key, entry in value.items() if key != "status"},
                                     provenance=provenance, certainty=operation.get("certainty", "CONFIRMED"),
                                     visibility=visibility, turn_id=self.turn.id, turn_index=self.turn.turn_index,
                                     is_player=is_player, resolver=self.resolver, log=self.log)
        for entries in abilities.values():
            for entry in entries if isinstance(entries, list) else [entries]:
                if isinstance(entry, str) and entry.strip():
                    await self.gain(character, {"name": entry, "source": "established in play"}, provenance)
                elif isinstance(entry, dict):
                    await self.gain(character, entry, provenance)
        if kind == "CREATE_CHARACTER" and not is_player and self.mentioned_outside_speech(character, reference):
            self.present.add(str(character.id))
        if kind == "MOVE_CHARACTER" and not is_player and isinstance(value.get("location"), str):
            character.attributes = {**(character.attributes or {}), "location": value["location"][:160]}
        self.touched.add(str(character.id))
        operation["character_id"] = str(character.id)
        operation["resolved_name"] = character.name
        if existed and kind in {"UPDATE_CHARACTER", "CREATE_CHARACTER", "CHANGE_CHARACTER_STATUS", "ADD_CHARACTER_FACT"}:
            self.log.metrics["characters_updated"] += 1
        return True

    async def player_changes(self, value: dict[str, Any], kind: str) -> None:
        location = value.get("location")
        if isinstance(location, str) and not is_unknown(location):
            if normalize_reference(location) != normalize_reference(self.state.get("current_location", "")):
                self.change("location", f"Moved to {location}")
            self.state["current_location"] = location.strip()[:160]
            self.player.attributes = {**(self.player.attributes or {}), "location": location.strip()[:160]}
        condition = dict(self.state.get("player_condition") or {})
        status = value.get("status") or value.get("physical_status")
        if isinstance(status, str) and not is_unknown(status):
            if is_dead(status) and not player_death_stated(self.narration, self.campaign.protagonist_name):
                self.log.reject({"kind": kind, "name": self.player.name}, "player_death_not_in_narration")
            else:
                condition["physical_status"] = status.strip()[:PLAYER_STATUS_LIMIT]
        for key in ("injuries", "conditions"):
            entries = value.get(key)
            if isinstance(entries, str):
                entries = [entries]
            if isinstance(entries, list):
                merged = list(condition.get(key) or [])
                for entry in entries:
                    if isinstance(entry, str) and entry.strip() and not any(same_statement(entry, old) for old in merged):
                        merged.append(entry.strip()[:200])
                condition[key] = merged[-12:]
        healed = value.get("healed") or value.get("injuries_healed")
        if isinstance(healed, list):
            condition["injuries"] = [entry for entry in condition.get("injuries", [])
                                     if not any(same_statement(entry, str(cured)) for cured in healed)]
        if isinstance(value.get("condition"), str) and not is_unknown(value["condition"]):
            merged = list(condition.get("conditions") or [])
            if not any(same_statement(value["condition"], old) for old in merged):
                merged.append(value["condition"][:200])
            condition["conditions"] = merged[-12:]
        if condition:
            self.state["player_condition"] = condition
            parts = [condition.get("physical_status") or "alive", *condition.get("injuries", [])[-3:]]
            self.state["player_status"] = ", ".join(part for part in parts if part)[:PLAYER_STATUS_LIMIT]
            self.player.status = str(condition.get("physical_status") or self.player.status or "alive")[:160]

    async def reveal_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        previous = str(value.get("previous_name") or operation.get("subject") or "")
        new_name = str(value.get("canonical_name") or operation.get("name") or value.get("name") or "")
        character = await self.person(previous, operation.get("character_id"), create=False, operation=operation)
        if character is None and new_name:
            character = await self.person(new_name, None, create=False, operation=operation)
        if character is None or character.id == self.player.id:
            return False
        aliases = [alias for alias in value.get("aliases", []) if isinstance(alias, str)] if isinstance(value.get("aliases"), list) else []
        target = await self.reveal(character, new_name or character.name, role=str(value.get("role") or ""),
                                   evidence=str(value.get("evidence") or ""), aliases=aliases)
        operation["character_id"] = str(target.id)
        return True

    async def merge_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        source = await self.person(str(value.get("source") or operation.get("subject") or ""), operation.get("character_id"),
                                   create=False, operation=operation)
        target = await self.person(str(value.get("target") or operation.get("name") or ""), operation.get("target_id"),
                                   create=False, operation=operation)
        if not source or not target or source.id == target.id or self.player.id in {source.id, target.id}:
            self.log.reject(operation, "merge_targets_invalid")
            return False
        # Conservative: only merge when the resolver independently links the two identities.
        others = [candidate for candidate in self.resolver.candidates if candidate.id != str(source.id)]
        check = EntityResolver(others, self.campaign.protagonist_name, self.state.get("current_location", ""))
        evidence = [check.resolve(name) for name in [source.name, *(alias for alias, _ in
                    self.resolver.by_id.get(str(source.id)).aliases)]] if str(source.id) in self.resolver.by_id else []
        supported = any(result.status == "RESOLVED" and result.character_id == str(target.id) and result.confidence >= 0.85
                        for result in evidence) or str(source.id) in {reveal.character_id for reveal in self.reveals.values()}
        if not supported:
            self.log.reject(operation, "merge_without_story_evidence")
            return False
        await merge_characters(self.session, source, target, turn_index=self.turn.turn_index,
                               reason=str(value.get("reason") or "State interpreter merge"), log=self.log,
                               resolver=self.resolver)
        self.characters.pop(str(source.id), None)
        self.change("identity", f"{source.name} is {target.name}")
        return True

    async def gain(self, character: Character, value: dict[str, Any], provenance: str) -> None:
        ability = await gain_ability(self.session, character, value, provenance=provenance,
                                     turn_index=self.turn.turn_index, log=self.log, source_default="established in play")
        if ability is not None and ability.acquired_turn_index == self.turn.turn_index:
            self.change("ability", f"{'You' if character.id == self.player.id else character.name} gained {ability.name}")
            await self.memory({"content": f"{character.name} gained the ability {ability.name}."
                               + (f" Source: {ability.source}." if ability.source else ""),
                               "type": "ABILITY_GAINED", "character_ids": [str(character.id)], "characters": [character.name]})

    async def ability_op(self, operation: dict[str, Any]) -> bool:
        value = dict(operation.get("value") or {})
        reference = str(operation.get("subject") or "")
        character = self.player if not reference or self.resolver.is_player_reference(reference) else \
            await self.person(reference, operation.get("character_id"), create=False, operation=operation)
        if character is None:
            return False
        value.setdefault("name", operation.get("name"))
        if operation["kind"] == "GAIN_ABILITY":
            await self.gain(character, value, provenance_for_certainty(operation.get("certainty")))
        else:
            if await lose_ability(self.session, character, str(value.get("name") or ""), self.log):
                self.change("ability", f"Lost ability: {value.get('name')}")
                await self.memory({"content": f"{character.name} lost the ability {value.get('name')}.",
                                   "type": "ABILITY_LOST", "character_ids": [str(character.id)]})
        return True

    async def item_op(self, operation: dict[str, Any]) -> bool:
        kind = operation["kind"]
        value = operation.get("value") or {}
        name = str(operation.get("name") or value.get("item") or "").strip()[:160]
        subject = str(operation.get("subject") or "")
        if kind in {"REMOVE_ITEM", "TRANSFER_ITEM", "UPDATE_ITEM"} and not name:
            name = subject
        if not name and not operation.get("item_id"):
            self.log.reject(operation, "item_without_name")
            return False
        owner_ref = str(value.get("owner") or (subject if kind == "ADD_ITEM" else "") or self.player.name)
        owner = self.player if self.resolver.is_player_reference(owner_ref) else \
            await self.person(owner_ref, None, create=looks_like_proper_name(owner_ref), operation=operation) or self.player
        by_id = next((item for item in self.items if str(item.id) == str(operation.get("item_id"))), None)
        if kind == "ADD_ITEM":
            match = resolve_named(name, self.item_rows(owner.name))
            item = by_id or (next(item for item in self.items if str(item.id) == match.character_id) if match.status == "RESOLVED" else None)
            if item is None:
                elsewhere = resolve_named(name, self.item_rows())
                if elsewhere.status == "RESOLVED":
                    item = next(item for item in self.items if str(item.id) == elsewhere.character_id)
                    self.log.note("item_transferred_by_add", item=item.name, to=owner.name)
                    item.owner_name = owner.name
            quantity = value.get("quantity", 1)
            quantity = max(1, min(int(quantity), 999)) if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) else 1
            if item is not None:
                if value.get("delta") or value.get("gained"):
                    item.quantity += quantity
                else:
                    if item.quantity >= quantity:
                        self.log.metrics["duplicate_item_prevented"] += 1
                    item.quantity = max(item.quantity, quantity)
                if normalize_reference(name) != normalize_reference(item.name) and name not in (item.aliases or []):
                    item.aliases = [*(item.aliases or []), name][:12]
                for key, limit in (("condition", 240), ("significance", 2000)):
                    if isinstance(value.get(key), str) and not is_unknown(value[key]):
                        setattr(item, key, value[key][:limit])
                if isinstance(value.get("description"), str) and not item.significance:
                    item.significance = value["description"][:2000]
                operation["item_id"] = str(item.id)
                return True
            item = Item(campaign_id=self.campaign.id, branch_id=self.branch.id, name=name, owner_name=owner.name,
                        quantity=quantity, condition=str(value.get("condition") or "intact")[:240],
                        properties=value.get("properties") if isinstance(value.get("properties"), dict) else {},
                        significance=str(value.get("significance") or value.get("description") or "")[:2000],
                        aliases=[], visibility=operation.get("visibility", "PLAYER_KNOWN"))
            self.session.add(item)
            await self.session.flush()
            self.items.append(item)
            operation["item_id"] = str(item.id)
            if owner.id == self.player.id:
                self.change("item", f"Gained: {name}")
                await self.memory({"content": f"{self.player.name} obtained {name}.", "type": "ITEM_DISCOVERY",
                                   "items": [name], "importance": 0.55})
            return True
        match = resolve_named(name, self.item_rows(self.player.name if kind == "REMOVE_ITEM" else None))
        item = by_id or (next(item for item in self.items if str(item.id) == match.character_id) if match.status == "RESOLVED" else None)
        if item is None:
            self.log.reject(operation, "item_not_owned" if kind == "REMOVE_ITEM" else "unknown_item")
            return False
        operation["item_id"] = str(item.id)
        if kind == "REMOVE_ITEM":
            quantity = value.get("quantity", item.quantity)
            quantity = max(1, min(int(quantity), 999)) if isinstance(quantity, (int, float)) and not isinstance(quantity, bool) else item.quantity
            if item.quantity <= quantity:
                await self.session.delete(item)
                self.items.remove(item)
                self.change("item", f"Lost: {item.name}")
            else:
                item.quantity -= quantity
            return True
        if kind == "TRANSFER_ITEM":
            new_owner_ref = str(value.get("owner") or value.get("to") or "")
            new_owner = self.player if self.resolver.is_player_reference(new_owner_ref) else \
                await self.person(new_owner_ref, operation.get("character_id"), create=looks_like_proper_name(new_owner_ref),
                                  operation=operation)
            if not new_owner:
                self.log.reject(operation, "transfer_owner_unknown")
                return False
            if item.owner_name != new_owner.name:
                self.change("item", f"{item.name} → {new_owner.name}")
            item.owner_name = new_owner.name
            return True
        for key, limit in (("condition", 240), ("significance", 2000)):
            if isinstance(value.get(key), str) and not is_unknown(value[key]):
                setattr(item, key, value[key][:limit])
        return True

    async def location_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        name = str(operation.get("name") or "").strip()
        subject = str(operation.get("subject") or "").strip()
        moving_player = self.resolver.is_player_reference(name) or self.resolver.is_player_reference(subject)
        location_name = str(value.get("location") or (name if not self.resolver.is_player_reference(name) else "")
                            or (subject if not self.resolver.is_player_reference(subject) else "")).strip()[:160]
        if not location_name or is_unknown(location_name):
            return False
        if moving_player:
            await self.player_changes({"location": location_name}, operation["kind"])
        rows = [(str(row.id), row.name, list(row.aliases or [])) for row in self.locations]
        match = resolve_named(location_name, rows, min_subset_tokens=2)
        location = next((row for row in self.locations if str(row.id) in {match.character_id, operation.get("location_id")}), None)
        if location is None:
            location = Location(campaign_id=self.campaign.id, branch_id=self.branch.id, name=location_name,
                                description=str(value.get("description") or "")[:3000], region=str(value.get("region") or "")[:160],
                                properties={}, aliases=[], visibility=operation.get("visibility", "PLAYER_KNOWN"))
            self.session.add(location)
            await self.session.flush()
            self.locations.append(location)
            self.change("location", f"Discovered: {location_name}")
        else:
            if normalize_reference(location_name) != normalize_reference(location.name) and location_name not in (location.aliases or []):
                location.aliases = [*(location.aliases or []), location_name][:12]
            description = value.get("description")
            if isinstance(description, str) and not is_unknown(description) and not same_statement(description, location.description or ""):
                location.description = description[:3000] if len(description) >= len(location.description or "") * 0.6 \
                    else location.description
        if isinstance(value.get("region"), str) and not is_unknown(value["region"]):
            location.region = value["region"][:160]
        properties = value.get("properties") if isinstance(value.get("properties"), dict) else {}
        flat = {key: entry for key, entry in value.items() if key not in {"location", "description", "region", "properties", "aliases"}
                and not is_unknown(entry)}
        if properties or flat:
            location.properties = {**(location.properties or {}), **properties, **flat}
        operation["location_id"] = str(location.id)
        return True

    async def event_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        return await self.event({"content": value.get("content") or operation.get("name") or operation.get("subject"),
                                 "certainty": operation.get("certainty"), "participants": value.get("participants"),
                                 "visibility": operation.get("visibility")})

    async def event(self, event: dict[str, Any]) -> bool:
        content = " ".join(str(event.get("content") or "").split())[:5000]
        if len(content) < 4:
            return False
        existing = (await self.session.scalars(select(Event).where(Event.branch_id == self.branch.id,
                                                                    Event.turn_id == self.turn.id))).all()
        if any(same_statement(row.content, content) for row in existing):
            return False
        participants = [str(name)[:120] for name in (event.get("participants") or []) if isinstance(name, str)][:20]
        if event.get("in_world_time"):
            self.turn.in_world_time = str(event["in_world_time"])[:120]
        visibility = str(event.get("visibility") or "PLAYER_KNOWN")[:24]
        self.session.add(Event(campaign_id=self.campaign.id, branch_id=self.branch.id, turn_id=self.turn.id,
                               content=content, certainty=str(event.get("certainty") or "CONFIRMED")[:16],
                               in_world_time=str(event.get("in_world_time") or "")[:120],
                               participants=participants, visibility=visibility))
        await self.memory({"content": content, "type": event.get("type") or "WORLD_EVENT", "characters": participants,
                           "visibility": visibility, "importance": event.get("importance")})
        return True

    async def memory_op(self, operation: dict[str, Any]) -> bool:
        value = dict(operation.get("value") or {})
        value.setdefault("content", value.get("summary") or operation.get("name") or "")
        value.setdefault("visibility", operation.get("visibility"))
        return await self.memory(value)

    async def memory(self, memory: dict[str, Any]) -> bool:
        names = [str(name) for name in (memory.get("characters") or []) if isinstance(name, str)]
        ids = [str(value) for value in (memory.get("character_ids") or []) if value]
        for name in names:
            result = self.resolver.resolve(name)
            if result.status in {"RESOLVED", "PLAYER"} and result.character_id and result.confidence >= AUTO_ACCEPT:
                ids.append(str(result.character_id))
        canonical = [self.characters[value].name for value in ids if value in self.characters]
        visibility = str(memory.get("visibility") or "PLAYER_KNOWN")[:24]
        row, outcome = await add_memory(
            self.session, campaign_id=self.campaign.id, branch_id=self.branch.id,
            content=str(memory.get("content") or ""), kind=memory.get("type") or memory.get("memory_type") or "WORLD_EVENT",
            importance=memory.get("importance"), confidence=memory.get("confidence", 0.8),
            visibility=visibility if visibility in {"PLAYER_KNOWN", "CHARACTER_KNOWN", "WORLD_SECRET", "GM_ONLY"} else "PLAYER_KNOWN",
            turn_id=self.turn.id, turn_index=self.turn.turn_index, characters=list(dict.fromkeys([*canonical, *names])),
            character_ids=list(dict.fromkeys(ids)), locations=memory.get("locations"), factions=memory.get("factions"),
            items=memory.get("items"), keywords=memory.get("keywords"),
            in_world_time=str((self.state.get("world_clock") or {}).get("label") or ""), pending=self.memories)
        self.log.metrics["memories_created" if outcome == "created" else "memories_deduplicated" if outcome == "deduplicated" else "memories_skipped"] += 1
        return row is not None

    async def relationship_op(self, operation: dict[str, Any]) -> bool:
        value = dict(operation.get("value") or {})
        value.setdefault("from", value.get("source") or self.player.name)
        value.setdefault("to", value.get("target") or operation.get("subject") or operation.get("name"))
        if operation.get("character_id"):
            value.setdefault("from_id", operation["character_id"])
        if operation.get("target_id"):
            value.setdefault("to_id", operation["target_id"])
        return await self.relationship(value)

    async def relationship(self, change: dict[str, Any]) -> bool:
        if not isinstance(change, dict):
            return False
        ends = []
        for side in ("from", "to"):
            reference = str(change.get(side) or change.get(f"{side}_name") or "").strip()
            hint = change.get(f"{side}_id")
            person = await self.person(reference, str(hint) if hint else None, create=looks_like_proper_name(reference),
                                       operation={"kind": "CHANGE_RELATIONSHIP", "name": reference})
            if person is None:
                return False
            ends.append(person)
        source, target = ends
        location = str(change.get("location") or self.state.get("current_location") or "")
        events = await apply_relationship_change(
            self.session, campaign_id=self.campaign.id, branch_id=self.branch.id, source=source, target=target,
            change=change, turn_id=self.turn.id, turn_index=self.turn.turn_index, location=location,
            narration=self.narration, log=self.log)
        for event in events:
            if event.delta is not None and abs(event.delta) >= 1:
                arrow = "↑" if event.delta > 0 else "↓"
                self.change("relationship", f"{source.name} → {target.name}: {event.dimension} {arrow}{abs(round(event.delta))}")
            if event.delta is not None and abs(event.delta) >= 12 and event.reason:
                await self.memory({"content": f"{source.name} toward {target.name}: {event.dimension} "
                                   f"{'rose' if event.delta > 0 else 'fell'} — {event.reason}",
                                   "type": "RELATIONSHIP", "character_ids": [str(source.id), str(target.id)],
                                   "importance": 0.65})
        if source.id != self.player.id and target.id != self.player.id and events:
            self.log.metrics["npc_npc_relationship_changes"] += 1
        self.touched |= {str(source.id), str(target.id)}
        return True

    async def knowledge(self, changes: list[dict[str, Any]]) -> None:
        for change in changes:
            if not isinstance(change, dict):
                continue
            reference = str(change.get("character") or change.get("who") or "").strip()
            fact = " ".join(str(change.get("fact") or change.get("content") or "").split())[:2000]
            if not reference or not fact:
                continue
            person = await self.person(reference, change.get("character_id"), create=False,
                                       operation={"kind": "KNOWLEDGE", "name": reference})
            if not person:
                continue
            certainty = str(change.get("certainty") or "CONFIRMED").upper()[:16]
            truth = str(change.get("truth") or change.get("knowledge_type") or
                        {"BELIEF": "BELIEF", "RUMOR": "RUMOR", "INFERRED": "INFERENCE"}.get(certainty, "CHARACTER_KNOWN")).upper()[:24]
            existing = list(person.knowledge or [])
            if any(isinstance(row, dict) and same_statement(str(row.get("fact", "")), fact) for row in existing):
                continue
            existing.append({"fact": fact, "certainty": certainty, "truth": truth,
                             "source": str(change.get("source") or "")[:160], "source_turn_id": str(self.turn.id),
                             "turn_index": self.turn.turn_index})
            person.knowledge = existing[-200:]
            self.log.metrics["knowledge_recorded"] += 1

    async def faction_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        name = str(operation.get("name") or "").strip()[:160]
        if not name:
            return False
        faction = await self.session.scalar(select(Faction).where(Faction.branch_id == self.branch.id, Faction.name.ilike(name)))
        motives = [str(part)[:300] for part in value.get("motives", [])[:20]] if isinstance(value.get("motives"), list) else []
        if faction is None:
            self.session.add(Faction(campaign_id=self.campaign.id, branch_id=self.branch.id, name=name,
                                     description=str(value.get("description") or "")[:3000], motives=motives,
                                     visibility=operation.get("visibility", "PLAYER_KNOWN")))
            self.change("faction", f"Faction: {name}")
        else:
            if isinstance(value.get("description"), str) and not is_unknown(value["description"]):
                faction.description = value["description"][:3000]
            faction.motives = list(dict.fromkeys([*(faction.motives or []), *motives]))[:20]
        return True

    async def faction_relationship_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        source_name = str(value.get("from") or operation.get("subject") or "").strip()[:160]
        target_name = str(value.get("to") or operation.get("name") or "").strip()[:160]
        if not source_name or not target_name or source_name.casefold() == target_name.casefold():
            return False
        relation = await self.session.scalar(select(FactionRelationship).where(
            FactionRelationship.branch_id == self.branch.id, FactionRelationship.from_faction.ilike(source_name),
            FactionRelationship.to_faction.ilike(target_name)))
        if relation is None:
            relation = FactionRelationship(campaign_id=self.campaign.id, branch_id=self.branch.id,
                                           from_faction=source_name, to_faction=target_name)
            self.session.add(relation)
        if isinstance(value.get("relation"), str) and not is_unknown(value["relation"]):
            relation.relation = value["relation"][:80]
        if isinstance(value.get("details"), str) and not is_unknown(value["details"]):
            relation.details = value["details"][:2000]
        return True

    async def time_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        seconds = value.get("seconds") or value.get("elapsed_seconds") or 0
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            seconds = 0
        self.extra_seconds = getattr(self, "extra_seconds", 0) + max(0, seconds)
        self.explicit_time = {key: value[key] for key in ("day", "time_of_day", "label") if key in value}
        return True

    async def secret_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        name = str(operation.get("name") or operation.get("subject") or "").strip()[:160]
        if not name:
            return False
        secret = await self.session.scalar(select(Secret).where(Secret.branch_id == self.branch.id, Secret.name.ilike(name)))
        if operation["kind"] == "CREATE_SECRET":
            content = str(value.get("content") or value.get("description") or "")[:5000]
            if secret is None:
                self.session.add(Secret(campaign_id=self.campaign.id, branch_id=self.branch.id, name=name, content=content,
                                        visibility=operation.get("visibility", "GM_ONLY"),
                                        discovered_by=[str(v)[:120] for v in (value.get("discovered_by") or value.get("known_by") or [])][:20],
                                        source_turn_id=self.turn.id))
            elif content and not secret.content:
                secret.content = content
            return True
        if secret:
            secret.visibility = "PLAYER_KNOWN"
            secret.discovered_by = list(dict.fromkeys([*(secret.discovered_by or []), self.player.name]))
            self.change("secret", f"Secret revealed: {secret.name}")
            await self.memory({"content": f"Secret revealed: {secret.name}. {secret.content}", "type": "SECRET",
                               "importance": 0.85})
            return True
        return False

    async def objective_op(self, operation: dict[str, Any]) -> bool:
        kind = operation["kind"]
        value = operation.get("value") or {}
        title = objective_rules.clean_title(operation.get("name") or value.get("title") or operation.get("subject"))
        if self.resolver.is_player_reference(title):
            title = objective_rules.clean_title(value.get("title") or value.get("description", "")[:120])
        objective = next((row for row in self.objectives if str(row.id) == str(operation.get("objective_id"))), None)
        if objective is None and title:
            objective = objective_rules.find_matching(self.objectives, title, {})
        if kind == "CREATE_OBJECTIVE":
            if not title:
                return False
            criteria = objective_rules.infer_criteria(title, self.resolver)
            match = objective or objective_rules.find_matching(self.objectives, title, criteria)
            if match is not None:
                if normalize_reference(title) != normalize_reference(match.title) and title not in (match.aliases or []):
                    match.aliases = [*(match.aliases or []), title][:12]
                description = value.get("description")
                if isinstance(description, str) and len(description) > len(match.description or ""):
                    match.description = description[:2000]
                self.log.metrics["objective_duplicates_merged"] += 1
                self.log.note("objective_merged", proposed=title, existing=match.title, status=match.status)
                operation["objective_id"] = str(match.id)
                return True
            objective = Objective(campaign_id=self.campaign.id, branch_id=self.branch.id, title=title,
                                  description=str(value.get("description") or "")[:2000], status="active",
                                  aliases=[], criteria=criteria,
                                  related_character_ids=[criteria["character_id"]] if criteria.get("character_id") else [],
                                  created_turn_index=self.turn.turn_index, visibility=operation.get("visibility", "PLAYER_KNOWN"))
            self.session.add(objective)
            await self.session.flush()
            self.objectives.append(objective)
            self.change("objective", f"New objective: {title}")
            await self.memory({"content": f"New objective: {title}. {objective.description}", "type": "OBJECTIVE"})
            operation["objective_id"] = str(objective.id)
            return True
        if objective is None:
            self.log.reject(operation, "unknown_objective")
            return False
        status = objective_rules.normalize_status("completed" if kind == "COMPLETE_OBJECTIVE" else "failed"
                                                  if kind == "FAIL_OBJECTIVE" else value.get("status"))
        if status:
            await self.set_objective_status(objective, status, str(value.get("reason") or value.get("resolution") or ""),
                                            retcon=str(value.get("source", "")).casefold() in {"retcon_command", "player_meta"})
        if isinstance(value.get("description"), str) and not is_unknown(value["description"]):
            objective.description = value["description"][:2000]
        operation["objective_id"] = str(objective.id)
        return True

    async def set_objective_status(self, objective: Objective, status: str, note: str = "", *, retcon: bool = False) -> None:
        if objective.status == status:
            return
        if objective.status in {"completed", "failed"} and status == "active" and not retcon:
            self.log.reject({"kind": "UPDATE_OBJECTIVE", "name": objective.title}, "completed_objective_cannot_reactivate")
            self.log.canon.append({"kind": "OBJECTIVE", "violation": "completed_objective_cannot_reactivate",
                                   "objective": objective.title})
            return
        objective.status = status
        if status == "completed":
            objective.completed_turn_index = self.turn.turn_index
            self.log.metrics["objectives_completed"] += 1
            self.change("objective", f"Objective completed: {objective.title}")
            await self.memory({"content": f"Completed objective: {objective.title}. {note}".strip(), "type": "OBJECTIVE",
                               "importance": 0.7})
        elif status == "failed":
            objective.failed_turn_index = self.turn.turn_index
            self.change("objective", f"Objective failed: {objective.title}")
        if note:
            objective.resolution_note = note[:1000]

    async def money_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        previous = self.state.get("money", {})
        if not isinstance(previous, dict):
            previous = {}
        currency = str(value.get("currency") or previous.get("currency") or "").casefold()[:40]
        amount, delta = value.get("amount"), value.get("delta")
        if isinstance(amount, int) and not isinstance(amount, bool) and currency:
            money = {"amount": max(0, amount), "currency": currency}
        elif isinstance(delta, int) and not isinstance(delta, bool) and isinstance(previous.get("amount"), int) \
                and currency == previous.get("currency"):
            money = {"amount": max(0, previous["amount"] + delta), "currency": currency}
        else:
            self.log.reject(operation, "money_change_unsupported")
            return False
        self.state["money"] = money
        self.player.attributes = {**(self.player.attributes or {}), "money": money}
        return True

    async def canon_rule_op(self, operation: dict[str, Any]) -> bool:
        value = operation.get("value") or {}
        if str(value.get("source", "")).casefold() not in {"player_meta", "canon_command", "retcon_command"}:
            self.log.reject(operation, "canon_rules_require_player_command")
            return False
        self.session.add(CanonRule(campaign_id=self.campaign.id, branch_id=self.branch.id,
                                   rule_type=str(value.get("rule_type", "PLAYER_META"))[:64],
                                   statement=str(value.get("statement", ""))[:4000], strength=str(value.get("strength", "SOFT"))[:16],
                                   exceptions=value.get("exceptions", []), visibility=operation.get("visibility", "PLAYER_KNOWN"),
                                   source=str(value["source"])[:64]))
        return True

    # --- deterministic reconciliation ----------------------------------------------------
    async def finish(self, interpretation: StateInterpretation) -> None:
        turn = self.turn
        explicit_identity = _explicit_identity(turn.player_action)
        if explicit_identity:
            self.player.attributes = {**(self.player.attributes or {}), **explicit_identity}
            constitution = dict(self.campaign.constitution or {})
            starting_state = dict(constitution.get("starting_state") or {})
            starting_state["identity"] = {**(starting_state.get("identity") or {}), **explicit_identity}
            constitution["starting_state"] = starting_state
            self.campaign.constitution = constitution
        if player_says_alive(turn.player_action, self.campaign.protagonist_name):
            self.state["player_status"] = "alive"
            self.state["player_condition"] = {**(self.state.get("player_condition") or {}), "physical_status": "alive"}
            self.player.status = "alive"

        # Scene participants: present this turn; carried over while the player stays put.
        location = str(self.state.get("current_location") or "")
        scene = dict(self.state.get("scene") or {})
        moved = normalize_reference(scene.get("location", "")) != normalize_reference(location)
        present = {value for value in self.present if value in self.characters and value != str(self.player.id)}
        if moved or not scene:
            scene = {"location": location, "character_ids": sorted(present), "started_turn_index": turn.turn_index}
        else:
            lingering = {value for value in scene.get("character_ids", []) if value in self.characters
                         and (self.characters[value].last_seen_turn_index or -99) >= turn.turn_index - 2}
            scene["character_ids"] = sorted(present | lingering)
        scene["last_updated_turn_index"] = turn.turn_index
        self.state["scene"] = scene
        for character_id in present:
            character = self.characters[character_id]
            attributes = dict(character.attributes or {})
            if character.last_seen_turn_index != turn.turn_index:
                attributes["seen_count"] = int(attributes.get("seen_count") or 0) + 1
            attributes["encountered"] = True
            if location and not attributes.get("first_meeting_place"):
                attributes["first_meeting_place"] = location[:160]
            if location:
                attributes["location"] = location[:160]
            character.attributes = attributes
            character.last_seen_turn_index = turn.turn_index
            if character.first_seen_turn_index is None:
                character.first_seen_turn_index = turn.turn_index
        self.log.metrics["scene_participants"] = len(scene.get("character_ids", []))

        # Objectives satisfied by actual state complete without another model call.
        player_items = [item for item in self.items if normalize_reference(item.owner_name) in
                        {normalize_reference(self.player.name), "player", "you"}]
        await self.session.flush()
        for objective in self.objectives:
            if objective.status != "active":
                continue
            if not objective.criteria:
                objective.criteria = objective_rules.infer_criteria(objective.title, self.resolver)
            note = objective_rules.check_completion(objective, self.characters, player_items, self.resolver)
            if note:
                await self.set_objective_status(objective, "completed", note)
                self.log.note("objective_completed_by_state", objective=objective.title, evidence=note)

        # Importance and fact mirrors for everyone touched or present this turn.
        relation_rows = (await self.session.scalars(select(CharacterRelationship).where(
            CharacterRelationship.branch_id == self.branch.id))).all()
        strength: dict[str, float] = {}
        for relation in relation_rows:
            dims = relation.dimensions or {}
            value = max([abs(float(dims[axis]) - (50 if axis in {"trust", "respect"} else 0))
                         for axis in ("trust", "respect", "fear", "hostility", "affection", "loyalty")
                         if isinstance(dims.get(axis), (int, float)) and not isinstance(dims.get(axis), bool)] or [0])
            for character_id in (str(relation.from_character_id), str(relation.to_character_id)):
                strength[character_id] = max(strength.get(character_id, 0), value)
        linked = {value for objective in self.objectives for value in (objective.related_character_ids or [])}
        for character_id in (self.touched | present) & set(self.characters):
            character = self.characters[character_id]
            fact_count = len((character.attributes or {}).get("known_facts") or [])
            await sync_fact_mirror(self.session, character)
            character.importance = compute_importance(
                character, relationship_strength=strength.get(character_id, 0), objective_linked=character_id in linked,
                fact_count=fact_count, is_player=character.id == self.player.id)

        if turn.gm_response:
            await add_memory(self.session, campaign_id=self.campaign.id, branch_id=self.branch.id, kind="TURN",
                             content=f"Player: {turn.player_action[:600]}\nWorld: {turn.gm_response[:1600]}",
                             importance=0.25, confidence=1.0, turn_id=turn.id, turn_index=turn.turn_index,
                             characters=[self.player.name, *(self.characters[value].name for value in sorted(present))][:12],
                             character_ids=[str(self.player.id), *sorted(present)][:12],
                             locations=[location] if location else [], pending=self.memories)

        seconds = interpretation.time_elapsed_seconds + getattr(self, "extra_seconds", 0)
        explicit = dict(getattr(self, "explicit_time", {}) or {})
        if interpretation.time_of_day and "time_of_day" not in explicit:
            explicit["time_of_day"] = interpretation.time_of_day
        clock = world_time.advance(self.state, seconds, self.narration, explicit)
        self.state["world_clock"] = clock
        self.state["elapsed_seconds"] = clock["elapsed_seconds"]
        self.state["world_time"] = clock["label"]
        self.state["scene_mood"] = {**read_mood(self.narration, suggested=interpretation.scene_mood,
                                                time_of_day=clock.get("time_of_day") or ""), "turn_index": turn.turn_index}
        turn.in_world_time = turn.in_world_time or clock["label"]
        self.branch.current_state = self.state
        self.log.metrics["characters_in_turn"] = len(self.touched)
        turn.state_delta = {"operations": self.applied, "time_elapsed_seconds": seconds, "changes": self.changes}
        await self.session.flush()


async def apply_interpretation(session: AsyncSession, campaign: Campaign, branch: Branch, turn: Turn,
                               interpretation: StateInterpretation, log: StoreLog | None = None) -> dict[str, Any]:
    applier = StateApplier(session, campaign, branch, turn, log or StoreLog())
    await applier.setup()
    await applier.run(interpretation)
    return turn.state_delta
