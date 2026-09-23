"""Durable, optional portrait queue. Never runs inside a story transaction."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, Character, ImageProfile, PortraitJob
from app.db.session import SessionLocal
from app.services.image_provider import (
    ComfyUIImageProvider,
    horde_result,
    importance_for,
    portrait_prompt,
    request_horde,
    stable_seed,
    store_portrait,
    workflow_for,
)

logger = logging.getLogger(__name__)


async def image_profile(session: AsyncSession) -> ImageProfile | None:
    return await session.scalar(select(ImageProfile).order_by(ImageProfile.updated_at.desc()).limit(1))


def profile_dict(profile: ImageProfile | None) -> dict:
    if profile is None:
        return {"provider": "none", "enabled": False, "base_url": "", "checkpoint": "",
                "workflow": "boundless_portrait_v1", "width": 768, "height": 1024, "steps": 28,
                "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras",
                "auto_recurring": True, "auto_major": True, "auto_companion": True, "auto_minor": False}
    return {key: getattr(profile, key) for key in (
        "provider", "enabled", "base_url", "checkpoint", "workflow", "width", "height",
        "steps", "cfg", "sampler", "scheduler", "auto_recurring", "auto_major",
        "auto_companion", "auto_minor")}


async def enqueue_portrait(session: AsyncSession, campaign: Campaign, character: Character,
                           *, new_identity_seed: bool = False) -> PortraitJob:
    profile = await image_profile(session)
    if not profile or not profile.enabled or profile.provider not in {"comfyui", "ai_horde"}:
        raise ValueError("Choose and enable an automatic portrait provider in Settings first.")
    if profile.provider == "comfyui" and (not profile.base_url or not profile.checkpoint):
        raise ValueError("Select a ComfyUI endpoint and checkpoint in Settings.")
    existing = await session.scalar(select(PortraitJob).where(
        PortraitJob.character_id == character.id,
        PortraitJob.status.in_(["QUEUED", "GENERATING"])).limit(1))
    if existing:
        return existing
    positive, negative = portrait_prompt(character, campaign)
    previous_seed = (character.attributes or {}).get("portrait_seed")
    from app.services.image_provider import new_seed
    seed = new_seed() if new_identity_seed else previous_seed if isinstance(previous_seed, int) else stable_seed(character.id)
    job = PortraitJob(campaign_id=campaign.id, branch_id=character.branch_id,
                      character_id=character.id, provider=profile.provider, status="QUEUED",
                      metadata_json={"prompt": positive, "negative_prompt": negative, "seed": seed,
                                     "checkpoint": profile.checkpoint, "workflow": profile.workflow,
                                     "width": profile.width, "height": profile.height, "steps": profile.steps,
                                     "cfg": profile.cfg, "sampler": profile.sampler, "scheduler": profile.scheduler},
                      next_attempt_at=datetime.now(UTC))
    session.add(job)
    await session.flush()
    character.attributes = {**(character.attributes or {}), "avatar_job": str(job.id),
                            "portrait_seed": seed, "importance": importance_for(character)}
    return job


async def queue_automatic_portraits(session: AsyncSession, campaign: Campaign, branch_id: UUID) -> None:
    profile = await image_profile(session)
    if not profile or not profile.enabled or profile.provider not in {"comfyui", "ai_horde"}:
        return
    enabled = {"RECURRING": profile.auto_recurring, "MAJOR": profile.auto_major,
               "COMPANION": profile.auto_companion, "MINOR": profile.auto_minor}
    people = (await session.scalars(select(Character).where(
        Character.campaign_id == campaign.id, Character.branch_id == branch_id,
        Character.visibility == "PLAYER_KNOWN"))).all()
    changed = False
    for person in people:
        if person.name == campaign.protagonist_name or (person.attributes or {}).get("avatar_url"):
            continue
        previous = await session.scalar(select(PortraitJob.id).where(
            PortraitJob.character_id == person.id).limit(1))
        if previous:
            continue
        importance = importance_for(person)
        if not enabled.get(importance, False):
            continue
        try:
            await enqueue_portrait(session, campaign, person)
            changed = True
        except ValueError:
            logger.warning("Automatic portrait skipped because image settings are incomplete")
            break
    if changed:
        await session.commit()


async def process_one_portrait() -> None:
    async with SessionLocal() as session:
        now = datetime.now(UTC)
        job = await session.scalar(select(PortraitJob).where(
            PortraitJob.status.in_(["QUEUED", "GENERATING"]),
            PortraitJob.next_attempt_at <= now).order_by(PortraitJob.created_at).limit(1))
        if not job:
            return
        profile = await image_profile(session)
        if not profile or not profile.enabled or profile.provider != job.provider:
            job.next_attempt_at = now + timedelta(minutes=2)
            await session.commit()
            return
        character = await session.get(Character, job.character_id)
        if not character:
            job.status = "CANCELLED"
            await session.commit()
            return
        if job.status == "GENERATING" and now - job.created_at > timedelta(minutes=20):
            job.status = "FAILED"
            job.error = "Portrait generation timed out. Retry from the character panel."
            await session.commit()
            return
        try:
            if job.status == "QUEUED":
                if job.provider == "comfyui":
                    client = ComfyUIImageProvider(profile.base_url)
                    meta = job.metadata_json
                    job.remote_job_id = await client.generate(workflow_for(
                        profile, meta["prompt"], meta["negative_prompt"], meta["seed"]))
                else:
                    job.remote_job_id = await request_horde(job.metadata_json["prompt"])
                job.status = "GENERATING"
                job.error = ""
                job.next_attempt_at = now + timedelta(seconds=5)
            else:
                image = await (ComfyUIImageProvider(profile.base_url).result(job.remote_job_id)
                               if job.provider == "comfyui" else horde_result(job.remote_job_id))
                if image is None:
                    job.next_attempt_at = now + timedelta(seconds=8)
                else:
                    job.image_path = store_portrait(image, job.campaign_id, job.character_id, job.id)
                    job.status = "COMPLETE"
                    attributes = dict(character.attributes or {})
                    attributes["avatar_url"] = f"/api/portraits/{job.id}"
                    attributes["portrait_seed"] = job.metadata_json["seed"]
                    attributes.pop("avatar_job", None)
                    character.attributes = attributes
            await session.commit()
        except (httpx.HTTPError, ValueError, OSError, KeyError) as exc:
            job.attempts += 1
            job.error = str(exc)[:500]
            if job.status == "QUEUED" and job.attempts < 5:
                job.next_attempt_at = now + timedelta(seconds=min(300, 30 * 2**(job.attempts - 1)))
            elif job.status == "GENERATING" and isinstance(exc, httpx.HTTPError):
                job.next_attempt_at = now + timedelta(seconds=60)
            else:
                job.status = "FAILED"
                attributes = dict(character.attributes or {})
                attributes.pop("avatar_job", None)
                character.attributes = attributes
            await session.commit()
            logger.warning("Portrait job %s: %s", job.id, type(exc).__name__)


async def portrait_worker() -> None:
    while True:
        try:
            await process_one_portrait()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Portrait worker tick failed")
        await asyncio.sleep(5)
