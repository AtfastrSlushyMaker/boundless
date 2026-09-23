from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, CampaignSummary
from app.llm.base import LLMProvider
from app.services.context_builder import history_for_branch
from app.services.narration import clean_history_narration
from app.services.turn_service import parse_json_response


async def update_campaign_summary(session: AsyncSession, provider: LLMProvider, campaign: Campaign,
                                 branch_id, head_turn_id, turn_index: int) -> None:
    if turn_index % 8 != 0:
        return
    existing = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch_id, CampaignSummary.summary_type == "campaign"))
    if existing and existing.through_turn_index >= turn_index:
        return
    recent = await history_for_branch(session, head_turn_id, limit=8)
    transcript = "\n".join(f"Player: {turn.player_action}\nGM: {clean_history_narration(turn.gm_response)}" for turn in recent)
    prior = existing.content if existing else ""
    prompt = "Update the concise, factual campaign summary with these new turns. Keep established hard canon verbatim. Return JSON {\"summary\":\"...\"}. Do not include hidden secrets unless the summary is marked GM-only."
    raw = await provider.complete_json([
        {"role": "system", "content": prompt},
        {"role": "user", "content": f"Previous summary:\n{prior}\n\nRecent turns:\n{transcript}"},
    ], max_tokens=700, temperature=0.1)
    summary = str(parse_json_response(raw).get("summary", ""))[:8000]
    if existing:
        existing.content = summary
        existing.through_turn_index = turn_index
    else:
        session.add(CampaignSummary(campaign_id=campaign.id, branch_id=branch_id,
            summary_type="campaign", content=summary, through_turn_index=turn_index))
    await session.commit()
