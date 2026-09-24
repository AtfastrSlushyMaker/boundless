import re
from typing import Any

from app.services.identity import content_tokens

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


# --- General canon beyond mortality -------------------------------------------------------

DEAD_STATUSES = {"dead", "deceased", "killed", "slain", "executed", "died"}
NOT_ALIVE_EXCEPTIONS = re.compile(r"\b(?:ghost|spirit|corpse|body|remember|remembered|memory|dream|vision|"
                                  r"echo|once said|used to|flashback|grave|funeral)\b", re.IGNORECASE)
ACTING_VERBS = r"(?:says|said|asks|asked|smiles|walks|nods|laughs|turns|steps|reaches|whispers|shouts|replies|grins|stands|draws|attacks)"
RESURRECTION = re.compile(r"\b(?:resurrect|revive|raise the dead|necroman|come back from the dead|undead|reincarnat)", re.IGNORECASE)
ITEM_USE = re.compile(
    r"\b(?:use|draw|drink|read|open|unlock|show|hold out|hand over|give (?:him|her|them)|throw|wield|swing|light)\s+"
    r"(?:my|the)\s+(?P<item>[a-z'-]+(?:\s+[a-z'-]+){0,2})", re.IGNORECASE)
ABSTRACT_OBJECTS = {"hand", "hands", "arm", "arms", "eyes", "voice", "mind", "head", "fist", "fists", "feet", "legs",
                    "leg", "body", "breath", "power", "powers", "magic", "skill", "skills", "ability", "abilities",
                    "spell", "spells", "door", "window", "way", "time", "chance", "moment", "wind", "flame", "fire",
                    "catalogue", "catalog", "gift", "wits", "name", "knowledge", "shoulder", "back", "face", "mouth"}


def is_dead(status: str | None) -> bool:
    return str(status or "").strip().casefold().split(",")[0].strip() in DEAD_STATUSES


def resurrection_allowed(constitution: dict[str, Any], rules: list[dict[str, Any]]) -> bool:
    texts = [str(value) for key in ("world_rules", "magic_rules", "powers", "abilities") for value in constitution.get(key, [])]
    texts += [str(rule.get("statement", "")) for rule in rules]
    return any(RESURRECTION.search(text) for text in texts)


def dead_character_acts(text: str, dead_names: list[str]) -> str | None:
    """Narration showing a confirmed-dead character acting in the present scene."""
    for name in dead_names:
        if not name or len(name) < 3:
            continue
        pattern = re.compile(rf"\b{re.escape(name)}\s+{ACTING_VERBS}\b", re.IGNORECASE)
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if pattern.search(sentence) and not NOT_ALIVE_EXCEPTIONS.search(sentence):
                return f"{name} is dead in established canon but acts in this scene. Keep them dead unless the story explicitly revives them."
    return None


def unowned_items(action: str, inventory_names: list[str], ability_names: list[str]) -> list[str]:
    """Items the player tries to use by possession ("my X", "the X") that are not in the inventory."""
    owned = set()
    for name in [*inventory_names, *ability_names]:
        owned |= content_tokens(name)
    missing = []
    for match in ITEM_USE.finditer(action):
        phrase = match.group("item").strip()
        tokens = content_tokens(phrase)
        if not tokens or tokens <= ABSTRACT_OBJECTS or tokens & owned:
            continue
        if "my" in match.group(0).casefold().split():
            missing.append(phrase)
    return missing


def action_canon_notes(action: str, *, inventory_names: list[str], abilities: list[Any],
                       innate_copying: bool) -> list[dict[str, str]]:
    """Soft checks on the player's attempt. They guide the GM; they do not block the turn."""
    from app.services.abilities import ability_known, referenced_abilities

    notes = []
    for phrase in referenced_abilities(action):
        if not ability_known(phrase, abilities):
            notes.append({"type": "UNKNOWN_ABILITY", "subject": phrase,
                          "note": f"The player references '{phrase}', which is not in their ability catalogue. "
                                  + ("Their established powers may let them acquire new abilities, but only through an on-screen, canon-consistent event; "
                                     if innate_copying else "")
                                  + "Treat it as an attempt; do not grant it silently."})
    ability_names = [ability.name for ability in abilities]
    for item in unowned_items(action, inventory_names, ability_names):
        notes.append({"type": "UNOWNED_ITEM", "subject": item,
                      "note": f"The player refers to their '{item}', but it is not in their inventory. Do not let them use an item they do not have."})
    return notes


def validate_general_operation(operation: dict[str, Any], *, current_status: str | None, resurrection: bool,
                               player_name: str) -> str | None:
    """Return a rejection reason for operations that would break confirmed canon."""
    kind = operation.get("kind")
    value = operation.get("value") or {}
    if kind in {"CHANGE_CHARACTER_STATUS", "UPDATE_CHARACTER"} and "status" in value and is_dead(current_status):
        new_status = str(value.get("status") or "")
        if new_status and not is_dead(new_status) and not (resurrection and value.get("resurrected")):
            return "confirmed_dead_character_cannot_return"
    return None
