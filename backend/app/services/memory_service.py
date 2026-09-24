"""Typed, deduplicated long-term memory with hybrid (semantic + structured) retrieval."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import Text, cast, or_, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Memory
from app.services.embeddings import get_embedding_provider
from app.services.identity import normalize_reference, same_statement, text_hash
from app.services.retrieval import score_memory

logger = logging.getLogger(__name__)

MEMORY_TYPES = {
    "CHARACTER_FACT": 0.6, "RELATIONSHIP": 0.65, "PROMISE": 0.8, "BETRAYAL": 0.9, "DISCOVERY": 0.7,
    "OBJECTIVE": 0.7, "LOCATION_DISCOVERY": 0.55, "ITEM_DISCOVERY": 0.6, "WORLD_EVENT": 0.55,
    "ABILITY_GAINED": 0.85, "ABILITY_LOST": 0.85, "SECRET": 0.85, "PLAYER_PREFERENCE": 0.8,
    "SETUP": 0.8, "TURN": 0.25,
}
TYPE_ALIASES = {
    "EVENT": "WORLD_EVENT", "FACT": "CHARACTER_FACT", "CHARACTER": "CHARACTER_FACT", "CLUE": "DISCOVERY",
    "LOCATION": "LOCATION_DISCOVERY", "ITEM": "ITEM_DISCOVERY", "ABILITY": "ABILITY_GAINED",
    "QUEST": "OBJECTIVE", "GOAL": "OBJECTIVE", "RELATION": "RELATIONSHIP", "LORE": "DISCOVERY",
}


def memory_type(value: Any) -> str:
    key = str(value or "WORLD_EVENT").strip().upper().replace(" ", "_")
    key = TYPE_ALIASES.get(key, key)
    return key if key in MEMORY_TYPES else "WORLD_EVENT"


def _clamp(value: Any, default: float) -> float:
    try:
        return max(0.0, min(float(value), 1.0))
    except (TypeError, ValueError):
        return default


async def add_memory(session: AsyncSession, *, campaign_id: UUID, branch_id: UUID, content: str,
                     kind: str = "WORLD_EVENT", importance: Any = None, confidence: Any = 0.8,
                     visibility: str = "PLAYER_KNOWN", turn_id: UUID | None = None, turn_index: int | None = None,
                     characters: list[str] | None = None, character_ids: list[str] | None = None,
                     locations: list[str] | None = None, factions: list[str] | None = None,
                     items: list[str] | None = None, keywords: list[str] | None = None,
                     in_world_time: str = "", pending: list[Memory] | None = None) -> tuple[Memory | None, str]:
    """Insert a memory unless it repeats one already stored. Returns (memory, outcome)."""
    content = " ".join(str(content or "").split())[:5000]
    if len(content) < 8:
        return None, "empty"
    kind = memory_type(kind)
    digest = text_hash(content)
    existing = await session.scalar(select(Memory).where(Memory.branch_id == branch_id, Memory.normalized_hash == digest).limit(1))
    candidates: list[Memory] = list(pending or [])
    if existing is None and kind != "TURN":
        candidates += list((await session.scalars(select(Memory).where(
            Memory.branch_id == branch_id, Memory.memory_type != "TURN",
        ).order_by(Memory.created_at.desc()).limit(200))).all())
        existing = next((row for row in candidates if row.memory_type != "TURN" and same_statement(row.content, content, 0.75)), None)
    elif existing is None:
        existing = next((row for row in candidates if row.normalized_hash == digest), None)
    weight = _clamp(importance, MEMORY_TYPES[kind]) if importance is not None else MEMORY_TYPES[kind]
    if existing is not None:
        existing.importance = max(existing.importance or 0.0, weight)
        merged_ids = list(dict.fromkeys([*(existing.character_ids or []), *(character_ids or [])]))
        if merged_ids != (existing.character_ids or []):
            existing.character_ids = merged_ids
        return existing, "deduplicated"

    def clean(values: list[str] | None, limit: int) -> list[str]:
        return [str(value)[:160] for value in (values or []) if isinstance(value, str) and value.strip()][:limit]

    memory = Memory(campaign_id=campaign_id, branch_id=branch_id, source_turn_id=turn_id, turn_index=turn_index,
                    memory_type=kind, content=content, normalized_hash=digest, importance=weight,
                    confidence=_clamp(confidence, 0.8), visibility=visibility, in_world_time=in_world_time[:120],
                    characters=clean(characters, 20), character_ids=list(dict.fromkeys(character_ids or []))[:20],
                    locations=clean(locations, 20), factions=clean(factions, 20), items=clean(items, 20),
                    keywords=clean(keywords, 30))
    session.add(memory)
    if pending is not None:
        pending.append(memory)
    return memory, "created"


async def embed_missing(session: AsyncSession, branch_id: UUID, limit: int = 200) -> int:
    """Attach embeddings to memories that lack one for the configured provider."""
    provider = get_embedding_provider()
    if provider is None:
        return 0
    rows = list((await session.scalars(select(Memory).where(
        Memory.branch_id == branch_id,
        or_(Memory.embedding.is_(None), Memory.embedding_model != provider.model),
    ).order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(limit))).all())
    if not rows:
        return 0
    vectors = await provider.embed([row.content[:2000] for row in rows])
    for row, vector in zip(rows, vectors, strict=True):
        row.embedding = vector
        row.embedding_model = provider.model
    return len(rows)


async def query_embedding(text: str) -> tuple[list[float] | None, str]:
    provider = get_embedding_provider()
    if provider is None or not text.strip():
        return None, ""
    try:
        return (await provider.embed([text[:2000]]))[0], provider.model
    except Exception as exc:  # retrieval must keep working when an embedding endpoint is down
        logger.warning("Query embedding failed: %s", type(exc).__name__)
        return None, ""


async def retrieve(session: AsyncSession, branch_id: UUID, query: str, *, entity_names: set[str] | None = None,
                   entity_ids: set[str] | None = None, current_turn_index: int | None = None,
                   limit: int = 10, include_gm_only: bool = False) -> list[Memory]:
    """Hybrid ranking: semantic similarity, entity match, lexical overlap, importance, recency, confidence."""
    visibility = [] if include_gm_only else [Memory.visibility != "GM_ONLY"]
    vector, model = await query_embedding(query)
    pool: dict[UUID, Memory] = {}
    if vector is not None:
        semantic = await session.scalars(select(Memory).where(
            Memory.branch_id == branch_id, Memory.embedding_model == model, Memory.embedding.is_not(None), *visibility,
        ).order_by(Memory.embedding.cosine_distance(vector)).limit(40))
        pool.update({row.id: row for row in semantic.all()})
    for query_rows in (
        select(Memory).where(Memory.branch_id == branch_id, *visibility).order_by(Memory.created_at.desc()).limit(60),
        select(Memory).where(Memory.branch_id == branch_id, Memory.memory_type != "TURN", *visibility)
        .order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(80),
    ):
        pool.update({row.id: row for row in (await session.scalars(query_rows)).all()})
    if entity_ids:
        linked = await session.scalars(select(Memory).where(
            Memory.branch_id == branch_id, Memory.character_ids.has_any(cast(list(entity_ids), ARRAY(Text))), *visibility,
        ).order_by(Memory.importance.desc()).limit(60))
        pool.update({row.id: row for row in linked.all()})
    names = {normalize_reference(name) for name in (entity_names or set()) if name}
    ranked = sorted(pool.values(), key=lambda row: score_memory(
        row, query, names, vector if row.embedding_model == model else None,
        entity_ids=entity_ids, current_turn_index=current_turn_index), reverse=True)
    chosen: list[Memory] = []
    for row in ranked:
        if any(same_statement(row.content, other.content, 0.7) for other in chosen):
            continue
        chosen.append(row)
        if len(chosen) >= limit:
            break
    return chosen
