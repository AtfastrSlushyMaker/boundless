"""Small, conservative fallback index for facts visible in narration.

Local models sometimes return an empty state interpretation. This records only
people and family links whose names or roles are explicit in the shown story.
"""

import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Branch, Campaign, Character, CharacterRelationship, Turn

TITLED_PERSON = re.compile(
    r"\b(?P<title>High Priestess|High Priest|Priestess|Prince|Princess|King|Queen|Duke|Duchess|Lord|Lady|Captain)"
    r"\s+(?P<name>of\s+[A-Z][\w'-]+|[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2})\b"
)
PARENTAGE = re.compile(
    r"\b(?P<child>(?:Prince|Princess)\s+[A-Z][\w'-]+)[^.!?\n]{0,100}?"
    r"\b(?:son|daughter|child)\s+of\s+(?P<first>(?:King|Queen)\s+[A-Z][\w'-]+)"
    r"(?:\s+and\s+(?P<second>(?:King|Queen)\s+[A-Z][\w'-]+))?",
    re.IGNORECASE,
)
SELF_PARENTAGE = re.compile(
    r"\b(?:I['’]m|I am)\s+(?!(?:not|never|the|a|an)\b)(?P<parent>[A-Z][\w'-]+)\b"
    r"[\s\S]{0,160}?\b(?:you['’]re|you are)\s+(?!(?:not|never)\b)(?P<child>[A-Z][\w'-]+)\b"
    r"[\s\S]{0,80}?\bmy\s+(?P<kin>son|daughter|child)\b",
    re.IGNORECASE,
)
UNNAMED_ROLE = re.compile(
    r"\b(?:a|the|an)\s+(?P<descriptor>(?:(?:wounded|lead|masked|scarred|hooded|blind|young|old|city|market|plaza|first|second|third)\s+){0,2})"
    r"(?P<role>merchant|servant|innkeeper|bartender|courier|guard|priest|watchman|soldier|knight|healer|captain|attendant|scribe|blacksmith|baker|prisoner)\b",
    re.IGNORECASE,
)
INTERACTION_EVENT = re.compile(
    r"\b(?:met|meet|introduced|introduces|greeted|greets|spoke|speaks|talked|talks|asked|asks|"
    r"told|tells|helped|helps|rescued|rescues|protected|protects|intervened|intervenes|"
    r"sold|sells|bought|buys|paid|pays|gave|gives|handed|hands|traded|trades|promised|promises|"
    r"threatened|threatens|attacked|attacks|fought|fights|arrested|arrests|captured|captures|"
    r"betrayed|betrays|refused|refuses|invited|invites|revealed|reveals|recognized|recognizes|"
    r"thanked|thanks|warned|warns|offered|offers|accepted|accepts|denied|denies|"
    r"trusted|trusts|feared|fears|sold|sells)\b",
    re.IGNORECASE,
)


def _explicit_interaction_reason(text: str, person_name: str, protagonist_name: str) -> str:
    """Keep a short, directly stated interaction as the relationship's evidence."""
    person = re.compile(rf"(?<![\w'-]){re.escape(person_name)}(?![\w'-])", re.IGNORECASE)
    protagonist = re.compile(rf"(?<![\w'-]){re.escape(protagonist_name)}(?![\w'-])", re.IGNORECASE)
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if person.search(sentence) and protagonist.search(sentence) and INTERACTION_EVENT.search(sentence):
            cleaned = " ".join(sentence.split()).strip(' \t\r\n\"“”')
            if len(cleaned) > 320:
                cleaned = cleaned[:317].rsplit(" ", 1)[0] + "…"
            return cleaned
    return ""


async def _person(session: AsyncSession, campaign: Campaign, branch: Branch, name: str,
                  role: str, turn_id: UUID, location: str = "") -> Character:
    person = await session.scalar(select(Character).where(
        Character.branch_id == branch.id, Character.name.ilike(name)))
    if person is None:
        attributes = {"first_seen_turn": str(turn_id), "last_seen_turn": str(turn_id),
                      "seen_count": 1, "encountered": True}
        if location:
            attributes["first_meeting_place"] = location[:160]
        person = Character(campaign_id=campaign.id, branch_id=branch.id, name=name,
                           role=role, visibility="PLAYER_KNOWN", attributes=attributes)
        session.add(person)
        await session.flush()
    else:
        if not person.role and role:
            person.role = role
        person.visibility = "PLAYER_KNOWN"
        attributes = {**(person.attributes or {}), "encountered": True,
                      "first_seen_turn": (person.attributes or {}).get("first_seen_turn", str(turn_id))}
        if attributes.get("last_seen_turn") != str(turn_id):
            attributes["last_seen_turn"] = str(turn_id)
            attributes["seen_count"] = int(attributes.get("seen_count") or 1) + 1
        if location and not attributes.get("first_meeting_place"):
            attributes["first_meeting_place"] = location[:160]
        person.attributes = attributes
    return person


async def _known_to_player(session: AsyncSession, campaign: Campaign, branch: Branch,
                           person: Character, turn: Turn, location: str = "",
                           reason: str = "") -> None:
    player = await session.scalar(select(Character).where(
        Character.branch_id == branch.id, Character.name.ilike(campaign.protagonist_name)))
    if player is None or player.id == person.id:
        return
    pair_query = select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch.id,
        or_(
            (CharacterRelationship.from_character_id == player.id)
            & (CharacterRelationship.to_character_id == person.id),
            (CharacterRelationship.from_character_id == person.id)
            & (CharacterRelationship.to_character_id == player.id),
        ),
    )
    pair_rows = list((await session.scalars(pair_query)).all())
    relation = next((row for row in pair_rows if row.visibility == "PLAYER_KNOWN"), None)
    if relation is None:
        directions = {(row.from_character_id, row.to_character_id) for row in pair_rows}
        if (person.id, player.id) not in directions:
            source_id, target_id = person.id, player.id
        elif (player.id, person.id) not in directions:
            source_id, target_id = player.id, person.id
        else:
            return
        relation = CharacterRelationship(campaign_id=campaign.id, branch_id=branch.id,
            from_character_id=source_id, to_character_id=target_id,
            dimensions={"awareness": "known"}, summary=f"Known to {campaign.protagonist_name}",
            visibility="PLAYER_KNOWN")
        session.add(relation)
    dimensions = dict(relation.dimensions or {})
    history = dimensions.get("history", [])
    if not isinstance(history, list):
        history = []
    turn_entry = next((entry for entry in history if isinstance(entry, dict)
                       and entry.get("turn_id") == str(turn.id)), None)
    if turn_entry is not None:
        if reason and turn_entry.get("reason") in {"First appeared in the story", "Appeared in the story"}:
            turn_entry["reason"] = reason
    else:
        note = reason or ("First appeared in the story" if not history else "Appeared in the story")
        entry = {"reason": note, "turn_index": turn.turn_index, "turn_id": str(turn.id)}
        if location:
            entry["location"] = location[:160]
        history.append(entry)
        dimensions["history"] = history[-100:]
    last = dimensions.get("last_interaction")
    try:
        last_turn_index = int(last.get("turn_index", -1)) if isinstance(last, dict) else -1
    except (TypeError, ValueError):
        last_turn_index = -1
    if last_turn_index <= turn.turn_index:
        last = {"turn_index": turn.turn_index, "turn_id": str(turn.id)}
        if location:
            last["location"] = location[:160]
        dimensions["last_interaction"] = last
    relation.dimensions = dimensions


async def _link_parent_child(session: AsyncSession, campaign: Campaign, branch: Branch,
                             parent: Character, child: Character) -> None:
    pair_query = select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch.id,
        or_(
            (CharacterRelationship.from_character_id == parent.id)
            & (CharacterRelationship.to_character_id == child.id),
            (CharacterRelationship.from_character_id == child.id)
            & (CharacterRelationship.to_character_id == parent.id),
        ),
    )
    pair_rows = list((await session.scalars(pair_query)).all())
    visible_relation = next((row for row in pair_rows if row.visibility == "PLAYER_KNOWN"), None)
    if visible_relation:
        dimensions = dict(visible_relation.dimensions or {})
        if not dimensions.get("kinship"):
            dimensions["kinship"] = "parent" if visible_relation.from_character_id == parent.id else "child"
            visible_relation.dimensions = dimensions
            if not visible_relation.summary:
                visible_relation.summary = f"Family link between {parent.name} and {child.name}"
        return
    directions = {(row.from_character_id, row.to_character_id) for row in pair_rows}
    if (parent.id, child.id) not in directions:
        source_id, target_id, kinship = parent.id, child.id, "parent"
    elif (child.id, parent.id) not in directions:
        source_id, target_id, kinship = child.id, parent.id, "child"
    else:
        return
    session.add(CharacterRelationship(campaign_id=campaign.id, branch_id=branch.id,
        from_character_id=source_id, to_character_id=target_id,
        dimensions={"kinship": kinship}, summary=f"Family link between {parent.name} and {child.name}",
        visibility="PLAYER_KNOWN"))


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


async def index_people_from_narration(session: AsyncSession, campaign: Campaign,
                                      branch: Branch, turn: Turn,
                                      current_location: str = "") -> int:
    """Upsert explicitly named or individually encountered people, idempotently."""
    text = turn.gm_response or ""
    seen: set[str] = set()
    def interaction_reason(name: str) -> str:
        return _explicit_interaction_reason(text, name, campaign.protagonist_name)

    for match in TITLED_PERSON.finditer(text):
        name = f"{match.group('title')} {match.group('name')}".strip()
        if name.casefold() in seen or name.casefold() == campaign.protagonist_name.casefold():
            continue
        seen.add(name.casefold())
        person = await _person(session, campaign, branch, name[:120], match.group("title"), turn.id, current_location)
        await _known_to_player(session, campaign, branch, person, turn, current_location,
                               interaction_reason(person.name))
    for match in UNNAMED_ROLE.finditer(text):
        role = match.group("role").capitalize()
        descriptor = " ".join(match.group("descriptor").split()).strip().title()
        if descriptor:
            name = f"{descriptor} {role}"
        else:
            existing = await session.scalar(select(Character).where(
                Character.branch_id == branch.id,
                Character.name.ilike(role) | Character.name.ilike(f"Unnamed {role}")))
            name = existing.name if existing else f"Unnamed {role}"
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        person = await _person(session, campaign, branch, name, role, turn.id, current_location)
        await _known_to_player(session, campaign, branch, person, turn, current_location,
                               interaction_reason(person.name))
    known_people = (await session.scalars(select(Character).where(
        Character.branch_id == branch.id,
        Character.visibility == "PLAYER_KNOWN",
    ))).all()
    for person in known_people:
        if person.name.casefold() == campaign.protagonist_name.casefold() or person.name.casefold() in seen:
            continue
        if re.search(rf"(?<![\w'-]){re.escape(person.name)}(?![\w'-])", text, re.IGNORECASE):
            seen.add(person.name.casefold())
            indexed = await _person(session, campaign, branch, person.name, person.role, turn.id, current_location)
            await _known_to_player(session, campaign, branch, indexed, turn, current_location,
                                   interaction_reason(indexed.name))
    for source in (text, campaign.original_prompt):
        for match in PARENTAGE.finditer(source):
            child_name = match.group("child")
            if source != text and child_name.casefold() not in seen:
                continue
            child = await _person(session, campaign, branch, child_name,
                                  child_name.split()[0], turn.id, current_location)
            for group in ("first", "second"):
                name = match.group(group)
                if not name:
                    continue
                parent = await _person(session, campaign, branch, name, name.split()[0], turn.id, current_location)
                await _known_to_player(session, campaign, branch, parent, turn, current_location,
                                       interaction_reason(parent.name))
                await _link_parent_child(session, campaign, branch, parent, child)
        if source == text:
            for match in SELF_PARENTAGE.finditer(source):
                parent_name = match.group("parent")
                child_name = match.group("child")
                if parent_name.casefold() == child_name.casefold():
                    continue
                if parent_name.casefold() not in seen or child_name.casefold() not in seen:
                    continue
                parent = await _person(session, campaign, branch, parent_name,
                                       "", turn.id, current_location)
                child = await _person(session, campaign, branch, child_name,
                                      "", turn.id, current_location)
                await _known_to_player(session, campaign, branch, parent, turn, current_location,
                                       interaction_reason(parent.name))
                await _known_to_player(session, campaign, branch, child, turn, current_location,
                                       interaction_reason(child.name))
                await _link_parent_child(session, campaign, branch, parent, child)
    return len(seen)
