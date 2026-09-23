import json
import platform
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secrets import get_deepseek_api_key, save_deepseek_api_key
from app.db.models import (
    Branch,
    Campaign,
    CampaignSummary,
    CanonRule,
    Character,
    CharacterRelationship,
    Checkpoint,
    Event,
    Faction,
    Item,
    Location,
    Memory,
    MessageVersion,
    ModelProfile,
    Objective,
    Secret,
    Turn,
)
from app.db.session import get_session
from app.llm.base import ModelUnavailable
from app.llm.gateway import DeepSeekProvider, mlx_base_url
from app.llm.ollama import OllamaProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.schemas import (
    BranchCreate,
    CampaignCreate,
    CampaignRename,
    DeepSeekModelsRequest,
    MLXStartRequest,
    ModelSettingsUpdate,
    RewindRequest,
    TurnCreate,
    TurnEdit,
    WorldEnhanceRequest,
)
from app.services.campaign_service import create_campaign
from app.services.canon_guard import check_narrative
from app.services.context_builder import history_for_branch
from app.services.export_service import export_campaign, import_campaign
from app.services.meta_commands import apply_meta_command
from app.services.narration import clean_history_narration
from app.services.state_service import apply_interpretation, capture_snapshot, restore_snapshot
from app.services.summary_service import update_campaign_summary
from app.services.timeline import fork_branch, rewind_branch
from app.services.turn_service import _interpret, active_profile, provider_for_profile, stream_turn

router = APIRouter()


def _mlx_supported() -> bool:
    apple_silicon = platform.machine().casefold() in {"arm64", "aarch64"}
    return apple_silicon and (platform.system() == "Darwin" or (
        platform.system() == "Linux" and settings.host_mlx_supported
    ))


def _utc_iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


async def _campaign(session: AsyncSession, campaign_id: UUID) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if not campaign:
        raise HTTPException(404, "Campaign not found.")
    return campaign


async def _branch(session: AsyncSession, campaign: Campaign, branch_id: UUID | None = None) -> Branch:
    branch = await session.get(Branch, branch_id or campaign.active_branch_id) if (branch_id or campaign.active_branch_id) else None
    if not branch or branch.campaign_id != campaign.id:
        branch = await session.scalar(select(Branch).where(Branch.campaign_id == campaign.id).order_by(Branch.created_at).limit(1))
    if not branch:
        raise HTTPException(404, "Campaign timeline not found.")
    return branch


def _model_status(profile: ModelProfile | None) -> dict:
    if not profile:
        return {"provider": "mlx", "model": "lukey03/Qwen3.5-9B-abliterated-MLX-4bit", "status": "not_configured"}
    return {"provider": profile.provider, "model": profile.model,
            "base_url": mlx_base_url(profile.base_url) if profile.provider == "mlx" else profile.base_url,
            "context_window": profile.context_window, "response_length": profile.response_length,
            "temperature": profile.temperature}


async def _detail(session: AsyncSession, campaign: Campaign, branch: Branch) -> dict:
    turns = await history_for_branch(session, branch.head_turn_id, limit=180)
    visible_turns = jsonable_encoder(turns)
    for turn in visible_turns:
        turn["gm_response"] = clean_history_narration(turn.get("gm_response") or "")
    characters = list((await session.scalars(select(Character).where(
        Character.branch_id == branch.id, Character.visibility != "GM_ONLY").order_by(Character.name))).all())
    locations = list((await session.scalars(select(Location).where(
        Location.branch_id == branch.id, Location.visibility != "GM_ONLY").order_by(Location.name))).all())
    factions = list((await session.scalars(select(Faction).where(
        Faction.branch_id == branch.id, Faction.visibility != "GM_ONLY").order_by(Faction.name))).all())
    items = list((await session.scalars(select(Item).where(
        Item.branch_id == branch.id, Item.visibility != "GM_ONLY").order_by(Item.name))).all())
    objectives = list((await session.scalars(select(Objective).where(
        Objective.branch_id == branch.id, Objective.visibility != "GM_ONLY").order_by(Objective.status, Objective.title))).all())
    events = list((await session.scalars(select(Event).where(
        Event.branch_id == branch.id, Event.visibility != "GM_ONLY").order_by(Event.created_at.desc()).limit(40))).all())
    memories = list((await session.scalars(select(Memory).where(
        Memory.branch_id == branch.id, Memory.visibility != "GM_ONLY").order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(40))).all())
    rules = list((await session.scalars(select(CanonRule).where(
        CanonRule.campaign_id == campaign.id,
        (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch.id)),
        CanonRule.visibility != "GM_ONLY").order_by(CanonRule.created_at))).all())
    relations = list((await session.scalars(select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch.id, CharacterRelationship.visibility != "GM_ONLY"))).all())
    known_secrets = list((await session.scalars(select(Secret).where(
        Secret.branch_id == branch.id, Secret.visibility != "GM_ONLY"))).all())
    branches = list((await session.scalars(select(Branch).where(Branch.campaign_id == campaign.id).order_by(Branch.created_at))).all())
    char_map = {row.id: row.name for row in characters}
    relationship_data = [{"id": str(row.id), "from": char_map.get(row.from_character_id, "Unknown"),
        "to": char_map.get(row.to_character_id, "Unknown"), "dimensions": row.dimensions, "summary": row.summary}
        for row in relations]
    current_location = branch.current_state.get("current_location", "")
    summary = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch.id, CampaignSummary.summary_type == "campaign"))
    return jsonable_encoder({
        "id": campaign.id, "title": campaign.title, "original_prompt": campaign.original_prompt,
        "constitution": campaign.constitution, "theme": campaign.theme_profile,
        "protagonist_name": campaign.protagonist_name, "genre": campaign.genre,
        "archived": campaign.archived, "created_at": campaign.created_at, "updated_at": campaign.updated_at,
        "active_branch_id": campaign.active_branch_id, "branch": branch,
        "branches": branches, "turns": visible_turns, "current_state": branch.current_state,
        "current_location": current_location, "characters": characters, "locations": locations,
        "factions": factions, "inventory": [row for row in items if row.owner_name.casefold() in {campaign.protagonist_name.casefold(), "player"}],
        "items": items, "relationships": relationship_data, "objectives": objectives,
        "events": events, "memories": memories, "canon_rules": rules,
        "known_secrets": known_secrets, "summary": summary.content if summary else "",
    })


@router.get("/ready", include_in_schema=False)
async def ready(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(503, "Database unavailable.") from exc
    return {"status": "ready"}


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    database = "connected"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = "offline"
    if database == "offline":
        return {"status": "degraded", "database": "offline", "model": {"status": "unknown"}, "version": "0.1.0"}
    profile = await active_profile(session)
    provider = provider_for_profile(profile)
    model = await provider.health()
    model["selection"] = profile.provider if profile else settings.llm_provider
    return {"status": "ok" if database == "connected" else "degraded", "database": database,
            "model": model, "version": "0.1.0"}


@router.get("/system/capabilities")
async def system_capabilities():
    return {"mlx_supported": _mlx_supported(), "mlx_default_base_url": mlx_base_url()}


@router.get("/settings/mlx/runtime")
async def mlx_runtime():
    if not _mlx_supported():
        raise HTTPException(422, "MLX requires Apple Silicon running macOS.")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{settings.mlx_launcher_base_url}/status")
            response.raise_for_status()
            payload = response.json()
            if payload.get("service") != "boundless-mlx-host":
                raise ValueError("Unexpected host service")
            return {"launcher_available": True, **payload}
    except (httpx.HTTPError, ValueError):
        return {"launcher_available": False, "server_status": "unknown", "models": [],
                "detail": "The Mac launcher is offline. Run make mlx-host from the project folder."}


@router.post("/settings/mlx/start")
async def start_mlx(payload: MLXStartRequest):
    if not _mlx_supported():
        raise HTTPException(422, "MLX requires Apple Silicon running macOS.")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            status = await client.get(f"{settings.mlx_launcher_base_url}/status")
            status.raise_for_status()
            if status.json().get("service") != "boundless-mlx-host":
                raise ValueError("Unexpected host service")
            response = await client.post(
                f"{settings.mlx_launcher_base_url}/start",
                json={"model": payload.model},
                headers={"X-Boundless-Launcher": "start"},
            )
        if response.is_error:
            detail = response.json().get("detail", "Could not start MLX.")
            raise HTTPException(response.status_code, detail)
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "The Mac launcher is offline. Run make mlx-host from the project folder.") from exc


@router.get("/settings/ollama/models")
async def list_ollama_models(base_url: str = Query(default="http://127.0.0.1:11434", max_length=400)):
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "Ollama endpoint must begin with http:// or https://.")
    try:
        return {"models": await OllamaProvider(base_url=base_url).list_models()}
    except ModelUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/settings/openai-compatible/models")
async def list_compatible_models(base_url: str = Query(min_length=8, max_length=400)):
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "Endpoint must begin with http:// or https://.")
    status = await OpenAICompatibleProvider(base_url=base_url).health()
    if status.get("status") != "connected":
        raise HTTPException(503, status.get("detail", "Could not load models from this endpoint."))
    return {"models": status.get("available_models", [])}


@router.post("/settings/deepseek/models")
async def list_deepseek_models(payload: DeepSeekModelsRequest):
    try:
        return {"models": await DeepSeekProvider(api_key=payload.api_key).list_models()}
    except ModelUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/settings/model")
async def read_model_settings(session: AsyncSession = Depends(get_session)):
    profile = await active_profile(session)
    provider = provider_for_profile(profile)
    status = await provider.health()
    return {**_model_status(profile), "health": status,
            "api_key_configured": bool(get_deepseek_api_key())}


@router.put("/settings/model")
async def write_model_settings(payload: ModelSettingsUpdate, session: AsyncSession = Depends(get_session)):
    if payload.provider == "mlx" and not _mlx_supported():
        raise HTTPException(422, "MLX requires Apple Silicon running macOS.")
    if payload.provider == "deepseek":
        if payload.base_url.rstrip("/") != "https://api.deepseek.com":
            raise HTTPException(422, "DeepSeek uses https://api.deepseek.com.")
        if payload.api_key:
            try:
                save_deepseek_api_key(payload.api_key)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
    profile = await active_profile(session)
    if not profile:
        profile = ModelProfile()
        session.add(profile)
    profile.name = "DeepSeek" if payload.provider == "deepseek" else "Custom" if payload.provider == "openai-compatible" else payload.provider.title()
    profile.provider = payload.provider
    profile.base_url = mlx_base_url(payload.base_url) if payload.provider == "mlx" else payload.base_url
    profile.model = payload.model.strip()
    profile.context_window = payload.context_window
    profile.response_length = payload.response_length
    profile.temperature = payload.temperature
    profile.active = True
    await session.commit()
    await session.refresh(profile)
    return _model_status(profile)


@router.get("/campaigns")
async def list_campaigns(include_archived: bool = False, session: AsyncSession = Depends(get_session)):
    query = select(Campaign).order_by(Campaign.updated_at.desc())
    if not include_archived:
        query = query.where(Campaign.archived.is_(False))
    campaigns = list((await session.scalars(query)).all())
    result = []
    for campaign in campaigns:
        branches = list((await session.scalars(select(Branch).where(
            Branch.campaign_id == campaign.id).order_by(Branch.created_at))).all())
        branch = next((row for row in branches if row.id == campaign.active_branch_id), branches[-1] if branches else None)
        history = await history_for_branch(session, branch.head_turn_id, limit=1) if branch else []
        last_turn = history[-1] if history else None
        result.append({
            "id": str(campaign.id), "title": campaign.title, "protagonist_name": campaign.protagonist_name,
            "genre": campaign.genre, "premise": campaign.constitution.get("premise", campaign.original_prompt)[:260],
            "current_location": branch.current_state.get("current_location", "") if branch else "",
            "turn_count": last_turn.turn_index if last_turn else 0,
            "last_played": _utc_iso(last_turn.created_at if last_turn else campaign.updated_at),
            "theme": campaign.theme_profile, "archived": campaign.archived,
            "branch_id": str(branch.id) if branch else None,
        })
    return result


@router.post("/campaigns", status_code=201)
async def new_campaign(payload: CampaignCreate, session: AsyncSession = Depends(get_session)):
    campaign, branch = await create_campaign(session, payload)
    return await _detail(session, campaign, branch)


@router.post("/campaigns/enhance")
async def enhance_world(payload: WorldEnhanceRequest, session: AsyncSession = Depends(get_session)):
    profile = await active_profile(session)
    provider = provider_for_profile(profile)
    messages = [
        {"role": "system", "content": (
            "You are helping a player make a fictional world premise more vivid. Preserve every stated fact, "
            "rule, character identity, point of view, and limitation. Add concrete detail only in the direction "
            "the player asks for. Do not begin the story, introduce a forced plot, or contradict the premise. "
            "Return only the revised premise, with no preface or commentary, and keep it under 30,000 characters."
        )},
        {"role": "user", "content": json.dumps({"current_premise": payload.prompt, "requested_detail": payload.direction}, ensure_ascii=False)},
    ]
    try:
        enhanced = await provider.complete(messages, temperature=profile.temperature if profile else settings.llm_temperature,
                                           max_tokens=5000)
    except ModelUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    enhanced = enhanced.strip()
    if not enhanced:
        raise HTTPException(503, "The model returned an empty enhancement.")
    if len(enhanced) > 30_000:
        raise HTTPException(422, "The enhanced world is too long. Shorten the starting premise and try again.")
    return {"prompt": enhanced}


@router.get("/campaigns/{campaign_id}")
async def campaign_detail(campaign_id: UUID, branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    return await _detail(session, campaign, branch)


@router.patch("/campaigns/{campaign_id}")
async def rename_campaign(campaign_id: UUID, payload: CampaignRename, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    campaign.title = payload.title.strip()
    campaign.updated_at = datetime.now(UTC)
    await session.commit()
    return {"id": str(campaign.id), "title": campaign.title}


@router.post("/campaigns/{campaign_id}/archive")
async def archive_campaign(campaign_id: UUID, archived: bool = True, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    campaign.archived = archived
    await session.commit()
    return {"id": str(campaign.id), "archived": campaign.archived}


@router.delete("/campaigns/{campaign_id}", status_code=204)
async def delete_campaign(campaign_id: UUID, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    await session.delete(campaign)
    await session.commit()
    return None


@router.post("/campaigns/{campaign_id}/duplicate", status_code=201)
async def duplicate_campaign(campaign_id: UUID, session: AsyncSession = Depends(get_session)):
    data = await export_campaign(session, campaign_id)
    data["campaign"]["title"] = f"{data['campaign']['title']} — copy"
    copy = await import_campaign(session, data)
    branch = await _branch(session, copy)
    return await _detail(session, copy, branch)


@router.get("/campaigns/{campaign_id}/export")
async def download_campaign(campaign_id: UUID, session: AsyncSession = Depends(get_session)):
    try:
        data = await export_campaign(session, campaign_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    title = re.sub(r"[^a-zA-Z0-9_-]+", "-", data["campaign"]["title"]).strip("-")[:80] or "campaign"
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{title}.boundless.json"'})


@router.post("/campaigns/import", status_code=201)
async def upload_campaign(file: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    content = await file.read(20 * 1024 * 1024 + 1)
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "Campaign export exceeds the 20 MB import limit.")
    try:
        data = json.loads(content)
        campaign = await import_campaign(session, data)
    except Exception as exc:
        await session.rollback()
        raise HTTPException(422, f"Campaign import failed: {str(exc)[:500]}") from exc
    branch = await _branch(session, campaign)
    return await _detail(session, campaign, branch)


@router.post("/campaigns/{campaign_id}/branches", status_code=201)
async def new_branch(campaign_id: UUID, payload: BranchCreate, branch_id: UUID | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    source = await _branch(session, campaign, branch_id)
    try:
        branch = await fork_branch(session, campaign.id, source.id, payload.name, payload.turn_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    campaign.active_branch_id = branch.id
    await session.commit()
    return {"id": str(branch.id), "name": branch.name, "parent_branch_id": str(branch.parent_branch_id), "head_turn_id": str(branch.head_turn_id) if branch.head_turn_id else None}


@router.post("/campaigns/{campaign_id}/branches/{branch_id}/activate")
async def activate_branch(campaign_id: UUID, branch_id: UUID, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    campaign.active_branch_id = branch.id
    await session.commit()
    return {"id": str(branch.id), "name": branch.name}


@router.post("/campaigns/{campaign_id}/rewind")
async def rewind(campaign_id: UUID, payload: RewindRequest, branch_id: UUID | None = Query(default=None), session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    try:
        changed = await rewind_branch(session, campaign.id, branch.id, payload.turn_id, payload.turn_index)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await _detail(session, campaign, changed)


@router.post("/campaigns/{campaign_id}/turns/stream")
async def create_turn(campaign_id: UUID, payload: TurnCreate, request: Request, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, payload.branch_id)
    action_for_gm, meta = await apply_meta_command(session, campaign, payload.action)

    async def event_stream():
        try:
            async for event in stream_turn(session, campaign, branch, action_for_gm, payload.instruction):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                if event.get("type") == "complete":
                    profile = await active_profile(session)
                    await update_campaign_summary(session, provider_for_profile(profile), campaign,
                        branch.id, branch.head_turn_id, int(event.get("turn_index", 0)))
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)[:600]})}\n\n"
        yield "data: [DONE]\n\n"

    headers = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.patch("/turns/{turn_id}")
async def edit_turn(turn_id: UUID, payload: TurnEdit, session: AsyncSession = Depends(get_session)):
    turn = await session.get(Turn, turn_id)
    if not turn:
        raise HTTPException(404, "Turn not found.")
    campaign = await _campaign(session, turn.campaign_id)
    branch = await _branch(session, campaign, turn.branch_id)
    before = await session.scalar(select(Checkpoint).where(
        Checkpoint.branch_id == branch.id, Checkpoint.turn_id == turn.id,
        Checkpoint.turn_index < turn.turn_index).order_by(Checkpoint.created_at.desc()))
    if not before:
        raise HTTPException(409, "The checkpoint before this response is missing.")
    await restore_snapshot(session, branch, before.state_snapshot)
    violation = check_narrative(payload.content, campaign.constitution, [
        {"rule_type": row.rule_type, "strength": row.strength, "exceptions": row.exceptions}
        for row in (await session.scalars(select(CanonRule).where(CanonRule.campaign_id == campaign.id))).all()
    ])
    if violation:
        await session.rollback()
        raise HTTPException(422, violation)
    turn.gm_response = payload.content.strip()
    profile = await active_profile(session)
    provider = provider_for_profile(profile)
    interpretation = await _interpret(session, provider, campaign, branch, turn, branch.current_state or {})
    await apply_interpretation(session, campaign, branch, turn, interpretation)
    versions = list((await session.scalars(select(MessageVersion).where(MessageVersion.turn_id == turn.id, MessageVersion.role == "gm"))).all())
    for row in versions:
        row.active = False
    session.add(MessageVersion(turn_id=turn.id, role="gm", version=max((row.version for row in versions), default=0) + 1,
                               content=turn.gm_response, active=True))
    turn.status = "complete"
    branch.head_turn_id = turn.id
    session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
        turn_index=turn.turn_index, state_snapshot=await capture_snapshot(session, branch)))
    await session.commit()
    return await _detail(session, campaign, branch)


@router.post("/turns/{turn_id}/regenerate")
async def regenerate_turn(turn_id: UUID, payload: TurnCreate, request: Request, session: AsyncSession = Depends(get_session)):
    turn = await session.get(Turn, turn_id)
    if not turn:
        raise HTTPException(404, "Turn not found.")
    campaign = await _campaign(session, turn.campaign_id)
    branch = await _branch(session, campaign, turn.branch_id)

    async def event_stream():
        async for event in stream_turn(session, campaign, branch, turn.player_action,
                                      payload.instruction or payload.action, existing_turn=turn):
            if await request.is_disconnected():
                break
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/campaigns/{campaign_id}/search")
async def search_lore(campaign_id: UUID, q: str = Query(min_length=2, max_length=200), branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    pattern = f"%{q.strip()}%"
    matches = []
    for model, label, columns in (
        (Character, "character", (Character.name, Character.role, Character.personality)),
        (Location, "location", (Location.name, Location.description, Location.region)),
        (Faction, "faction", (Faction.name, Faction.description)),
        (Event, "event", (Event.content,)),
        (Memory, "memory", (Memory.content,)),
        (Item, "item", (Item.name, Item.significance)),
    ):
        filters = [column.ilike(pattern) for column in columns]
        rows = (await session.scalars(select(model).where(
            model.campaign_id == campaign.id, model.branch_id == branch.id,
            model.visibility != "GM_ONLY", or_(*filters),
        ).limit(20))).all()
        matches.extend({"type": label, "id": str(row.id), "title": getattr(row, "name", "") or getattr(row, "content", "")[:120],
                        "detail": getattr(row, "description", "") or getattr(row, "content", "")}
                       for row in rows)
    return matches[:60]


@router.get("/campaigns/{campaign_id}/lore")
async def campaign_lore(campaign_id: UUID, branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    return await _detail(session, campaign, branch)
