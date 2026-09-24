"""Rolling campaign summary that keeps advancing even when a model cannot produce JSON.

Runs as a post-turn job in its own transaction: a failure here never touches the turn.
"""

import logging
import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, CampaignSummary, Event, Memory, Objective, Turn
from app.llm.base import LLMProvider
from app.llm.structured import parse_json_response
from app.services.context_builder import history_for_branch
from app.services.narration import clean_history_narration

logger = logging.getLogger(__name__)
INTERVAL = 8
MAX_MODEL_FAILURES = 2
PROMPT = ("Update the concise, factual campaign summary with the new turns. Keep established hard canon, names, "
          "identities, deaths, possessions, and open goals exact. Write 150-400 words of plain prose. "
          "Return JSON {\"summary\":\"...\"}. Do not include GM-only secrets.")


def summary_due(existing: CampaignSummary | None, head_index: int) -> bool:
    through = existing.through_turn_index if existing else 0
    if head_index - through >= INTERVAL:
        return True
    return head_index % INTERVAL == 0 and head_index > through


def _first_sentences(text: str, count: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(parts[:count])[:400]


async def deterministic_summary(session: AsyncSession, campaign: Campaign, branch_id: UUID, prior: str,
                                turns: list[Turn]) -> str:
    """No-model fallback: important memories, open objectives, and a line per recent turn."""
    memories = (await session.scalars(select(Memory).where(
        Memory.branch_id == branch_id, Memory.visibility != "GM_ONLY", Memory.memory_type.notin_(["TURN", "SETUP"]),
    ).order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(12))).all()
    objectives = (await session.scalars(select(Objective).where(
        Objective.branch_id == branch_id, Objective.visibility != "GM_ONLY"))).all()
    events = (await session.scalars(select(Event).where(Event.branch_id == branch_id, Event.visibility != "GM_ONLY")
                                    .order_by(Event.created_at.desc()).limit(8))).all()
    lines: list[str] = []
    if prior:
        lines.append(prior.strip()[-2500:])
    lines.append("Recent turns: " + " ".join(
        f"(Turn {turn.turn_index}) {campaign.protagonist_name}: {turn.player_action[:120]} — {_first_sentences(clean_history_narration(turn.gm_response), 1)}"
        for turn in turns[-INTERVAL:]))
    if memories:
        lines.append("Key facts: " + " ".join(row.content[:220] for row in memories))
    if events:
        lines.append("Events: " + " ".join(row.content[:160] for row in reversed(events)))
    open_goals = [row.title for row in objectives if row.status == "active"]
    done = [row.title for row in objectives if row.status == "completed"]
    if open_goals:
        lines.append("Open objectives: " + "; ".join(open_goals) + ".")
    if done:
        lines.append("Completed: " + "; ".join(done[-8:]) + ".")
    return "\n".join(lines)[:8000]


async def update_campaign_summary(session: AsyncSession, provider: LLMProvider | None, campaign: Campaign,
                                  branch_id: UUID, head_turn_id: UUID | None, turn_index: int,
                                  *, force: bool = False) -> CampaignSummary | None:
    existing = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch_id, CampaignSummary.summary_type == "campaign"))
    if not force and not summary_due(existing, turn_index):
        return existing
    if existing is None:
        existing = CampaignSummary(campaign_id=campaign.id, branch_id=branch_id, summary_type="campaign", content="",
                                   through_turn_index=0)
        session.add(existing)
    since = existing.through_turn_index
    turns = [turn for turn in await history_for_branch(session, head_turn_id, limit=max(INTERVAL, turn_index - since) + 2)
             if turn.turn_index > since]
    if not turns:
        return existing
    existing.last_attempt_turn_index = turn_index
    summary = ""
    method = "model"
    if provider is not None and existing.consecutive_failures < MAX_MODEL_FAILURES:
        transcript = "\n".join(f"Turn {turn.turn_index}. Player: {turn.player_action[:800]}\nGM: {clean_history_narration(turn.gm_response)[:2400]}"
                               for turn in turns[-16:])
        try:
            raw = await provider.complete_json([
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": f"Previous summary:\n{existing.content}\n\nNew turns:\n{transcript}"},
            ], max_tokens=900, temperature=0.1)
            summary = str(parse_json_response(raw).get("summary", "")).strip()[:8000]
            if len(summary) < 40:
                raise ValueError("Summary model returned an empty or tiny summary.")
        except Exception as exc:
            existing.consecutive_failures += 1
            existing.last_error = f"{type(exc).__name__}: {str(exc)[:400]}"
            logger.warning("Summary model failed for branch %s at turn %s: %s", branch_id, turn_index, existing.last_error)
            summary = ""
    if not summary:
        if provider is not None and existing.consecutive_failures < MAX_MODEL_FAILURES and not force:
            return existing  # retry with the model on the next turn before falling back
        summary = await deterministic_summary(session, campaign, branch_id, existing.content, turns)
        method = "deterministic"
        existing.consecutive_failures = 0  # try the model again at the next interval
    else:
        existing.consecutive_failures = 0
        existing.last_error = ""
    existing.content = summary
    existing.through_turn_index = max(turn.turn_index for turn in turns)
    existing.last_success_at = datetime.now(UTC)
    existing.method = method
    return existing
