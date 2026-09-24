"""Stable visual identity for characters: what they look like, for narration continuity and portraits.

Stable traits (face, hair, eyes, build, marks) fill once and are kept; clothing and current
state change with the story. Mature body detail is only recorded and only used in portraits
for characters the story clearly establishes as adults, and only when the player enables
mature portraits. Anyone who may be a minor is always treated as strictly non-sexual.
"""

import json
import re
from typing import Any

from app.llm.base import LLMProvider
from app.llm.structured import parse_json_response
from app.services.identity import is_unknown

STABLE_FIELDS = ("species", "apparent_age", "gender_presentation", "height", "build", "skin", "face", "eyes", "hair")
CHANGING_FIELDS = ("clothing", "current_state")
MINOR_WORDS = re.compile(r"\b(?:child|children|kid|kids|boy|girl|teen|teenage|teenager|adolescent|youngster|minor|infant|"
                         r"toddler|baby|little one|schoolchild|pupil|underage|young lad|young lass|urchin|street kid|"
                         r"\d{1,2}[- ]year[- ]old)\b", re.IGNORECASE)
ADULT_WORDS = re.compile(r"\b(?:adult|grown|middle[- ]aged|elderly|old|aged|mature|in (?:his|her|their) (?:twenties|thirties|"
                         r"forties|fifties|sixties|seventies|eighties)|twenties|thirties|forties|fifties|sixties|veteran|"
                         r"grandmother|grandfather|mother of|father of|widow|widower|woman|man)\b", re.IGNORECASE)
AGE_NUMBER = re.compile(r"\b(\d{1,3})\b")

VISUAL_PROMPT = """You describe how a character in a role-playing story looks, for continuity and a portrait.
Use only what the narration and known facts establish, plus plausible, restrained details that fit the setting where the text is silent. Keep established details exactly.
Return JSON only:
{"apparent_age":"e.g. late thirties","gender_presentation":"","species":"","height":"","build":"","skin":"","face":"","eyes":"","hair":"",
 "clothing":"what they wear now","current_state":"visible condition now (wounds, dirt, posture)","features":["distinctive marks"],
 "body":"","adult":true}
Rules:
- "adult" is true only if the story makes clear they are a grown adult. If age is unknown or they could be under 18, set "adult": false.
- "body": only for adults, and only what the narration shows (for example, if they are undressed, describe it plainly and without euphemism). Leave it empty otherwise.
- Never describe anyone who is or might be under 18 in a sexual or suggestive way. For them, describe only face, hair, clothing, and ordinary features.
- Short phrases, no story, no names in the fields."""


def looks_minor(visual: dict[str, Any], character: Any | None = None) -> bool:
    text = " ".join(str(value) for value in visual.values() if isinstance(value, (str, int)))
    if character is not None:
        text += f" {getattr(character, 'name', '')} {getattr(character, 'role', '')}"
    if MINOR_WORDS.search(text):
        return True
    for match in AGE_NUMBER.finditer(str(visual.get("apparent_age") or "")):
        if int(match.group(1)) < 18:
            return True
    return False


def confirmed_adult(visual: dict[str, Any], character: Any | None = None) -> bool:
    """Adulthood must be positive evidence; uncertainty never unlocks mature content."""
    if looks_minor(visual, character):
        return False
    age = str(visual.get("apparent_age") or "")
    numbers = [int(match.group(1)) for match in AGE_NUMBER.finditer(age)]
    if numbers:
        return min(numbers) >= 18
    return visual.get("adult") is True and bool(ADULT_WORDS.search(age))


def merge_visual(current: dict[str, Any] | None, proposed: dict[str, Any] | None) -> dict[str, Any]:
    visual = dict(current or {})
    proposed = proposed or {}
    for key in STABLE_FIELDS:
        value = proposed.get(key)
        if isinstance(value, str) and not is_unknown(value) and not visual.get(key):
            visual[key] = value.strip()[:200]
    for key in CHANGING_FIELDS:
        value = proposed.get(key)
        if isinstance(value, str) and not is_unknown(value):
            visual[key] = value.strip()[:300]
    features = [str(value).strip()[:160] for value in (proposed.get("features") or []) if isinstance(value, str) and value.strip()]
    if features:
        existing = list(visual.get("features") or [])
        visual["features"] = (existing + [value for value in features if value.casefold() not in {old.casefold() for old in existing}])[:10]
    if proposed.get("adult") is True and not looks_minor({**visual, **{key: proposed.get(key) for key in STABLE_FIELDS}}):
        visual["adult"] = True
    if proposed.get("adult") is False:
        visual["adult"] = False
    body = proposed.get("body")
    if isinstance(body, str) and body.strip() and confirmed_adult(visual):
        visual["body"] = body.strip()[:400]
    if looks_minor(visual):
        visual.pop("body", None)
        visual["adult"] = False
    return visual


def needs_visual(character: Any) -> bool:
    visual = (character.attributes or {}).get("visual_identity")
    return not isinstance(visual, dict) or sum(1 for key in STABLE_FIELDS if visual.get(key)) < 3


async def describe_character(provider: LLMProvider, *, name: str, role: str, facts: list[str], excerpts: list[str],
                             tone: str) -> dict[str, Any]:
    raw = await provider.complete_json([
        {"role": "system", "content": VISUAL_PROMPT},
        {"role": "user", "content": json.dumps({"character": name, "role": role, "known_facts": facts[-12:],
                                                "story_tone": tone, "narration_excerpts": excerpts[-6:]}, ensure_ascii=False)},
    ], temperature=0.3, max_tokens=700)
    result = parse_json_response(raw)
    return result if isinstance(result, dict) else {}
