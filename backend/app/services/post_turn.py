"""Best-effort work after the canonical turn commits: summary, embeddings, portraits, setup extraction.

Each job runs in its own session and transaction. A failure is recorded on the job row and
retried with backoff; it can never roll back or corrupt a completed turn.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Branch, Campaign, Character, PostTurnJob
from app.db.session import SessionLocal
from app.llm.router import provider_for_profile, role_profile, route, visual_model
from app.llm.structured import parse_json_response
from app.services.abilities import gain_ability
from app.services.identity import normalize_reference
from app.services.memory_service import embed_missing

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5
KINDS = ("SUMMARY_UPDATE", "MEMORY_EMBEDDING", "PORTRAIT_QUEUE", "CONSTITUTION_EXTRACTION")
CONSTITUTION_PROMPT = """Extract the player's campaign setup into structured canon. Read only the setup text.
Return JSON: {"identity":{"name":"","sex":"","gender":"","pronouns":""},"abilities":[{"name":"short name","description":"what it does, in the player's terms","source":"innate|learned|item|other","limitations":["..."]}],
"limitations":["..."],"mortality":["..."],"world_rules":["..."],"magic_rules":["..."],"factions":["..."],"relationships":["..."],"goals":["..."],
"tone":"","narrative_preferences":["..."],"hidden_canon_permissions":["..."]}
Rules: copy the player's meaning exactly; do not soften, add, or invent. An ability is something the player can do (for example "the ability to steal magic" -> {"name":"Magic theft",...}). Leave fields empty when the setup says nothing. Leave identity fields empty unless stated explicitly."""


def enqueue(session: AsyncSession, *, campaign_id: UUID, branch_id: UUID, kind: str, turn_id: UUID | None = None,
            payload: dict | None = None) -> PostTurnJob:
    job = PostTurnJob(campaign_id=campaign_id, branch_id=branch_id, turn_id=turn_id, kind=kind, status="QUEUED",
                      payload=payload or {}, next_attempt_at=datetime.now(UTC))
    session.add(job)
    return job


async def _summary(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    from app.services.summary_service import update_campaign_summary

    campaign = await session.get(Campaign, job.campaign_id)
    branch = await session.get(Branch, job.branch_id)
    if not campaign or not branch or not branch.head_turn_id:
        return
    routed = await route(session, "summary", factory)
    from app.db.models import Turn
    head = await session.get(Turn, branch.head_turn_id)
    await update_campaign_summary(session, routed.provider, campaign, branch.id, branch.head_turn_id,
                                  head.turn_index if head else 0, force=bool(job.payload.get("force")))


async def _constitution(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    campaign = await session.get(Campaign, job.campaign_id)
    if not campaign:
        return
    routed = await route(session, "state", factory)
    raw = await routed.provider.complete_json([
        {"role": "system", "content": CONSTITUTION_PROMPT},
        {"role": "user", "content": campaign.original_prompt[:24_000]},
    ], temperature=0.0, max_tokens=1800)
    extracted = parse_json_response(raw)
    await merge_extracted_constitution(session, campaign, extracted, model=routed.describe())


async def merge_extracted_constitution(session: AsyncSession, campaign: Campaign, extracted: dict, *, model: dict) -> None:
    """Additive merge: the model can add structure; it cannot remove deterministic or player-edited canon."""
    constitution = dict(campaign.constitution or {})

    def strings(value) -> list[str]:
        return [str(entry).strip()[:500] for entry in value if isinstance(entry, str) and entry.strip()] if isinstance(value, list) else []

    for source, target in (("limitations", "limitations"), ("mortality", "mortality_rules"), ("world_rules", "world_rules"),
                           ("magic_rules", "magic_rules"), ("factions", "important_factions"),
                           ("relationships", "important_relationships"), ("goals", "goals"),
                           ("narrative_preferences", "narrative_preferences"), ("hidden_canon_permissions", "hidden_canon_permissions")):
        existing = list(constitution.get(target) or [])
        for entry in strings(extracted.get(source)):
            if not any(normalize_reference(entry) == normalize_reference(old) for old in existing):
                existing.append(entry)
        constitution[target] = existing[:30]
    if isinstance(extracted.get("tone"), str) and extracted["tone"].strip() and not constitution.get("tone"):
        constitution["tone"] = extracted["tone"].strip()[:80]
    catalogue = list(constitution.get("ability_catalogue") or [])
    rules = list(constitution.get("rules") or [])
    new_abilities = []
    for entry in extracted.get("abilities") or []:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            continue
        name = str(entry["name"]).strip()[:120]
        if any(normalize_reference(name) == normalize_reference(old.get("name", "")) for old in catalogue):
            continue
        record = {"name": name, "description": str(entry.get("description") or "")[:1200],
                  "source": str(entry.get("source") or "campaign_setup")[:120],
                  "limitations": strings(entry.get("limitations"))[:8], "strength": "HARD", "source_text": "llm_extraction"}
        catalogue.append(record)
        new_abilities.append(record)
        rules.append({"statement": f"{campaign.protagonist_name} has the ability: {name}.", "type": "ABILITY",
                      "strength": "HARD", "source": "setup_extraction", "source_text": record["description"][:500]})
    for kind, key in (("LIMITATION", "limitations"), ("WORLD_RULE", "world_rules"), ("MAGIC_RULE", "magic_rules")):
        for entry in strings(extracted.get(key)):
            if not any(normalize_reference(entry) == normalize_reference(rule.get("statement", "")) for rule in rules):
                rules.append({"statement": entry, "type": kind, "strength": "HARD" if kind == "LIMITATION" else "SOFT",
                              "source": "setup_extraction", "source_text": entry})
    constitution["ability_catalogue"] = catalogue[:24]
    constitution["rules"] = rules[:80]
    constitution["extraction"] = {"status": "complete", "model": model.get("model"), "provider": model.get("provider"),
                                  "at": datetime.now(UTC).isoformat()}
    campaign.constitution = constitution
    if new_abilities:
        players = (await session.scalars(select(Character).where(Character.campaign_id == campaign.id))).all()
        for player in players:
            if normalize_reference(player.name) != normalize_reference(campaign.protagonist_name):
                continue
            for record in new_abilities:
                await gain_ability(session, player, record, provenance="PLAYER_EXPLICIT", turn_index=0,
                                   source_default="campaign_setup")


async def _portraits(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    from app.services.portrait_jobs import queue_automatic_portraits

    campaign = await session.get(Campaign, job.campaign_id)
    if campaign:
        await queue_automatic_portraits(session, campaign, job.branch_id)


async def _profile(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    from app.db.models import Checkpoint, Turn
    from app.services.story_profile import evolve_profile

    campaign = await session.get(Campaign, job.campaign_id)
    branch = await session.get(Branch, job.branch_id)
    if not campaign or not branch:
        return
    routed = await route(session, "summary", factory)
    await evolve_profile(session, routed.provider, campaign, branch, force=bool(job.payload.get("force")))
    # Keep the head checkpoint in step so a rewind to this turn keeps the updated profile.
    if branch.head_turn_id:
        head = await session.get(Turn, branch.head_turn_id)
        checkpoint = await session.scalar(select(Checkpoint).where(
            Checkpoint.branch_id == branch.id, Checkpoint.turn_id == branch.head_turn_id,
            Checkpoint.turn_index == (head.turn_index if head else -1)).order_by(Checkpoint.created_at.desc()))
        if checkpoint:
            snapshot = dict(checkpoint.state_snapshot or {})
            snapshot["current_state"] = {**(snapshot.get("current_state") or {}),
                                         "player_profile": (branch.current_state or {}).get("player_profile")}
            checkpoint.state_snapshot = snapshot


async def describe_and_store(session: AsyncSession, campaign: Campaign, person: Character, provider, turns,
                             *, mature_detail: bool = False) -> bool:
    """Build a character's visual identity from narration that mentions them. Returns True when stored."""
    import re

    from app.db.models import CharacterAlias
    from app.services.narration import clean_history_narration
    from app.services.visual_identity import describe_character, merge_visual

    names = [person.name, *(row.alias for row in (await session.scalars(select(CharacterAlias).where(
        CharacterAlias.character_id == person.id))).all())]
    excerpts: list[str] = []
    for turn in turns:
        text = clean_history_narration(turn.gm_response or "")
        follow = False
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            named = any(name and len(name) > 2 and name.casefold() in sentence.casefold() for name in names)
            if named or (follow and sentence.lower().startswith(("she ", "he ", "they ", "her ", "his ", "their "))):
                excerpts.append(sentence[:400])
            follow = named or (follow and sentence.lower().startswith(("she ", "he ", "they ", "her ", "his ", "their ")))
    if not excerpts and not (person.attributes or {}).get("known_facts"):
        return False
    tone = str((campaign.constitution or {}).get("tone") or (campaign.theme_profile or {}).get("family") or "")
    proposed = await describe_character(provider, name=person.name, role=person.role or "",
                                        facts=list((person.attributes or {}).get("known_facts") or []),
                                        excerpts=excerpts[-12:], tone=tone, mature_detail=mature_detail)
    attributes = dict(person.attributes or {})
    attributes["visual_identity"] = merge_visual(attributes.get("visual_identity"), proposed)
    person.attributes = attributes
    return True


def needs_mature_visual(person: Character, image_settings, mature_role_configured: bool) -> bool:
    """Refresh an existing look once before an automatic mature portrait is queued."""
    from app.services.image_provider import importance_for

    if not (mature_role_configured and image_settings and image_settings.enabled and image_settings.allow_mature):
        return False
    attributes = person.attributes or {}
    if attributes.get("avatar_url") or attributes.get("avatar_job") or "portrait_seed" in attributes:
        return False
    enabled = {"RECURRING": image_settings.auto_recurring, "MAJOR": image_settings.auto_major,
               "COMPANION": image_settings.auto_companion, "MINOR": image_settings.auto_minor}
    return bool(enabled.get(importance_for(person), False))


async def _visuals(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    """Give people who just appeared a visual identity the narration supports, before portraits queue."""
    from app.db.models import Turn
    from app.services.context_builder import history_for_branch
    from app.services.portrait_jobs import image_profile
    from app.services.visual_identity import needs_visual

    campaign = await session.get(Campaign, job.campaign_id)
    branch = await session.get(Branch, job.branch_id)
    if not campaign or not branch or not branch.head_turn_id:
        return
    head = await session.get(Turn, branch.head_turn_id)
    turns = await history_for_branch(session, branch.head_turn_id, limit=12)
    people = (await session.scalars(select(Character).where(
        Character.branch_id == branch.id, Character.visibility == "PLAYER_KNOWN"))).all()
    player_key = normalize_reference(campaign.protagonist_name)
    image_settings = await image_profile(session)
    mature_role_configured = bool(image_settings and image_settings.enabled and image_settings.allow_mature
                                  and await role_profile(session, "mature"))
    candidates = [person for person in people if normalize_reference(person.name) != player_key
                  and (needs_visual(person) or needs_mature_visual(person, image_settings, mature_role_configured))
                  and person.importance != "BACKGROUND" and head and person.last_seen_turn_index is not None
                  and head.turn_index - person.last_seen_turn_index <= 1][:4]
    if not candidates:
        return
    routed = await visual_model(session, factory)
    for person in candidates:
        await describe_and_store(session, campaign, person, routed.provider, turns,
                                 mature_detail=bool(image_settings and image_settings.allow_mature
                                                    and routed.source_role == "mature"))


async def _factions(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    """Link people to groups every turn; ask the state model when new people are ungrouped or every eight turns."""
    from app.db.models import Turn
    from app.services.affiliations import sync_affiliations

    campaign = await session.get(Campaign, job.campaign_id)
    branch = await session.get(Branch, job.branch_id)
    if not campaign or not branch:
        return
    head = await session.get(Turn, branch.head_turn_id) if branch.head_turn_id else None
    state = dict(branch.current_state or {})
    last = int(state.get("faction_sync_turn") or 0)
    people = (await session.scalars(select(Character).where(
        Character.branch_id == branch.id, Character.visibility == "PLAYER_KNOWN"))).all()
    unchecked = [person for person in people if person.importance != "BACKGROUND"
                 and not (person.attributes or {}).get("affiliation_checked")]
    use_model = bool(head) and (len(unchecked) >= 1 and head.turn_index - last >= 2 or head.turn_index - last >= 8)
    provider = (await route(session, "state", factory)).provider if use_model else None
    await sync_affiliations(session, campaign, branch, provider)
    if provider is not None and head:
        for person in people:
            person.attributes = {**(person.attributes or {}), "affiliation_checked": True}
        branch.current_state = {**state, "faction_sync_turn": head.turn_index}
    if head:
        # Keep the head checkpoint in step so a rewind to this turn keeps the groups.
        from app.db.models import Checkpoint
        from app.services.state_service import capture_snapshot
        checkpoint = await session.scalar(select(Checkpoint).where(
            Checkpoint.branch_id == branch.id, Checkpoint.turn_id == head.id,
            Checkpoint.turn_index == head.turn_index).order_by(Checkpoint.created_at.desc()))
        if checkpoint:
            checkpoint.state_snapshot = await capture_snapshot(session, branch)


async def _embeddings(session: AsyncSession, job: PostTurnJob, factory: Callable) -> None:
    await embed_missing(session, job.branch_id)


HANDLERS = {"SUMMARY_UPDATE": _summary, "MEMORY_EMBEDDING": _embeddings, "PORTRAIT_QUEUE": _portraits,
            "CONSTITUTION_EXTRACTION": _constitution, "PROFILE_EVOLUTION": _profile, "VISUAL_PROFILE": _visuals,
            "FACTION_SYNC": _factions}


async def run_job(job_id: UUID, factory: Callable | None = None) -> str:
    async with SessionLocal() as session:
        job = await session.get(PostTurnJob, job_id)
        if not job or job.status not in {"QUEUED", "RETRY"}:
            return job.status if job else "MISSING"
        job.status = "RUNNING"
        job.attempts += 1
        await session.commit()
        try:
            await HANDLERS[job.kind](session, job, factory or provider_for_profile)
            job.status = "COMPLETE"
            job.error = ""
            await session.commit()
        except Exception as exc:
            await session.rollback()
            job = await session.get(PostTurnJob, job_id)
            job.error = f"{type(exc).__name__}: {str(exc)[:400]}"
            job.status = "FAILED" if job.attempts >= MAX_ATTEMPTS else "RETRY"
            job.next_attempt_at = datetime.now(UTC) + timedelta(seconds=min(900, 20 * 2 ** job.attempts))
            await session.commit()
            logger.warning("Post-turn job %s (%s) failed: %s", job.id, job.kind, job.error)
        return job.status


async def run_jobs(job_ids: list[UUID], factory: Callable | None = None) -> dict[str, str]:
    return {str(job_id): await run_job(job_id, factory) for job_id in job_ids}


async def process_due_jobs(limit: int = 5) -> int:
    async with SessionLocal() as session:
        jobs = (await session.scalars(select(PostTurnJob.id).where(
            PostTurnJob.status.in_(["QUEUED", "RETRY"]), PostTurnJob.next_attempt_at <= datetime.now(UTC),
        ).order_by(PostTurnJob.created_at).limit(limit))).all()
    for job_id in jobs:
        await run_job(job_id)
    return len(jobs)


async def post_turn_worker() -> None:
    while True:
        try:
            await process_due_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Post-turn worker tick failed")
        await asyncio.sleep(10)


def describe_jobs(jobs: list[PostTurnJob]) -> list[dict]:
    return [{"id": str(job.id), "kind": job.kind, "status": job.status, "attempts": job.attempts,
             "error": job.error, "turn_id": str(job.turn_id) if job.turn_id else None,
             "updated_at": job.updated_at.isoformat() if job.updated_at else None} for job in jobs]
