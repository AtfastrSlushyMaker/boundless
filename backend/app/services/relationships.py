"""Relationship state as simulation: multidimensional scores with meaningful, dated history."""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Character, CharacterRelationship, RelationshipEvent
from app.services.character_store import StoreLog
from app.services.identity import is_unknown, normalize_reference, same_statement

AXES = ("trust", "affection", "fear", "respect", "loyalty", "hostility", "attraction", "debt", "dependence")
BASELINE = {"trust": 50, "respect": 50}
AXIS_ALIASES = {"love": "affection", "liking": "affection", "friendship": "affection", "anger": "hostility",
                "hatred": "hostility", "hate": "hostility", "suspicion": "trust", "desire": "attraction",
                "obligation": "debt", "reliance": "dependence", "loyal": "loyalty", "hostile": "hostility"}
MAX_DELTA = 30
NOISE = {"appeared in the story", "first appeared in the story", "appeared", "was present", "present in the scene"}
NON_AXIS_BLOCKLIST = {"history", "last_interaction", "deltas", "reason", "reasons"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        if isinstance(value, str) and re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value.strip()):
            return float(value)
        return None
    return float(value)


def _axis(name: str) -> str | None:
    key = str(name).strip().casefold()
    key = AXIS_ALIASES.get(key, key)
    return key if key in AXES else None


def clamp(value: float) -> int:
    return max(0, min(100, round(value)))


def interaction_sentence(text: str, first: str, second: str) -> str:
    """Directly stated interaction between two named people, used when the model gave no reason."""
    left = re.compile(rf"(?<![\w'-]){re.escape(first)}(?![\w'-])", re.IGNORECASE)
    right = re.compile(rf"(?<![\w'-]){re.escape(second)}(?![\w'-])|\byou\b", re.IGNORECASE)
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        if left.search(sentence) and right.search(sentence) and len(sentence) > 20:
            cleaned = " ".join(sentence.split()).strip(' "“”')
            return cleaned if len(cleaned) <= 240 else cleaned[:237].rsplit(" ", 1)[0] + "…"
    return ""


async def apply_relationship_change(session: AsyncSession, *, campaign_id: UUID, branch_id: UUID,
                                    source: Character, target: Character, change: dict[str, Any],
                                    turn_id: UUID | None, turn_index: int | None, location: str,
                                    narration: str, log: StoreLog) -> list[RelationshipEvent]:
    if source.id == target.id:
        log.reject({"kind": "CHANGE_RELATIONSHIP", "name": source.name}, "relationship_with_self")
        return []
    supplied = change.get("dimensions") if isinstance(change.get("dimensions"), dict) else {}
    deltas_in = change.get("deltas") if isinstance(change.get("deltas"), dict) else {}
    absolute: dict[str, int] = {}
    deltas: dict[str, float] = {}
    extras: dict[str, Any] = {}
    for key, value in supplied.items():
        axis = _axis(key)
        number = _number(value)
        if axis and number is not None:
            absolute[axis] = clamp(number)
        elif axis is None and key not in NON_AXIS_BLOCKLIST and not is_unknown(value) and not isinstance(value, (dict, list)):
            extras[str(key)[:40]] = value if not isinstance(value, str) else value[:160]
    for key, value in deltas_in.items():
        axis = _axis(key)
        number = _number(value)
        if axis and number is not None and number != 0:
            deltas[axis] = max(-MAX_DELTA, min(MAX_DELTA, number))
    reasons = [str(value).strip() for value in (change.get("reasons") or []) if isinstance(value, str)]
    for key in ("reason", "event", "why"):
        if isinstance(change.get(key), str) and change[key].strip():
            reasons.insert(0, change[key].strip())
    reasons = [reason[:500] for reason in reasons if reason.casefold().strip(" .") not in NOISE]
    summary = change.get("summary") if isinstance(change.get("summary"), str) and not is_unknown(change.get("summary")) else ""
    if not absolute and not deltas and not extras and not summary:
        log.reject({"kind": "CHANGE_RELATIONSHIP", "name": f"{source.name}->{target.name}"}, "no_relationship_content")
        return []

    relation = await session.scalar(select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch_id, CharacterRelationship.from_character_id == source.id,
        CharacterRelationship.to_character_id == target.id))
    visibility = str(change.get("visibility") or "PLAYER_KNOWN")[:24]
    if visibility not in {"PLAYER_KNOWN", "CHARACTER_KNOWN", "WORLD_SECRET", "GM_ONLY"}:
        visibility = "PLAYER_KNOWN"
    if relation is None:
        relation = CharacterRelationship(campaign_id=campaign_id, branch_id=branch_id, from_character_id=source.id,
                                         to_character_id=target.id, dimensions={}, visibility=visibility)
        session.add(relation)
        await session.flush()
        log.metrics["relationships_created"] += 1
    elif visibility == "PLAYER_KNOWN":
        relation.visibility = visibility
    dimensions = dict(relation.dimensions or {})
    reason = reasons[0] if reasons else interaction_sentence(narration, source.name, target.name) or \
        interaction_sentence(narration, target.name, source.name)
    recent = list((await session.scalars(select(RelationshipEvent).where(
        RelationshipEvent.relationship_id == relation.id).order_by(RelationshipEvent.created_at.desc()).limit(12))).all())
    events: list[RelationshipEvent] = []
    for axis in AXES:
        before = _number(dimensions.get(axis))
        if axis in absolute:
            after = absolute[axis]
        elif axis in deltas:
            start = before if before is not None else BASELINE.get(axis, 0)
            after = clamp(start + deltas[axis])
        else:
            continue
        if before is not None and round(before) == after:
            log.metrics["relationship_noop"] += 1
            continue
        repeated = next((event for event in recent if event.dimension == axis and reason and event.reason
                         and same_statement(event.reason, reason, 0.75) and event.turn_index is not None
                         and turn_index is not None and 0 < turn_index - event.turn_index <= 2), None)
        if repeated:
            log.metrics["relationship_repeat_rejected"] += 1
            continue
        same_turn = next((event for event in recent if event.dimension == axis and event.turn_id == turn_id), None)
        dimensions[axis] = after
        if same_turn:
            same_turn.after_value = after
            same_turn.delta = after - (same_turn.before_value if same_turn.before_value is not None else BASELINE.get(axis, 0))
            if reason and not same_turn.reason:
                same_turn.reason = reason
            continue
        event = RelationshipEvent(campaign_id=campaign_id, branch_id=branch_id, relationship_id=relation.id,
                                  turn_id=turn_id, turn_index=turn_index, dimension=axis, before_value=before,
                                  after_value=after,
                                  delta=after - (before if before is not None else BASELINE.get(axis, 0)),
                                  reason=reason, location=location[:160], visibility=visibility)
        session.add(event)
        events.append(event)
        log.metrics["relationship_changes"] += 1
    for key, value in extras.items():
        if dimensions.get(key) != value:
            dimensions[key] = value
            note = reason or f"{key.replace('_', ' ').title()}: {value}"
            if not any(event.dimension == key and event.turn_id == turn_id for event in recent):
                event = RelationshipEvent(campaign_id=campaign_id, branch_id=branch_id, relationship_id=relation.id,
                                          turn_id=turn_id, turn_index=turn_index, dimension=key[:32],
                                          reason=note[:500], location=location[:160], visibility=visibility)
                session.add(event)
                events.append(event)
    if events:
        interaction = {"turn_index": turn_index, "turn_id": str(turn_id) if turn_id else None}
        if location:
            interaction["location"] = location[:160]
        dimensions["last_interaction"] = interaction
    relation.dimensions = dimensions
    if summary and normalize_reference(summary) != normalize_reference(relation.summary or ""):
        relation.summary = summary[:2000]
    return events
