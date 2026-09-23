"""Suggest optional player actions from the visible scene."""

import asyncio
import json
import logging
import re

from app.llm.base import LLMProvider

logger = logging.getLogger(__name__)

CHOICE_PROMPT = """You suggest the player's next actions in a text role-playing game.
Read only the player-facing scene and return JSON in this exact shape: {"choices":["I ...","I ...","I ..."]}.
Give three short, distinct, concrete actions that the player could attempt right now.
Use first person present tense. Vary the approach; for example, investigate, speak, or act.
An action is an attempt, not a guaranteed result. Do not decide the player's thoughts or invent an item or ability.
Use only what the player knows from this scene. Do not reveal secrets or force the plot onward.
Do not include numbered labels, explanations, outcomes, or a fourth choice.
"""


async def suggest_choices(provider: LLMProvider, narration: str, player_action: str,
                          protagonist_name: str) -> list[str]:
    try:
        raw = await asyncio.wait_for(provider.complete_json([
            {"role": "system", "content": CHOICE_PROMPT},
            {"role": "user", "content": json.dumps({
                "player": protagonist_name,
                "latest_player_action": player_action[:500],
                "scene": narration[-6000:],
            }, ensure_ascii=False)},
        ], temperature=0.55, max_tokens=300), timeout=30)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            start, end = cleaned.find("{"), cleaned.rfind("}")
            payload = json.loads(cleaned[start:end + 1]) if start >= 0 and end > start else None
        options = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(options, list):
            return []
        choices: list[str] = []
        seen: set[str] = set()
        for option in options:
            if not isinstance(option, str):
                continue
            text = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", option).strip().strip('"')
            if not 4 <= len(text) <= 160 or "<|" in text or "\n" in text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            choices.append(text)
            seen.add(key)
            if len(choices) == 3:
                break
        return choices if len(choices) >= 2 else []
    except Exception as exc:
        logger.warning("Could not generate optional choices: %s", type(exc).__name__)
        return []
