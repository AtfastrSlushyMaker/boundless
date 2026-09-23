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
    if rule and player_death_stated(text, str(constitution.get("player_identity", "You"))):
        return "Narration states that the player dies despite a hard immortality rule."
    return None


def player_death_stated(text: str, player_name: str) -> bool:
    if any(re.search(pattern, text, re.IGNORECASE) for pattern in DEATH_PATTERNS):
        return True
    name = re.escape(player_name)
    if re.search(rf"\b{name}\s+dies\b|\b{name}'s death\b", text, re.IGNORECASE):
        return True
    for match in re.finditer(
        rf"\b{name}(?:'s)?\s+(?:is|was|has been|lies|falls)[^.!?]{{0,35}}\b(?:dead|killed|lifeless)\b",
        text, re.IGNORECASE):
        if not re.search(r"\b(?:not|never)\b", match.group(), re.IGNORECASE):
            return True
    return False


def check_player_agency(text: str, player_name: str, action: str,
                        previous_narration: str = "") -> str | None:
    """Reject clear player takeovers and sudden death in generated narration."""
    name = re.escape(player_name)
    opening = action.strip() == "Open on the campaign's stated starting moment."
    risky_action = bool(re.search(
        r"\b(?:attack|fight|charge|stab|strike|shoot|kill|battle|jump|leap|swing|duel|punch|"
        r"lunge|rush|cast|fire|drink poison|risk my life)\b",
        action, re.IGNORECASE))
    if opening or not risky_action:
        takeover = re.search(
            rf"\b{name}\s+(?:steps forward|charges|attacks|draws|stabs|slashes|kills|"
            r"steals|grabs|tackles|strangles|walks past|pulls free|rips|rolls under|"
            r"parries|sidesteps|catches the)\b",
            text, re.IGNORECASE)
        if takeover:
            return "The narration invents a major physical action for the player. Stop before that action and let the player choose."
    if opening and re.search(rf"\b{name}\s+(?:says|asks|shouts|yells|replies)\b", text, re.IGNORECASE):
        return "The opening scene invents dialogue for the player. Let the player speak first."
    if not risky_action:
        quoted_speech = re.findall(
            rf"[\"“]([^\"”]{{3,}})[\"”]\s*,?\s*{name}\s+(?:says|asks|shouts|yells|replies)\b|"
            rf"\b{name}\s+(?:says|asks|shouts|yells|replies)\s*[\"“]([^\"”]{{3,}})[\"”]",
            text, re.IGNORECASE)
        action_words = set(re.findall(r"[a-z]{3,}", action.casefold()))
        for before, after in quoted_speech:
            speech_words = set(re.findall(r"[a-z]{3,}", (before or after).casefold()))
            if len(speech_words & action_words) < min(2, len(speech_words)):
                return "The narration invents dialogue for the player. Keep only what the player chose to say."

    if player_death_stated(text, player_name):
        explicit_death = bool(re.search(r"\b(?:I die|kill me|let me die|I accept death)\b", action, re.IGNORECASE))
        warned = bool(re.search(r"\b(?:about to die|fatal|lethal|kill (?:you|him|her|them)|blade at|at your throat|"
                                r"at his throat|at her throat|collapsing|dying)\b", previous_narration, re.IGNORECASE))
        if not explicit_death and not (risky_action and warned):
            return "The player dies without choosing a lethal risk after a visible warning. Preserve danger, but stop before death so the player can respond."
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
