"""Tolerant parsing of structured (JSON) model output."""

import json
import re
from typing import Any

from app.schemas import StateInterpretation, StateOperation


def close_truncated_json(text: str) -> str | None:
    """Recover the longest valid prefix of a JSON object cut off by a token limit."""
    start = text.find("{")
    if start < 0:
        return None
    stack: list[str] = []
    in_string = escape = False
    cut_points: list[tuple[int, list[str]]] = []
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack:
                break
            stack.pop()
            cut_points.append((index + 1, list(stack)))
            if not stack:
                return text[start:index + 1]
    for end, remaining in reversed(cut_points[-200:]):
        candidate = re.sub(r",\s*$", "", text[start:end]) + "".join(reversed(remaining))
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue
    return None


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0:
            raise
        if end <= start:
            recovered = close_truncated_json(cleaned)
            if recovered is None:
                raise
            end = start + len(recovered) - 1
            cleaned = cleaned[:start] + recovered
        candidate = cleaned[start:end + 1]
        try:
            result = json.loads(candidate)
        except json.JSONDecodeError:
            try:
                # Small models often leave trailing commas.
                result = json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
            except json.JSONDecodeError:
                recovered = close_truncated_json(cleaned)
                if recovered is None:
                    raise
                result = json.loads(recovered)
    if not isinstance(result, dict):
        raise ValueError("Structured model output must be a JSON object.")
    return result


def salvage_interpretation(payload: dict[str, Any]) -> tuple[StateInterpretation, int]:
    """Keep independently valid facts when a small model mixes up schema fields.

    Returns the interpretation and the number of operations that had to be dropped.
    """
    operations = []
    dropped = 0
    candidates = payload.get("state_changes", payload.get("operations", payload.get("changes", [])))
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            dropped += 1
            continue
        candidate = dict(candidate)
        kind = str(candidate.get("kind", candidate.get("type", ""))).upper()
        value = candidate.get("value")
        if not isinstance(value, dict):
            field = {"MOVE_CHARACTER": "location", "UPDATE_LOCATION": "location",
                     "CHANGE_CHARACTER_STATUS": "status", "CREATE_EVENT": "content",
                     "CREATE_MEMORY": "content", "UPDATE_OBJECTIVE": "status"}.get(kind)
            candidate["value"] = {field: value} if field and isinstance(value, (str, int, float)) else {}
        try:
            operations.append(StateOperation.model_validate(candidate))
        except ValueError:
            dropped += 1
    cleaned: dict[str, Any] = {"state_changes": operations}
    for field in ("events", "new_memories", "knowledge_changes", "relationship_changes"):
        rows = payload.get(field)
        cleaned[field] = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    cleaned["time_elapsed_seconds"] = payload.get("time_elapsed_seconds", 0)
    cleaned["time_of_day"] = str(payload.get("time_of_day") or "")[:40]
    cleaned["scene_mood"] = str(payload.get("scene_mood") or "")[:20]
    return StateInterpretation.model_validate(cleaned), dropped
