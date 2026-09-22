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
    Branch,
    Campaign,
    CampaignSummary,
    CanonRule,
    Character,
    CharacterRelationship,
    Event,
    Faction,
    FactionRelationship,
    Item,
    Location,
    Memory,
    Objective,
    Secret,
    Turn,
)
from app.schemas import StateInterpretation
from app.services.canon_guard import validate_state_operation

SNAPSHOT_MODELS = [
    CanonRule, Faction, Character, Location, Item, FactionRelationship,
    CharacterRelationship, Event, Memory, Secret, Objective, CampaignSummary,
]


async def capture_snapshot(session: AsyncSession, branch: Branch) -> dict[str, Any]:
    result: dict[str, Any] = {"current_state": deepcopy(branch.current_state or {})}
    for model in SNAPSHOT_MODELS:
        rows = (await session.scalars(select(model).where(model.branch_id == branch.id))).all()
        result[model.__tablename__] = [jsonable_encoder({column.name: getattr(row, column.name) for column in model.__table__.columns}) for row in rows]
    return result


def _coerce_row(model: type[Base], values: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for column in model.__table__.columns:
        if column.name not in values:
            continue
        value = values[column.name]
        if isinstance(column.type, Uuid) and isinstance(value, str):
            value = UUID(value)
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
    for model in SNAPSHOT_MODELS:
        for payload in snapshot.get(model.__tablename__, []):
            row_data = _coerce_row(model, payload)
            row_data["branch_id"] = branch.id
            session.add(model(**row_data))
    await session.flush()


async def apply_interpretation(session: AsyncSession, campaign: Campaign, branch: Branch, turn: Turn,
                               interpretation: StateInterpretation) -> dict[str, Any]:
    rows = await session.scalars(select(CanonRule).where(
        CanonRule.campaign_id == campaign.id,
        (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch.id)),
    ))
    rules = [{"rule_type": row.rule_type, "statement": row.statement,
              "strength": row.strength, "exceptions": row.exceptions} for row in rows.all()]
    applied: list[dict[str, Any]] = []
    for operation_model in interpretation.state_changes:
        operation = operation_model.model_dump(mode="json")
        validate_state_operation(operation, campaign.constitution, rules, branch.current_state or {})
        kind = operation["kind"]
        subject = operation.get("subject", "").strip()[:160]
        name = operation.get("name", "").strip()[:180]
        value = operation.get("value", {})
        visibility = operation.get("visibility", "PLAYER_KNOWN")
        if kind in {"CREATE_CHARACTER", "UPDATE_CHARACTER", "MOVE_CHARACTER", "CHANGE_CHARACTER_STATUS"}:
            character_name = name or subject
            if character_name.casefold() in {campaign.protagonist_name.casefold(), "player", "protagonist"}:
                if kind == "MOVE_CHARACTER":
                    branch.current_state = {**(branch.current_state or {}), "current_location": str(value.get("location", ""))[:160]}
                elif kind == "CHANGE_CHARACTER_STATUS":
                    branch.current_state = {**(branch.current_state or {}), "player_status": str(value.get("status", "alive"))[:32]}
            character = await session.scalar(select(Character).where(
                Character.campaign_id == campaign.id, Character.branch_id == branch.id,
                Character.name.ilike(character_name),
            ))
            if kind == "CREATE_CHARACTER" and character is None and character_name:
                character = Character(campaign_id=campaign.id, branch_id=branch.id, name=character_name,
                    role=str(value.get("role", ""))[:160], personality=str(value.get("personality", ""))[:2000],
                    motivations=value.get("motivations", [])[:12], attributes=value.get("attributes", {}), visibility=visibility)
                session.add(character)
            elif character:
                if kind == "CHANGE_CHARACTER_STATUS":
                    character.status = str(value.get("status", character.status))[:32]
                if kind == "MOVE_CHARACTER":
                    character.attributes = {**(character.attributes or {}), "location": str(value.get("location", ""))[:160]}
                if kind == "UPDATE_CHARACTER":
                    for field in ("role", "personality", "status"):
                        if field in value:
                            setattr(character, field, str(value[field])[:2000])
                    if "motivations" in value:
                        character.motivations = value["motivations"][:12]
                    if "attributes" in value and isinstance(value["attributes"], dict):
                        character.attributes = {**(character.attributes or {}), **value["attributes"]}
        elif kind in {"CREATE_LOCATION", "UPDATE_LOCATION"} and (name or subject):
            location_name = name or subject
            location = await session.scalar(select(Location).where(
                Location.campaign_id == campaign.id, Location.branch_id == branch.id,
                Location.name.ilike(location_name),
            ))
            if kind == "CREATE_LOCATION" and location is None:
                session.add(Location(campaign_id=campaign.id, branch_id=branch.id, name=location_name,
                    description=str(value.get("description", ""))[:3000], region=str(value.get("region", ""))[:160],
                    properties=value.get("properties", {}), visibility=visibility))
            elif location:
                if "description" in value:
                    location.description = str(value["description"])[:3000]
                if "region" in value:
                    location.region = str(value["region"])[:160]
                if "properties" in value:
                    location.properties = {**(location.properties or {}), **value["properties"]}
        elif kind == "ADD_ITEM" and name:
            item = await session.scalar(select(Item).where(Item.campaign_id == campaign.id, Item.branch_id == branch.id, Item.name.ilike(name)))
            if item:
                item.quantity += max(1, min(int(value.get("quantity", 1)), 999))
            else:
                session.add(Item(campaign_id=campaign.id, branch_id=branch.id, name=name,
                    owner_name=str(value.get("owner", campaign.protagonist_name))[:120],
                    quantity=max(1, min(int(value.get("quantity", 1)), 999)), condition=str(value.get("condition", "intact"))[:80],
                    properties=value.get("properties", {}), significance=str(value.get("significance", ""))[:2000], visibility=visibility))
        elif kind in {"REMOVE_ITEM", "TRANSFER_ITEM"} and (name or subject):
            item = await session.scalar(select(Item).where(Item.campaign_id == campaign.id, Item.branch_id == branch.id, Item.name.ilike(name or subject)))
            if item:
                if kind == "REMOVE_ITEM":
                    quantity = max(1, min(int(value.get("quantity", item.quantity)), 999))
                    if item.quantity <= quantity:
                        await session.delete(item)
                    else:
                        item.quantity -= quantity
                else:
                    item.owner_name = str(value.get("owner", ""))[:120]
        elif kind in {"CREATE_EVENT", "CREATE_MEMORY"}:
            content = str(value.get("content", name or subject))[:5000]
            if content:
                if kind == "CREATE_EVENT":
                    session.add(Event(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
                        content=content, certainty=operation.get("certainty", "CONFIRMED"),
                        participants=value.get("participants", [])[:20], visibility=visibility))
                else:
                    session.add(Memory(campaign_id=campaign.id, branch_id=branch.id, source_turn_id=turn.id,
                        memory_type=str(value.get("memory_type", "EVENT"))[:40], content=content,
                        importance=max(0.0, min(float(value.get("importance", 0.4)), 1.0)),
                        confidence=max(0.0, min(float(value.get("confidence", 0.8)), 1.0)), visibility=visibility,
                        characters=value.get("characters", [])[:20], locations=value.get("locations", [])[:20],
                        factions=value.get("factions", [])[:20], items=value.get("items", [])[:20], keywords=value.get("keywords", [])[:30]))
        elif kind == "CHANGE_RELATIONSHIP":
            source_name = str(value.get("from", campaign.protagonist_name)).casefold()
            target_name = str(value.get("to", subject or name)).casefold()
            source = await session.scalar(select(Character).where(Character.branch_id == branch.id, Character.name.ilike(source_name)))
            target = await session.scalar(select(Character).where(Character.branch_id == branch.id, Character.name.ilike(target_name)))
            if source and target and source.id != target.id:
                relation = await session.scalar(select(CharacterRelationship).where(
                    CharacterRelationship.branch_id == branch.id, CharacterRelationship.from_character_id == source.id,
                    CharacterRelationship.to_character_id == target.id,
                ))
                if relation is None:
                    relation = CharacterRelationship(campaign_id=campaign.id, branch_id=branch.id,
                        from_character_id=source.id, to_character_id=target.id, dimensions={}, visibility=visibility)
                    session.add(relation)
                relation.dimensions = {**(relation.dimensions or {}), **value.get("dimensions", {})}
                if "summary" in value:
                    relation.summary = str(value["summary"])[:2000]
        elif kind == "CREATE_SECRET" and name:
            session.add(Secret(campaign_id=campaign.id, branch_id=branch.id, name=name,
                content=str(value.get("content", ""))[:5000], visibility=visibility,
                discovered_by=value.get("discovered_by", [])[:20], source_turn_id=turn.id))
        elif kind == "REVEAL_SECRET" and (name or subject):
            secret = await session.scalar(select(Secret).where(Secret.branch_id == branch.id, Secret.name.ilike(name or subject)))
            if secret:
                secret.visibility = "PLAYER_KNOWN"
                secret.discovered_by = list(set(secret.discovered_by or []) | {campaign.protagonist_name})
        elif kind == "CREATE_OBJECTIVE" and name:
            session.add(Objective(campaign_id=campaign.id, branch_id=branch.id, title=name,
                description=str(value.get("description", ""))[:2000], status="active", visibility=visibility))
        elif kind == "UPDATE_OBJECTIVE" and (name or subject):
            objective = await session.scalar(select(Objective).where(Objective.branch_id == branch.id, Objective.title.ilike(name or subject)))
            if objective:
                objective.status = str(value.get("status", objective.status))[:24]
                if "description" in value:
                    objective.description = str(value["description"])[:2000]
        elif kind == "CREATE_FACTION" and name:
            faction = await session.scalar(select(Faction).where(
                Faction.campaign_id == campaign.id, Faction.branch_id == branch.id,
                Faction.name.ilike(name),
            ))
            if faction is None:
                session.add(Faction(campaign_id=campaign.id, branch_id=branch.id,
                    name=name, description=str(value.get("description", ""))[:3000],
                    motives=[str(part)[:300] for part in value.get("motives", [])[:20]],
                    visibility=visibility))
            else:
                if "description" in value:
                    faction.description = str(value["description"])[:3000]
                if "motives" in value:
                    faction.motives = [str(part)[:300] for part in value["motives"][:20]]
        elif kind == "CHANGE_FACTION_RELATIONSHIP":
            source_name = str(value.get("from", subject)).strip()[:160]
            target_name = str(value.get("to", name)).strip()[:160]
            if source_name and target_name and source_name.casefold() != target_name.casefold():
                relation = await session.scalar(select(FactionRelationship).where(
                    FactionRelationship.branch_id == branch.id,
                    FactionRelationship.from_faction.ilike(source_name),
                    FactionRelationship.to_faction.ilike(target_name),
                ))
                if relation is None:
                    relation = FactionRelationship(campaign_id=campaign.id, branch_id=branch.id,
                        from_faction=source_name, to_faction=target_name)
                    session.add(relation)
                relation.relation = str(value.get("relation", relation.relation or "unknown"))[:80]
                relation.details = str(value.get("details", relation.details or ""))[:2000]
        elif kind == "ADVANCE_WORLD_TIME":
            next_state = {**(branch.current_state or {}), "time_elapsed_seconds":
                int((branch.current_state or {}).get("time_elapsed_seconds", 0)) + max(0, min(int(value.get("seconds", 0)), 31_536_000))}
            if value.get("label"):
                next_state["world_time"] = str(value["label"])[:120]
            branch.current_state = next_state
        elif kind in {"ADD_CANON_RULE", "MODIFY_CANON_RULE"} and str(value.get("source", "")).casefold() in {"player_meta", "canon_command", "retcon_command"}:
            session.add(CanonRule(campaign_id=campaign.id, branch_id=branch.id,
                rule_type=str(value.get("rule_type", "PLAYER_META"))[:64], statement=str(value.get("statement", ""))[:4000],
                strength=str(value.get("strength", "SOFT"))[:16], exceptions=value.get("exceptions", []), visibility=visibility,
                source=str(value["source"])[:64]))
        applied.append(operation)

    for change in interpretation.knowledge_changes[:40]:
        person_name = str(change.get("character", change.get("who", ""))).strip()[:120]
        fact = str(change.get("fact", change.get("content", ""))).strip()[:2000]
        if not person_name or not fact:
            continue
        person = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(person_name)))
        if person:
            existing = list(person.knowledge or [])
            if not any(row.get("fact") == fact for row in existing):
                existing.append({"fact": fact, "certainty": str(change.get("certainty", "CONFIRMED"))[:16],
                                 "source_turn_id": str(turn.id)})
                person.knowledge = existing[-200:]
    for change in interpretation.relationship_changes[:30]:
        source_name = str(change.get("from", "")).strip()[:120]
        target_name = str(change.get("to", "")).strip()[:120]
        if not source_name or not target_name or source_name.casefold() == target_name.casefold():
            continue
        source = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(source_name)))
        target = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(target_name)))
        if not source or not target:
            continue
        relation = await session.scalar(select(CharacterRelationship).where(
            CharacterRelationship.branch_id == branch.id,
            CharacterRelationship.from_character_id == source.id,
            CharacterRelationship.to_character_id == target.id))
        if relation is None:
            relation = CharacterRelationship(campaign_id=campaign.id, branch_id=branch.id,
                from_character_id=source.id, to_character_id=target.id,
                dimensions={}, visibility=str(change.get("visibility", "PLAYER_KNOWN"))[:24])
            session.add(relation)
        if isinstance(change.get("dimensions"), dict):
            relation.dimensions = {**(relation.dimensions or {}), **change["dimensions"]}
        if "summary" in change:
            relation.summary = str(change["summary"])[:2000]
    for event in interpretation.events[:30]:
        content = str(event.get("content", ""))[:5000]
        if content:
            if event.get("in_world_time"):
                turn.in_world_time = str(event["in_world_time"])[:120]
                branch.current_state = {**(branch.current_state or {}), "world_time": turn.in_world_time}
            session.add(Event(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
                content=content, certainty=str(event.get("certainty", "CONFIRMED"))[:16],
                in_world_time=str(event.get("in_world_time", ""))[:120],
                participants=event.get("participants", [])[:20],
                visibility=str(event.get("visibility", "PLAYER_KNOWN"))[:24]))
    for memory in interpretation.new_memories[:20]:
        content = str(memory.get("content", ""))[:5000]
        if content:
            session.add(Memory(campaign_id=campaign.id, branch_id=branch.id, source_turn_id=turn.id,
                memory_type=str(memory.get("type", "EVENT"))[:40], content=content,
                importance=max(0.0, min(float(memory.get("importance", 0.4)), 1.0)),
                confidence=max(0.0, min(float(memory.get("confidence", 0.8)), 1.0)),
                visibility=str(memory.get("visibility", "PLAYER_KNOWN"))[:24],
                characters=memory.get("characters", [])[:20], locations=memory.get("locations", [])[:20],
                factions=memory.get("factions", [])[:20], items=memory.get("items", [])[:20],
                keywords=memory.get("keywords", [])[:30]))
    branch.current_state = {**(branch.current_state or {}), "elapsed_seconds":
        int((branch.current_state or {}).get("elapsed_seconds", 0)) + interpretation.time_elapsed_seconds}
    turn.state_delta = {"operations": applied, "time_elapsed_seconds": interpretation.time_elapsed_seconds}
    await session.flush()
    return turn.state_delta
