"""Deterministic text helpers for entity identity, provenance, and merge-safe values.

Everything here is pure so the resolver, reconciler, repair tool, and tests share one
definition of "the same name", "a weaker value", and "the same fact".
"""

import hashlib
import re
from typing import Any

ARTICLES = re.compile(r"\b(?:the|a|an)\b")

# Provenance, strongest first. A weaker source never overwrites a stronger value.
PROVENANCE_ORDER = [
    "MODEL_GUESS", "RUMOR", "INFERRED", "RELIABLE_NPC_STATEMENT",
    "DIRECT_OBSERVATION", "CONFIRMED_CANON", "PLAYER_EXPLICIT",
]
PROVENANCE_RANK = {name: rank for rank, name in enumerate(PROVENANCE_ORDER)}
CERTAINTY_PROVENANCE = {
    "CONFIRMED": "DIRECT_OBSERVATION", "OBSERVED": "DIRECT_OBSERVATION",
    "INFERRED": "INFERRED", "RUMOR": "RUMOR", "BELIEF": "RUMOR", "UNKNOWN": "MODEL_GUESS",
}

UNKNOWN_VALUES = {
    "", "unknown", "none", "n/a", "na", "null", "?", "unclear", "unspecified", "tbd",
    "role unknown", "no role", "unknown role", "not known", "unnamed", "-", "—",
}

# Nouns that describe a kind of person, not a specific person. A bare reference built from
# these ("the guard", "a merchant") only resolves through an established alias or a unique
# person in the current scene; it never becomes an alias on its own.
GENERIC_PERSON_NOUNS = {
    "man", "woman", "boy", "girl", "child", "kid", "person", "figure", "stranger", "guard",
    "guards", "merchant", "priest", "priests", "priestess", "soldier", "soldiers", "servant",
    "innkeeper", "bartender", "courier", "watchman", "knight", "healer", "captain", "attendant",
    "scribe", "blacksmith", "baker", "prisoner", "thief", "beggar", "noble", "lord", "lady",
    "old man", "old woman", "youth", "elder", "villager", "citizen", "pursuer", "watcher",
    "companion", "mage", "wizard", "witch", "sailor", "dockworker", "vendor", "prostitute",
    "worker", "traveler", "rider", "hunter", "monk", "nun", "cultist", "assassin", "bandit",
}
# Adjectives that describe a passing state rather than identity; "bound merchant" and
# "merchant" can name the same person when the scene supports it.
STATE_ADJECTIVES = {
    "bound", "bleeding", "bloodied", "wounded", "injured", "kneeling", "trembling", "sobbing",
    "sleeping", "drunk", "dead", "dying", "frightened", "scared", "angry", "tired", "silent",
    "young", "old", "little", "small", "tall", "short", "thin", "fat", "hooded", "masked",
    "cloaked", "street", "same", "other", "nervous", "burning", "beaten", "captured",
    "unnamed", "unknown", "mysterious", "strange", "lone",
}
ORDINAL_WORDS = {"first", "second", "third", "fourth", "fifth", "1", "2", "3", "4", "5", "another", "other"}
TITLE_WORDS = {
    "king", "queen", "prince", "princess", "duke", "duchess", "lord", "lady", "captain", "high",
    "priestess", "priest", "emperor", "empress", "general", "commander", "chancellor", "baron",
    "baroness", "count", "countess", "sir", "dame", "master", "mistress", "former", "elder",
}


def normalize_reference(value: Any) -> str:
    """Case, punctuation, and article-insensitive key for a name or alias."""
    text = str(value or "").casefold().replace("’", "'")
    text = re.sub(r"[^\w\s'()-]", " ", text)
    text = ARTICLES.sub(" ", text)
    return " ".join(text.split())[:160]


def strip_qualifier(value: str) -> tuple[str, str]:
    """Split "masked man (spokesperson)" into ("masked man", "spokesperson")."""
    match = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", value)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return value.strip(), ""


def words(value: str) -> list[str]:
    return re.findall(r"[\w']+", normalize_reference(value))


def head_noun(value: str) -> str:
    """The noun a descriptive reference is about: "man in the dark coat" -> "man"."""
    base, _ = strip_qualifier(normalize_reference(value))
    base = re.split(r"\b(?:in|with|from|of|at|near|on|by|who|that|wearing)\b", base, maxsplit=1)[0]
    tokens = [token for token in base.split() if token not in ORDINAL_WORDS]
    return tokens[-1] if tokens else ""


def descriptive_tail(value: str) -> str:
    """The identifying clause after the head noun: "man in the dark coat" -> "in dark coat"."""
    base, _ = strip_qualifier(normalize_reference(value))
    match = re.search(r"\b(?:in|with|wearing|from)\b.*$", base)
    return match.group(0).strip() if match else ""


def is_generic_reference(value: str) -> bool:
    """True for "guard", "the bound merchant", "second guard"; false for names and titles-with-names."""
    base, _ = strip_qualifier(normalize_reference(value))
    if not base:
        return True
    if descriptive_tail(base):
        return False
    tokens = [token for token in base.split() if token not in ORDINAL_WORDS and not token.isdigit()]
    if not tokens:
        return True
    head = tokens[-1]
    modifiers = tokens[:-1]
    return (head in GENERIC_PERSON_NOUNS and all(
        token in STATE_ADJECTIVES or token in GENERIC_PERSON_NOUNS or token in {"city", "temple", "palace", "market", "gate", "plaza", "tavern", "dock"}
        for token in modifiers))


def looks_like_proper_name(value: str) -> bool:
    """A capitalised personal name such as "Mara" or "Oren Vale", not a description."""
    raw = strip_qualifier(str(value or "").strip())[0]
    if not raw or is_generic_reference(raw):
        return False
    tokens = raw.split()
    if len(tokens) > 4 or tokens[0].casefold() in {"former", "ex", "late", "old", "young", "new"}:
        # "Former High Archivist" is a title, not a person's name.
        return False
    return all(token[:1].isupper() for token in tokens if token.casefold() not in {"of", "the", "de", "van", "von"}) and \
        not any(token.casefold() in GENERIC_PERSON_NOUNS for token in tokens[-1:])


def is_title(value: str) -> bool:
    tokens = words(value)
    return bool(tokens) and tokens[0] in TITLE_WORDS


def is_unknown(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return not value
    text = normalize_reference(value)
    return text in UNKNOWN_VALUES or text.startswith("unknown ") and len(text.split()) <= 3


def provenance_rank(level: str | None) -> int:
    return PROVENANCE_RANK.get(str(level or "").upper(), PROVENANCE_RANK["INFERRED"])


def provenance_for_certainty(certainty: str | None) -> str:
    return CERTAINTY_PROVENANCE.get(str(certainty or "").upper(), "INFERRED")


def merge_scalar(existing: str, existing_level: str | None, proposed: Any, proposed_level: str,
                 *, field: str = "") -> tuple[str, str | None, str]:
    """Return (value, provenance, decision) without ever losing established information.

    Empty and "unknown" proposals never replace a value. A weaker source never replaces a
    stronger one. A role/title that is a strictly less specific form of the stored value
    ("High Archivist" for "former High Archivist") is treated as a confirmation.
    """
    new = str(proposed or "").strip()
    if is_unknown(new):
        return existing, existing_level, "kept_existing_unknown_proposal"
    if is_unknown(existing):
        return new, proposed_level, "filled"
    if normalize_reference(new) == normalize_reference(existing):
        stronger = proposed_level if provenance_rank(proposed_level) > provenance_rank(existing_level) else existing_level
        return existing, stronger, "confirmed"
    if provenance_rank(proposed_level) < provenance_rank(existing_level):
        return existing, existing_level, "kept_stronger_existing"
    if field in {"role", "title"}:
        old_key, new_key = normalize_reference(existing), normalize_reference(new)
        if new_key in old_key:
            return existing, existing_level, "kept_more_specific"
    return new, proposed_level, "replaced"


def text_hash(value: str) -> str:
    return hashlib.sha256(normalize_reference(value).encode()).hexdigest()


STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "by", "from", "is",
    "was", "were", "are", "be", "been", "has", "have", "had", "he", "she", "they", "it", "his", "her",
    "their", "its", "that", "this", "as", "into", "not", "but", "who", "what", "when", "will", "would",
}


def content_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9']+", str(value or "").casefold())
            if len(token) > 2 and token not in STOPWORDS}


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def same_statement(left: str, right: str, threshold: float = 0.8) -> bool:
    """Near-duplicate check for facts and memories written with slightly different wording."""
    if normalize_reference(left) == normalize_reference(right):
        return True
    a, b = content_tokens(left), content_tokens(right)
    if not a or not b:
        return False
    if a <= b or b <= a:
        return min(len(a), len(b)) >= 3 and jaccard(a, b) >= 0.6
    return jaccard(a, b) >= threshold


FACT_TYPE_RULES = (
    ("appearance", r"\b(?:hair|eyes?|scar|scars|wears|wearing|tall|short|face|skin|robe|coat|cloak|tattoo|notched|beard|voice)\b"),
    ("injury", r"\b(?:injur|wound|bleed|bruise|burn(?:ed|s)? hand|broken|limp)\w*"),
    ("ability", r"\b(?:can|able to|knows how|casts?|controls?|magic|spell|power)\b"),
    ("title", r"\b(?:is|was|became|named|crowned|serves as) (?:the |a )?(?:former )?(?:high archivist|high priest|king|queen|captain|lord|lady|leader)\b"),
    ("affiliation", r"\b(?:member of|serves|works for|belongs to|loyal to|leader of|guild|order|house)\b"),
    ("history", r"\b(?:was|used to|before|once|formerly|years ago|born)\b"),
    ("location", r"\b(?:lives in|stays at|hides in|found in|located)\b"),
)


def classify_fact(content: str) -> str:
    lowered = content.casefold()
    for fact_type, pattern in FACT_TYPE_RULES:
        if re.search(pattern, lowered):
            return fact_type
    return "general"


def mention_pattern(value: str) -> re.Pattern[str] | None:
    """Regex for a name or descriptor in prose, tolerant of articles and case ("man in a dark coat")."""
    tokens = normalize_reference(value).split()
    if not tokens:
        return None
    body = r"\s+(?:(?:the|a|an)\s+)?".join(re.escape(token) for token in tokens)
    return re.compile(rf"(?<![\w'-]){body}(?![\w'-])", re.IGNORECASE)


def is_mentioned(text: str, values: list[str]) -> bool:
    for value in values:
        if len(value) < 3:
            continue
        pattern = mention_pattern(value)
        if pattern and pattern.search(text):
            return True
    return False
