"""Portable campaign exports.

Version 2 adds aliases, facts, relationship events, the ability catalogue, memory metadata,
model-role names (never keys), and excludes failed generation attempts by default.
Version 1 files import through an upgrade step that builds the new structures.
"""

from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.sqltypes import Uuid

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
    Checkpoint,
    Event,
    Faction,
    FactionRelationship,
    Item,
    Location,
    Memory,
    MessageVersion,
    ModelProfile,
    Objective,
    RelationshipEvent,
    Secret,
    Turn,
)
from app.schemas import CampaignImport
from app.services.identity import text_hash
from app.services.idmap import remap_ids

EXPORT_VERSION = 2
TABLES = ["canon_rules", "factions", "characters", "character_aliases", "character_facts", "abilities", "locations",
          "items", "faction_relationships", "character_relationships", "relationship_events", "events", "memories",
          "secrets", "objectives", "campaign_summaries"]
MODELS = {
    "canon_rules": CanonRule, "factions": Faction, "characters": Character, "character_aliases": CharacterAlias,
    "character_facts": CharacterFact, "abilities": Ability, "locations": Location, "items": Item,
    "faction_relationships": FactionRelationship, "character_relationships": CharacterRelationship,
    "relationship_events": RelationshipEvent, "events": Event, "memories": Memory, "secrets": Secret,
    "objectives": Objective, "campaign_summaries": CampaignSummary,
}
SKIP_COLUMNS = {"embedding"}


def _row(model, row):
    return jsonable_encoder({column.name: getattr(row, column.name) for column in model.__table__.columns
                             if column.name not in SKIP_COLUMNS})


async def export_campaign(session: AsyncSession, campaign_id: UUID, *, include_failed: bool = False) -> dict:
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ValueError("Campaign not found.")
    turn_query = select(Turn).where(Turn.campaign_id == campaign_id)
    if not include_failed:
        turn_query = turn_query.where(Turn.canonical.is_(True))
    turns = list((await session.scalars(turn_query.order_by(Turn.turn_index, Turn.created_at))).all())
    turn_ids = {turn.id for turn in turns}
    checkpoints = [row for row in (await session.scalars(select(Checkpoint).where(Checkpoint.campaign_id == campaign_id))).all()
                   if row.turn_id is None or row.turn_id in turn_ids]
    data = {
        "format": "boundless-campaign", "version": EXPORT_VERSION,
        "campaign": _row(Campaign, campaign),
        "branches": [_row(Branch, row) for row in (await session.scalars(select(Branch).where(Branch.campaign_id == campaign_id))).all()],
        "turns": [_row(Turn, row) for row in turns],
        "checkpoints": [_row(Checkpoint, row) for row in checkpoints],
        "message_versions": [_row(MessageVersion, row) for row in (await session.scalars(
            select(MessageVersion).join(Turn, Turn.id == MessageVersion.turn_id).where(Turn.campaign_id == campaign_id))).all()
            if row.turn_id in turn_ids],
    }
    for table, model in MODELS.items():
        data[table] = [_row(model, row) for row in (await session.scalars(select(model).where(model.campaign_id == campaign_id))).all()]
    profile = await session.scalar(select(ModelProfile).where(ModelProfile.active.is_(True), ModelProfile.role == "narrator"))
    if profile:
        data["model_profile"] = {
            "name": profile.name, "provider": profile.provider, "base_url": profile.base_url,
            "model": profile.model, "context_window": profile.context_window,
            "response_length": profile.response_length, "temperature": profile.temperature,
        }
    roles = (await session.scalars(select(ModelProfile).where(ModelProfile.role != "narrator", ModelProfile.active.is_(True)))).all()
    # Informational only: which models tracked state. No endpoints with credentials, no API keys.
    data["model_roles"] = [{"role": row.role, "provider": row.provider, "model": row.model} for row in roles]
    return data


def _datetime(value: Any) -> Any:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value


def _row_data(model, payload: dict, id_map: dict[str, str], campaign_id: UUID) -> dict:
    result = {}
    for column in model.__table__.columns:
        key = column.name
        if key not in payload or key in SKIP_COLUMNS:
            continue
        value = payload[key]
        if key == "campaign_id":
            value = campaign_id
        elif key == "id":
            value = UUID(id_map.get(str(value), str(uuid4())))
        elif isinstance(column.type, Uuid):
            value = UUID(id_map[str(value)]) if value and str(value) in id_map else None
        elif value is not None and str(column.type).startswith(("DATETIME", "TIMESTAMP")):
            value = _datetime(value)
        elif isinstance(value, (dict, list)):
            value = remap_ids(value, id_map)
        result[key] = value
    return result


async def import_campaign(session: AsyncSession, untrusted: dict) -> Campaign:
    payload = CampaignImport.model_validate(untrusted)
    if payload.version not in {1, EXPORT_VERSION}:
        raise ValueError(f"Unsupported campaign export version: {payload.version}")
    for field in ("branches", "turns", "checkpoints", "message_versions", *TABLES):
        rows = untrusted.get(field, [])
        if not isinstance(rows, list) or len(rows) > 50_000:
            raise ValueError(f"Invalid or oversized {field} collection.")

    old_campaign = payload.campaign
    campaign_id = uuid4()
    campaign = Campaign(
        id=campaign_id, title=str(old_campaign.get("title", "Imported world"))[:180],
        original_prompt=str(old_campaign.get("original_prompt", ""))[:30_000],
        constitution=old_campaign.get("constitution", {}), theme_profile=old_campaign.get("theme_profile", {}),
        protagonist_name=str(old_campaign.get("protagonist_name", "You"))[:120],
        genre=str(old_campaign.get("genre", "Open world"))[:80],
        game_mode=old_campaign.get("game_mode") if isinstance(old_campaign.get("game_mode"), str)
        and old_campaign.get("game_mode") in {"freeform", "guided"} else "freeform",
        archived=False,
    )
    session.add(campaign)
    await session.flush()

    branch_rows = untrusted.get("branches", [])
    turn_rows = sorted(untrusted.get("turns", []), key=lambda row: int(row.get("turn_index", 0)))
    id_map: dict[str, str] = {}
    for field in ("branches", "turns", "checkpoints", "message_versions", *TABLES):
        for row in untrusted.get(field, []):
            if isinstance(row, dict) and row.get("id"):
                id_map[str(row["id"])] = str(uuid4())

    branch_parents = []
    for row in branch_rows:
        values = _row_data(Branch, row, id_map, campaign_id)
        parent_id = values.pop("parent_branch_id", None)
        head_id = values.pop("head_turn_id", None)
        forked = values.pop("forked_from_turn_id", None)
        branch = Branch(**values)
        session.add(branch)
        branch_parents.append((branch, parent_id, head_id, forked))
    await session.flush()
    for branch, parent_id, _, _ in branch_parents:
        branch.parent_branch_id = parent_id
    await session.flush()
    for row in turn_rows:
        values = _row_data(Turn, row, id_map, campaign_id)
        if payload.version == 1 and str(row.get("status")) in {"error", "interrupted", "failed"}:
            values["status"], values["canonical"] = "failed", False
        session.add(Turn(**values))
        await session.flush()
    for branch, _, head_id, forked in branch_parents:
        branch.head_turn_id, branch.forked_from_turn_id = head_id, forked
    for row in untrusted.get("message_versions", []):
        values = _row_data(MessageVersion, row, id_map, campaign_id)
        if values.get("turn_id"):
            session.add(MessageVersion(**values))
    for table in TABLES:
        model = MODELS[table]
        for row in untrusted.get(table, []):
            session.add(model(**_row_data(model, row, id_map, campaign_id)))
        await session.flush()
    for row in untrusted.get("checkpoints", []):
        values = _row_data(Checkpoint, row, id_map, campaign_id)
        snapshot = remap_ids(deepcopy(row.get("state_snapshot") or {}), id_map)
        for rows in snapshot.values():
            if isinstance(rows, list):
                for entity in rows:
                    if isinstance(entity, dict):
                        entity.pop("embedding", None)
        values["state_snapshot"] = snapshot
        session.add(Checkpoint(**values))

    source_active = old_campaign.get("active_branch_id")
    first_branch = branch_parents[0][0].id if branch_parents else None
    campaign.active_branch_id = UUID(id_map[str(source_active)]) if source_active and str(source_active) in id_map else first_branch
    await session.flush()
    if payload.version == 1:
        await upgrade_v1_campaign(session, campaign)
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def upgrade_v1_campaign(session: AsyncSession, campaign: Campaign) -> None:
    """Build aliases, fact rows, relationship events, memory metadata, and time for a v1 export."""
    from app.services.character_store import ensure_identity_rows
    from app.services.world_time import initial_clock

    noise = {"first appeared in the story", "appeared in the story"}
    turn_index = {turn.id: turn.turn_index for turn in (await session.scalars(select(Turn).where(Turn.campaign_id == campaign.id))).all()}
    for branch in (await session.scalars(select(Branch).where(Branch.campaign_id == campaign.id))).all():
        await ensure_identity_rows(session, branch.id)
        for relation in (await session.scalars(select(CharacterRelationship).where(CharacterRelationship.branch_id == branch.id))).all():
            for entry in (relation.dimensions or {}).get("history", []) or []:
                if not isinstance(entry, dict) or str(entry.get("reason", "")).strip().casefold() in noise or not entry.get("reason"):
                    continue
                session.add(RelationshipEvent(campaign_id=campaign.id, branch_id=branch.id, relationship_id=relation.id,
                                              turn_index=entry.get("turn_index") if isinstance(entry.get("turn_index"), int) else None,
                                              dimension="note", reason=str(entry["reason"])[:2000],
                                              location=str(entry.get("location") or "")[:160], visibility=relation.visibility))
        state = dict(branch.current_state or {})
        if not state.get("world_clock"):
            clock = initial_clock()
            clock["elapsed_seconds"] = int(state.get("elapsed_seconds") or 0)
            state["world_clock"] = clock
            branch.current_state = state
    for memory in (await session.scalars(select(Memory).where(Memory.campaign_id == campaign.id))).all():
        memory.normalized_hash = text_hash(memory.content)
        memory.turn_index = turn_index.get(memory.source_turn_id)
    await session.flush()
