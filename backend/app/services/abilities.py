"""Structured ability catalogue: what the protagonist (or anyone) can actually do."""

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Ability, Character
from app.services.character_store import StoreLog
from app.services.entity_resolver import resolve_named
from app.services.identity import content_tokens, is_unknown, normalize_reference

# "use my wind skill", "cast a fireball", "use the white flame", "call up the white flame"
ABILITY_USE = re.compile(
    r"\b(?:use|using|used|cast|casting|call(?:ing)? (?:up|on)|summon|channel|unleash|activate|fire|hurl|throw)\s+"
    r"(?:my|the|a|an|her|his|this|that)?\s*(?P<ability>(?:[a-z'-]+\s+){0,3}?(?:skill|spell|power|ability|magic|flame|fire|"
    r"fireball|wind|gust|teleport(?:ation)?|healing|illusion|lightning|ice|shadow|blast))\b",
    re.IGNORECASE,
)
GENERIC_ABILITY_WORDS = {"skill", "spell", "power", "ability", "magic", "my", "the", "a", "an", "new", "stolen", "own"}


async def catalogue(session: AsyncSession, character_id: UUID) -> list[Ability]:
    return list((await session.scalars(select(Ability).where(
        Ability.character_id == character_id).order_by(Ability.created_at))).all())


async def gain_ability(session: AsyncSession, character: Character, value: dict[str, Any], *, provenance: str,
                       turn_index: int | None, log: StoreLog | None = None, source_default: str = "") -> Ability | None:
    name = " ".join(str(value.get("name") or value.get("ability") or "").split())[:160]
    if not name or is_unknown(name):
        return None
    abilities = await catalogue(session, character.id)
    match = resolve_named(name, [(str(row.id), row.name, list(row.aliases or [])) for row in abilities])
    limitations = value.get("limitations") if isinstance(value.get("limitations"), list) else \
        [value["limitations"]] if isinstance(value.get("limitations"), str) and value["limitations"].strip() else []
    if match.status == "RESOLVED":
        ability = next(row for row in abilities if str(row.id) == match.character_id)
        if normalize_reference(name) != ability.normalized and name not in (ability.aliases or []):
            ability.aliases = [*(ability.aliases or []), name][:12]
        if ability.status != "ACTIVE" and str(value.get("status", "ACTIVE")).upper() == "ACTIVE":
            ability.status = "ACTIVE"
        description = str(value.get("description") or "")
        if description and len(description) > len(ability.description or ""):
            ability.description = description[:2000]
        ability.limitations = list(dict.fromkeys([*(ability.limitations or []), *map(str, limitations)]))[:12]
        return ability
    ability = Ability(campaign_id=character.campaign_id, branch_id=character.branch_id, character_id=character.id,
                      name=name, normalized=normalize_reference(name),
                      aliases=[str(alias)[:160] for alias in value.get("aliases", []) if isinstance(alias, str)][:12]
                      if isinstance(value.get("aliases"), list) else [],
                      description=str(value.get("description") or "")[:2000],
                      source=str(value.get("source") or source_default)[:240],
                      acquired_turn_index=turn_index, status="ACTIVE",
                      limitations=[str(item)[:300] for item in limitations][:12], provenance=provenance,
                      visibility=str(value.get("visibility") or "PLAYER_KNOWN")[:24])
    session.add(ability)
    if log:
        log.metrics["abilities_gained"] += 1
        log.note("ability_gained", character=character.name, ability=name, source=ability.source)
    return ability


async def lose_ability(session: AsyncSession, character: Character, name: str, log: StoreLog | None = None) -> bool:
    abilities = await catalogue(session, character.id)
    match = resolve_named(name, [(str(row.id), row.name, list(row.aliases or [])) for row in abilities])
    if match.status != "RESOLVED":
        return False
    ability = next(row for row in abilities if str(row.id) == match.character_id)
    ability.status = "LOST"
    if log:
        log.note("ability_lost", character=character.name, ability=ability.name)
    return True


def referenced_abilities(action: str) -> list[str]:
    found = []
    for match in ABILITY_USE.finditer(action):
        phrase = " ".join(match.group("ability").split())
        if content_tokens(phrase) - GENERIC_ABILITY_WORDS:
            found.append(phrase)
    return found


def ability_known(phrase: str, abilities: list[Ability]) -> bool:
    words = content_tokens(phrase) - GENERIC_ABILITY_WORDS
    if not words:
        return True
    for ability in abilities:
        if ability.status != "ACTIVE":
            continue
        pool = content_tokens(" ".join([ability.name, ability.description or "", *(ability.aliases or [])]))
        if words & pool:
            return True
    return False
