"""The player's living profile: traits, goals, history, world rules, and reputation that evolve with the story.

Premise entries are seeded with their source. Story entries are added by a periodic
post-turn pass (and on demand). Changes never delete: a retired entry becomes "past" with
the turn and reason, so the sheet shows how the character changed. The profile lives in
branch state, so rewinds and branches keep their own version.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Branch, Campaign, Objective
from app.llm.base import LLMProvider
from app.llm.structured import parse_json_response
from app.services.context_builder import history_for_branch
from app.services.identity import is_unknown, same_statement
from app.services.narration import clean_history_narration

CATEGORIES = ("traits", "goals", "history", "world_rules", "reputation")
INTERVAL = 6
PROMPT = """You keep a role-playing character's living profile up to date with the story.
You receive the current profile (each entry has an id and a source) and the story since the last update.
Return JSON only:
{"add":{"traits":[],"goals":[],"history":[],"world_rules":[],"reputation":[]},
 "retire":[{"id":"...","reason":"..."}],
 "update":[{"id":"...","text":"..."}]}
- traits: who the character has shown themselves to be (temperament, habits, values), in third person.
- goals: what the character now wants or is working toward. Retire a goal that was achieved or abandoned.
- history: important things that happened to or were done by the character. One sentence each, past tense.
- world_rules: how this world works as learned in play (magic, law, politics, dangers). Not one-off events.
- reputation: how others in the world now see the character.
Retire or update an entry only when the story clearly shows it changed. Premise entries can change if the story changes them.
At most 3 additions per category. Keep each entry to one short sentence. Do not repeat existing entries. Do not invent."""


def _item_id(category: str, text: str) -> str:
    return hashlib.sha1(f"{category}:{text}".encode()).hexdigest()[:10]


def _item(category: str, text: str, source: str, turn_index: int | None) -> dict[str, Any]:
    return {"id": _item_id(category, text), "text": text.strip()[:400], "source": source, "turn_index": turn_index,
            "status": "active"}


def seed_profile(constitution: dict[str, Any]) -> dict[str, Any]:
    seeds = {"traits": constitution.get("traits") or [], "goals": constitution.get("goals") or constitution.get("preferences") or [],
             "history": constitution.get("history") or [], "world_rules": constitution.get("world_rules") or [],
             "reputation": []}
    profile: dict[str, Any] = {category: [] for category in CATEGORIES}
    for category, values in seeds.items():
        for value in values:
            if isinstance(value, str) and value.strip() and not any(same_statement(value, row["text"]) for row in profile[category]):
                profile[category].append(_item(category, value, "premise", 0))
    profile["updated_through_turn"] = 0
    return profile


def ensure_profile(campaign: Campaign, branch: Branch) -> dict[str, Any]:
    state = dict(branch.current_state or {})
    profile = state.get("player_profile")
    if not isinstance(profile, dict) or not all(isinstance(profile.get(category), list) for category in CATEGORIES):
        profile = seed_profile(campaign.constitution or {})
        state["player_profile"] = profile
        branch.current_state = state
    return profile


def merge_changes(profile: dict[str, Any], changes: dict[str, Any], turn_index: int) -> list[dict[str, str]]:
    """Apply a model's proposal additively. Returns human-readable change notes."""
    notes: list[dict[str, str]] = []
    by_id = {row["id"]: (category, row) for category in CATEGORIES for row in profile.get(category, [])}
    for entry in changes.get("retire") or []:
        if isinstance(entry, dict) and entry.get("id") in by_id:
            category, row = by_id[entry["id"]]
            if row.get("status") == "active":
                row.update({"status": "past", "ended_turn_index": turn_index, "note": str(entry.get("reason") or "")[:300]})
                notes.append({"type": "profile", "text": f"No longer: {row['text']}"})
    for entry in changes.get("update") or []:
        if isinstance(entry, dict) and entry.get("id") in by_id and isinstance(entry.get("text"), str) and not is_unknown(entry["text"]):
            category, row = by_id[entry["id"]]
            if not same_statement(row["text"], entry["text"]) and row.get("status") == "active":
                row.update({"status": "past", "ended_turn_index": turn_index, "note": "Changed in the story"})
                profile[category].append({**_item(category, entry["text"], "story", turn_index), "replaces": row["id"]})
                notes.append({"type": "profile", "text": f"Changed: {entry['text'][:160]}"})
    additions = changes.get("add") if isinstance(changes.get("add"), dict) else {}
    for category in CATEGORIES:
        for text in (additions.get(category) or [])[:3]:
            if not isinstance(text, str) or is_unknown(text) or len(text.strip()) < 6:
                continue
            if any(same_statement(text, row["text"], 0.7) for row in profile[category]):
                continue
            profile[category].append(_item(category, text, "story", turn_index))
            label = {"traits": "Trait", "goals": "Goal", "history": "History", "world_rules": "World rule",
                     "reputation": "Reputation"}[category]
            notes.append({"type": "profile", "text": f"{label}: {text.strip()[:160]}"})
        profile[category] = profile[category][-60:]
    profile["updated_through_turn"] = turn_index
    return notes


async def deterministic_updates(session: AsyncSession, profile: dict[str, Any], branch: Branch, turn_index: int) -> None:
    """Facts the record already proves: completed objectives become history without a model call."""
    objectives = (await session.scalars(select(Objective).where(
        Objective.branch_id == branch.id, Objective.status == "completed"))).all()
    for objective in objectives:
        text = f"Completed: {objective.title}."
        if not any(same_statement(text, row["text"]) for row in profile["history"]):
            profile["history"].append(_item("history", text, "record", objective.completed_turn_index or turn_index))
        for row in profile["goals"]:
            if row.get("status") == "active" and same_statement(row["text"], objective.title, 0.6):
                row.update({"status": "past", "ended_turn_index": objective.completed_turn_index or turn_index,
                            "note": "Achieved"})


async def evolve_profile(session: AsyncSession, provider: LLMProvider | None, campaign: Campaign, branch: Branch,
                         *, force: bool = False) -> list[dict[str, str]]:
    profile = ensure_profile(campaign, branch)
    turns = await history_for_branch(session, branch.head_turn_id, limit=400)
    head = turns[-1].turn_index if turns else 0
    since = int(profile.get("updated_through_turn") or 0)
    await deterministic_updates(session, profile, branch, head)
    notes: list[dict[str, str]] = []
    if provider is not None and (force or head - since >= INTERVAL):
        recent = [turn for turn in turns if force or turn.turn_index > since][-12:]
        if recent:
            current = {category: [{"id": row["id"], "text": row["text"], "source": row["source"]}
                                  for row in profile[category] if row.get("status") == "active"] for category in CATEGORIES}
            transcript = "\n".join(f"Turn {turn.turn_index}. Player: {turn.player_action[:500]}\nGM: {clean_history_narration(turn.gm_response)[:1800]}"
                                   for turn in recent)
            raw = await provider.complete_json([
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": json.dumps({"character": campaign.protagonist_name, "profile": current,
                                                        "story": transcript}, ensure_ascii=False)},
            ], temperature=0.2, max_tokens=1200)
            notes = merge_changes(profile, parse_json_response(raw), head)
    state = dict(branch.current_state or {})
    state["player_profile"] = profile
    branch.current_state = state
    return notes
