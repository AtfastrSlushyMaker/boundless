"""Focused, ID-bearing context for the state interpreter and relevance scoring for the GM.

Small local models do much better when they see a short list of candidate entities with
IDs than when they must reconstruct identity from a giant world dump.
"""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ability,
    Branch,
    Campaign,
    Character,
    CharacterAlias,
    CharacterRelationship,
    Faction,
    Item,
    Location,
    Objective,
)
from app.services.identity import is_mentioned, looks_like_proper_name, normalize_reference

PERSON_MENTION = re.compile(
    r"\b(?:the|a|an)\s+(?P<mention>(?:[a-z'-]+\s+){0,2}(?:man|woman|boy|girl|figure|stranger|guard|priest|priestess|merchant|"
    r"soldier|servant|courier|thief|beggar|child|kid|mage|sailor|watchman|captain|pursuer|youth|elder)"
    r"(?:\s+(?:in|with|wearing)\s+(?:the|a|an)?\s*[a-z'-]+(?:\s+[a-z'-]+)?)?)\b",
    re.IGNORECASE)
QUOTED = re.compile(r"[\"“][^\"”]*[\"”]")


def new_people_candidates(narration: str, resolver) -> list[dict[str, str]]:
    """People the narration shows who are not saved yet, each with a temporary id the model can use.

    Weak models invent ids for new people; giving them one to copy keeps identity deterministic.
    """
    from app.services.world_index import (
        APPOSITIVE,
        COMMON_WORDS,
        NAMED,
        SPEAKER_AFTER,
        SPEAKER_BEFORE,
        TITLED_PERSON,
    )

    outside = QUOTED.sub(" ", narration)
    found: list[str] = []
    for pattern in (SPEAKER_AFTER, SPEAKER_BEFORE, NAMED, APPOSITIVE):
        found += [match.group("name").strip() for match in pattern.finditer(narration)
                  if match.group("name").split()[0] not in COMMON_WORDS]
    found += [f"{match.group('title')} {match.group('name')}" for match in TITLED_PERSON.finditer(narration)]
    for match in PERSON_MENTION.finditer(outside):
        mention = " ".join(match.group("mention").split())
        mention = re.sub(r"\s+(?:is|was|who|and|as)$", "", mention)
        found.append(mention)
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for mention in found:
        key = normalize_reference(mention)
        if not key or key in seen or len(key) < 3:
            continue
        seen.add(key)
        resolution = resolver.resolve(mention)
        if resolution.status != "NEW":
            continue
        if not looks_like_proper_name(mention) and len(key.split()) == 1 and key in {"man", "woman", "figure", "stranger"}:
            continue
        result.append({"id": f"new-{len(result) + 1}", "mention": mention})
        if len(result) >= 8:
            break
    return result

IMPORTANCE_WEIGHT = {"COMPANION": 4, "MAJOR": 3, "RECURRING": 2, "MINOR": 1, "BACKGROUND": 0}
AXES = ("trust", "affection", "fear", "respect", "loyalty", "hostility", "attraction", "debt", "dependence")


def mentioned(text: str, values: list[str]) -> bool:
    return is_mentioned(text, values)


async def relevant_characters(session: AsyncSession, campaign: Campaign, branch_id: UUID, state: dict[str, Any],
                              text: str, limit: int = 12, include_gm_only: bool = False
                              ) -> tuple[list[Character], dict[UUID, list[str]], Character | None]:
    """Score people by mention, scene presence, location, recency, relationships, and importance."""
    query = select(Character).where(Character.branch_id == branch_id)
    if not include_gm_only:
        query = query.where(Character.visibility != "GM_ONLY")
    characters = list((await session.scalars(query)).all())
    aliases: dict[UUID, list[str]] = {}
    for row in (await session.scalars(select(CharacterAlias).where(CharacterAlias.branch_id == branch_id))).all():
        aliases.setdefault(row.character_id, []).append(row.alias)
    player_key = normalize_reference(campaign.protagonist_name)
    player = next((row for row in characters if normalize_reference(row.name) == player_key), None)
    scene = set((state.get("scene") or {}).get("character_ids") or [])
    location = normalize_reference(state.get("current_location") or "")
    head = max([row.last_seen_turn_index or 0 for row in characters] or [0])
    mentioned_ids: set[UUID] = set()
    scores: dict[UUID, float] = {}
    for row in characters:
        if player and row.id == player.id:
            continue
        names = [row.name, *aliases.get(row.id, [])]
        score = 0.0
        if mentioned(text, names):
            score += 10
            mentioned_ids.add(row.id)
        if str(row.id) in scene:
            score += 6
        attributes = row.attributes or {}
        if location and normalize_reference(str(attributes.get("location") or "")) == location:
            score += 3
        if row.last_seen_turn_index is not None:
            score += max(0.0, 3 - (head - row.last_seen_turn_index) * 0.5)
        score += IMPORTANCE_WEIGHT.get(row.importance or "MINOR", 1)
        if str(row.status or "").casefold().startswith(("dead", "deceased")):
            score -= 2
        scores[row.id] = score
    if mentioned_ids:
        relations = (await session.scalars(select(CharacterRelationship).where(
            CharacterRelationship.branch_id == branch_id))).all()
        for relation in relations:
            for left, right in ((relation.from_character_id, relation.to_character_id),
                                (relation.to_character_id, relation.from_character_id)):
                if left in mentioned_ids and right in scores:
                    scores[right] += 1.5
    ranked = sorted((row for row in characters if row.id in scores), key=lambda row: scores[row.id], reverse=True)
    chosen = [row for row in ranked if scores[row.id] >= 3][:limit]
    return chosen, aliases, player


def character_card(row: Character, aliases: list[str], facts_limit: int = 6) -> dict[str, Any]:
    attributes = row.attributes or {}
    card = {"id": str(row.id), "canonical_name": row.name,
            "aliases": [alias for alias in aliases if normalize_reference(alias) != normalize_reference(row.name)][:6],
            "role": row.role or None, "status": row.status or None,
            "location": attributes.get("location") or None,
            "known_facts": list(attributes.get("known_facts") or [])[-facts_limit:],
            "importance": row.importance}
    return {key: value for key, value in card.items() if value not in (None, "", [], {})}


async def build_interpreter_request(session: AsyncSession, campaign: Campaign, branch: Branch, action: str,
                                    narration: str, previous_state: dict[str, Any]) -> dict[str, Any]:
    text = f"{action}\n{narration}"
    people, aliases, player = await relevant_characters(session, campaign, branch.id, previous_state, text,
                                                        limit=12, include_gm_only=True)
    ids = {row.id for row in people}
    if player:
        ids.add(player.id)
    names = {row.id: row.name for row in people}
    if player:
        names[player.id] = player.name
    relations = (await session.scalars(select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch.id))).all()
    relationship_rows = []
    for relation in relations:
        if relation.from_character_id in ids and relation.to_character_id in ids:
            dims = {axis: relation.dimensions[axis] for axis in AXES
                    if isinstance((relation.dimensions or {}).get(axis), (int, float))}
            if dims or (relation.dimensions or {}).get("kinship"):
                relationship_rows.append({"from_id": str(relation.from_character_id), "from": names[relation.from_character_id],
                                          "to_id": str(relation.to_character_id), "to": names[relation.to_character_id],
                                          **dims, **({"kinship": relation.dimensions["kinship"]} if relation.dimensions.get("kinship") else {})})
    player_name = player.name if player else campaign.protagonist_name
    items = (await session.scalars(select(Item).where(Item.branch_id == branch.id))).all()
    inventory = [{"id": str(item.id), "name": item.name, "aliases": list(item.aliases or [])[:4], "quantity": item.quantity}
                 for item in items if normalize_reference(item.owner_name) in {normalize_reference(player_name), "player"}]
    locations = (await session.scalars(select(Location).where(Location.branch_id == branch.id))).all()
    current = normalize_reference(previous_state.get("current_location") or "")
    location_rows = [{"id": str(row.id), "name": row.name, **({"aliases": list(row.aliases)[:4]} if row.aliases else {})}
                     for row in locations if normalize_reference(row.name) == current or mentioned(text, [row.name, *(row.aliases or [])])][:8]
    objectives = (await session.scalars(select(Objective).where(
        Objective.branch_id == branch.id, Objective.status == "active"))).all()
    factions = (await session.scalars(select(Faction).where(Faction.branch_id == branch.id))).all()
    abilities = (await session.scalars(select(Ability).where(
        Ability.branch_id == branch.id, Ability.character_id == (player.id if player else None)))).all() if player else []
    constitution = campaign.constitution or {}
    from app.services.character_store import build_resolver
    resolver, _ = await build_resolver(session, campaign, branch.id, previous_state)
    new_people = new_people_candidates(narration, resolver)
    return {
        "player": {"name": player_name, "id": str(player.id) if player else None,
                   "location": previous_state.get("current_location") or None,
                   "status": previous_state.get("player_status") or None,
                   "abilities": [ability.name for ability in abilities if ability.status == "ACTIVE"],
                   "money": previous_state.get("money")},
        "known_characters": [character_card(row, aliases.get(row.id, [])) for row in people],
        "new_people": new_people,
        "known_locations": location_rows,
        "inventory": inventory,
        "relationships": relationship_rows[:20],
        "active_objectives": [{"id": str(row.id), "title": row.title, **({"aliases": row.aliases[:3]} if row.aliases else {})}
                              for row in objectives][:12],
        "known_factions": [row.name for row in factions if mentioned(text, [row.name])][:8],
        "world_time": (previous_state.get("world_clock") or {}).get("label") or previous_state.get("world_time"),
        "hard_rules": [rule.get("statement") for rule in constitution.get("rules", []) if rule.get("strength") == "HARD"][:12],
        "action": action[:3000],
        "game_master_narration": narration[:9000],
    }
