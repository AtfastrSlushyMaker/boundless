"""Resolve model-written references to existing entity IDs before anything is created.

The LLM proposes; this module decides identity deterministically from names, aliases,
titles, descriptors, the current scene, location, and recency. When the evidence does not
single out one person it returns AMBIGUOUS instead of silently merging two people.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from app.services.identity import (
    ORDINAL_WORDS,
    STATE_ADJECTIVES,
    content_tokens,
    descriptive_tail,
    head_noun,
    is_generic_reference,
    is_title,
    looks_like_proper_name,
    normalize_reference,
    strip_qualifier,
)

AUTO_ACCEPT = 0.7
PLAYER_REFERENCES = {"player", "protagonist", "you", "me", "myself", "i", "the player", "player character"}


@dataclass
class CharacterCandidate:
    id: str
    name: str
    aliases: list[tuple[str, str]] = field(default_factory=list)  # (alias, alias_type)
    role: str = ""
    status: str = ""
    location: str = ""
    first_meeting_place: str = ""
    last_seen: int | None = None
    in_scene: bool = False
    importance: str = "MINOR"
    is_player: bool = False

    def keys(self) -> set[str]:
        values = {normalize_reference(self.name)} | {normalize_reference(alias) for alias, _ in self.aliases}
        return {value for value in values if value}

    def places(self) -> set[str]:
        return {normalize_reference(value) for value in (self.location, self.first_meeting_place) if value}


@dataclass
class Resolution:
    status: Literal["RESOLVED", "AMBIGUOUS", "NEW", "PLAYER"]
    reference: str
    character_id: str | None = None
    confidence: float = 0.0
    method: str = ""
    candidates: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"reference": self.reference, "status": self.status, "character_id": self.character_id,
                "confidence": round(self.confidence, 2), "method": self.method, "candidates": self.candidates}


def tails_match(left: str, right: str) -> bool:
    """"in dark coat" matches "in dark coat does" (a greedy narration capture), not "in dark"."""
    a, b = left.split(), right.split()
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 2 and longer[:len(shorter)] == shorter


def _is_numbered(value: str) -> bool:
    base, qualifier = strip_qualifier(normalize_reference(value))
    return bool(qualifier) or any(token in ORDINAL_WORDS or token.isdigit() for token in base.split())


def _modifiers(value: str) -> set[str]:
    base, _ = strip_qualifier(normalize_reference(value))
    base = re.split(r"\b(?:in|with|from|of|at|near|on|by|who|that|wearing)\b", base, maxsplit=1)[0]
    tokens = base.split()
    return set(tokens[:-1]) if tokens else set()


class EntityResolver:
    def __init__(self, candidates: list[CharacterCandidate], protagonist_name: str,
                 current_location: str = "") -> None:
        self.candidates = candidates
        self.by_id = {candidate.id: candidate for candidate in candidates}
        self.protagonist = normalize_reference(protagonist_name)
        self.location = normalize_reference(current_location)

    def add(self, candidate: CharacterCandidate) -> None:
        self.candidates.append(candidate)
        self.by_id[candidate.id] = candidate

    def is_player_reference(self, reference: str) -> bool:
        key = normalize_reference(reference)
        return key in PLAYER_REFERENCES or key == self.protagonist

    def _pick(self, matches: list[CharacterCandidate], reference: str, confidence: float, method: str) -> Resolution:
        if len(matches) == 1:
            return Resolution("RESOLVED", reference, matches[0].id, confidence, method)
        in_scene = [match for match in matches if match.in_scene]
        if len(in_scene) == 1:
            return Resolution("RESOLVED", reference, in_scene[0].id, confidence - 0.05, method + "+scene")
        here = [match for match in matches if self.location and self.location in match.places()]
        if len(here) == 1:
            return Resolution("RESOLVED", reference, here[0].id, confidence - 0.08, method + "+location")
        return Resolution("AMBIGUOUS", reference, None, 0.0, method, [match.id for match in matches])

    def resolve(self, reference: str, *, id_hint: str | None = None, role_hint: str = "") -> Resolution:
        reference = str(reference or "").strip()
        if id_hint and id_hint in self.by_id:
            candidate = self.by_id[id_hint]
            return Resolution("PLAYER" if candidate.is_player else "RESOLVED", reference or candidate.name,
                              candidate.id, 1.0, "id")
        if not reference:
            return Resolution("NEW", reference, method="empty")
        if self.is_player_reference(reference):
            player = next((candidate for candidate in self.candidates if candidate.is_player), None)
            return Resolution("PLAYER", reference, player.id if player else None, 1.0, "player")
        people = [candidate for candidate in self.candidates if not candidate.is_player]

        exact = [candidate for candidate in people if candidate.name == reference]
        if exact:
            return self._pick(exact, reference, 1.0, "canonical_name")
        key = normalize_reference(reference)
        by_name = [candidate for candidate in people if normalize_reference(candidate.name) == key]
        if by_name:
            return self._pick(by_name, reference, 0.97, "canonical_name_normalized")
        by_alias = [candidate for candidate in people
                    if any(normalize_reference(alias) == key for alias, _ in candidate.aliases)]
        if by_alias:
            return self._pick(by_alias, reference, 0.95, "alias")

        base, qualifier = strip_qualifier(reference)
        if qualifier and base:
            if looks_like_proper_name(base):
                result = self.resolve(base)
                if result.status == "RESOLVED":
                    result.method = "qualified_" + result.method
                    return result
            # "Mara (former High Archivist)": the qualifier can carry the title.
            titled = self.resolve(qualifier) if not is_generic_reference(qualifier) else None
            if titled and titled.status == "RESOLVED" and titled.confidence >= 0.85 and not looks_like_proper_name(base):
                return titled

        if not is_generic_reference(reference) and not looks_like_proper_name(reference):
            by_role = [candidate for candidate in people if candidate.role and normalize_reference(candidate.role) == key]
            if by_role and (is_title(reference) or len(key.split()) >= 2):
                return self._pick(by_role, reference, 0.86, "title")

        if looks_like_proper_name(reference):
            ref_tokens = set(key.split())
            containing = [candidate for candidate in people if looks_like_proper_name(candidate.name)
                          and ref_tokens < set(normalize_reference(candidate.name).split())]
            if containing:
                result = self._pick(containing, reference, 0.8, "partial_name")
                if result.status == "RESOLVED":
                    return result
            titled = [candidate for candidate in people if is_title(candidate.name)
                      and set(normalize_reference(candidate.name).split()) < ref_tokens]
            if len(titled) == 1:
                return Resolution("RESOLVED", reference, titled[0].id, 0.75, "title_gained_name")
            return Resolution("NEW", reference, method="proper_name")

        return self._resolve_descriptor(reference, people, role_hint)

    def _resolve_descriptor(self, reference: str, people: list[CharacterCandidate], role_hint: str) -> Resolution:
        if _is_numbered(reference):
            # "second guard", "Guard 2", "masked man (right)" only match exactly.
            return Resolution("NEW", reference, method="numbered_descriptor")
        head = head_noun(reference)
        tail = descriptive_tail(reference)
        modifiers = _modifiers(reference)
        strong, weak, cross = [], [], []
        for candidate in people:
            for value in [candidate.name, *(alias for alias, _ in candidate.aliases)]:
                if _is_numbered(value):
                    continue
                other_tail = descriptive_tail(value)
                other_head = head_noun(value)
                if tail and other_tail and tails_match(tail, other_tail):
                    (strong if other_head == head else cross).append(candidate)
                    break
                if head and other_head == head and not tail and not other_tail:
                    other_modifiers = _modifiers(value)
                    nested = modifiers <= other_modifiers or other_modifiers <= modifiers
                    if nested and all(token in STATE_ADJECTIVES for token in modifiers ^ other_modifiers):
                        weak.append(candidate)
                        break
        strong = list({candidate.id: candidate for candidate in strong}.values())
        weak = [candidate for candidate in {candidate.id: candidate for candidate in weak}.values()
                if candidate.id not in {match.id for match in strong}]
        if strong:
            return self._pick(strong, reference, 0.9, "descriptor")
        cross = list({candidate.id: candidate for candidate in cross}.values())
        if cross:
            # "woman in the dark coat" vs "man in the dark coat": only the current scene can
            # tell us the hooded figure and the revealed woman are one person.
            in_scene = [candidate for candidate in cross if candidate.in_scene]
            if len(in_scene) == 1:
                return Resolution("RESOLVED", reference, in_scene[0].id, 0.74, "descriptor_cross_scene")
            return Resolution("AMBIGUOUS", reference, None, 0.0, "descriptor_cross", [c.id for c in cross])
        if weak:
            same_head = [candidate for candidate in people if not _is_numbered(candidate.name)
                         and not descriptive_tail(candidate.name) and head_noun(candidate.name) == head
                         and (modifiers <= _modifiers(candidate.name) or _modifiers(candidate.name) <= modifiers)]
            if len(same_head) > 1:
                # Several people share this description ("City Guard", "temple guard", ...): only the scene decides.
                in_scene = [candidate for candidate in weak if candidate.in_scene]
                if len(in_scene) == 1:
                    return Resolution("RESOLVED", reference, in_scene[0].id, 0.72, "descriptor_variant+scene")
                return Resolution("AMBIGUOUS", reference, None, 0.0, "descriptor_shared", [c.id for c in same_head])
            result = self._pick(weak, reference, 0.76, "descriptor_variant")
            if result.status == "RESOLVED":
                candidate = self.by_id[result.character_id]
                supported = candidate.in_scene or (self.location and self.location in candidate.places()) or len(weak) == 1
                if not supported:
                    return Resolution("AMBIGUOUS", reference, None, 0.0, "descriptor_variant_unsupported", [candidate.id])
                if not candidate.in_scene and not (self.location and self.location in candidate.places()):
                    result.confidence = 0.72
            return result
        return Resolution("NEW", reference, method="descriptor")


# --- Identity reveals in narration -------------------------------------------------------

NOT_NAMES = {
    "Not", "Here", "The", "Going", "Sorry", "Fine", "Afraid", "Sure", "Nobody", "No", "Yes", "Just",
    "What", "Who", "Your", "You", "It", "That", "This", "A", "An", "Only", "Still", "Done", "Late",
    "Tired", "Leaving", "Telling", "Asking", "Here's", "There", "Now", "Well", "High", "Former",
}
REVEAL_NAME = re.compile(
    r"(?i:\bmy name is|\bmy name's|\bI am called|\bI'm called|\bcall me|\bthey call me|\bI am|\bI'm|"
    r"\bintroduces (?:herself|himself|themselves) as|\breveals (?:herself|himself|themselves) (?:to be|as)|"
    r"\bher name is|\bhis name is|\btheir name is)"
    r"\s+(?P<name>[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)",
)
FORMER_ROLE = re.compile(r"(?i:\bI was) (?:the |a )?(?P<title>[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+){0,2}) before\b")
DESCRIPTOR_MENTION = re.compile(
    r"\b(?:the|a|an)\s+(?P<descriptor>(?:[a-z'-]+\s+){0,2}(?:man|woman|figure|stranger|person|boy|girl)"
    r"(?:\s+(?:in|with|wearing)\s+(?:the|a|an)?\s*[a-z'-]+(?:\s+[a-z'-]+){0,2})?)",
    re.IGNORECASE,
)


@dataclass
class IdentityReveal:
    character_id: str
    name: str
    evidence: str
    former_role: str = ""


def detect_identity_reveals(narration: str, resolver: EntityResolver, recent_ids: set[str]) -> list[IdentityReveal]:
    """Find "the woman in the dark coat ... 'My name is Mara'" in the same passage.

    Only descriptor-named people who are in the current scene or were seen recently can be
    revealed, and only when the descriptor has a distinctive clause ("in the dark coat").
    """
    reveals: list[IdentityReveal] = []
    for match in REVEAL_NAME.finditer(narration):
        name = match.group("name").strip()
        first = name.split()[0]
        if first in NOT_NAMES or resolver.is_player_reference(name):
            continue
        existing = resolver.resolve(name)
        if existing.status == "RESOLVED" and existing.method in {"canonical_name", "canonical_name_normalized"}:
            continue
        window = narration[max(0, match.start() - 900):match.start()]
        descriptors = [found.group("descriptor") for found in DESCRIPTOR_MENTION.finditer(window)]
        chosen: CharacterCandidate | None = None
        for descriptor in reversed(descriptors):
            tail = descriptive_tail(descriptor)
            if not tail:
                continue
            for candidate in resolver.candidates:
                if candidate.is_player or looks_like_proper_name(candidate.name):
                    continue
                if candidate.id not in recent_ids and not candidate.in_scene:
                    continue
                values = [candidate.name, *(alias for alias, _ in candidate.aliases)]
                if any(tails_match(descriptive_tail(value), tail) for value in values):
                    chosen = candidate
                    break
            if chosen:
                break
        if not chosen:
            continue
        after = narration[match.start():match.start() + 400]
        former = FORMER_ROLE.search(after)
        start = narration.rfind(".", 0, match.start()) + 1
        end = narration.find(".", match.end())
        reveals.append(IdentityReveal(chosen.id, name, " ".join(narration[start:end + 1 if end > 0 else None].split())[:300],
                                      f"former {former.group('title')}" if former else ""))
    unique: dict[str, IdentityReveal] = {}
    for reveal in reveals:
        unique.setdefault(reveal.character_id, reveal)
    return list(unique.values())


# --- Items and locations -------------------------------------------------------------------

def _name_tokens(value: str) -> set[str]:
    return content_tokens(normalize_reference(value)) or set(normalize_reference(value).split())


def resolve_named(reference: str, rows: list[tuple[str, str, list[str]]], *, min_subset_tokens: int = 1) -> Resolution:
    """Resolve items or locations given (id, name, aliases) rows.

    Exact and alias matches win. Otherwise one name's words may be a subset of the other's
    ("ledger" / "debt ledger" / "The Ledger") when exactly one row fits.
    """
    key = normalize_reference(reference)
    if not key:
        return Resolution("NEW", reference, method="empty")
    exact = [row for row in rows if normalize_reference(row[1]) == key]
    if len(exact) == 1:
        return Resolution("RESOLVED", reference, exact[0][0], 0.97, "name")
    alias = [row for row in rows if any(normalize_reference(value) == key for value in row[2])]
    if len(alias) == 1:
        return Resolution("RESOLVED", reference, alias[0][0], 0.95, "alias")
    if len(exact) > 1 or len(alias) > 1:
        return Resolution("AMBIGUOUS", reference, None, 0.0, "name", [row[0] for row in exact or alias])
    tokens = _name_tokens(reference)
    subset = []
    for row in rows:
        for value in [row[1], *row[2]]:
            other = _name_tokens(value)
            smaller = min(len(tokens), len(other))
            if tokens and other and (tokens <= other or other <= tokens) and smaller >= min_subset_tokens:
                subset.append(row)
                break
    if len(subset) == 1:
        return Resolution("RESOLVED", reference, subset[0][0], 0.8, "word_subset")
    if len(subset) > 1:
        return Resolution("AMBIGUOUS", reference, None, 0.0, "word_subset", [row[0] for row in subset])
    return Resolution("NEW", reference, method="none")
