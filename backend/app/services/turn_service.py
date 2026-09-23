import asyncio
import json
import logging
import re
from copy import deepcopy
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Branch,
    Campaign,
    CanonRule,
    Checkpoint,
    MessageVersion,
    ModelProfile,
    Secret,
    Turn,
)
from app.llm.base import LLMProvider, ModelUnavailable
from app.llm.gateway import get_provider
from app.schemas import StateInterpretation, StateOperation
from app.services.canon_guard import CanonViolation, check_narrative
from app.services.context_builder import build_messages
from app.services.narration import stream_narration
from app.services.state_service import apply_interpretation, capture_snapshot

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
LENGTH_TOKENS = {"concise": 520, "standard": 1200, "detailed": 1900, "novelistic": 2600}
logger = logging.getLogger(__name__)


def _is_local_endpoint(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
        return host.casefold() == "localhost" or ip_address(host).is_loopback
    except ValueError:
        return False


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        result = json.loads(cleaned[start:end + 1])
    if not isinstance(result, dict):
        raise ValueError("Structured model output must be a JSON object.")
    return result


def salvage_interpretation(payload: dict[str, Any]) -> StateInterpretation:
    """Keep independently valid facts when a small model mixes up schema fields."""
    operations = []
    for candidate in payload.get("state_changes", []) if isinstance(payload.get("state_changes"), list) else []:
        if not isinstance(candidate, dict):
            continue
        try:
            operations.append(StateOperation.model_validate(candidate))
        except ValueError:
            continue
    cleaned: dict[str, Any] = {"state_changes": operations}
    for field in ("events", "new_memories", "knowledge_changes", "relationship_changes"):
        rows = payload.get(field)
        cleaned[field] = [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    elapsed = payload.get("time_elapsed_seconds", 0)
    cleaned["time_elapsed_seconds"] = elapsed if isinstance(elapsed, int) and 0 <= elapsed <= 31_536_000 else 0
    return StateInterpretation.model_validate(cleaned)


async def active_profile(session: AsyncSession) -> ModelProfile | None:
    return await session.scalar(select(ModelProfile).where(ModelProfile.active.is_(True)).order_by(ModelProfile.updated_at.desc()))


def provider_for_profile(profile: ModelProfile | None) -> LLMProvider:
    if not profile:
        return get_provider()
    return get_provider(profile.provider, profile.base_url, profile.model)


async def ensure_hidden_canon(session: AsyncSession, campaign: Campaign, branch: Branch, provider: LLMProvider) -> None:
    permissions = campaign.constitution.get("hidden_canon_permissions", [])
    if not permissions:
        return
    existing = list((await session.scalars(select(Secret).where(
        Secret.branch_id == branch.id, Secret.visibility == "GM_ONLY",
    ))).all())
    if existing:
        return
    system = (PROMPT_DIR / "hidden_canon.md").read_text(encoding="utf-8")
    raw = await provider.complete_json([
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"original_prompt": campaign.original_prompt,
            "permissions": permissions, "hard_rules": campaign.constitution.get("hard_invariants", [])}, ensure_ascii=False)},
    ], max_tokens=700, temperature=0.35)
    payload = parse_json_response(raw)
    secrets = payload.get("secrets", [])
    allowed_count = 1 if any("exactly one" in str(rule).casefold() or "one hidden" in str(rule).casefold() for rule in permissions) else 4
    secrets = secrets[:allowed_count]
    for fact in secrets:
        name = str(fact.get("name", "Unrevealed fact"))[:160]
        content = str(fact.get("content", ""))[:5000]
        if content:
            session.add(Secret(campaign_id=campaign.id, branch_id=branch.id, name=name, content=content,
                               visibility="GM_ONLY", discovered_by=[]))
    exceptions = payload.get("canon_exceptions", [])[:allowed_count]
    if exceptions and not any(phrase in campaign.original_prompt.casefold() for phrase in ("no exception", "no known or unknown exception")):
        rules = list((await session.scalars(select(CanonRule).where(
            CanonRule.campaign_id == campaign.id, CanonRule.rule_type == "PLAYER_CANNOT_DIE",
        ))).all())
        for rule in rules:
            if not rule.exceptions:
                rule.exceptions = [str(value)[:500] for value in exceptions]
        branch.current_state = {**(branch.current_state or {}),
                                "confirmed_canon_exceptions": [str(value)[:500] for value in exceptions]}
    await session.flush()


async def _interpret(session: AsyncSession, provider: LLMProvider, campaign: Campaign,
                     branch: Branch, turn: Turn, previous_state: dict[str, Any]) -> StateInterpretation:
    prompt = (PROMPT_DIR / "state_interpreter.md").read_text(encoding="utf-8")
    request = {
        "previous_canonical_state": previous_state,
        "campaign_constitution": campaign.constitution,
        "action": turn.player_action,
        "game_master_narration": turn.gm_response,
    }
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False, default=str)}]
    raw = await provider.complete_json(messages, temperature=0.1, max_tokens=1600)
    try:
        return StateInterpretation.model_validate(parse_json_response(raw))
    except ValueError as first_error:
        retry = await provider.complete_json(messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Correct the JSON to match the required schema. "
             "Every state_changes.value must be an object such as {\"location\":\"road\"} or {\"status\":\"injured\"}. "
             "Visibility must be PLAYER_KNOWN, CHARACTER_KNOWN, WORLD_SECRET, or GM_ONLY; LOW is not valid. "
             "Drop an operation if you cannot express it safely. Return only the corrected JSON. "
             f"Validation error: {str(first_error)[:1200]}"},
        ], temperature=0.0, max_tokens=1800)
        try:
            return StateInterpretation.model_validate(parse_json_response(retry))
        except ValueError as retry_error:
            for candidate in (retry, raw):
                try:
                    result = salvage_interpretation(parse_json_response(candidate))
                    logger.warning("State interpreter returned invalid operations; kept %d valid changes: %s",
                                   len(result.state_changes), type(retry_error).__name__)
                    return result
                except ValueError:
                    continue
            logger.warning("State interpreter returned no usable JSON; saving narration without state changes")
            return StateInterpretation()


async def _repair_canon(provider: LLMProvider, messages: list[dict[str, str]], text: str, violation: str) -> str:
    repair = await provider.complete(messages + [
        {"role": "assistant", "content": text},
        {"role": "user", "content": f"Rewrite the narration so it no longer contradicts hard canon. {violation} Keep the same scene and consequences that remain possible. Output only the corrected narration."},
    ], temperature=0.35, max_tokens=settings.llm_max_output_tokens)
    return repair.strip()


async def _checkpoint_before(session: AsyncSession, campaign: Campaign, branch: Branch, turn: Turn,
                             previous_state: dict[str, Any]) -> None:
    session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
        turn_index=max(0, turn.turn_index - 1), state_snapshot=previous_state))
    await session.flush()


async def stream_turn(session: AsyncSession, campaign: Campaign, branch: Branch, action: str,
                      instruction: str | None = None, existing_turn: Turn | None = None):
    profile = await active_profile(session)
    provider = provider_for_profile(profile)
    await ensure_hidden_canon(session, campaign, branch, provider)
    previous_state = deepcopy(branch.current_state or {})
    parent_turn_id = existing_turn.parent_turn_id if existing_turn else branch.head_turn_id
    if existing_turn:
        before = await session.scalar(select(Checkpoint).where(
            Checkpoint.branch_id == branch.id, Checkpoint.turn_id == existing_turn.id,
            Checkpoint.turn_index < existing_turn.turn_index,
        ).order_by(Checkpoint.created_at.desc()))
        if not before:
            raise ValueError("The saved checkpoint before this response is missing.")
        from app.services.state_service import restore_snapshot
        await restore_snapshot(session, branch, before.state_snapshot)
        previous_state = deepcopy(branch.current_state or {})
        existing_turn.status = "generating"
        turn = existing_turn
        await session.flush()
    else:
        turn_index = 1 if parent_turn_id is None else (await session.scalar(select(Turn.turn_index).where(Turn.id == parent_turn_id)) or 0) + 1
        turn = Turn(campaign_id=campaign.id, branch_id=branch.id, parent_turn_id=parent_turn_id,
                    turn_index=turn_index, player_action=action, status="generating")
        session.add(turn)
        await session.flush()
        await _checkpoint_before(session, campaign, branch, turn, await capture_snapshot(session, branch))
        await session.commit()

    provider_kind = profile.provider if profile else settings.llm_provider
    endpoint = profile.base_url if profile else settings.llm_base_url
    is_local_runtime = provider_kind in {"mlx", "ollama"} or (
        provider_kind == "openai-compatible" and _is_local_endpoint(endpoint)
    )
    messages = await build_messages(session, campaign, branch.id, action, parent_turn_id,
                                    branch.current_state or {}, instruction,
                                    context_window=profile.context_window if profile and is_local_runtime else None)
    full = ""
    try:
        async for piece in stream_narration(provider,
            messages,
            max_tokens=LENGTH_TOKENS.get(profile.response_length if profile else "standard", 1200),
            temperature=profile.temperature if profile else settings.llm_temperature,
        ):
            full += piece
            yield {"type": "delta", "text": piece, "turn_id": str(turn.id)}
        if not full.strip():
            raise ModelUnavailable("The model returned an empty response. Check its chat template and model profile.")
        rules = list((await session.scalars(select(CanonRule).where(
            CanonRule.campaign_id == campaign.id,
            (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch.id)),
        ))).all())
        rule_payload = [{"rule_type": row.rule_type, "strength": row.strength,
                         "statement": row.statement, "exceptions": row.exceptions} for row in rules]
        violation = check_narrative(full, campaign.constitution, rule_payload)
        if violation:
            corrected = await _repair_canon(provider, messages, full, violation)
            if check_narrative(corrected, campaign.constitution, rule_payload):
                raise CanonViolation("The model could not produce a narration consistent with hard canon. Nothing from that response was saved.")
            full = corrected
            yield {"type": "replace", "text": full, "turn_id": str(turn.id), "reason": "canon_repair"}
        turn.gm_response = full.strip()
        turn.status = "interpreting"
        versions = list((await session.scalars(select(MessageVersion).where(
            MessageVersion.turn_id == turn.id, MessageVersion.role == "gm",
        ))).all())
        version_number = max((row.version for row in versions), default=0) + 1
        for row in versions:
            row.active = False
        session.add(MessageVersion(turn_id=turn.id, role="gm", version=version_number, content=turn.gm_response, active=True))
        await session.flush()
        interpretation = await _interpret(session, provider, campaign, branch, turn, previous_state)
        await apply_interpretation(session, campaign, branch, turn, interpretation)
        turn.status = "complete"
        branch.head_turn_id = turn.id
        after = await capture_snapshot(session, branch)
        session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
                               turn_index=turn.turn_index, state_snapshot=after))
        await session.commit()
        yield {"type": "complete", "turn_id": str(turn.id), "turn_index": turn.turn_index,
               "branch_id": str(branch.id), "state_delta": turn.state_delta}
    except (GeneratorExit, asyncio.CancelledError):
        turn_id = turn.id
        await session.rollback()
        if not existing_turn:
            saved = await session.get(Turn, turn_id)
            if saved:
                saved.status = "interrupted"
                await session.commit()
        raise
    except Exception as exc:
        turn_id = turn.id
        await session.rollback()
        if not existing_turn:
            saved = await session.get(Turn, turn_id)
            if saved:
                saved.status = "error"
                await session.commit()
        yield {"type": "error", "turn_id": str(turn_id), "message": str(exc)[:600]}
