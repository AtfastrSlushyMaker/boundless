import json
import logging
import platform
import re
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secrets import get_deepseek_api_key, save_deepseek_api_key
from app.db.models import (
    Ability,
    Branch,
    Campaign,
    CampaignNote,
    CampaignSummary,
    CanonRule,
    Character,
    CharacterAlias,
    CharacterFact,
    CharacterRelationship,
    Checkpoint,
    Event,
    Faction,
    FactionRelationship,
    ImageProfile,
    Item,
    Location,
    Memory,
    MessageVersion,
    ModelProfile,
    Objective,
    PortraitJob,
    PostTurnJob,
    RelationshipEvent,
    Secret,
    Turn,
)
from app.db.session import get_session
from app.llm.base import ModelUnavailable
from app.llm.gateway import DeepSeekProvider, mlx_base_url
from app.llm.ollama import OllamaProvider
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.router import ROLES, is_hosted, route, visual_model
from app.schemas import (
    BranchCreate,
    CampaignCreate,
    CampaignModeUpdate,
    CampaignRename,
    CampaignThemeUpdate,
    CharacterUpdate,
    DeepSeekModelsRequest,
    ImageSettingsUpdate,
    MLXStartRequest,
    ModelRolesUpdate,
    ModelSettingsUpdate,
    NoteWrite,
    PortraitRequest,
    RelationshipUpdate,
    RepairApply,
    RewindRequest,
    TurnCreate,
    TurnEdit,
    WorldEnhanceRequest,
)
from app.services.campaign_service import create_campaign, refresh_setup_from_premise
from app.services.canon_guard import check_narrative, player_death_stated
from app.services.character_store import StoreLog, add_alias, ensure_identity_rows
from app.services.choice_service import suggest_choices
from app.services.constitution import derive_theme
from app.services.context_builder import history_for_branch
from app.services.export_service import export_campaign, import_campaign
from app.services.identity import normalize_reference
from app.services.image_provider import (
    MAX_IMAGE_BYTES,
    ComfyUIImageProvider,
    portrait_file,
    store_portrait,
    validate_endpoint,
)
from app.services.meta_commands import apply_meta_command
from app.services.narration import clean_history_narration
from app.services.portrait_jobs import (
    enqueue_portrait,
    image_profile,
    profile_dict,
)
from app.services.post_turn import describe_jobs, enqueue, run_jobs
from app.services.repair_service import analyze_campaign, apply_repair
from app.services.state_service import (
    apply_interpretation,
    capture_snapshot,
    player_says_alive,
    restore_snapshot,
)
from app.services.timeline import fork_branch, rewind_branch
from app.services.turn_service import (
    active_profile,
    interpret_turn,
    provider_for_profile,
    stream_turn,
)
from app.services.world_index import index_people_from_narration, player_location_for_turn

router = APIRouter()
logger = logging.getLogger(__name__)


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


async def _ensure_branch_choices(session: AsyncSession, campaign: Campaign, branch: Branch) -> None:
    if campaign.game_mode != "guided" or not branch.head_turn_id:
        return
    latest = await session.get(Turn, branch.head_turn_id)
    if latest and latest.status == "complete" and not latest.suggested_actions:
        profile = await active_profile(session)
        latest.suggested_actions = await suggest_choices(
            provider_for_profile(profile), latest.gm_response, latest.player_action, campaign.protagonist_name)


def _model_status(profile: ModelProfile | None) -> dict:
    if not profile:
        return {"provider": settings.llm_provider, "model": settings.llm_model,
                "base_url": mlx_base_url(settings.llm_base_url) if settings.llm_provider == "mlx" else settings.llm_base_url,
                "context_window": settings.llm_context_window, "response_length": "standard",
                "temperature": settings.llm_temperature, "status": "not_configured"}
    return {"provider": profile.provider, "model": profile.model,
            "base_url": mlx_base_url(profile.base_url) if profile.provider == "mlx" else profile.base_url,
            "context_window": profile.context_window, "response_length": profile.response_length,
            "temperature": profile.temperature}


NOISE_REASONS = {"first appeared in the story", "appeared in the story"}


def _history_entries(relation: CharacterRelationship, events: list[RelationshipEvent]) -> list[dict]:
    rows = [{"id": str(event.id), "turn_index": event.turn_index, "turn_id": str(event.turn_id) if event.turn_id else None,
             "dimension": event.dimension, "before": event.before_value, "after": event.after_value,
             "delta": event.delta, "reason": event.reason, "location": event.location}
            for event in events if event.reason or event.delta]
    return sorted(rows, key=lambda row: (row["turn_index"] if row["turn_index"] is not None else -1))


async def _detail(session: AsyncSession, campaign: Campaign, branch: Branch) -> dict:
    turns = await history_for_branch(session, branch.head_turn_id, limit=180)
    visible_turns = []
    for turn in turns:
        row = jsonable_encoder(turn, exclude={"diagnostics", "state_delta"})
        row["gm_response"] = clean_history_narration(turn.gm_response or "")
        row["changes"] = (turn.state_delta or {}).get("changes", [])
        row["metrics"] = (turn.diagnostics or {}).get("metrics", {})
        visible_turns.append(row)
    characters = list((await session.scalars(select(Character).where(
        Character.branch_id == branch.id, Character.visibility == "PLAYER_KNOWN").order_by(Character.name))).all())
    aliases: dict = {}
    for row in (await session.scalars(select(CharacterAlias).where(CharacterAlias.branch_id == branch.id))).all():
        aliases.setdefault(row.character_id, []).append({"alias": row.alias, "type": row.alias_type})
    facts: dict = {}
    for row in (await session.scalars(select(CharacterFact).where(
            CharacterFact.branch_id == branch.id, CharacterFact.active.is_(True),
            CharacterFact.visibility == "PLAYER_KNOWN").order_by(CharacterFact.created_at))).all():
        facts.setdefault(row.character_id, []).append({"id": str(row.id), "content": row.content, "type": row.fact_type,
                                                       "certainty": row.certainty, "provenance": row.provenance,
                                                       "turn_index": row.first_seen_turn_index})
    abilities: dict = {}
    for row in (await session.scalars(select(Ability).where(Ability.branch_id == branch.id,
                                                             Ability.visibility == "PLAYER_KNOWN").order_by(Ability.created_at))).all():
        abilities.setdefault(row.character_id, []).append({"id": str(row.id), "name": row.name, "description": row.description,
                                                           "source": row.source, "status": row.status,
                                                           "acquired_turn_index": row.acquired_turn_index,
                                                           "limitations": row.limitations})
    character_rows = []
    for character in characters:
        row = jsonable_encoder(character, exclude={"provenance"})
        row["aliases"] = [entry for entry in aliases.get(character.id, [])
                          if normalize_reference(entry["alias"]) != normalize_reference(character.name)]
        row["facts"] = facts.get(character.id, [])
        row["abilities"] = abilities.get(character.id, [])
        character_rows.append(row)
    locations = list((await session.scalars(select(Location).where(
        Location.branch_id == branch.id, Location.visibility == "PLAYER_KNOWN").order_by(Location.name))).all())
    factions = list((await session.scalars(select(Faction).where(
        Faction.branch_id == branch.id, Faction.visibility == "PLAYER_KNOWN").order_by(Faction.name))).all())
    items = list((await session.scalars(select(Item).where(
        Item.branch_id == branch.id, Item.visibility == "PLAYER_KNOWN").order_by(Item.name))).all())
    faction_relations = list((await session.scalars(select(FactionRelationship).where(
        FactionRelationship.branch_id == branch.id))).all())
    objectives = list((await session.scalars(select(Objective).where(
        Objective.branch_id == branch.id, Objective.visibility == "PLAYER_KNOWN").order_by(Objective.status, Objective.title))).all())
    events = list((await session.scalars(select(Event).where(
        Event.branch_id == branch.id, Event.visibility == "PLAYER_KNOWN").order_by(Event.created_at.desc()).limit(80))).all())
    memories = list((await session.scalars(select(Memory).where(
        Memory.branch_id == branch.id, Memory.visibility == "PLAYER_KNOWN", Memory.memory_type != "TURN",
    ).order_by(Memory.importance.desc(), Memory.created_at.desc()).limit(40))).all())
    rules = list((await session.scalars(select(CanonRule).where(
        CanonRule.campaign_id == campaign.id,
        (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch.id)),
        CanonRule.visibility == "PLAYER_KNOWN").order_by(CanonRule.created_at))).all())
    relations = list((await session.scalars(select(CharacterRelationship).where(
        CharacterRelationship.branch_id == branch.id, CharacterRelationship.visibility == "PLAYER_KNOWN"))).all())
    relation_events: dict = {}
    for event in (await session.scalars(select(RelationshipEvent).where(
            RelationshipEvent.branch_id == branch.id, RelationshipEvent.visibility != "GM_ONLY"))).all():
        relation_events.setdefault(event.relationship_id, []).append(event)
    known_secrets = list((await session.scalars(select(Secret).where(
        Secret.branch_id == branch.id, Secret.visibility == "PLAYER_KNOWN"))).all())
    branches = list((await session.scalars(select(Branch).where(Branch.campaign_id == campaign.id).order_by(Branch.created_at))).all())
    char_map = {row.id: row.name for row in characters}
    relationship_data = []
    for row in relations:
        if row.from_character_id not in char_map or row.to_character_id not in char_map:
            continue
        dimensions = {key: value for key, value in (row.dimensions or {}).items() if key != "history"}
        history = [entry for entry in (row.dimensions or {}).get("history", []) or []
                   if isinstance(entry, dict) and str(entry.get("reason", "")).casefold() not in NOISE_REASONS]
        relationship_data.append({"id": str(row.id), "from": char_map[row.from_character_id], "to": char_map[row.to_character_id],
                                  "from_id": str(row.from_character_id), "to_id": str(row.to_character_id),
                                  "dimensions": {**dimensions, "history": history}, "summary": row.summary,
                                  "events": _history_entries(row, relation_events.get(row.id, []))})
    current_location = branch.current_state.get("current_location", "")
    summary = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch.id, CampaignSummary.summary_type == "campaign"))
    player_key = normalize_reference(campaign.protagonist_name)
    return jsonable_encoder({
        "id": campaign.id, "title": campaign.title, "original_prompt": campaign.original_prompt,
        "constitution": {key: value for key, value in (campaign.constitution or {}).items()},
        "theme": campaign.theme_profile,
        "protagonist_name": campaign.protagonist_name, "genre": campaign.genre, "game_mode": campaign.game_mode,
        "archived": campaign.archived, "created_at": campaign.created_at, "updated_at": campaign.updated_at,
        "active_branch_id": campaign.active_branch_id, "branch": branch,
        "branches": branches, "turns": visible_turns, "current_state": branch.current_state,
        "current_location": current_location, "characters": character_rows, "locations": locations,
        "factions": _faction_rows(factions, characters, faction_relations),
        "inventory": [row for row in items if normalize_reference(row.owner_name) in {player_key, "player"}],
        "items": items, "relationships": relationship_data, "objectives": objectives,
        "events": events, "memories": memories, "canon_rules": rules,
        "known_secrets": known_secrets, "summary": summary.content if summary else "",
        "summary_state": {"through_turn_index": summary.through_turn_index, "last_attempt_turn_index": summary.last_attempt_turn_index,
                          "last_success_at": summary.last_success_at, "last_error": summary.last_error,
                          "method": summary.method} if summary else None,
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


def _faction_rows(factions, characters, relations) -> list[dict]:
    """Each group with its kind, its members (by id, with their role and status), and its stance to other groups."""
    rows = []
    for faction in factions:
        names = {normalize_reference(name) for name in [faction.name, *(faction.aliases or [])]}
        members = []
        for person in characters:
            for entry in (person.attributes or {}).get("affiliations") or []:
                if isinstance(entry, dict) and normalize_reference(entry.get("name", "")) in names:
                    members.append({"id": str(person.id), "role": entry.get("role", ""), "status": entry.get("status", "member")})
                    break
        stance = [{"to": row.to_faction if normalize_reference(row.from_faction) in names else row.from_faction,
                   "relation": row.relation, "details": row.details}
                  for row in relations if row.relation and row.relation != "unknown"
                  and (normalize_reference(row.from_faction) in names or normalize_reference(row.to_faction) in names)]
        rows.append({"id": str(faction.id), "name": faction.name, "kind": faction.kind or "faction",
                     "description": faction.description, "motives": faction.motives or [], "aliases": faction.aliases or [],
                     "members": members, "relations": stance})
    return rows


async def _roles_payload(session: AsyncSession) -> dict:
    roles = []
    for role in ROLES:
        if role == "narrator":
            continue
        own = await session.scalar(select(ModelProfile).where(ModelProfile.role == role).order_by(ModelProfile.updated_at.desc()))
        routed = await route(session, role, provider_for_profile) if role != "state_fallback" else None
        roles.append({
            "role": role, "inherit": not (own and own.active),
            "provider": own.provider if own else "mlx", "base_url": own.base_url if own else "",
            "model": own.model if own else "", "temperature": own.temperature if own else 0.1,
            "context_window": own.context_window if own else 32768,
            "hosted": is_hosted(own.provider, own.base_url) if own and own.active else (routed.hosted if routed else False),
            "effective": routed.describe() if routed else (
                {"provider": own.provider, "model": own.model, "hosted": is_hosted(own.provider, own.base_url)} if own and own.active else None),
        })
    narrator = await active_profile(session)
    return {"narrator": _model_status(narrator), "narrator_hosted": is_hosted(narrator.provider, narrator.base_url) if narrator else False,
            "roles": roles, "hosted_fallback_enabled": any(row["role"] == "state_fallback" and not row["inherit"] for row in roles)}


@router.get("/settings/roles")
async def read_model_roles(session: AsyncSession = Depends(get_session)):
    return await _roles_payload(session)


@router.put("/settings/roles")
async def write_model_roles(payload: ModelRolesUpdate, session: AsyncSession = Depends(get_session)):
    for update in payload.roles:
        own = await session.scalar(select(ModelProfile).where(ModelProfile.role == update.role))
        enabled = not update.inherit and (update.role != "state_fallback" or payload.hosted_fallback_enabled)
        if not enabled:
            if own:
                own.active = False
            continue
        if update.provider == "mlx" and not _mlx_supported():
            raise HTTPException(422, "MLX requires Apple Silicon running macOS.")
        if update.provider == "deepseek" and update.api_key:
            try:
                save_deepseek_api_key(update.api_key)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        if own is None:
            own = ModelProfile(role=update.role)
            session.add(own)
        own.name = f"{update.role.replace('_', ' ').title()} model"
        own.provider = update.provider
        own.base_url = "https://api.deepseek.com" if update.provider == "deepseek" else (
            mlx_base_url(update.base_url) if update.provider == "mlx" else update.base_url)
        own.model = update.model.strip()
        own.temperature = update.temperature
        own.context_window = update.context_window
        own.active = True
    if not payload.hosted_fallback_enabled:
        fallback = await session.scalar(select(ModelProfile).where(ModelProfile.role == "state_fallback"))
        if fallback:
            fallback.active = False
    await session.commit()
    return await _roles_payload(session)


@router.get("/settings/images")
async def read_image_settings(session: AsyncSession = Depends(get_session)):
    return profile_dict(await image_profile(session))


@router.put("/settings/images")
async def write_image_settings(payload: ImageSettingsUpdate, session: AsyncSession = Depends(get_session)):
    profile = await image_profile(session)
    if profile is None:
        profile = ImageProfile()
        session.add(profile)
    for key, value in payload.model_dump().items():
        setattr(profile, key, value)
    await session.commit()
    return profile_dict(profile)


@router.get("/settings/images/test")
async def test_image_connection(base_url: str = Query(default="", max_length=400)):
    try:
        provider = ComfyUIImageProvider(validate_endpoint(base_url))
        return await provider.health_check()
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {"status": "offline", "detail": str(exc)[:240], "models": []}


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
            "genre": campaign.genre, "game_mode": campaign.game_mode,
            "premise": campaign.constitution.get("premise", campaign.original_prompt)[:260],
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


async def _known_character(session: AsyncSession, campaign_id: UUID, branch_id: UUID,
                           character_id: UUID) -> Character:
    character = await session.get(Character, character_id)
    if not character or character.campaign_id != campaign_id or character.branch_id != branch_id \
            or character.visibility != "PLAYER_KNOWN":
        raise HTTPException(404, "Character not found in this timeline.")
    return character


@router.post("/campaigns/{campaign_id}/characters/{character_id}/avatar")
async def generate_character_avatar(campaign_id: UUID, character_id: UUID, branch_id: UUID,
                                    payload: PortraitRequest | None = None,
                                    session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    await _branch(session, campaign, branch_id)
    character = await _known_character(session, campaign_id, branch_id, character_id)
    from app.services.post_turn import describe_and_store
    from app.services.visual_identity import needs_visual
    if needs_visual(character):
        # Build the look from the story first, so the portrait matches the narration.
        branch = await session.get(Branch, branch_id)
        try:
            routed = await visual_model(session, provider_for_profile)
            await describe_and_store(session, campaign, character, routed.provider,
                                     await history_for_branch(session, branch.head_turn_id, limit=80))
        except Exception:
            logger.warning("Visual description failed before portrait generation", exc_info=True)
    try:
        job = await enqueue_portrait(session, campaign, character,
                                     new_identity_seed=bool(payload and payload.new_seed))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await session.commit()
    return {"done": False, "job_id": str(job.id), "status": job.status}


@router.get("/campaigns/{campaign_id}/characters/{character_id}/avatar")
async def character_avatar_status(campaign_id: UUID, character_id: UUID, branch_id: UUID,
                                  session: AsyncSession = Depends(get_session)):
    await _branch(session, await _campaign(session, campaign_id), branch_id)
    character = await _known_character(session, campaign_id, branch_id, character_id)
    job = await session.scalar(select(PortraitJob).where(
        PortraitJob.character_id == character.id).order_by(PortraitJob.created_at.desc()).limit(1))
    return {"done": job.status == "COMPLETE" if job else bool((character.attributes or {}).get("avatar_url")),
            "job_id": str(job.id) if job else "", "status": job.status if job else "NONE",
            "error": job.error if job and job.status == "FAILED" else "",
            "avatar_url": (character.attributes or {}).get("avatar_url")}


@router.delete("/campaigns/{campaign_id}/characters/{character_id}/avatar")
async def remove_character_avatar(campaign_id: UUID, character_id: UUID, branch_id: UUID,
                                  session: AsyncSession = Depends(get_session)):
    await _branch(session, await _campaign(session, campaign_id), branch_id)
    character = await _known_character(session, campaign_id, branch_id, character_id)
    jobs = (await session.scalars(select(PortraitJob).where(
        PortraitJob.character_id == character.id))).all()
    current_url = (character.attributes or {}).get("avatar_url", "")
    for job in jobs:
        if job.status in {"QUEUED", "GENERATING"}:
            job.status = "CANCELLED"
        if current_url == f"/api/portraits/{job.id}":
            if job.image_path:
                try:
                    portrait_file(job.image_path).unlink(missing_ok=True)
                except ValueError:
                    pass
                job.image_path = ""
            job.status = "CANCELLED"
    attributes = dict(character.attributes or {})
    attributes.pop("avatar_url", None)
    attributes.pop("avatar_job", None)
    character.attributes = attributes
    await session.commit()
    return {"removed": True}


@router.post("/campaigns/{campaign_id}/characters/{character_id}/avatar/upload")
async def upload_character_avatar(campaign_id: UUID, character_id: UUID, branch_id: UUID,
                                  image: UploadFile = File(...), session: AsyncSession = Depends(get_session)):
    await _branch(session, await _campaign(session, campaign_id), branch_id)
    character = await _known_character(session, campaign_id, branch_id, character_id)
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Portrait image exceeds 12 MB.")
    pending = (await session.scalars(select(PortraitJob).where(
        PortraitJob.character_id == character_id,
        PortraitJob.status.in_(["QUEUED", "GENERATING"])))).all()
    for queued_job in pending:
        queued_job.status = "CANCELLED"
    job = PortraitJob(campaign_id=campaign_id, branch_id=branch_id, character_id=character_id,
                      provider="perchance_assisted", status="COMPLETE", metadata_json={"source": "user_upload"},
                      next_attempt_at=datetime.now(UTC))
    session.add(job)
    await session.flush()
    try:
        job.image_path = store_portrait(data, campaign_id, character_id, job.id)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    character.attributes = {**(character.attributes or {}), "avatar_url": f"/api/portraits/{job.id}"}
    await session.commit()
    return {"done": True, "avatar_url": f"/api/portraits/{job.id}"}


@router.get("/portraits/{portrait_id}")
async def serve_portrait(portrait_id: UUID, session: AsyncSession = Depends(get_session)):
    job = await session.get(PortraitJob, portrait_id)
    if not job or job.status != "COMPLETE" or not job.image_path:
        raise HTTPException(404, "Portrait not found.")
    try:
        path = portrait_file(job.image_path)
    except ValueError as exc:
        raise HTTPException(404, "Portrait not found.") from exc
    if not path.is_file():
        raise HTTPException(404, "Portrait file is missing.")
    return FileResponse(path, media_type={".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix.lower()],
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})


@router.patch("/campaigns/{campaign_id}/characters/{character_id}")
async def update_character(campaign_id: UUID, character_id: UUID, branch_id: UUID,
                           payload: CharacterUpdate, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    character = await _known_character(session, campaign_id, branch_id, character_id)
    # Player edits are explicit canon: they may override anything, and model updates cannot undo them.
    character.role = payload.role.strip()
    character.personality = payload.personality.strip()
    character.provenance = {**(character.provenance or {}), "role": "PLAYER_EXPLICIT", "personality": "PLAYER_EXPLICIT",
                            **{key: "PLAYER_EXPLICIT" for key in ("sex", "gender", "pronouns") if getattr(payload, key).strip()}}
    if payload.role.strip():
        await add_alias(session, character, payload.role.strip(), "TITLE", source="player_edit", confidence=1.0)
    attributes = dict(character.attributes or {})
    for key in ("appearance", "sex", "gender", "pronouns"):
        value = getattr(payload, key).strip()
        if value:
            attributes[key] = value
        else:
            attributes.pop(key, None)
    if payload.affiliations is not None:
        # The player's list is final: groups they removed are dropped, the rest become player-owned.
        from app.services.affiliations import ensure_faction, normalize_affiliations
        wanted = {normalize_reference(name) for name in payload.affiliations}
        kept = [dict(entry, source="player") for entry in attributes.get("affiliations") or []
                if isinstance(entry, dict) and normalize_reference(entry.get("name", "")) in wanted]
        kept_keys = {normalize_reference(entry["name"]) for entry in kept}
        added = [entry for entry in normalize_affiliations(payload.affiliations, source="player")
                 if normalize_reference(entry["name"]) not in kept_keys]
        attributes["affiliations"] = kept + added
        for entry in added:
            faction = await ensure_faction(session, campaign, branch, entry["name"], kind=entry["kind"])
            entry["name"], entry["kind"] = faction.name, faction.kind
    character.attributes = attributes
    await session.commit()
    return await _detail(session, campaign, branch)


@router.patch("/campaigns/{campaign_id}/relationships/{relationship_id}")
async def update_relationship(campaign_id: UUID, relationship_id: UUID, branch_id: UUID,
                              payload: RelationshipUpdate, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    relation = await session.get(CharacterRelationship, relationship_id)
    if not relation or relation.campaign_id != campaign_id or relation.branch_id != branch_id \
            or relation.visibility != "PLAYER_KNOWN":
        raise HTTPException(404, "Relationship not found in this timeline.")
    dimensions = dict(relation.dimensions or {})
    for axis in ("trust", "respect", "fear", "hostility"):
        value = getattr(payload, axis)
        before = dimensions.get(axis)
        if value is None:
            dimensions.pop(axis, None)
        else:
            dimensions[axis] = value
        if before != value and value is not None:
            session.add(RelationshipEvent(campaign_id=campaign.id, branch_id=branch.id, relationship_id=relation.id,
                                          dimension=axis, before_value=before if isinstance(before, (int, float)) else None,
                                          after_value=value, delta=value - before if isinstance(before, (int, float)) else None,
                                          reason="Set by the player", visibility=relation.visibility))
    if payload.status.strip():
        dimensions["status"] = payload.status.strip()
    else:
        dimensions.pop("status", None)
    relation.dimensions = dimensions
    relation.summary = payload.summary.strip()
    await session.commit()
    return await _detail(session, campaign, branch)


@router.patch("/campaigns/{campaign_id}")
async def rename_campaign(campaign_id: UUID, payload: CampaignRename, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    campaign.title = payload.title.strip()
    campaign.updated_at = datetime.now(UTC)
    await session.commit()
    return {"id": str(campaign.id), "title": campaign.title}


@router.put("/campaigns/{campaign_id}/game-mode")
async def update_game_mode(campaign_id: UUID, payload: CampaignModeUpdate,
                           branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    campaign.game_mode = payload.game_mode
    campaign.updated_at = datetime.now(UTC)
    await _ensure_branch_choices(session, campaign, branch)
    await session.commit()
    return await _detail(session, campaign, branch)


@router.put("/campaigns/{campaign_id}/theme")
async def update_theme(campaign_id: UUID, payload: CampaignThemeUpdate,
                       branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    campaign.theme_profile = derive_theme(campaign.original_prompt, campaign.genre,
                                          campaign.constitution.get("tone", ""),
                                          payload.theme_family).model_dump(mode="json")
    campaign.updated_at = datetime.now(UTC)
    await session.commit()
    return await _detail(session, campaign, branch)


@router.post("/campaigns/{campaign_id}/refresh-setup")
async def refresh_campaign_setup(campaign_id: UUID, branch_id: UUID | None = None,
                                 session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    await refresh_setup_from_premise(session, campaign)
    return await _detail(session, campaign, branch)


@router.post("/campaigns/{campaign_id}/refresh-story")
async def refresh_from_story(campaign_id: UUID, branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    """Re-read the story so far and update traits, goals, history, world rules, and reputation."""
    from app.services.story_profile import evolve_profile

    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    routed = await route(session, "summary", provider_for_profile)
    try:
        notes = await evolve_profile(session, routed.provider, campaign, branch, force=True)
    except ModelUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, f"The model's profile update could not be read: {str(exc)[:200]}") from exc
    await session.commit()
    return {"changes": notes, "campaign": await _detail(session, campaign, branch)}


@router.post("/campaigns/{campaign_id}/factions/sync")
async def sync_campaign_factions(campaign_id: UUID, branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    """Regroup people into factions, nations, guilds and crews using the story so far."""
    from app.services.affiliations import sync_affiliations
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    try:
        provider = (await route(session, "state", provider_for_profile)).provider
    except Exception:  # noqa: BLE001 - deterministic grouping still runs without a model
        provider = None
    stats = await sync_affiliations(session, campaign, branch, provider)
    await session.commit()
    return {"stats": stats, "campaign": await _detail(session, campaign, branch)}


@router.post("/campaigns/{campaign_id}/reindex-people")
async def reindex_campaign_people(campaign_id: UUID, branch_id: UUID | None = None,
                                  session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    await ensure_identity_rows(session, branch.id)
    last_death = -1
    last_alive_correction = -1
    starting_state = (campaign.constitution or {}).get("starting_state", {})
    current_location = str(starting_state.get("current_location", "")) if isinstance(starting_state, dict) else ""
    log = StoreLog()
    for turn in await history_for_branch(session, branch.head_turn_id, limit=5000):
        current_location = player_location_for_turn(campaign, turn, current_location)
        if turn.status == "complete" and turn.gm_response:
            await index_people_from_narration(session, campaign, branch, turn, current_location, log)
            if player_death_stated(turn.gm_response, campaign.protagonist_name):
                last_death = turn.turn_index
        if player_says_alive(turn.player_action, campaign.protagonist_name):
            last_alive_correction = turn.turn_index
    if last_alive_correction > last_death:
        branch.current_state = {**(branch.current_state or {}), "player_status": "alive"}
        protagonist = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(campaign.protagonist_name)))
        if protagonist:
            protagonist.status = "alive"
    from app.services.affiliations import sync_affiliations
    try:
        await sync_affiliations(session, campaign, branch, (await route(session, "state", provider_for_profile)).provider)
    except Exception:  # noqa: BLE001 - grouping must never block recovering people
        logger.warning("Faction regroup during people recovery failed", exc_info=True)
    if branch.head_turn_id:
        head = await session.get(Turn, branch.head_turn_id)
        if head:
            checkpoint = await session.scalar(select(Checkpoint).where(
                Checkpoint.branch_id == branch.id, Checkpoint.turn_id == head.id,
                Checkpoint.turn_index == head.turn_index).order_by(Checkpoint.created_at.desc()))
            if checkpoint:
                checkpoint.state_snapshot = await capture_snapshot(session, branch)
    await session.commit()
    return await _detail(session, campaign, branch)


@router.get("/campaigns/{campaign_id}/diagnostics")
async def campaign_diagnostics(campaign_id: UUID, branch_id: UUID | None = None, limit: int = Query(default=30, le=200),
                               session: AsyncSession = Depends(get_session)):
    """Development view of state extraction: structured outputs and decisions only, never hidden reasoning."""
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    turns = await history_for_branch(session, branch.head_turn_id, limit=limit)
    failed = (await session.scalars(select(Turn).where(Turn.branch_id == branch.id, Turn.canonical.is_(False))
                                    .order_by(Turn.created_at.desc()).limit(20))).all()
    jobs = (await session.scalars(select(PostTurnJob).where(PostTurnJob.branch_id == branch.id)
                                  .order_by(PostTurnJob.created_at.desc()).limit(40))).all()
    summary = await session.scalar(select(CampaignSummary).where(
        CampaignSummary.branch_id == branch.id, CampaignSummary.summary_type == "campaign"))
    totals: dict[str, int] = {}
    for turn in turns:
        for key, value in ((turn.diagnostics or {}).get("metrics") or {}).items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return jsonable_encoder({
        "turns": [{"turn_index": turn.turn_index, "id": turn.id, "attempt": turn.attempt, "diagnostics": turn.diagnostics,
                   "changes": (turn.state_delta or {}).get("changes", [])} for turn in turns],
        "metric_totals": totals,
        "failed_attempts": [{"id": turn.id, "turn_index": turn.turn_index, "status": turn.status, "attempt": turn.attempt,
                             "action": turn.player_action[:200], "error": (turn.diagnostics or {}).get("error")} for turn in failed],
        "jobs": describe_jobs(list(jobs)),
        "summary": {"through_turn_index": summary.through_turn_index, "last_attempt_turn_index": summary.last_attempt_turn_index,
                    "last_success_at": summary.last_success_at, "last_error": summary.last_error,
                    "method": summary.method} if summary else None,
    })


@router.get("/campaigns/{campaign_id}/repair")
async def campaign_repair_report(campaign_id: UUID, branch_id: UUID | None = None, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    return await analyze_campaign(session, campaign, branch)


@router.post("/campaigns/{campaign_id}/repair")
async def campaign_repair_apply(campaign_id: UUID, payload: RepairApply, branch_id: UUID | None = None,
                                session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    result = await apply_repair(session, campaign, branch, set(payload.finding_ids),
                                include_high_confidence=payload.include_high_confidence)
    await session.commit()
    return {"result": result, "campaign": await _detail(session, campaign, branch)}


@router.post("/campaigns/{campaign_id}/archive")
async def archive_campaign(campaign_id: UUID, archived: bool = True, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    campaign.archived = archived
    await session.commit()
    return {"id": str(campaign.id), "archived": campaign.archived}


def _note(note: CampaignNote) -> dict:
    return {"id": str(note.id), "title": note.title, "body": note.body, "quote": note.quote, "tag": note.tag,
            "pinned": note.pinned, "turn_id": str(note.turn_id) if note.turn_id else None, "turn_index": note.turn_index,
            "branch_id": str(note.branch_id) if note.branch_id else None,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None}


@router.get("/campaigns/{campaign_id}/notes")
async def list_notes(campaign_id: UUID, session: AsyncSession = Depends(get_session)):
    await _campaign(session, campaign_id)
    notes = (await session.scalars(select(CampaignNote).where(CampaignNote.campaign_id == campaign_id)
                                   .order_by(CampaignNote.pinned.desc(), CampaignNote.updated_at.desc()))).all()
    return {"notes": [_note(note) for note in notes]}


@router.post("/campaigns/{campaign_id}/notes", status_code=201)
async def create_note(campaign_id: UUID, payload: NoteWrite, branch_id: UUID | None = None,
                      session: AsyncSession = Depends(get_session)):
    await _campaign(session, campaign_id)
    if not ((payload.title or "").strip() or (payload.body or "").strip() or (payload.quote or "").strip()):
        raise HTTPException(422, "Write something or save a passage first.")
    note = CampaignNote(campaign_id=campaign_id, branch_id=branch_id, title=(payload.title or "").strip(),
                        body=(payload.body or "").strip(), quote=(payload.quote or "").strip(),
                        tag=payload.tag or ("quote" if payload.quote else "note"), pinned=bool(payload.pinned))
    if payload.turn_id:
        turn = await session.get(Turn, payload.turn_id)
        if turn and turn.campaign_id == campaign_id:
            note.turn_id, note.turn_index = turn.id, turn.turn_index
    session.add(note)
    await session.commit()
    await session.refresh(note)
    return _note(note)


async def _owned_note(session: AsyncSession, campaign_id: UUID, note_id: UUID) -> CampaignNote:
    note = await session.get(CampaignNote, note_id)
    if not note or note.campaign_id != campaign_id:
        raise HTTPException(404, "Note not found.")
    return note


@router.patch("/campaigns/{campaign_id}/notes/{note_id}")
async def update_note(campaign_id: UUID, note_id: UUID, payload: NoteWrite, session: AsyncSession = Depends(get_session)):
    note = await _owned_note(session, campaign_id, note_id)
    for field in ("title", "body", "quote", "tag", "pinned"):
        value = getattr(payload, field)
        if value is not None:
            setattr(note, field, value.strip() if isinstance(value, str) else value)
    await session.commit()
    await session.refresh(note)
    return _note(note)


@router.delete("/campaigns/{campaign_id}/notes/{note_id}", status_code=204)
async def delete_note(campaign_id: UUID, note_id: UUID, session: AsyncSession = Depends(get_session)):
    note = await _owned_note(session, campaign_id, note_id)
    await session.delete(note)
    await session.commit()
    return None


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
    await _ensure_branch_choices(session, campaign, branch)
    await session.commit()
    return {"id": str(branch.id), "name": branch.name, "parent_branch_id": str(branch.parent_branch_id), "head_turn_id": str(branch.head_turn_id) if branch.head_turn_id else None}


@router.post("/campaigns/{campaign_id}/branches/{branch_id}/activate")
async def activate_branch(campaign_id: UUID, branch_id: UUID, session: AsyncSession = Depends(get_session)):
    campaign = await _campaign(session, campaign_id)
    branch = await _branch(session, campaign, branch_id)
    campaign.active_branch_id = branch.id
    await _ensure_branch_choices(session, campaign, branch)
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


def _job_factory(profile):
    return provider_for_profile(profile)


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
                    # Optional work runs after the turn committed; its failures stay on the job rows.
                    await run_jobs([UUID(job_id) for job_id in event.get("jobs", [])], _job_factory)
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
    previous_state = dict(branch.current_state or {})
    turn.gm_response = payload.content.strip()
    interpretation, interpreter_diagnostics = await interpret_turn(session, campaign, branch, turn, previous_state)
    log = StoreLog()
    await apply_interpretation(session, campaign, branch, turn, interpretation, log)
    profile = await active_profile(session)
    provider = provider_for_profile(profile)
    turn.suggested_actions = (await suggest_choices(provider, turn.gm_response, turn.player_action,
                              campaign.protagonist_name)) if campaign.game_mode == "guided" else []
    versions = list((await session.scalars(select(MessageVersion).where(MessageVersion.turn_id == turn.id, MessageVersion.role == "gm"))).all())
    for row in versions:
        row.active = False
    session.add(MessageVersion(turn_id=turn.id, role="gm", version=max((row.version for row in versions), default=0) + 1,
                               content=turn.gm_response, active=True))
    turn.status = "complete"
    turn.canonical = True
    turn.diagnostics = {"edited": True, "interpreter": interpreter_diagnostics, **log.as_dict()}
    branch.head_turn_id = turn.id
    session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
        turn_index=turn.turn_index, state_snapshot=await capture_snapshot(session, branch)))
    jobs = [enqueue(session, campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id, kind="MEMORY_EMBEDDING")]
    await session.commit()
    await run_jobs([job.id for job in jobs], _job_factory)
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
            if event.get("type") == "complete":
                await run_jobs([UUID(job_id) for job_id in event.get("jobs", [])], _job_factory)
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
