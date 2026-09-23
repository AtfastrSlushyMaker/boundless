from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Branch,
    Campaign,
    CampaignSummary,
    CanonRule,
    Character,
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
    Secret,
    Turn,
)
from app.schemas import CampaignImport

TABLES = ["canon_rules", "factions", "characters", "locations", "items", "faction_relationships",
          "character_relationships", "events", "memories", "secrets", "objectives", "campaign_summaries"]
MODELS = {
    "canon_rules": CanonRule, "factions": Faction, "characters": Character, "locations": Location,
    "items": Item, "faction_relationships": FactionRelationship,
    "character_relationships": CharacterRelationship, "events": Event, "memories": Memory,
    "secrets": Secret, "objectives": Objective, "campaign_summaries": CampaignSummary,
}


def _row(model, row):
    return jsonable_encoder({column.name: getattr(row, column.name) for column in model.__table__.columns})


async def export_campaign(session: AsyncSession, campaign_id: UUID) -> dict:
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise ValueError("Campaign not found.")
    data = {
        "format": "boundless-campaign", "version": 1,
        "campaign": _row(Campaign, campaign),
        "branches": [_row(Branch, row) for row in (await session.scalars(select(Branch).where(Branch.campaign_id == campaign_id))).all()],
        "turns": [_row(Turn, row) for row in (await session.scalars(select(Turn).where(Turn.campaign_id == campaign_id).order_by(Turn.turn_index))).all()],
        "checkpoints": [_row(Checkpoint, row) for row in (await session.scalars(select(Checkpoint).where(Checkpoint.campaign_id == campaign_id))).all()],
        "message_versions": [_row(MessageVersion, row) for row in (await session.scalars(select(MessageVersion).join(Turn, Turn.id == MessageVersion.turn_id).where(Turn.campaign_id == campaign_id))).all()],
    }
    for table, model in MODELS.items():
        data[table] = [_row(model, row) for row in (await session.scalars(select(model).where(model.campaign_id == campaign_id))).all()]
    profile = await session.scalar(select(ModelProfile).where(ModelProfile.active.is_(True)))
    if profile:
        data["model_profile"] = {
            "name": profile.name, "provider": profile.provider, "base_url": profile.base_url,
            "model": profile.model, "context_window": profile.context_window,
            "response_length": profile.response_length, "temperature": profile.temperature,
        }
    return data


def _as_uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return value


def _row_data(model, payload: dict, id_map: dict[str, UUID], campaign_id: UUID,
              branch_map: dict[str, UUID], turn_map: dict[str, UUID], character_map: dict[str, UUID]) -> dict:
    result = {}
    for column in model.__table__.columns:
        key = column.name
        if key not in payload:
            continue
        value = payload[key]
        if key == "id":
            value = id_map.get(str(value), uuid4())
        elif key == "campaign_id":
            value = campaign_id
        elif key == "branch_id" and value:
            value = branch_map.get(str(value))
        elif key in {"parent_branch_id"} and value:
            value = branch_map.get(str(value))
        elif key in {"head_turn_id", "forked_from_turn_id", "parent_turn_id", "source_turn_id", "turn_id"} and value:
            value = turn_map.get(str(value))
        elif key in {"from_character_id", "to_character_id"} and value:
            value = character_map.get(str(value))
        elif key == "embedding":
            value = None
        elif value is not None and isinstance(value, str) and str(column.type).startswith("DATETIME"):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        result[key] = value
    return result


async def import_campaign(session: AsyncSession, untrusted: dict) -> Campaign:
    payload = CampaignImport.model_validate(untrusted)
    if payload.version != 1:
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
    branch_map = {str(row.get("id")): uuid4() for row in branch_rows if row.get("id")}
    turn_map = {str(row.get("id")): uuid4() for row in turn_rows if row.get("id")}
    entity_maps = {table: {str(row.get("id")): uuid4() for row in untrusted.get(table, []) if row.get("id")} for table in TABLES}
    id_map = {**branch_map, **turn_map}
    for mapping in entity_maps.values():
        id_map.update(mapping)
    character_map = entity_maps["characters"]

    branch_parents = []
    for row in branch_rows:
        values = _row_data(Branch, row, id_map, campaign_id, branch_map, turn_map, character_map)
        parent_id = values.pop("parent_branch_id", None)
        branch = Branch(**values)
        session.add(branch)
        branch_parents.append((branch, parent_id))
    await session.flush()
    for branch, parent_id in branch_parents:
        branch.parent_branch_id = parent_id
    await session.flush()
    for row in turn_rows:
        values = _row_data(Turn, row, id_map, campaign_id, branch_map, turn_map, character_map)
        session.add(Turn(**values))
    await session.flush()
    for row in untrusted.get("message_versions", []):
        values = _row_data(MessageVersion, row, id_map, campaign_id, branch_map, turn_map, character_map)
        session.add(MessageVersion(**values))
    for table in TABLES:
        model = MODELS[table]
        rows = untrusted.get(table, [])
        for row in rows:
            values = _row_data(model, row, id_map, campaign_id, branch_map, turn_map, character_map)
            session.add(model(**values))
    for row in untrusted.get("checkpoints", []):
        values = _row_data(Checkpoint, row, id_map, campaign_id, branch_map, turn_map, character_map)
        snapshot = deepcopy(values.get("state_snapshot", {}))
        for table, entries in snapshot.items():
            if not isinstance(entries, list):
                continue
            for entity in entries:
                if entity.get("id"):
                    entity["id"] = str(id_map.get(str(entity["id"]), uuid4()))
                if entity.get("branch_id"):
                    entity["branch_id"] = str(branch_map.get(str(entity["branch_id"]), ""))
                for key in ("from_character_id", "to_character_id"):
                    if entity.get(key):
                        entity[key] = str(character_map.get(str(entity[key]), ""))
                for key in ("source_turn_id", "turn_id"):
                    if entity.get(key):
                        entity[key] = str(turn_map.get(str(entity[key]), ""))
        values["state_snapshot"] = snapshot
        session.add(Checkpoint(**values))

    source_active = old_campaign.get("active_branch_id")
    campaign.active_branch_id = branch_map.get(str(source_active)) if source_active else next(iter(branch_map.values()), None)
    await session.commit()
    await session.refresh(campaign)
    return campaign
