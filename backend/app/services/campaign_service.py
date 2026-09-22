from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Branch, Campaign, CanonRule, Character, Checkpoint, ModelProfile
from app.schemas import CampaignCreate
from app.services.constitution import derive_constitution, derive_theme, infer_title
from app.services.state_service import capture_snapshot


async def create_campaign(session: AsyncSession, payload: CampaignCreate) -> tuple[Campaign, Branch]:
    constitution = derive_constitution(payload.prompt)
    theme = derive_theme(payload.prompt, constitution.genre, constitution.tone).model_dump(mode="json")
    campaign = Campaign(
        title=(payload.title or infer_title(payload.prompt)).strip(),
        original_prompt=payload.prompt.strip(), constitution=constitution.model_dump(mode="json"),
        theme_profile=theme, protagonist_name=constitution.player_identity, genre=constitution.genre,
    )
    session.add(campaign)
    await session.flush()
    branch = Branch(campaign_id=campaign.id, name="First thread", current_state={
        "world_time": "Opening", "player_status": "alive", "current_location": "",
        "confirmed_canon_exceptions": [], "elapsed_seconds": 0,
    })
    session.add(branch)
    await session.flush()
    campaign.active_branch_id = branch.id
    session.add(Character(campaign_id=campaign.id, branch_id=branch.id, name=constitution.player_identity,
        role="Player character", status="alive", personality="", motivations=[], visibility="PLAYER_KNOWN",
        attributes={"abilities": constitution.abilities, "powers": constitution.powers,
                    "limitations": constitution.limitations, "origin": constitution.origin}))
    for invariant in constitution.hard_invariants:
        session.add(CanonRule(campaign_id=campaign.id, rule_type=invariant["type"],
            statement="The player cannot permanently die." if invariant["type"] == "PLAYER_CANNOT_DIE" else str(invariant),
            strength=invariant.get("strength", "HARD"), exceptions=invariant.get("exceptions", []),
            visibility="PLAYER_KNOWN", source="campaign_setup"))
    session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_index=0, state_snapshot=await capture_snapshot(session, branch)))
    if not await session.scalar(select(ModelProfile.id).where(ModelProfile.active.is_(True))):
        session.add(ModelProfile())
    await session.commit()
    await session.refresh(campaign)
    await session.refresh(branch)
    return campaign, branch


async def get_history_rows(session: AsyncSession, campaign_id: UUID, branch_id: UUID):
    from app.db.models import Branch
    from app.services.context_builder import history_for_branch
    branch = await session.get(Branch, branch_id)
    if not branch or branch.campaign_id != campaign_id:
        return None
    return await history_for_branch(session, branch.head_turn_id, limit=300)
