"""Mature scenes: the game master plans the turn, a separate (usually local) model writes the prose.

Players can pick a "mature scenes" model in AI roles. When a turn is sexual, or graphically
violent (gore, torture, severe injuries), and everyone involved is an adult, the narrator still
decides what happens next, as short beats with no prose, and the mature model writes the passage
from those beats without toning it down. If anyone who is or might be under 18 is in the scene,
the turn is never routed here, and the writer's own rules forbid sexual content involving minors.
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Character
from app.llm.base import LLMProvider
from app.services.visual_identity import AGE_PHRASE, looks_minor

EXPLICIT_SCENE = re.compile(
    r"\b(?:sex(?:ual|ually)?|nude|nudity|naked|undress(?:es|ed|ing)?|strip(?:s|ped|ping)? (?:off|naked|down)|topless|"
    r"breasts?|nipples?|cock|dick|penis|pussy|vagina|clit\w*|genitals?|groin|erect(?:ion)?|orgasm\w*|climax(?:es|ed)?|"
    r"moan(?:s|ed|ing)?|thrust(?:s|ed|ing)?|fuck\w*|lust(?:ful)?|aroused|arousal|erotic|sensual|seduc\w*|caress\w*|"
    r"make love|making love|made love|bed (?:her|him|them)|intimate|intimacy|foreplay|kiss(?:es|ed|ing)? (?:her|his|their) "
    r"(?:neck|body|breasts?|thighs?)|prostitut\w*|brothel|whore|lap dance|straddl\w*)\b", re.IGNORECASE)

# Kept short on purpose: small local models follow a few plain lines better than a long rulebook.
WRITER_RULES = """Write this passage for an adult reader who opted into mature content, following the game master's beats and the player's action.
Be vivid and explicit where the scene calls for it: sex and bodies, violence, gore and injuries. No fading to black, no warnings.
Sexual content only between adults.
Do not speak or decide for the player. Output only the narration."""

DIRECTOR_PROMPT = """Plan this turn in 4 to 8 short bullet points, no prose: what happens, what each person does, the consequences, and where the passage stops.
Say plainly what happens, including injuries; a separate writer handles the explicit prose."""

GRAPHIC_SCENE = re.compile(
    r"\b(?:gore|gory|blood(?:y|ied|soaked)?|bleed(?:s|ing)?|torture[sd]?|torturing|dismember\w*|decapitat\w*|behead\w*|"
    r"mutilat\w*|eviscerat\w*|disembowel\w*|entrails|guts|intestines|viscera|flay(?:s|ed|ing)?|impal\w*|sever(?:s|ed|ing)? "
    r"(?:his|her|their|the)|gouge[sd]?|gouging|stab(?:s|bed|bing)?|slit (?:his|her|their) throat|throat slit|"
    r"crush(?:es|ed|ing)? (?:his|her|their) (?:skull|throat)|bones? (?:snap|crack|break)s?|broken bones?|compound fracture|"
    r"burn(?:s|ed|ing)? alive|execut(?:e|es|ed|ion)|massacre\w*|slaughter\w*|maim\w*|carv(?:e|es|ed|ing) (?:into|out)|"
    r"wounds?|gash(?:es|ed)?|amputat\w*|skull|corpse\w*|mangled)\b", re.IGNORECASE)

REFUSAL = re.compile(r"\b(?:I can(?:'|no)t|I(?:'m| am) (?:not able|unable)|I won'?t|as an AI|not appropriate|cannot (?:help|continue|write))\b",
                     re.IGNORECASE)


# Clear signs of a child. Words like "girl", "boy" or "minor" are too often used for adults
# ("the girl at the bar", "a minor wound") to block a turn on their own.
CLEAR_MINOR = re.compile(r"\b(?:child|children|kids?|teens?|teenage|teenager|adolescent|underage|infant|toddler|baby|schoolchild|"
                         r"schoolgirl|schoolboy|little (?:girl|boy))\b", re.IGNORECASE)


def _score(pattern: re.Pattern[str], action: str, recent: str) -> int:
    return 2 * len(pattern.findall(action or "")) + len(pattern.findall(recent or ""))


def explicit_score(action: str, recent: str) -> int:
    """How strongly the action and the scene so far point to sexual or graphically violent content."""
    return _score(EXPLICIT_SCENE, action, recent) + _score(GRAPHIC_SCENE, action, recent)


async def scene_has_minor(session: AsyncSession, branch_id, state: dict[str, Any], text: str) -> bool:
    """True when the scene clearly involves someone under 18 (a stated age, a clear word, or a person recorded as a minor)."""
    if CLEAR_MINOR.search(text or "") or any(int(age) < 18 for age in AGE_PHRASE.findall(text or "")):
        return True
    ids = [value for value in ((state.get("scene") or {}).get("character_ids") or []) if value]
    if not ids:
        return False
    people = (await session.scalars(select(Character).where(Character.branch_id == branch_id, Character.id.in_(ids)))).all()
    return any(looks_minor((person.attributes or {}).get("visual_identity") or {}, person) for person in people)


async def is_mature_turn(session: AsyncSession, branch_id, state: dict[str, Any], action: str, recent: str) -> bool:
    recent = recent[-2500:]
    if explicit_score(action, recent) < 2:
        return False
    if _score(EXPLICIT_SCENE, action, recent) >= 2:
        # Sexual turns need an all-adult scene; violent ones do not.
        return not await scene_has_minor(session, branch_id, state, f"{action}\n{recent}")
    return True


async def direct(provider: LLMProvider, messages: list[dict[str, str]], *, temperature: float) -> str:
    """The game master's beats for this turn, or "" when it declines or fails."""
    try:
        text = await provider.complete([*messages[:-1], {
            "role": "user", "content": f"{messages[-1]['content']}\n\n{DIRECTOR_PROMPT}"}],
            max_tokens=500, temperature=min(temperature, 0.8))
    except Exception:
        return ""
    text = (text or "").strip()
    return "" if not text or REFUSAL.search(text[:240]) else text[:3000]


def writer_messages(messages: list[dict[str, str]], beats: str) -> list[dict[str, str]]:
    system, *rest = messages
    last = rest[-1]
    plan = f"\n\nGame master's beats for this turn (follow them; do not show them):\n{beats}" if beats else ""
    return [{"role": "system", "content": f"{system['content']}\n\n{WRITER_RULES}"}, *rest[:-1],
            {"role": last["role"], "content": f"{last['content']}{plan}"}]
