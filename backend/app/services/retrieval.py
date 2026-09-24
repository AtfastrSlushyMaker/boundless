import math
import re
from datetime import UTC, datetime
from typing import Any

TOKEN = re.compile(r"[\w'-]+", re.UNICODE)
QUERY_STOPWORDS = {"the", "and", "you", "your", "with", "what", "where", "who", "did", "does", "for", "that", "this", "are", "was"}


def cosine_similarity(left: Any, right: Any) -> float:
    if left is None or right is None:
        return 0.0
    left, right = list(left), list(right)
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_l = math.sqrt(sum(a * a for a in left))
    norm_r = math.sqrt(sum(b * b for b in right))
    return dot / (norm_l * norm_r) if norm_l and norm_r else 0.0


def _field(memory: Any, name: str, default: Any = None) -> Any:
    return memory.get(name, default) if isinstance(memory, dict) else getattr(memory, name, default)


def score_memory(memory: Any, query: str, entity_names: set[str] | None = None,
                 query_embedding: list[float] | None = None, *, entity_ids: set[str] | None = None,
                 current_turn_index: int | None = None) -> float:
    """Hybrid score. Structured signals (entities, type, importance) keep weak embeddings honest."""
    content = str(_field(memory, "content", ""))
    importance = float(_field(memory, "importance", 0.4) or 0.0)
    confidence = float(_field(memory, "confidence", 0.8) or 0.0)
    raw_keywords = _field(memory, "keywords", []) or []
    raw_entities = []
    for key in ("characters", "locations", "factions", "items"):
        raw_entities.extend(_field(memory, key, []) or [])
    query_terms = {term.casefold() for term in TOKEN.findall(query) if len(term) > 2} - QUERY_STOPWORDS
    memory_terms = {term.casefold() for term in TOKEN.findall(content)}
    memory_terms.update(str(term).casefold() for term in raw_keywords)
    lexical = len(query_terms & memory_terms) / max(1, len(query_terms))
    names = {str(name).casefold() for name in (entity_names or set())}
    entities = {str(name).casefold() for name in raw_entities}
    ids = {str(value) for value in (_field(memory, "character_ids", []) or [])}
    entity_match = 1.0 if (names & entities) or (entity_ids and ids & set(entity_ids)) else 0.0
    if not entity_match and names and any(name in content.casefold() for name in names if len(name) > 3):
        entity_match = 0.6
    recency = 0.0
    turn_index = _field(memory, "turn_index")
    if isinstance(turn_index, int) and isinstance(current_turn_index, int):
        recency = math.exp(-max(0, current_turn_index - turn_index) / 40)
    else:
        created_at = _field(memory, "created_at")
        if isinstance(created_at, datetime):
            age_days = max(0.0, (datetime.now(UTC) - created_at.astimezone(UTC)).total_seconds() / 86400)
            recency = math.exp(-age_days / 45)
    semantic = max(0.0, cosine_similarity(query_embedding, _field(memory, "embedding")))
    score = (0.28 * semantic + 0.22 * lexical + 0.2 * entity_match + 0.17 * importance
             + 0.08 * recency + 0.05 * confidence)
    if str(_field(memory, "memory_type", "")) == "TURN":
        score *= 0.75
    return score


def rank_memories(memories: list[Any], query: str, entity_names: set[str] | None = None, limit: int = 8,
                  query_embedding: list[float] | None = None) -> list[Any]:
    return sorted(memories, key=lambda memory: score_memory(memory, query, entity_names, query_embedding), reverse=True)[:limit]
