"""Small, conservative fallback index for facts stated outright in narration.

Local models sometimes return an empty state interpretation. This records only titled,
named people ("Captain Mara") and explicit family links, always through the entity
resolver so it never creates a second record for someone already known. It does not
create "Unnamed Guard" placeholders or "appeared in the story" relationship noise.
"""

import re

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Branch, Campaign, Character, Turn
from app.services.character_store import StoreLog, add_alias, build_resolver
from app.services.entity_resolver import CharacterCandidate
from app.services.identity import normalize_reference
from app.services.relationships import apply_relationship_change

TITLED_PERSON = re.compile(
    r"\b(?P<title>High Priestess|High Priest|Prince|Princess|King|Queen|Duke|Duchess|Lord|Lady|Captain)"
    r"\s+(?P<name>of\s+[A-Z][\w'-]+|[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2})\b"
)
PARENTAGE = re.compile(
    r"\b(?P<child>(?:Prince|Princess)\s+[A-Z][\w'-]+)[^.!?\n]{0,100}?"
    r"\b(?:son|daughter|child)\s+of\s+(?P<first>(?:King|Queen)\s+[A-Z][\w'-]+)"
    r"(?:\s+and\s+(?P<second>(?:King|Queen)\s+[A-Z][\w'-]+))?",
    re.IGNORECASE,
)
NOT_A_NAME = {"The", "A", "An", "Of", "And", "But", "Who", "You", "Your", "His", "Her", "Their", "This", "That"}
# Dialogue tags and introductions are strong evidence of a named individual.
SPEAKER_AFTER = re.compile(r"[\"”]\s*,?\s*(?P<name>[A-Z][a-z'-]{2,}(?:\s+[A-Z][a-z'-]{2,})?)\s+"
                           r"(?:says|said|asks|asked|replies|replied|whispers|whispered|shouts|mutters|snaps|adds|calls)\b")
SPEAKER_BEFORE = re.compile(r"(?:^|[.!?]\s+)(?P<name>[A-Z][a-z'-]{2,})\s+(?:says|asks|replies|whispers|shouts|mutters|snaps|adds)[,:]?\s*[\"“]")
NAMED = re.compile(r"\b(?:named|called)\s+(?P<name>[A-Z][a-z'-]{2,}(?:\s+[A-Z][a-z'-]{2,})?)")
APPOSITIVE = re.compile(r"\b(?P<name>[A-Z][a-z'-]{2,}),\s+(?:the|a|an)\s+(?P<role>(?:[a-z-]+\s+){0,2}(?:guard|priest|priestess|merchant|"
                        r"thief|captain|soldier|servant|smuggler|mage|healer|clerk|courier|scribe|knight|innkeeper|sailor|informant))\b")
COMMON_WORDS = {"The", "She", "He", "You", "It", "They", "Then", "His", "Her", "Behind", "Below", "Above", "Someone", "Nobody",
                "Everyone", "Good", "Yes", "No", "Now", "What", "Why", "How", "When", "Where", "Priestess", "Captain", "Lord",
                "Lady", "King", "Queen", "Somewhere", "Another", "One", "Two", "Three", "Fine", "Well", "Stop", "Wait", "Here",
                "There", "Nothing", "Something", "Tonight", "Today", "God", "Gods", "Guard", "Guards", "Priest", "Merchant"}


def player_location_for_turn(campaign: Campaign, turn: Turn, previous_location: str = "") -> str:
    """Carry the player's explicit location forward along this branch's timeline."""
    aliases = {campaign.protagonist_name.casefold(), "player", "protagonist", "you"}
    location = previous_location
    operations = (turn.state_delta or {}).get("operations", [])
    if not isinstance(operations, list):
        return location
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        kind = str(operation.get("kind", "")).upper()
        subject = str(operation.get("subject") or operation.get("name") or "").casefold()
        value = operation.get("value") if isinstance(operation.get("value"), dict) else {}
        if kind in {"UPDATE_CHARACTER", "MOVE_CHARACTER", "CREATE_CHARACTER", "UPDATE_LOCATION", "CREATE_LOCATION"}:
            if subject in aliases and isinstance(value.get("location"), str) and value["location"].strip():
                location = value["location"].strip()[:160]
    return location


async def index_people_from_narration(session: AsyncSession, campaign: Campaign, branch: Branch, turn: Turn,
                                      current_location: str = "", log: StoreLog | None = None) -> int:
    """Upsert explicitly titled people and family links, idempotently."""
    log = log or StoreLog()
    text = turn.gm_response or ""
    resolver, characters = await build_resolver(session, campaign, branch.id, branch.current_state or {})
    touched: dict[str, Character] = {}

    async def person(name: str, role: str) -> Character | None:
        result = resolver.resolve(name)
        if result.status == "PLAYER":
            return None
        if result.status == "RESOLVED" and result.confidence >= 0.85:
            character = characters[str(result.character_id)]
        elif result.status == "NEW":
            character = Character(campaign_id=campaign.id, branch_id=branch.id, name=name[:120], role=role,
                                  attributes={"first_meeting_place": current_location[:160]} if current_location else {},
                                  visibility="PLAYER_KNOWN", first_seen_turn_index=turn.turn_index, importance="MINOR")
            session.add(character)
            await session.flush()
            await add_alias(session, character, character.name, "CANONICAL_NAME", turn_index=turn.turn_index,
                            source="narration_index", confidence=1.0)
            characters[str(character.id)] = character
            resolver.add(CharacterCandidate(id=str(character.id), name=character.name,
                                            aliases=[(character.name, "CANONICAL_NAME")], role=role))
            log.metrics["characters_created_by_index"] += 1
        else:
            return None
        if not character.role and role:
            character.role = role
        touched[str(character.id)] = character
        return character

    for match in TITLED_PERSON.finditer(text):
        if match.group("name").split()[0] in NOT_A_NAME:
            continue
        name = f"{match.group('title')} {match.group('name')}".strip()
        if normalize_reference(name) == normalize_reference(campaign.protagonist_name):
            continue
        await person(name, match.group("title"))
    for pattern in (SPEAKER_AFTER, SPEAKER_BEFORE, NAMED, APPOSITIVE):
        for match in pattern.finditer(text):
            name = match.group("name").strip()
            if name.split()[0] in COMMON_WORDS or name in COMMON_WORDS:
                continue
            role = match.group("role") if "role" in pattern.groupindex else ""
            before = len(touched)
            person_row = await person(name, role)
            if person_row is not None and len(touched) > before:
                log.metrics["named_people_indexed"] += 1
            if person_row is not None and pattern is not NAMED:
                # Speaking or described on screen: the player has met them here.
                attributes = dict(person_row.attributes or {})
                if not attributes.get("encountered"):
                    attributes["encountered"] = True
                    attributes["seen_count"] = max(1, int(attributes.get("seen_count") or 0))
                    person_row.attributes = attributes
                    person_row.last_seen_turn_index = turn.turn_index
    for source in (text, campaign.original_prompt):
        for match in PARENTAGE.finditer(source):
            child = await person(match.group("child"), match.group("child").split()[0])
            for group in ("first", "second"):
                if not match.group(group) or child is None:
                    continue
                parent = await person(match.group(group), match.group(group).split()[0])
                if parent is None:
                    continue
                await apply_relationship_change(
                    session, campaign_id=campaign.id, branch_id=branch.id, source=parent, target=child,
                    change={"dimensions": {"kinship": "parent"}, "reason": f"{parent.name} is a parent of {child.name}"},
                    turn_id=turn.id, turn_index=turn.turn_index, location=current_location, narration=text, log=log)
    return len(touched)
