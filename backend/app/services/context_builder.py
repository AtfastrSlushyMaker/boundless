"""Layered Game Master context.

Order of authority: constitution -> hard canon -> protagonist -> current location ->
scene participants -> directly mentioned entities -> relevant relationships -> active
objectives -> retrieved memories -> campaign summary -> recent turns. Background NPCs
are left out unless the scene or the action makes them relevant.
"""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Ability,
    Campaign,
    CampaignSummary,
    CanonRule,
    Character,
    CharacterRelationship,
    Faction,
    Item,
    Location,
    Objective,
    RelationshipEvent,
    Secret,
    Turn,
)
from app.services.narration import clean_history_narration

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
AXES = ("trust", "affection", "fear", "respect", "loyalty", "hostility", "attraction", "debt", "dependence")


def _public(row: Any) -> dict[str, Any]:
    return {key: value for key, value in {
        "name": getattr(row, "name", ""), "role": getattr(row, "role", ""),
        "status": getattr(row, "status", ""), "personality": getattr(row, "personality", ""),
        "description": getattr(row, "description", ""), "region": getattr(row, "region", ""),
        "owner": getattr(row, "owner_name", ""), "quantity": getattr(row, "quantity", None),
        "condition": getattr(row, "condition", ""), "significance": getattr(row, "significance", ""),
        "summary": getattr(row, "summary", ""), "motives": getattr(row, "motives", []),
    }.items() if value not in (None, "", {}, [])}


async def history_for_branch(session: AsyncSession, head_turn_id: UUID | None, limit: int = 12) -> list[Turn]:
    if head_turn_id is None:
        return []
    ancestry = select(Turn.id, Turn.parent_turn_id, Turn.turn_index).where(Turn.id == head_turn_id).cte("turn_ancestry", recursive=True)
    ancestry = ancestry.union_all(select(Turn.id, Turn.parent_turn_id, Turn.turn_index).join(ancestry, Turn.id == ancestry.c.parent_turn_id))
    result = await session.scalars(select(Turn).join(ancestry, Turn.id == ancestry.c.id).order_by(Turn.turn_index.desc()).limit(limit))
    return list(reversed(result.all()))


def _person_card(row: Character, aliases: list[str], *, facts: int = 8) -> dict[str, Any]:
    attributes = row.attributes or {}
    card = {"name": row.name, "also_known_as": [alias for alias in aliases if alias.casefold() != row.name.casefold()][:5],
            "role": row.role, "status": row.status, "personality": row.personality,
            "motivations": row.motivations, "gender": attributes.get("gender"), "pronouns": attributes.get("pronouns"),
            "appearance": attributes.get("current_appearance") or attributes.get("appearance"),
            "location": attributes.get("location"), "known_facts": list(attributes.get("known_facts") or [])[-facts:],
            "knows": [entry.get("fact") for entry in (row.knowledge or [])[-4:] if isinstance(entry, dict)],
            "importance": row.importance}
    return {key: value for key, value in card.items() if value not in (None, "", [], {})}


async def build_messages(session: AsyncSession, campaign: Campaign, branch_id: UUID, action: str, head_turn_id: UUID | None,
                         current_state: dict[str, Any], instruction: str | None = None,
                         context_window: int | None = None, canon_notes: list[dict[str, str]] | None = None
                         ) -> list[dict[str, str]]:
    from app.services.memory_service import retrieve
    from app.services.state_context import relevant_characters

    rules = [{"type": row.rule_type, "strength": row.strength, "statement": row.statement, "exceptions": row.exceptions}
             for row in (await session.scalars(select(CanonRule).where(
                 CanonRule.campaign_id == campaign.id,
                 (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch_id))))).all()]
    recent = await history_for_branch(session, head_turn_id, limit=10)
    recent_text = "\n".join(clean_history_narration(turn.gm_response)[-1500:] for turn in recent[-2:])
    people, aliases, player = await relevant_characters(session, campaign, branch_id, current_state,
                                                        f"{action}\n{recent_text}", limit=14)
    scene_ids = set((current_state.get("scene") or {}).get("character_ids") or [])
    scene = [row for row in people if str(row.id) in scene_ids]
    lowered = action.casefold()
    mentioned = [row for row in people if row not in scene and any(
        len(name) > 2 and name.casefold() in lowered for name in [row.name, *aliases.get(row.id, [])])]
    others = [row for row in people if row not in scene and row not in mentioned]
    included = {row.id for row in people} | ({player.id} if player else set())
    names = {row.id: row.name for row in people}
    if player:
        names[player.id] = player.name

    relationships = []
    for relation in (await session.scalars(select(CharacterRelationship).where(
            CharacterRelationship.branch_id == branch_id, CharacterRelationship.visibility != "GM_ONLY"))).all():
        if relation.from_character_id not in included or relation.to_character_id not in included:
            continue
        dims = relation.dimensions or {}
        scores = {axis: dims[axis] for axis in AXES if isinstance(dims.get(axis), (int, float))}
        events = (await session.scalars(select(RelationshipEvent).where(
            RelationshipEvent.relationship_id == relation.id, RelationshipEvent.reason != "")
            .order_by(RelationshipEvent.created_at.desc()).limit(3))).all()
        if not scores and not dims.get("kinship") and not relation.summary and not events:
            continue
        relationships.append({"from": names[relation.from_character_id], "to": names[relation.to_character_id],
                              **scores, **({"kinship": dims["kinship"]} if dims.get("kinship") else {}),
                              **({"summary": relation.summary} if relation.summary else {}),
                              "recent_reasons": [f"turn {event.turn_index}: {event.reason}" for event in reversed(events)]})

    player_name = player.name if player else campaign.protagonist_name
    items = (await session.scalars(select(Item).where(Item.branch_id == branch_id, Item.visibility != "GM_ONLY"))).all()
    inventory = [_public(row) for row in items if row.owner_name.casefold() in {player_name.casefold(), "player"}]
    abilities = (await session.scalars(select(Ability).where(Ability.character_id == player.id))).all() if player else []
    location_row = None
    if current_state.get("current_location"):
        location_row = await session.scalar(select(Location).where(
            Location.branch_id == branch_id, Location.name.ilike(str(current_state["current_location"]))))
    objectives = (await session.scalars(select(Objective).where(
        Objective.branch_id == branch_id, Objective.visibility != "GM_ONLY"))).all()
    factions = (await session.scalars(select(Faction).where(Faction.branch_id == branch_id, Faction.visibility != "GM_ONLY"))).all()
    dead = (await session.scalars(select(Character.name).where(
        Character.branch_id == branch_id, Character.status.ilike("dead%") | Character.status.ilike("deceased%")))).all()
    secrets = (await session.scalars(select(Secret).where(Secret.branch_id == branch_id, Secret.visibility == "GM_ONLY"))).all()
    summary = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch_id, CampaignSummary.summary_type == "campaign"))
    head_index = recent[-1].turn_index if recent else 0
    memories = await retrieve(session, branch_id, action, entity_names={row.name for row in scene + mentioned},
                              entity_ids={str(row.id) for row in scene + mentioned}, current_turn_index=head_index, limit=10)

    constitution = dict(campaign.constitution or {})
    constitution["original_prompt"] = campaign.original_prompt
    constitution.pop("extraction", None)
    player_card = _person_card(player, aliases.get(player.id, [])) if player else {"name": player_name}
    player_card.update({
        "location": current_state.get("current_location") or None,
        "condition": current_state.get("player_condition") or current_state.get("player_status"),
        "money": current_state.get("money"),
        "ability_catalogue": [{"name": ability.name, "description": ability.description[:300], "source": ability.source,
                               **({"limitations": ability.limitations} if ability.limitations else {})}
                              for ability in abilities if ability.status == "ACTIVE"],
        "inventory": inventory,
    })
    profile = current_state.get("player_profile") if isinstance(current_state.get("player_profile"), dict) else {}
    if profile:
        # The living profile: who the character has become in play, not only the premise.
        player_card["living_profile"] = {category: [row["text"] for row in profile.get(category, [])
                                                    if isinstance(row, dict) and row.get("status") == "active"][-8:]
                                         for category in ("traits", "goals", "reputation", "world_rules")}
    if current_state.get("player_status") and "status" not in player_card:
        player_card["status"] = current_state["player_status"]
    layers = {
        "hard_canon": {
            "rules": rules,
            "confirmed_dead": list(dead),
            "established_identities": {row.name: aliases.get(row.id, [])[:5] for row in scene + mentioned if aliases.get(row.id)},
            "completed_objectives": [row.title for row in objectives if row.status == "completed"][-10:],
        },
        "protagonist": player_card,
        "world_time": (current_state.get("world_clock") or {}).get("label") or current_state.get("world_time"),
        "current_location": _public(location_row) if location_row else current_state.get("current_location"),
        "scene_participants": [_person_card(row, aliases.get(row.id, [])) for row in scene],
        "mentioned_in_action": [_person_card(row, aliases.get(row.id, [])) for row in mentioned],
        "other_relevant_people": [_person_card(row, aliases.get(row.id, []), facts=3) for row in others[:6]],
        "relationships": relationships[:24],
        "active_objectives": [{"title": row.title, "description": row.description} for row in objectives if row.status == "active"],
        "factions": [_public(row) for row in factions][:12],
        "retrieved_memories": [row.content for row in memories],
        "campaign_summary": summary.content if summary else "",
        "hidden_canon_for_game_master_only": [{"name": secret.name, "content": secret.content} for secret in secrets],
    }
    system = (PROMPT_DIR / "gm_system.md").read_text(encoding="utf-8")
    if campaign.game_mode == "guided":
        system += ("\n\nThis campaign uses choice play. Narrate the scene normally, then stop. "
                   "Do not write a list of choices or ask 'What do you do?' The app offers actions separately, "
                   "and the player may still type any action they want.")
    context_block = "\n\nCAMPAIGN CONSTITUTION (authoritative; never summarize away hard rules):\n" + json.dumps(constitution, ensure_ascii=False, default=str)
    context_block += "\n\nWORLD PRESENTATION (soft mood and visual direction):\n" + json.dumps(campaign.theme_profile or {}, ensure_ascii=False, default=str)
    context_block += "\n\nCANONICAL WORLD CONTEXT (most important first):\n" + json.dumps(layers, ensure_ascii=False, default=str)
    if canon_notes:
        context_block += "\n\nCANON NOTES FOR THIS TURN (follow these):\n" + "\n".join(f"- {note['note']}" for note in canon_notes)
    messages: list[dict[str, str]] = [{"role": "system", "content": system + context_block}]
    for turn in recent:
        if turn.player_action:
            messages.append({"role": "user", "content": turn.player_action[:8000]})
        if turn.gm_response:
            messages.append({"role": "assistant", "content": clean_history_narration(turn.gm_response)[:12000]})
    current = action
    if instruction:
        current += f"\n\nTemporary direction for this response only: {instruction}"
    messages.append({"role": "user", "content": current})
    # Keep the constitution and canon intact. Trim only the oldest verbatim history if the context is large.
    effective_window = context_window or (campaign.theme_profile or {}).get("context_window", 131_072)
    budget_chars = max(12_000, min(460_000, effective_window * 3))
    while sum(len(message["content"]) for message in messages) > budget_chars and len(messages) > 3:
        messages.pop(1)
    return messages
