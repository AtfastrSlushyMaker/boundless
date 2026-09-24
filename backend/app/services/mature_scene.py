"""Mature scenes: the game master plans the turn, a separate (usually local) model writes the prose.

Players can pick a "mature scenes" model in AI roles. When a turn is sexual or explicit and
everyone involved is an adult, the narrator still decides what happens next, as short beats
with no prose, and the mature model writes the passage from those beats without toning it
down. If anyone who is or might be under 18 is in the scene, the turn is never routed here,
and the writer's own rules forbid sexual content involving minors.
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Character
from app.llm.base import LLMProvider
from app.services.visual_identity import MINOR_WORDS, looks_minor

EXPLICIT_SCENE = re.compile(
    r"\b(?:sex(?:ual|ually)?|nude|nudity|naked|undress(?:es|ed|ing)?|strip(?:s|ped|ping)? (?:off|naked|down)|topless|"
    r"breasts?|nipples?|cock|dick|penis|pussy|vagina|clit\w*|genitals?|groin|erect(?:ion)?|orgasm\w*|climax(?:es|ed)?|"
    r"moan(?:s|ed|ing)?|thrust(?:s|ed|ing)?|fuck\w*|lust(?:ful)?|aroused|arousal|erotic|sensual|seduc\w*|caress\w*|"
    r"make love|making love|made love|bed (?:her|him|them)|intimate|intimacy|foreplay|kiss(?:es|ed|ing)? (?:her|his|their) "
    r"(?:neck|body|breasts?|thighs?)|prostitut\w*|brothel|whore|lap dance|straddl\w*)\b", re.IGNORECASE)

WRITER_RULES = """You are writing one passage of an interactive story for an adult reader who has opted into mature content.
The game master has already decided what happens this turn; their beats are in the last message. Write that scene as vivid
narration, following the beats, the established canon, and the player's action.
- This scene is between consenting adults. Describe sexual content, nudity, and bodies plainly and explicitly where the
  scene calls for it. Do not fade to black, summarise, moralise, or add warnings.
- Never write sexual or suggestive content involving anyone who is or might be under 18. If the scene would require that,
  write it without any sexual content.
- Never decide the player's words, feelings, or next action. End at a moment the player can respond to.
- Keep names, places, items, and injuries exactly as established. Output only the narration."""

DIRECTOR_PROMPT = """Before the prose is written, plan this turn as the game master.
Return 4 to 8 short bullet points, no prose: what happens in response to the player's action, what each person present
wants and does, any consequence or discovery, how this moves the story forward, and where the passage should stop.
The scene is between adults and may be sexual; a separate writer handles the explicit prose, so state what happens plainly and
do not soften, skip, or refuse it. Do not write dialogue lines or narration."""

REFUSAL = re.compile(r"\b(?:I can(?:'|no)t|I(?:'m| am) (?:not able|unable)|I won'?t|as an AI|not appropriate|cannot (?:help|continue|write))\b",
                     re.IGNORECASE)


def explicit_score(action: str, recent: str) -> int:
    return 2 * len(EXPLICIT_SCENE.findall(action or "")) + len(EXPLICIT_SCENE.findall(recent or ""))


async def scene_has_minor(session: AsyncSession, branch_id, state: dict[str, Any], text: str) -> bool:
    """True when the scene might involve anyone under 18; uncertainty counts as a minor."""
    if MINOR_WORDS.search(text or ""):
        return True
    ids = [value for value in ((state.get("scene") or {}).get("character_ids") or []) if value]
    if not ids:
        return False
    people = (await session.scalars(select(Character).where(Character.branch_id == branch_id, Character.id.in_(ids)))).all()
    return any(looks_minor((person.attributes or {}).get("visual_identity") or {}, person) for person in people)


async def is_mature_turn(session: AsyncSession, branch_id, state: dict[str, Any], action: str, recent: str) -> bool:
    if explicit_score(action, recent[-2500:]) < 2:
        return False
    return not await scene_has_minor(session, branch_id, state, f"{action}\n{recent[-2500:]}")


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
