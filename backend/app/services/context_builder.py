import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Campaign,
    CampaignSummary,
    CanonRule,
    Character,
    CharacterRelationship,
    Faction,
    Item,
    Location,
    Memory,
    Objective,
    Secret,
    Turn,
)
from app.services.narration import clean_history_narration
from app.services.retrieval import rank_memories

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def _public(row: Any) -> dict[str, Any]:
    return {key: value for key, value in {
        "name": getattr(row, "name", ""), "role": getattr(row, "role", ""),
        "status": getattr(row, "status", ""), "personality": getattr(row, "personality", ""),
        "description": getattr(row, "description", ""), "region": getattr(row, "region", ""),
        "properties": getattr(row, "properties", {}), "owner": getattr(row, "owner_name", ""),
        "quantity": getattr(row, "quantity", None), "condition": getattr(row, "condition", ""),
        "significance": getattr(row, "significance", ""), "summary": getattr(row, "summary", ""),
        "dimensions": getattr(row, "dimensions", {}), "motives": getattr(row, "motives", []),
        "goals": getattr(row, "goals", []),
    }.items() if value not in (None, "", {}, [])}


async def history_for_branch(session: AsyncSession, head_turn_id: UUID | None, limit: int = 12) -> list[Turn]:
    if head_turn_id is None:
        return []
    ancestry = select(Turn.id, Turn.parent_turn_id, Turn.turn_index).where(Turn.id == head_turn_id).cte("turn_ancestry", recursive=True)
    ancestry = ancestry.union_all(select(Turn.id, Turn.parent_turn_id, Turn.turn_index).join(ancestry, Turn.id == ancestry.c.parent_turn_id))
    result = await session.scalars(select(Turn).join(ancestry, Turn.id == ancestry.c.id).order_by(Turn.turn_index.desc()).limit(limit))
    return list(reversed(result.all()))


async def build_messages(session: AsyncSession, campaign: Campaign, branch_id: UUID, action: str, head_turn_id: UUID | None,
                         current_state: dict[str, Any], instruction: str | None = None,
                         context_window: int | None = None) -> list[dict[str, str]]:
    branch_rules = await session.scalars(select(CanonRule).where(
        CanonRule.campaign_id == campaign.id,
        (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch_id)),
    ))
    rules = [
        {"type": row.rule_type, "strength": row.strength, "statement": row.statement, "exceptions": row.exceptions}
        for row in branch_rules.all()
    ]
    player_name = campaign.protagonist_name.casefold()
    characters = list((await session.scalars(select(Character).where(
        Character.branch_id == branch_id, Character.visibility != "GM_ONLY",
    ).limit(80))).all())
    locations = list((await session.scalars(select(Location).where(
        Location.branch_id == branch_id, Location.visibility != "GM_ONLY",
    ).limit(80))).all())
    items = list((await session.scalars(select(Item).where(
        Item.branch_id == branch_id, Item.visibility != "GM_ONLY",
    ).limit(80))).all())
    factions = list((await session.scalars(select(Faction).where(
        Faction.branch_id == branch_id, Faction.visibility != "GM_ONLY",
    ).limit(50))).all())
    relationships = list((await session.scalars(select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch_id, CharacterRelationship.visibility != "GM_ONLY",
    ).limit(80))).all())
    objectives = list((await session.scalars(select(Objective).where(
        Objective.branch_id == branch_id, Objective.visibility != "GM_ONLY", Objective.status == "active",
    ).limit(40))).all())
    secrets = list((await session.scalars(select(Secret).where(
        Secret.branch_id == branch_id,
    ).limit(80))).all())
    memories = list((await session.scalars(select(Memory).where(
        Memory.branch_id == branch_id, Memory.visibility != "GM_ONLY",
    ).order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(160))).all())
    names = {row.name for row in characters + locations + items + factions}
    relevant_memories = rank_memories(memories, action, names, limit=8)
    summary = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch_id, CampaignSummary.summary_type == "campaign",
    ))
    recent = await history_for_branch(session, head_turn_id, limit=10)

    constitution = dict(campaign.constitution)
    constitution["original_prompt"] = campaign.original_prompt
    canon = {
        "rules": rules,
        "current_state": current_state,
        "characters": [_public(row) for row in characters if row.name.casefold() != player_name],
        "locations": [_public(row) for row in locations],
        "inventory": [_public(row) for row in items if row.owner_name.casefold() in {player_name, "player"}],
        "relationships": [_public(row) for row in relationships],
        "factions": [_public(row) for row in factions],
        "active_objectives": [_public(row) for row in objectives],
        "known_memories": [row.content for row in relevant_memories],
        "hidden_canon_for_game_master_only": [
            {"name": secret.name, "content": secret.content, "visibility": secret.visibility}
            for secret in secrets if secret.visibility == "GM_ONLY"
        ],
        "campaign_summary": summary.content if summary else "",
    }
    system = (PROMPT_DIR / "gm_system.md").read_text(encoding="utf-8")
    if campaign.game_mode == "guided":
        system += ("\n\nThis campaign uses choice play. Narrate the scene normally, then stop. "
                   "Do not write a list of choices or ask 'What do you do?' The app offers actions separately, "
                   "and the player may still type any action they want.")
    context_block = "\n\nCAMPAIGN CONSTITUTION (authoritative; never summarize away hard_invariants):\n" + json.dumps(constitution, ensure_ascii=False, default=str)
    context_block += "\n\nCANONICAL WORLD CONTEXT:\n" + json.dumps(canon, ensure_ascii=False, default=str)
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
    # Keep the hard constitution intact. Trim only the oldest verbatim history if context is unusually large.
    effective_window = context_window or campaign.theme_profile.get("context_window", 131_072)
    budget_chars = max(12_000, min(460_000, effective_window * 3))
    while sum(len(message["content"]) for message in messages) > budget_chars and len(messages) > 3:
        messages.pop(1)
    return messages
