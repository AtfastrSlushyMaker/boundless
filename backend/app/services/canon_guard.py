import re
from typing import Any

DEATH_PATTERNS = (
    r"\byou (?:are|were) dead\b", r"\byou die\b", r"\byour death\b",
    r"\byour life (?:ends|is over)\b", r"\bpermanently kills you\b",
    r"\byou (?:have been|are) killed\b", r"\byou draw your last breath\b",
)


class CanonViolation(ValueError):
    """A proposed outcome contradicts established hard canon."""


def hard_immortality(constitution: dict[str, Any], rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    for rule in reversed(rules):
        if rule.get("rule_type") == "PLAYER_MORTALITY" and str(rule.get("strength", "")).upper() == "HARD":
            return None
        if rule.get("rule_type") == "PLAYER_CANNOT_DIE" and str(rule.get("strength", "")).upper() == "HARD":
            return {"exceptions": rule.get("exceptions", []), "statement": rule.get("statement", "")}
    for invariant in constitution.get("hard_invariants", []):
        if invariant.get("type") == "PLAYER_CANNOT_DIE" and str(invariant.get("strength", "")).upper() == "HARD":
            return invariant
    return None


def check_narrative(text: str, constitution: dict[str, Any], rules: list[dict[str, Any]]) -> str | None:
    rule = hard_immortality(constitution, rules)
    if rule and any(re.search(pattern, text, re.IGNORECASE) for pattern in DEATH_PATTERNS):
        return "Narration states that the player dies despite a hard immortality rule."
    return None


def validate_state_operation(operation: dict[str, Any], constitution: dict[str, Any], rules: list[dict[str, Any]], state: dict[str, Any]) -> None:
    if operation.get("kind") != "CHANGE_CHARACTER_STATUS":
        return
    player = str(constitution.get("player_identity", "You")).casefold()
    subject = str(operation.get("subject", "")).casefold()
    status = str(operation.get("value", {}).get("status", "")).casefold()
    if status not in {"dead", "deceased"} or subject != player:
        return
    hard_rule = hard_immortality(constitution, rules)
    if not hard_rule:
        return
    allowed = [str(value).casefold().strip() for value in hard_rule.get("exceptions", [])]
    used = str(operation.get("value", {}).get("exception_used", "")).casefold().strip()
    confirmed = {str(value).casefold().strip() for value in state.get("confirmed_canon_exceptions", [])}
    if not used or used not in allowed or used not in confirmed:
        raise CanonViolation("Rejected player death: hard immortality has no confirmed matching exception.")
