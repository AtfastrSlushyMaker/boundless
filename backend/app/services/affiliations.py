"""Who belongs to what: factions, nations, cities, guilds, faiths, houses, armies, and crews.

Each character keeps ``attributes["affiliations"]``: a list of
``{"name", "kind", "role", "status", "source", "turn_index"}``. ``status`` is member, leader,
or former. ``source`` is player (never overridden), story (the narration or the state model
said so), or inferred (read from a role or fact). Groups are also saved as ``Faction`` rows
so they have a kind, description, and relations to each other.

Linking is deterministic first (known group names and "X of the Y" roles); a structured
model pass can add groups the text implies, such as a masked man's crew.
"""

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Branch,
    Campaign,
    Character,
    CharacterFact,
    Faction,
    FactionRelationship,
    Location,
)
from app.llm.base import LLMProvider
from app.llm.structured import parse_json_response
from app.services.identity import is_unknown, mention_pattern, normalize_reference

logger = logging.getLogger(__name__)

KINDS = ("faction", "nation", "city", "guild", "religion", "house", "military", "government", "crew")
STATUSES = ("member", "leader", "former")
KIND_WORDS = (
    ("religion", r"\b(?:church|temple|faith|cult|order of|priesthood|flame|creed|sect|clergy|monastery|shrine|god|goddess)\b"),
    ("nation", r"\b(?:kingdom|empire|republic|realm|nation|country|dominion|federation|principality|duchy|tribe|khanate)\b"),
    ("military", r"\b(?:army|legion|guard|guards|watch|militia|navy|knights|regiment|company of|garrison|patrol)\b"),
    ("guild", r"\b(?:guild|syndicate|cartel|gang|company|consortium|brotherhood|sisterhood|thieves|merchants|league)\b"),
    ("house", r"\b(?:house|clan|family|dynasty|bloodline)\b"),
    ("government", r"\b(?:council|court|senate|parliament|ministry|crown|throne|magistrate|office)\b"),
    ("city", r"\b(?:city|town|village|district|quarter|port|harbou?r)\b"),
    ("crew", r"\b(?:crew|band|gang|pack|circle|cell|pursuers|followers|retinue|companions)\b"),
)
ROLE_KIND_WORDS = (
    ("religion", r"\b(?:priest|priestess|acolyte|cleric|monk|nun|templar|temple|high priestess|oracle|zealot)\b"),
    ("military", r"\b(?:guard|soldier|captain|sergeant|knight|watchman|legionary|general|commander)\b"),
    ("government", r"\b(?:minister|chancellor|magistrate|councillor|councilor|senator|official)\b"),
)
MEMBERSHIP = re.compile(r"\b(?:member of|serves|serving|servant of|agent of|loyal to|sworn to|soldier of|knight of|"
                        r"priest(?:ess)? of|high priest(?:ess)? of|leader of|head of|captain of|citizen of|works for|"
                        r"belongs to|part of)\s+(?:the\s+)?([A-Z][\w'’-]+(?:\s+(?:of\s+the\s+|of\s+)?[A-Z][\w'’-]+){0,4})")
TITLE_OF = re.compile(r"\bof\s+(?:the\s+)?([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){0,3})")
JOINS = (r"member of|members of|serves|served|serving|servant of|loyal to|belongs to|belonged to|part of|sworn to|works for|"
         r"worked for|agent of|agents of|leader of|leads|led|commands|joined|recruit of|citizen of|priest(?:ess)? of|knight of|soldier of")
LEADER_WORDS = re.compile(r"\b(?:leader|head|high priest(?:ess)?|captain|king|queen|lord|lady|master|mistress|commander|chief|boss)\b", re.I)


def kind_for(name: str, hint: str = "") -> str:
    text = f"{name} {hint}"
    for kind, pattern in KIND_WORDS:
        if re.search(pattern, text, re.IGNORECASE):
            return kind
    for kind, pattern in ROLE_KIND_WORDS:
        if re.search(pattern, hint, re.IGNORECASE):
            return kind
    return "faction"


def clean_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "")).strip(" .,;:'\"")
    name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.IGNORECASE)
    return "" if is_unknown(name) or len(name) < 3 else name[:160]


def normalize_affiliations(raw: Any, *, source: str = "story", turn_index: int | None = None) -> list[dict[str, Any]]:
    """Accept a list of dicts or names (or one name) from the model, a player edit, or legacy keys."""
    items = raw if isinstance(raw, list) else [raw] if raw else []
    result: list[dict[str, Any]] = []
    for item in items[:12]:
        entry = item if isinstance(item, dict) else {"name": item}
        name = clean_name(entry.get("name") or entry.get("faction"))
        if not name:
            continue
        kind = str(entry.get("kind") or "").strip().casefold()
        status = str(entry.get("status") or "").strip().casefold()
        role = str(entry.get("role") or "").strip()[:80]
        result.append({"name": name, "kind": kind if kind in KINDS else kind_for(name, role),
                       "role": "" if is_unknown(role) else role,
                       "status": status if status in STATUSES else ("leader" if LEADER_WORDS.search(role) else "member"),
                       "source": source, "turn_index": turn_index})
    return result


SOURCE_RANK = {"inferred": 0, "story": 1, "player": 2}


def merge_affiliations(existing: list[dict[str, Any]] | None, proposed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add new groups and update known ones; a weaker source never overrides a stronger one."""
    merged = [dict(entry) for entry in (existing or []) if isinstance(entry, dict) and entry.get("name")]
    for entry in proposed:
        key = normalize_reference(entry["name"])
        match = next((row for row in merged if normalize_reference(row["name"]) == key), None)
        if match is None:
            merged.append(entry)
            continue
        if SOURCE_RANK.get(entry.get("source", "story"), 1) < SOURCE_RANK.get(match.get("source", "story"), 1):
            continue
        for field in ("kind", "role", "status"):
            if entry.get(field) and (field != "kind" or entry[field] != "faction" or not match.get("kind")):
                match[field] = entry[field]
        match["source"] = max(match.get("source", "story"), entry.get("source", "story"), key=lambda s: SOURCE_RANK.get(s, 1))
    return merged[:12]


def _faction_names(factions: list[Faction]) -> list[tuple[Faction, list[str]]]:
    return [(row, [row.name, *(row.aliases or [])]) for row in factions]


def infer_from_text(role: str, facts: list[str], factions: list[Faction], *, people_names: set[str] | None = None,
                    places: set[str] | None = None) -> list[dict[str, Any]]:
    """Affiliations a role or fact states outright: a known group's name, or "priestess of the Veiled Flame".

    Facts count only when they speak of belonging ("serves", "member of"), so "fought the City Watch"
    does not make someone a member. A person's name is never a group, and a known place is a city.
    """
    found: list[dict[str, Any]] = []
    people_names = people_names or set()
    places = places or set()
    texts = [role, *facts]
    for row, names in _faction_names(factions):
        for text in texts:
            if text == role:
                hit = next((pattern.search(text) for name in names if len(name) >= 3 and (pattern := mention_pattern(name))
                            and pattern.search(text)), None)
            else:
                # A fact only counts when it says the person belongs: "serves the City Watch", not "fought the City Watch".
                hit = next((match for name in names if len(name) >= 3 and (match := re.search(
                    rf"\b(?:{JOINS})\s+(?:the\s+)?{re.escape(name)}\b", text, re.IGNORECASE))), None)
            if hit:
                former = bool(re.search(r"\b(?:former|ex-|once|used to|formerly)\b", text[:hit.start() + 1], re.IGNORECASE))
                found.append({"name": row.name, "kind": row.kind or kind_for(row.name, role), "role": role[:80] if text == role else "",
                              "status": "former" if former else ("leader" if text == role and LEADER_WORDS.search(role) else "member"),
                              "source": "inferred", "turn_index": None})
                break
    for text in texts:
        for match in [*MEMBERSHIP.finditer(text), *(TITLE_OF.finditer(text) if text == role else [])]:
            name = clean_name(match.group(1))
            key = normalize_reference(name)
            if not name or key in people_names or any(key == normalize_reference(entry["name"]) for entry in found):
                continue
            former = bool(re.search(r"\b(?:former|ex-|formerly)\b", text[:match.start() + 1], re.IGNORECASE))
            found.append({"name": name, "kind": "city" if key in places else kind_for(name, role), "role": role[:80] if text == role else "",
                          "status": "former" if former else ("leader" if text == role and LEADER_WORDS.search(role) else "member"),
                          "source": "inferred", "turn_index": None})
    return found


async def ensure_faction(session: AsyncSession, campaign: Campaign, branch: Branch, name: str, *, kind: str = "",
                         description: str = "", known: dict[str, Faction] | None = None) -> Faction:
    key = normalize_reference(name)
    if known is not None and key in known:
        faction = known[key]
    else:
        faction = await session.scalar(select(Faction).where(Faction.branch_id == branch.id, Faction.name.ilike(name)))
    if faction is None:
        faction = Faction(campaign_id=campaign.id, branch_id=branch.id, name=name, kind=kind or kind_for(name, description),
                          description=description[:3000], motives=[], aliases=[], visibility="PLAYER_KNOWN")
        session.add(faction)
        await session.flush()
    else:
        if kind in KINDS and (not faction.kind or faction.kind == "faction") and kind != "faction":
            faction.kind = kind
        if description and not faction.description:
            faction.description = description[:3000]
    if known is not None:
        known[key] = faction
    return faction


FACTION_PROMPT = """You group the people of a role-playing story into the factions, nations, cities, guilds, faiths, noble houses, armies, and crews they belong to.
Use only what the roles, facts, and story establish. A shared leader or cause is a group (for example "the masked man's crew"); a shared job alone is not.
Return JSON only:
{"factions":[{"name":"Veiled Flame","kind":"faction|nation|city|guild|religion|house|military|government|crew","description":"one short line"}],
 "memberships":[{"character_id":"<id from people>","faction":"Veiled Flame","role":"high priestess","status":"member|leader|former"}],
 "relations":[{"from":"Veiled Flame","to":"City Watch","relation":"allied|hostile|rival|at war|trade partners|vassal|neutral"}],
 "remove":[{"character_id":"<id>","faction":"City Watch"}]}
Being captured, guarded, chased, attacked, hired once, or simply standing near a group is not membership.
Each person's "current" lists groups already saved; if one is clearly wrong, add {"character_id","faction"} to "remove".
Rules: only ids from the input; reuse known faction names exactly; at most 12 factions; skip anyone whose group is unclear; never invent a group for one person."""


async def model_pass(provider: LLMProvider, campaign: Campaign, people: list[dict[str, Any]], factions: list[Faction]) -> dict[str, Any]:
    raw = await provider.complete_json([
        {"role": "system", "content": FACTION_PROMPT},
        {"role": "user", "content": json.dumps({
            "world": (campaign.constitution or {}).get("premise") or campaign.original_prompt[:1200],
            "known_factions": [{"name": row.name, "kind": row.kind, "description": row.description[:200]} for row in factions],
            "people": people[:60]}, ensure_ascii=False)},
    ], temperature=0.1, max_tokens=1600)
    result = parse_json_response(raw)
    return result if isinstance(result, dict) else {}


async def sync_affiliations(session: AsyncSession, campaign: Campaign, branch: Branch,
                            provider: LLMProvider | None = None) -> dict[str, int]:
    """Link people to groups. Deterministic every time; with a provider, also ask the state model."""
    people = list((await session.scalars(select(Character).where(Character.branch_id == branch.id))).all())
    factions = list((await session.scalars(select(Faction).where(Faction.branch_id == branch.id))).all())
    known = {normalize_reference(row.name): row for row in factions}
    for alias_row in factions:
        for alias in alias_row.aliases or []:
            known.setdefault(normalize_reference(alias), alias_row)
    facts: dict[Any, list[str]] = {}
    for fact in (await session.scalars(select(CharacterFact).where(
            CharacterFact.branch_id == branch.id, CharacterFact.active.is_(True)))).all():
        facts.setdefault(fact.character_id, []).append(fact.content)
    stats = {"linked": 0, "groups": 0}

    # Legacy single-value keys become structured affiliations.
    for person in people:
        attributes = dict(person.attributes or {})
        legacy = [attributes.get(key) for key in ("faction", "faction_name", "allegiance", "nation", "country", "organization", "guild")
                  if isinstance(attributes.get(key), str)]
        if legacy:
            attributes["affiliations"] = merge_affiliations(attributes.get("affiliations"), normalize_affiliations(legacy))
            person.attributes = attributes

    if provider is not None:
        payload = [{"id": str(person.id), "name": person.name, "role": person.role or "",
                    "facts": [text[:160] for text in facts.get(person.id, [])[-6:]],
                    "current": [entry["name"] for entry in (person.attributes or {}).get("affiliations") or [] if isinstance(entry, dict)]}
                   for person in people]
        try:
            result = await model_pass(provider, campaign, payload, factions)
        except Exception:  # noqa: BLE001 - grouping is best effort; deterministic links still apply
            logger.warning("Faction model pass failed", exc_info=True)
            result = {}
        for group in (result.get("factions") or [])[:12]:
            if isinstance(group, dict) and clean_name(group.get("name")):
                await ensure_faction(session, campaign, branch, clean_name(group["name"]), kind=str(group.get("kind") or ""),
                                     description=str(group.get("description") or ""), known=known)
        by_id = {str(person.id): person for person in people}
        for membership in (result.get("memberships") or [])[:120]:
            if not isinstance(membership, dict) or str(membership.get("character_id")) not in by_id:
                continue
            person = by_id[str(membership["character_id"])]
            entries = normalize_affiliations([{"name": membership.get("faction"), "role": membership.get("role"),
                                               "status": membership.get("status"),
                                               "kind": getattr(known.get(normalize_reference(str(membership.get("faction") or ""))), "kind", "")}],
                                             source="story")
            if entries:
                attributes = dict(person.attributes or {})
                attributes["affiliations"] = merge_affiliations(attributes.get("affiliations"), entries)
                person.attributes = attributes
        for removal in (result.get("remove") or [])[:60]:
            if not isinstance(removal, dict) or str(removal.get("character_id")) not in by_id:
                continue
            person = by_id[str(removal["character_id"])]
            drop = normalize_reference(str(removal.get("faction") or ""))
            attributes = dict(person.attributes or {})
            attributes["affiliations"] = [entry for entry in attributes.get("affiliations") or []
                                          if not isinstance(entry, dict) or entry.get("source") == "player"
                                          or normalize_reference(entry.get("name", "")) != drop]
            person.attributes = attributes
        for relation in (result.get("relations") or [])[:24]:
            if not isinstance(relation, dict):
                continue
            source, target = clean_name(relation.get("from")), clean_name(relation.get("to"))
            if not source or not target or normalize_reference(source) == normalize_reference(target):
                continue
            row = await session.scalar(select(FactionRelationship).where(
                FactionRelationship.branch_id == branch.id, FactionRelationship.from_faction.ilike(source),
                FactionRelationship.to_faction.ilike(target)))
            if row is None:
                row = FactionRelationship(campaign_id=campaign.id, branch_id=branch.id, from_faction=source, to_faction=target)
                session.add(row)
            if isinstance(relation.get("relation"), str) and not is_unknown(relation["relation"]):
                row.relation = relation["relation"][:80]

    factions = list({id(row): row for row in known.values()}.values())
    people_names = {normalize_reference(person.name) for person in people}
    places = {normalize_reference(row.name) for row in (await session.scalars(select(Location).where(Location.branch_id == branch.id))).all()}
    for person in people:
        attributes = dict(person.attributes or {})
        inferred = infer_from_text(person.role or "", facts.get(person.id, []), factions,
                                   people_names=people_names, places=places)
        # Inferred links are derived data: recompute them instead of letting stale ones pile up.
        kept = [entry for entry in attributes.get("affiliations") or [] if isinstance(entry, dict) and entry.get("source") != "inferred"]
        merged = merge_affiliations(kept, inferred)
        for entry in merged:
            faction = await ensure_faction(session, campaign, branch, entry["name"], kind=entry.get("kind", ""), known=known)
            entry["name"], entry["kind"] = faction.name, faction.kind
        if merged != attributes.get("affiliations"):
            attributes["affiliations"] = merged
            person.attributes = attributes
            stats["linked"] += 1
    stats["groups"] = len({id(row) for row in known.values()})
    return stats
