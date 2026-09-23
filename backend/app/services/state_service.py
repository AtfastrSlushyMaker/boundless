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
from app.services.canon_guard import player_death_stated, validate_state_operation
from app.services.constitution import _explicit_identity

SNAPSHOT_MODELS = [
    CanonRule, Faction, Character, Location, Item, FactionRelationship,
    CharacterRelationship, Event, Memory, Secret, Objective, CampaignSummary,
]


def player_says_alive(action: str, name: str) -> bool:
    return bool(re.search(
        rf"\b(?:I am|I'm|{re.escape(name)} is)\s+(?:not dead(?: yet)?|alive)\b",
        action, re.IGNORECASE))


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
    async def ensure_person(raw_name: str, visibility: str = "PLAYER_KNOWN") -> Character | None:
        name = raw_name.strip()[:120]
        if not name:
            return None
        if name.casefold() in {"player", "protagonist", "you", campaign.protagonist_name.casefold()}:
            name = campaign.protagonist_name
        person = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(name)))
        if person is None:
            title = name.split(" ", 1)[0] if name.startswith(("King ", "Queen ", "Prince ", "Princess ")) else ""
            person = Character(campaign_id=campaign.id, branch_id=branch.id, name=name,
                               role=title, attributes={}, visibility=visibility)
            session.add(person)
            await session.flush()
        return person

    relationship_axes = ("trust", "respect", "fear", "hostility")

    async def save_relationship(change: dict[str, Any], fallback_from: str = "", fallback_to: str = "") -> None:
        source_name = str(change.get("from", fallback_from)).strip()[:120]
        target_name = str(change.get("to", fallback_to)).strip()[:120]
        if not source_name or not target_name or source_name.casefold() == target_name.casefold():
            return
        visibility = str(change.get("visibility", "PLAYER_KNOWN"))[:24]
        if visibility not in {"PLAYER_KNOWN", "CHARACTER_KNOWN", "WORLD_SECRET", "GM_ONLY"}:
            visibility = "PLAYER_KNOWN"
        source = await ensure_person(source_name, visibility)
        target = await ensure_person(target_name, visibility)
        if not source or not target:
            return
        relation = await session.scalar(select(CharacterRelationship).where(
            CharacterRelationship.branch_id == branch.id,
            CharacterRelationship.from_character_id == source.id,
            CharacterRelationship.to_character_id == target.id,
        ))
        if relation is None:
            relation = CharacterRelationship(campaign_id=campaign.id, branch_id=branch.id,
                from_character_id=source.id, to_character_id=target.id, dimensions={}, visibility=visibility)
            session.add(relation)

        dimensions = dict(relation.dimensions or {})
        supplied = change.get("dimensions") if isinstance(change.get("dimensions"), dict) else {}
        deltas = change.get("deltas") if isinstance(change.get("deltas"), dict) else {}
        axis_values: dict[str, int] = {}
        for axis in relationship_axes:
            current = dimensions.get(axis)
            if isinstance(current, (int, float)) and not isinstance(current, bool):
                axis_values[axis] = max(0, min(100, round(current)))
            absolute = supplied.get(axis)
            if isinstance(absolute, (int, float)) and not isinstance(absolute, bool):
                axis_values[axis] = max(0, min(100, round(absolute)))

        reasons = change.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = []
        reason = change.get("reason") or change.get("event")
        if isinstance(reason, str) and reason.strip():
            reasons = [*reasons, reason]
        reasons = [str(value).strip()[:500] for value in reasons if str(value).strip()][:8]
        has_axis_update = any(
            isinstance(supplied.get(axis), (int, float)) and not isinstance(supplied.get(axis), bool)
            or isinstance(deltas.get(axis), (int, float)) and not isinstance(deltas.get(axis), bool)
            for axis in relationship_axes
        )
        for axis in relationship_axes:
            delta = deltas.get(axis)
            if isinstance(delta, (int, float)) and not isinstance(delta, bool):
                axis_values[axis] = max(0, min(100, axis_values.get(axis, 50 if axis in {"trust", "respect"} else 0) + round(delta)))
        dimensions.update({key: value for key, value in supplied.items()
                           if key not in {*relationship_axes, "history", "last_interaction", "deltas"}})
        dimensions.update(axis_values)

        if reasons or has_axis_update:
            location = str(change.get("location") or (branch.current_state or {}).get("current_location") or "").strip()[:160]
            history = dimensions.get("history", [])
            if not isinstance(history, list):
                history = []
            for note in reasons:
                record = {"reason": note, "turn_index": turn.turn_index, "turn_id": str(turn.id)}
                if location:
                    record["location"] = location
                if not any(item.get("turn_id") == record["turn_id"] and item.get("reason") == note
                           for item in history if isinstance(item, dict)):
                    history.append(record)
            dimensions["history"] = history[-100:]
            last_interaction = {"turn_index": turn.turn_index, "turn_id": str(turn.id)}
            if location:
                last_interaction["location"] = location
            dimensions["last_interaction"] = last_interaction
        relation.dimensions = dimensions
        if isinstance(change.get("summary"), str):
            relation.summary = change["summary"][:2000]
        relation.visibility = visibility

    rows = await session.scalars(select(CanonRule).where(
        CanonRule.campaign_id == campaign.id,
        (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch.id)),
    ))
    rules = [{"rule_type": row.rule_type, "statement": row.statement,
              "strength": row.strength, "exceptions": row.exceptions} for row in rows.all()]
    applied: list[dict[str, Any]] = []
    for operation_model in interpretation.state_changes:
        operation = operation_model.model_dump(mode="json")
        if operation.get("kind") in {"UPDATE_CHARACTER", "CHANGE_CHARACTER_STATUS"}:
            person = str(operation.get("name") or operation.get("subject") or "").casefold()
            status = str(operation.get("value", {}).get("status", "")).casefold()
            if (person in {"player", "protagonist", "you", campaign.protagonist_name.casefold()}
                    and status in {"dead", "deceased"}
                    and not player_death_stated(turn.gm_response or "", campaign.protagonist_name)):
                continue
        validate_state_operation(operation, campaign.constitution, rules, branch.current_state or {})
        kind = operation["kind"]
        subject = operation.get("subject", "").strip()[:160]
        name = operation.get("name", "").strip()[:180]
        value = operation.get("value", {})
        visibility = operation.get("visibility", "PLAYER_KNOWN")
        if kind in {"CREATE_CHARACTER", "UPDATE_CHARACTER", "MOVE_CHARACTER", "CHANGE_CHARACTER_STATUS"}:
            character_name = campaign.protagonist_name if (name or subject).casefold() in {
                campaign.protagonist_name.casefold(), "player", "protagonist", "you",
            } else (name or subject)
            if character_name == campaign.protagonist_name:
                changes = {}
                if value.get("location"):
                    changes["current_location"] = str(value["location"])[:160]
                if value.get("status"):
                    changes["player_status"] = str(value["status"])[:32]
                if changes:
                    branch.current_state = {**(branch.current_state or {}), **changes}
            character = await session.scalar(select(Character).where(
                Character.campaign_id == campaign.id, Character.branch_id == branch.id,
                Character.name.ilike(character_name),
            )) if character_name else None
            if character is None and character_name:
                character = Character(campaign_id=campaign.id, branch_id=branch.id, name=character_name,
                    role=str(value.get("role", ""))[:160], personality=str(value.get("personality", ""))[:2000],
                    motivations=value.get("motivations", [])[:12] if isinstance(value.get("motivations"), list) else [],
                    attributes={}, visibility=visibility)
                session.add(character)
                await session.flush()
            if character:
                if kind == "CHANGE_CHARACTER_STATUS":
                    character.status = str(value.get("status", character.status))[:32]
                for field, limit in (("role", 160), ("personality", 2000), ("status", 32)):
                    if field in value:
                        setattr(character, field, str(value[field])[:limit])
                if "motivations" in value and isinstance(value["motivations"], list):
                    character.motivations = value["motivations"][:12]
                attributes = value.get("attributes") if isinstance(value.get("attributes"), dict) else {}
                flat = {key: entry for key, entry in value.items() if key not in {
                    "role", "personality", "status", "motivations", "attributes",
                }}
                if character_name == campaign.protagonist_name:
                    for identity_field in ("sex", "gender", "pronouns"):
                        attributes.pop(identity_field, None)
                        flat.pop(identity_field, None)
                if attributes or flat:
                    character.attributes = {**(character.attributes or {}), **attributes, **flat}
        elif kind in {"CREATE_LOCATION", "UPDATE_LOCATION"} and (name or subject):
            if (name or subject).casefold() in {"player", "protagonist", "you", campaign.protagonist_name.casefold()} and not value.get("location"):
                continue
            location_name = str(value.get("location") or name or subject)[:160]
            if {name.casefold(), subject.casefold()} & {"player", "protagonist", "you", campaign.protagonist_name.casefold()}:
                branch.current_state = {**(branch.current_state or {}), "current_location": location_name}
            location = await session.scalar(select(Location).where(
                Location.campaign_id == campaign.id, Location.branch_id == branch.id,
                Location.name.ilike(location_name),
            ))
            if location is None:
                location = Location(campaign_id=campaign.id, branch_id=branch.id, name=location_name,
                    description=str(value.get("description", ""))[:3000], region=str(value.get("region", ""))[:160],
                    properties={}, visibility=visibility)
                session.add(location)
            if location:
                if "description" in value:
                    location.description = str(value["description"])[:3000]
                if "region" in value:
                    location.region = str(value["region"])[:160]
                properties = value.get("properties") if isinstance(value.get("properties"), dict) else {}
                flat = {key: entry for key, entry in value.items() if key not in {
                    "location", "description", "region", "properties",
                }}
                if properties or flat:
                    location.properties = {**(location.properties or {}), **properties, **flat}
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
            await save_relationship(value, campaign.protagonist_name, subject or name)
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
        elif kind == "UPDATE_MONEY":
            previous = (branch.current_state or {}).get("money", {})
            if not isinstance(previous, dict):
                previous = {}
            currency = str(value.get("currency") or previous.get("currency") or "").casefold()[:40]
            amount = value.get("amount")
            delta = value.get("delta")
            if isinstance(amount, int) and not isinstance(amount, bool) and currency:
                money = {"amount": max(0, amount), "currency": currency}
            elif isinstance(delta, int) and not isinstance(delta, bool) and isinstance(previous.get("amount"), int) and currency == previous.get("currency"):
                money = {"amount": max(0, previous["amount"] + delta), "currency": currency}
            else:
                money = None
            if money:
                branch.current_state = {**(branch.current_state or {}), "money": money}
                protagonist = await session.scalar(select(Character).where(
                    Character.branch_id == branch.id, Character.name.ilike(campaign.protagonist_name)))
                if protagonist:
                    protagonist.attributes = {**(protagonist.attributes or {}), "money": money}
        elif kind in {"ADD_CANON_RULE", "MODIFY_CANON_RULE"} and str(value.get("source", "")).casefold() in {"player_meta", "canon_command", "retcon_command"}:
            session.add(CanonRule(campaign_id=campaign.id, branch_id=branch.id,
                rule_type=str(value.get("rule_type", "PLAYER_META"))[:64], statement=str(value.get("statement", ""))[:4000],
                strength=str(value.get("strength", "SOFT"))[:16], exceptions=value.get("exceptions", []), visibility=visibility,
                source=str(value["source"])[:64]))
        applied.append(operation)

    explicit_identity = _explicit_identity(turn.player_action)
    if explicit_identity:
        protagonist = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(campaign.protagonist_name)))
        if protagonist:
            protagonist.attributes = {**(protagonist.attributes or {}), **explicit_identity}
        constitution = dict(campaign.constitution or {})
        starting_state = dict(constitution.get("starting_state") or {})
        starting_state["identity"] = {**(starting_state.get("identity") or {}), **explicit_identity}
        constitution["starting_state"] = starting_state
        campaign.constitution = constitution
    if player_says_alive(turn.player_action, campaign.protagonist_name):
        branch.current_state = {**(branch.current_state or {}), "player_status": "alive"}
        protagonist = await ensure_person(campaign.protagonist_name)
        if protagonist:
            protagonist.status = "alive"

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
        await save_relationship(change)
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
    if turn.gm_response:
        session.add(Memory(campaign_id=campaign.id, branch_id=branch.id, source_turn_id=turn.id,
            memory_type="TURN", content=(f"Player: {turn.player_action[:600]}\n"
                f"World: {turn.gm_response[:1600]}")[:2200], importance=0.45,
            confidence=1.0, visibility="PLAYER_KNOWN",
            characters=[campaign.protagonist_name],
            locations=[str((branch.current_state or {}).get("current_location", ""))][:1]))
    branch.current_state = {**(branch.current_state or {}), "elapsed_seconds":
        int((branch.current_state or {}).get("elapsed_seconds", 0)) + interpretation.time_elapsed_seconds}
    turn.state_delta = {"operations": applied, "time_elapsed_seconds": interpretation.time_elapsed_seconds}
    await session.flush()
    return turn.state_delta
