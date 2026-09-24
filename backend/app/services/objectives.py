"""Objective identity and deterministic completion against actual world state."""

import re
from typing import Any

from app.db.models import Character, Item, Objective
from app.services.entity_resolver import EntityResolver, resolve_named
from app.services.identity import (
    content_tokens,
    is_unknown,
    jaccard,
    looks_like_proper_name,
    normalize_reference,
)

STATUSES = {"active", "completed", "failed", "abandoned", "superseded"}
STATUS_ALIASES = {"done": "completed", "complete": "completed", "success": "completed", "succeeded": "completed",
                  "achieved": "completed", "fulfilled": "completed", "resolved": "completed", "failure": "failed",
                  "lost": "failed", "dropped": "abandoned", "cancelled": "abandoned", "canceled": "abandoned",
                  "replaced": "superseded", "open": "active", "in_progress": "active", "ongoing": "active",
                  "pending": "active"}
ACQUIRE = r"(?:acquire|obtain|get|steal|take|retrieve|recover|secure|claim|buy|collect|grab|seize|win)"
MEET = r"(?:find|meet|locate|speak (?:to|with)|talk (?:to|with)|reach|visit|contact|track down|seek out)"
VERBS = {"acquire", "obtain", "get", "steal", "take", "retrieve", "recover", "secure", "claim", "buy", "collect",
         "grab", "seize", "win", "find", "meet", "locate", "speak", "talk", "reach", "visit", "contact", "track",
         "down", "seek", "out", "learn", "about", "tell", "ask"}


def normalize_status(value: Any) -> str | None:
    key = str(value or "").strip().casefold().replace(" ", "_")
    key = STATUS_ALIASES.get(key, key)
    return key if key in STATUSES else None


def verb_class(title: str) -> str:
    lowered = title.casefold().strip()
    if re.match(rf"^(?:{ACQUIRE})\b", lowered):
        return "acquire"
    if re.match(rf"^(?:{MEET})\b", lowered):
        return "meet"
    return lowered.split(" ", 1)[0] if lowered else ""


def key_tokens(title: str) -> set[str]:
    return content_tokens(title) - VERBS


def target_phrase(title: str) -> str:
    match = re.match(rf"^(?:{ACQUIRE}|{MEET})\s+(?P<target>.+?)(?:\s+(?:in|at|from|before|by|and|to|so|while)\b.*)?$",
                     title.strip(), re.IGNORECASE)
    return match.group("target").strip(" .") if match else ""


def infer_criteria(title: str, resolver: EntityResolver) -> dict[str, Any]:
    kind = verb_class(title)
    target = target_phrase(title)
    if kind not in {"meet", "acquire"} or not target:
        return {}
    person = resolver.resolve(target)
    if person.status == "RESOLVED" and person.confidence >= 0.85:
        return {"type": "meet_character", "character_id": person.character_id, "name": target}
    if kind == "meet" and looks_like_proper_name(target):
        return {"type": "meet_character", "name": target}
    return {"type": "acquire_item", "item": normalize_reference(target), "name": target}


def same_objective(left_title: str, left_aliases: list[str], right_title: str) -> bool:
    if verb_class(left_title) != verb_class(right_title):
        return False
    right = key_tokens(right_title)
    for title in [left_title, *left_aliases]:
        if normalize_reference(title) == normalize_reference(right_title):
            return True
        left = key_tokens(title)
        if left and right and (left <= right or right <= left or jaccard(left, right) >= 0.5):
            return True
    return False


def find_matching(objectives: list[Objective], title: str, criteria: dict[str, Any]) -> Objective | None:
    for objective in objectives:
        if objective.status == "superseded":
            continue
        other = objective.criteria or {}
        if criteria and other and criteria.get("type") == other.get("type"):
            if criteria.get("character_id") and criteria.get("character_id") == other.get("character_id"):
                return objective
        if same_objective(objective.title, list(objective.aliases or []), title):
            return objective
    return None


def check_completion(objective: Objective, characters: dict[str, Character], player_items: list[Item],
                     resolver: EntityResolver | None = None) -> str | None:
    """Return a resolution note when world state already satisfies the objective."""
    criteria = objective.criteria or {}
    if criteria.get("type") == "meet_character":
        character_id = str(criteria.get("character_id") or "")
        if not character_id and resolver and criteria.get("name"):
            found = resolver.resolve(str(criteria["name"]))
            if found.status == "RESOLVED" and found.confidence >= 0.85:
                character_id = str(found.character_id)
                objective.criteria = {**criteria, "character_id": character_id}
        person = characters.get(character_id)
        if person and (person.attributes or {}).get("encountered"):
            first = person.first_seen_turn_index
            return f"Met {person.name}" + (f" (turn {first})" if first is not None else "") + "."
    if criteria.get("type") == "acquire_item":
        wanted = str(criteria.get("item") or criteria.get("name") or "")
        if wanted:
            rows = [(str(item.id), item.name, list(item.aliases or [])) for item in player_items if item.quantity > 0]
            match = resolve_named(wanted, rows)
            if match.status == "RESOLVED":
                item = next(item for item in player_items if str(item.id) == match.character_id)
                return f"{item.name} is in the inventory."
    return None


def clean_title(value: Any) -> str:
    text = " ".join(str(value or "").split()).strip(" .")
    return "" if is_unknown(text) else text[:180]
