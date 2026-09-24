"""Merge-safe character persistence: canonical names, aliases, facts, provenance, and merges.

Rule: a partial or weak update never silently deletes established information. Only an
explicit retcon (PLAYER_EXPLICIT provenance) may replace a stronger value.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ability,
    Campaign,
    Character,
    CharacterAlias,
    CharacterFact,
    CharacterRelationship,
    Event,
    Item,
    Memory,
    Objective,
    PortraitJob,
    RelationshipEvent,
    Secret,
)
from app.services.entity_resolver import CharacterCandidate, EntityResolver
from app.services.identity import (
    classify_fact,
    is_generic_reference,
    is_title,
    is_unknown,
    merge_scalar,
    normalize_reference,
    provenance_rank,
    same_statement,
)

IDENTITY_FIELDS = {"sex", "gender", "pronouns"}
RESERVED_VALUE_KEYS = {
    "role", "personality", "status", "motivations", "attributes", "known_facts", "facts", "aliases", "alias",
    "name", "canonical_name", "title", "location", "character_id", "id", "previous_name", "injuries",
    "conditions", "condition", "physical_status", "knowledge", "visual", "visual_identity",
}


@dataclass
class StoreLog:
    """Structured decisions for turn diagnostics; never chain-of-thought, only outcomes."""

    resolutions: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    reconciliation: list[dict] = field(default_factory=list)
    canon: list[dict] = field(default_factory=list)
    metrics: Counter = field(default_factory=Counter)

    def reject(self, operation: Any, reason: str) -> None:
        kind = operation.get("kind") if isinstance(operation, dict) else str(operation)
        self.rejected.append({"kind": kind, "reason": reason,
                              "subject": (operation.get("name") or operation.get("subject") or "")[:120]
                              if isinstance(operation, dict) else ""})
        self.metrics["operations_rejected"] += 1

    def note(self, kind: str, **details: Any) -> None:
        self.reconciliation.append({"kind": kind, **{key: value for key, value in details.items() if value not in (None, "")}})

    def as_dict(self) -> dict:
        return {"resolutions": self.resolutions[:80], "rejected": self.rejected[:80],
                "reconciliation": self.reconciliation[:120], "canon": self.canon[:40],
                "metrics": dict(self.metrics)}


async def load_characters(session: AsyncSession, branch_id: UUID) -> list[Character]:
    return list((await session.scalars(select(Character).where(Character.branch_id == branch_id))).all())


async def load_aliases(session: AsyncSession, branch_id: UUID) -> dict[UUID, list[CharacterAlias]]:
    rows = (await session.scalars(select(CharacterAlias).where(CharacterAlias.branch_id == branch_id))).all()
    result: dict[UUID, list[CharacterAlias]] = {}
    for row in rows:
        result.setdefault(row.character_id, []).append(row)
    return result


def candidate_for(character: Character, aliases: list[CharacterAlias], protagonist_name: str,
                  scene_ids: set[str]) -> CharacterCandidate:
    attributes = character.attributes or {}
    return CharacterCandidate(
        id=str(character.id), name=character.name,
        aliases=[(row.alias, row.alias_type) for row in aliases],
        role=character.role or "", status=character.status or "",
        location=str(attributes.get("location") or ""),
        first_meeting_place=str(attributes.get("first_meeting_place") or ""),
        last_seen=character.last_seen_turn_index, in_scene=str(character.id) in scene_ids,
        importance=character.importance or "MINOR",
        is_player=normalize_reference(character.name) == normalize_reference(protagonist_name))


async def build_resolver(session: AsyncSession, campaign: Campaign, branch_id: UUID, current_state: dict
                         ) -> tuple[EntityResolver, dict[str, Character]]:
    characters = await load_characters(session, branch_id)
    aliases = await load_aliases(session, branch_id)
    scene = set((current_state.get("scene") or {}).get("character_ids") or [])
    resolver = EntityResolver([candidate_for(row, aliases.get(row.id, []), campaign.protagonist_name, scene)
                               for row in characters], campaign.protagonist_name,
                              str(current_state.get("current_location") or ""))
    return resolver, {str(row.id): row for row in characters}


def alias_type_for(alias: str) -> str:
    if is_title(alias):
        return "TITLE"
    if is_generic_reference(alias) or " in " in f" {normalize_reference(alias)} " or " with " in f" {normalize_reference(alias)} ":
        return "DESCRIPTION"
    return "NICKNAME"


async def add_alias(session: AsyncSession, character: Character, alias: str, alias_type: str | None = None, *,
                    turn_index: int | None = None, source: str = "state_interpreter", confidence: float = 0.8,
                    resolver: EntityResolver | None = None, log: StoreLog | None = None) -> bool:
    alias = " ".join(str(alias or "").split())[:160]
    key = normalize_reference(alias)
    if not key or is_unknown(alias):
        return False
    existing = await session.scalar(select(CharacterAlias).where(
        CharacterAlias.branch_id == character.branch_id, CharacterAlias.character_id == character.id,
        CharacterAlias.normalized == key))
    if existing:
        return False
    # An alias another person already owns would make references ambiguous; keep it off.
    owner = await session.scalar(select(CharacterAlias).where(
        CharacterAlias.branch_id == character.branch_id, CharacterAlias.normalized == key,
        CharacterAlias.character_id != character.id).limit(1))
    if owner and alias_type != "CANONICAL_NAME":
        if log:
            log.note("alias_conflict", alias=alias, character=character.name)
        return False
    kind = alias_type or alias_type_for(alias)
    session.add(CharacterAlias(campaign_id=character.campaign_id, branch_id=character.branch_id,
                               character_id=character.id, alias=alias, normalized=key, alias_type=kind,
                               confidence=confidence, first_seen_turn_index=turn_index, source=source))
    if resolver and str(character.id) in resolver.by_id:
        resolver.by_id[str(character.id)].aliases.append((alias, kind))
    if log:
        log.metrics["aliases_added"] += 1
    return True


async def add_fact(session: AsyncSession, character: Character, content: str, *, certainty: str = "CONFIRMED",
                   provenance: str = "DIRECT_OBSERVATION", visibility: str = "PLAYER_KNOWN",
                   fact_type: str | None = None, turn_id: UUID | None = None, turn_index: int | None = None,
                   existing: list[CharacterFact] | None = None, log: StoreLog | None = None) -> CharacterFact | None:
    """Accumulate a fact. Near-duplicates reconfirm the stored fact instead of adding noise."""
    text = " ".join(str(content or "").split()).strip()[:2000]
    if len(text) < 3 or is_unknown(text):
        return None
    if existing is None:
        existing = list((await session.scalars(select(CharacterFact).where(
            CharacterFact.character_id == character.id, CharacterFact.active.is_(True)))).all())
    for fact in existing:
        if same_statement(fact.content, text):
            fact.last_confirmed_turn_index = turn_index if turn_index is not None else fact.last_confirmed_turn_index
            if provenance_rank(provenance) > provenance_rank(fact.provenance):
                fact.provenance = provenance
                fact.certainty = certainty
            if log:
                log.metrics["facts_reconfirmed"] += 1
            return fact
    fact = CharacterFact(campaign_id=character.campaign_id, branch_id=character.branch_id, character_id=character.id,
                         fact_type=fact_type or classify_fact(text), content=text, normalized=normalize_reference(text),
                         certainty=certainty, provenance=provenance, visibility=visibility,
                         first_seen_turn_index=turn_index, last_confirmed_turn_index=turn_index,
                         source_turn_id=turn_id, active=True)
    session.add(fact)
    existing.append(fact)
    if log:
        log.metrics["facts_added"] += 1
    return fact


async def sync_fact_mirror(session: AsyncSession, character: Character) -> None:
    """Keep attributes.known_facts as a read-only mirror for older clients and exports."""
    facts = (await session.scalars(select(CharacterFact).where(
        CharacterFact.character_id == character.id, CharacterFact.active.is_(True),
        CharacterFact.visibility != "GM_ONLY").order_by(CharacterFact.created_at))).all()
    mirror = [fact.content for fact in facts][-60:]
    if (character.attributes or {}).get("known_facts") != mirror:
        character.attributes = {**(character.attributes or {}), "known_facts": mirror}


def _merge_attribute(current: Any, proposed: Any) -> Any:
    if is_unknown(proposed):
        return current
    if isinstance(current, list) or isinstance(proposed, list):
        left = current if isinstance(current, list) else ([current] if not is_unknown(current) else [])
        right = proposed if isinstance(proposed, list) else [proposed]
        merged = list(left)
        for value in right:
            if not any(str(value).casefold() == str(old).casefold() for old in merged):
                merged.append(value)
        return merged[-40:]
    if isinstance(current, dict) and isinstance(proposed, dict):
        return {**current, **{key: value for key, value in proposed.items() if not is_unknown(value)}}
    return proposed


async def merge_character_values(session: AsyncSession, character: Character, value: dict[str, Any], *,
                                 provenance: str, certainty: str, visibility: str, turn_id: UUID | None,
                                 turn_index: int | None, is_player: bool, resolver: EntityResolver | None,
                                 log: StoreLog) -> None:
    """Apply an UPDATE/CREATE payload without downgrading or erasing known data."""
    levels = dict(character.provenance or {})
    for field_name, limit in (("role", 160), ("personality", 2000)):
        if field_name in value:
            before = getattr(character, field_name) or ""
            new, level, decision = merge_scalar(before, levels.get(field_name),
                                                str(value[field_name])[:limit], provenance, field=field_name)
            if decision.startswith("kept_") and not is_unknown(before):
                log.metrics["downgrades_prevented"] += 1
                log.note("kept_" + field_name, character=character.name, kept=getattr(character, field_name),
                         proposed=str(value[field_name])[:160], reason=decision)
            setattr(character, field_name, new)
            if level:
                levels[field_name] = level
    title = value.get("title")
    if isinstance(title, str) and not is_unknown(title):
        if is_unknown(character.role):
            character.role = title[:160]
            levels["role"] = provenance
        await add_alias(session, character, title, "TITLE", turn_index=turn_index, resolver=resolver, log=log)
    if isinstance(value.get("motivations"), list):
        character.motivations = _merge_attribute(character.motivations or [], [str(item)[:300] for item in value["motivations"]])[:12]
    aliases = value.get("aliases") if isinstance(value.get("aliases"), list) else [value["alias"]] if isinstance(value.get("alias"), str) else []
    for alias in aliases[:8]:
        if isinstance(alias, str):
            await add_alias(session, character, alias, turn_index=turn_index, resolver=resolver, log=log)
    facts = value.get("known_facts", value.get("facts"))
    if isinstance(facts, str):
        facts = [facts]
    if isinstance(facts, list):
        existing = list((await session.scalars(select(CharacterFact).where(
            CharacterFact.character_id == character.id, CharacterFact.active.is_(True)))).all())
        for fact in facts[:20]:
            if isinstance(fact, dict):
                await add_fact(session, character, str(fact.get("content") or fact.get("fact") or ""),
                               certainty=str(fact.get("certainty") or certainty)[:16], provenance=provenance,
                               visibility=str(fact.get("visibility") or visibility)[:24],
                               fact_type=fact.get("type") if isinstance(fact.get("type"), str) else None,
                               turn_id=turn_id, turn_index=turn_index, existing=existing, log=log)
            elif isinstance(fact, str):
                await add_fact(session, character, fact, certainty=certainty, provenance=provenance,
                               visibility=visibility, turn_id=turn_id, turn_index=turn_index, existing=existing, log=log)
    attributes = dict(character.attributes or {})
    nested = value.get("attributes") if isinstance(value.get("attributes"), dict) else {}
    flat = {key: entry for key, entry in value.items() if key not in RESERVED_VALUE_KEYS}
    for key, entry in {**nested, **flat}.items():
        if key in {"known_facts", "avatar_url", "avatar_job", "portrait_seed", "merged_from"}:
            continue
        if key in IDENTITY_FIELDS:
            if is_player or is_unknown(entry):
                continue
            if attributes.get(key) and provenance_rank(provenance) < provenance_rank(levels.get(key)):
                continue
            levels[key] = provenance
        if key == "first_meeting_place" and attributes.get(key):
            continue
        if key == "appearance" and attributes.get("appearance") and not is_unknown(entry) and \
                str(entry).casefold() != str(attributes["appearance"]).casefold():
            attributes["current_appearance"] = str(entry)[:1000]
            continue
        attributes[key] = _merge_attribute(attributes.get(key), entry)
    visual = value.get("visual") if isinstance(value.get("visual"), dict) else value.get("visual_identity")
    if isinstance(visual, dict):
        from app.services.visual_identity import merge_visual
        attributes["visual_identity"] = merge_visual(attributes.get("visual_identity"), visual)
    if isinstance(value.get("location"), str) and not is_unknown(value["location"]):
        attributes["location"] = value["location"][:160]
    character.attributes = attributes
    character.provenance = levels
    if visibility == "PLAYER_KNOWN" and character.visibility != "PLAYER_KNOWN":
        character.visibility = "PLAYER_KNOWN"


async def rename_character(session: AsyncSession, character: Character, new_name: str, *, turn_index: int | None,
                           resolver: EntityResolver | None, log: StoreLog, source: str = "identity_reveal") -> None:
    """Make ``new_name`` canonical; the old name stays as an alias so old references still resolve."""
    old = character.name
    if normalize_reference(old) == normalize_reference(new_name):
        return
    await add_alias(session, character, old, alias_type_for(old) if not is_title(old) else "FORMER_TITLE",
                    turn_index=turn_index, source=source, confidence=1.0, resolver=resolver, log=log)
    character.name = new_name[:120]
    await add_alias(session, character, new_name, "CANONICAL_NAME", turn_index=turn_index, source=source,
                    confidence=1.0, resolver=resolver, log=log)
    for item in (await session.scalars(select(Item).where(Item.branch_id == character.branch_id,
                                                           Item.owner_name == old))).all():
        item.owner_name = character.name
    if resolver and str(character.id) in resolver.by_id:
        resolver.by_id[str(character.id)].name = character.name
    log.note("identity_revealed", character_id=str(character.id), previous=old, canonical=character.name)
    log.metrics["identity_reveals"] += 1


def _merge_dimensions(target: dict, source: dict) -> dict:
    merged = dict(source)
    merged.update({key: value for key, value in target.items() if not is_unknown(value)})
    return merged


async def merge_characters(session: AsyncSession, source: Character, target: Character, *, turn_index: int | None,
                           reason: str, log: StoreLog, resolver: EntityResolver | None = None) -> Character:
    """Fold ``source`` into ``target`` without losing facts, aliases, relationships, or history."""
    if source.id == target.id or source.branch_id != target.branch_id:
        return target
    branch_id = target.branch_id
    # Release the source's aliases first so they are free to move to the target.
    old_aliases = [(row.alias, row.alias_type, row.first_seen_turn_index, row.confidence) for row in
                   (await session.scalars(select(CharacterAlias).where(CharacterAlias.character_id == source.id))).all()]
    await session.execute(delete(CharacterAlias).where(CharacterAlias.character_id == source.id))
    await session.flush()
    await add_alias(session, target, source.name, alias_type_for(source.name) if not is_title(source.name) else "TITLE",
                    turn_index=turn_index, source="merge", confidence=1.0, resolver=resolver, log=log)
    for alias, kind, first_seen, confidence in old_aliases:
        await add_alias(session, target, alias, alias_type_for(alias) if kind == "CANONICAL_NAME" else kind,
                        turn_index=first_seen, source="merge", confidence=confidence, resolver=resolver, log=None)
    target_facts = list((await session.scalars(select(CharacterFact).where(CharacterFact.character_id == target.id))).all())
    for fact in (await session.scalars(select(CharacterFact).where(CharacterFact.character_id == source.id))).all():
        if any(same_statement(fact.content, other.content) for other in target_facts):
            await session.delete(fact)
        else:
            fact.character_id = target.id
            target_facts.append(fact)
    for fact in (source.attributes or {}).get("known_facts") or []:
        if isinstance(fact, str):
            await add_fact(session, target, fact, turn_index=turn_index, existing=target_facts)
    for ability in (await session.scalars(select(Ability).where(Ability.character_id == source.id))).all():
        ability.character_id = target.id
    # Relationships: re-point, and merge into an existing edge when the pair already exists.
    relations = list((await session.scalars(select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch_id,
        or_(CharacterRelationship.from_character_id == source.id, CharacterRelationship.to_character_id == source.id),
    ))).all())
    for relation in relations:
        new_from = target.id if relation.from_character_id == source.id else relation.from_character_id
        new_to = target.id if relation.to_character_id == source.id else relation.to_character_id
        if new_from == new_to:
            await session.delete(relation)
            continue
        existing = await session.scalar(select(CharacterRelationship).where(
            CharacterRelationship.branch_id == branch_id, CharacterRelationship.from_character_id == new_from,
            CharacterRelationship.to_character_id == new_to, CharacterRelationship.id != relation.id))
        if existing:
            existing.dimensions = _merge_dimensions(existing.dimensions or {}, relation.dimensions or {})
            if not existing.summary and relation.summary:
                existing.summary = relation.summary
            for event in (await session.scalars(select(RelationshipEvent).where(
                    RelationshipEvent.relationship_id == relation.id))).all():
                event.relationship_id = existing.id
            await session.flush()
            await session.delete(relation)
        else:
            relation.from_character_id, relation.to_character_id = new_from, new_to
    await session.flush()
    for job in (await session.scalars(select(PortraitJob).where(PortraitJob.character_id == source.id))).all():
        job.character_id = target.id
    source_ids = {str(source.id)}
    for memory in (await session.scalars(select(Memory).where(Memory.branch_id == branch_id))).all():
        if source.name in (memory.characters or []):
            memory.characters = list(dict.fromkeys([target.name if name == source.name else name for name in memory.characters]))
        if source_ids & set(memory.character_ids or []):
            memory.character_ids = list(dict.fromkeys([str(target.id) if value in source_ids else value
                                                       for value in memory.character_ids]))
    for event in (await session.scalars(select(Event).where(Event.branch_id == branch_id))).all():
        if source.name in (event.participants or []):
            event.participants = list(dict.fromkeys([target.name if name == source.name else name for name in event.participants]))
    for objective in (await session.scalars(select(Objective).where(Objective.branch_id == branch_id))).all():
        if str(source.id) in (objective.related_character_ids or []):
            objective.related_character_ids = list(dict.fromkeys(
                [str(target.id) if value == str(source.id) else value for value in objective.related_character_ids]))
        criteria = dict(objective.criteria or {})
        if criteria.get("character_id") == str(source.id):
            criteria["character_id"] = str(target.id)
            objective.criteria = criteria
    for item in (await session.scalars(select(Item).where(Item.branch_id == branch_id, Item.owner_name == source.name))).all():
        item.owner_name = target.name
    for secret in (await session.scalars(select(Secret).where(Secret.branch_id == branch_id))).all():
        if source.name in (secret.discovered_by or []):
            secret.discovered_by = list(dict.fromkeys([target.name if n == source.name else n for n in secret.discovered_by]))

    # Scalars: keep the stronger or more specific value; never replace known with unknown.
    for field_name in ("role", "personality"):
        value, level, _ = merge_scalar(getattr(target, field_name) or "", (target.provenance or {}).get(field_name),
                                       getattr(source, field_name) or "", (source.provenance or {}).get(field_name) or "INFERRED",
                                       field=field_name)
        setattr(target, field_name, value)
        if level:
            target.provenance = {**(target.provenance or {}), field_name: level}
    if is_unknown(target.status) or target.status == "alive" and source.status and source.status not in {"alive", ""}:
        target.status = source.status or target.status
    target.motivations = _merge_attribute(target.motivations or [], source.motivations or [])[:12]
    knowledge = list(target.knowledge or [])
    for entry in source.knowledge or []:
        if not any(isinstance(old, dict) and old.get("fact") == entry.get("fact") for old in knowledge):
            knowledge.append(entry)
    target.knowledge = knowledge[-200:]
    attributes = dict(source.attributes or {})
    for key, value in (target.attributes or {}).items():
        attributes[key] = _merge_attribute(attributes.get(key), value) if key not in {"avatar_url", "portrait_seed", "avatar_job"} else value
    if not (target.attributes or {}).get("avatar_url") and (source.attributes or {}).get("avatar_url"):
        attributes["avatar_url"] = source.attributes["avatar_url"]
        attributes["portrait_seed"] = source.attributes.get("portrait_seed")
    seen = [value for value in ((target.attributes or {}).get("seen_count"), (source.attributes or {}).get("seen_count"))
            if isinstance(value, int)]
    if seen:
        attributes["seen_count"] = sum(seen)
    if (source.attributes or {}).get("encountered") or (target.attributes or {}).get("encountered"):
        attributes["encountered"] = True
    merged_from = list((target.attributes or {}).get("merged_from") or [])
    merged_from.append({"id": str(source.id), "name": source.name, "role": source.role, "status": source.status,
                        "turn_index": turn_index, "reason": reason[:300]})
    attributes["merged_from"] = merged_from[-20:]
    attributes.pop("known_facts", None)
    target.attributes = attributes
    firsts = [value for value in (target.first_seen_turn_index, source.first_seen_turn_index) if value is not None]
    lasts = [value for value in (target.last_seen_turn_index, source.last_seen_turn_index) if value is not None]
    target.first_seen_turn_index = min(firsts) if firsts else None
    target.last_seen_turn_index = max(lasts) if lasts else None
    if source.visibility == "PLAYER_KNOWN":
        target.visibility = "PLAYER_KNOWN"
    await session.flush()
    await session.delete(source)
    await session.flush()
    await sync_fact_mirror(session, target)
    if resolver:
        resolver.candidates = [candidate for candidate in resolver.candidates if candidate.id != str(source.id)]
        resolver.by_id.pop(str(source.id), None)
    log.note("characters_merged", source=source.name, source_id=str(source.id), target=target.name,
             target_id=str(target.id), reason=reason[:200])
    log.metrics["characters_merged"] += 1
    return target


async def ensure_identity_rows(session: AsyncSession, branch_id: UUID) -> int:
    """Backfill canonical aliases and fact rows for characters restored from older snapshots."""
    created = 0
    aliases = await load_aliases(session, branch_id)
    for character in await load_characters(session, branch_id):
        keys = {row.normalized for row in aliases.get(character.id, [])}
        if normalize_reference(character.name) not in keys:
            session.add(CharacterAlias(campaign_id=character.campaign_id, branch_id=branch_id, character_id=character.id,
                                       alias=character.name, normalized=normalize_reference(character.name),
                                       alias_type="CANONICAL_NAME", confidence=1.0, source="backfill"))
            created += 1
        facts = (character.attributes or {}).get("known_facts")
        if isinstance(facts, list) and facts:
            has_rows = await session.scalar(select(CharacterFact.id).where(CharacterFact.character_id == character.id).limit(1))
            if not has_rows:
                existing: list[CharacterFact] = []
                for fact in facts:
                    if isinstance(fact, str):
                        await add_fact(session, character, fact, existing=existing)
                created += 1
    if created:
        await session.flush()
    return created
