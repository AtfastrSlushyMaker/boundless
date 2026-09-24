"""One story turn: narration, state interpretation, reconciliation, and a clean canonical commit.

Turn lifecycle: generating -> interpreting -> complete (canonical) | failed (non-canonical).
A turn becomes canonical only when its narration, state, and checkpoint commit together.
A retry of a failed attempt for the same action reuses that attempt's row, so there is never
more than one record per logical turn in the story chain.
"""

import asyncio
import json
import logging
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Ability,
    Branch,
    Campaign,
    CanonRule,
    Character,
    Checkpoint,
    Item,
    MessageVersion,
    ModelProfile,
    Secret,
    Turn,
)
from app.llm.base import LLMProvider, ModelUnavailable
from app.llm.router import RoutedModel, hosted_fallback_enabled, is_local_endpoint, route
from app.llm.router import active_profile as _active_profile
from app.llm.router import provider_for_profile as _provider_for_profile
from app.llm.structured import parse_json_response, salvage_interpretation
from app.schemas import StateInterpretation
from app.services.canon_guard import (
    CanonViolation,
    action_canon_notes,
    check_narrative,
    check_player_agency,
    dead_character_acts,
)
from app.services.character_store import StoreLog
from app.services.choice_service import suggest_choices
from app.services.context_builder import build_messages
from app.services.identity import normalize_reference
from app.services.narration import stream_narration
from app.services.post_turn import enqueue
from app.services.state_context import build_interpreter_request
from app.services.state_service import apply_interpretation, capture_snapshot, player_says_alive
from app.services.world_index import index_people_from_narration

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"
LENGTH_TOKENS = {"concise": 520, "standard": 900, "detailed": 1300, "novelistic": 1600}
logger = logging.getLogger(__name__)
_is_local_endpoint = is_local_endpoint

__all__ = ["parse_json_response", "salvage_interpretation", "stream_turn", "interpret_turn", "provider_for_profile",
           "active_profile"]


def provider_for_profile(profile: ModelProfile | None) -> LLMProvider:
    return _provider_for_profile(profile)


async def active_profile(session: AsyncSession) -> ModelProfile | None:
    return await _active_profile(session)


def _factory(profile: ModelProfile | None) -> LLMProvider:
    # Looked up at call time so tests (and the settings UI) can swap providers.
    return provider_for_profile(profile)


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
    for fact in secrets[:allowed_count]:
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


async def _request_interpretation(routed: RoutedModel, messages: list[dict[str, str]], diagnostics: dict[str, Any]
                                  ) -> StateInterpretation | None:
    """One model's attempt: parse, one schema repair, then salvage of valid operations."""
    raw = await routed.provider.complete_json(messages, temperature=0.1, max_tokens=2200)
    if settings.state_debug:
        diagnostics.setdefault("raw", []).append(raw[:12000])
    try:
        return StateInterpretation.model_validate(parse_json_response(raw))
    except ValueError as first_error:
        diagnostics["repair_used"] = True
        retry = await routed.provider.complete_json(messages + [
            {"role": "assistant", "content": raw[:6000]},
            {"role": "user", "content": "Correct the JSON to match the required schema. Every value must be an object. "
             "Visibility is PLAYER_KNOWN or GM_ONLY. Drop any operation you cannot express. Return only the JSON. "
             f"Validation error: {str(first_error)[:800]}"},
        ], temperature=0.0, max_tokens=2200)
        if settings.state_debug:
            diagnostics["raw"].append(retry[:12000])
        try:
            return StateInterpretation.model_validate(parse_json_response(retry))
        except ValueError:
            for candidate in (retry, raw):
                try:
                    result, dropped = salvage_interpretation(parse_json_response(candidate))
                    diagnostics["salvage_used"] = True
                    diagnostics["salvage_dropped"] = dropped
                    return result
                except ValueError:
                    continue
    return None


async def interpret_turn(session: AsyncSession, campaign: Campaign, branch: Branch, turn: Turn,
                         previous_state: dict[str, Any]) -> tuple[StateInterpretation, dict[str, Any]]:
    prompt = (PROMPT_DIR / "state_interpreter.md").read_text(encoding="utf-8")
    request = await build_interpreter_request(session, campaign, branch, turn.player_action, turn.gm_response or "",
                                              previous_state)
    messages = [{"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False, default=str)}]
    routed = await route(session, "state", _factory)
    diagnostics: dict[str, Any] = {"model": routed.describe(), "candidates": len(request["known_characters"])}
    started = time.monotonic()
    result: StateInterpretation | None = None
    try:
        result = await _request_interpretation(routed, messages, diagnostics)
    except ModelUnavailable as exc:
        diagnostics["error"] = str(exc)[:300]
    if result is None and await hosted_fallback_enabled(session):
        fallback = await route(session, "state_fallback", _factory)
        if fallback is not None:
            diagnostics["fallback_used"] = fallback.describe()
            try:
                result = await _request_interpretation(fallback, messages, diagnostics)
            except ModelUnavailable as exc:
                diagnostics["fallback_error"] = str(exc)[:300]
    diagnostics["latency_ms"] = round((time.monotonic() - started) * 1000)
    if result is None:
        diagnostics["failed"] = True
        logger.warning("State interpreter returned no usable JSON; saving narration with deterministic reconciliation only")
        result = StateInterpretation()
    result.mention_ids = {row["id"]: row["mention"] for row in request.get("new_people", [])}
    diagnostics["new_people_offered"] = len(result.mention_ids)
    if settings.state_debug:
        diagnostics["parsed"] = result.model_dump(mode="json")
    diagnostics["operations_proposed"] = len(result.state_changes)
    return result, diagnostics


async def _repair_canon(provider: LLMProvider, messages: list[dict[str, str]], text: str, violation: str) -> str:
    repair = await provider.complete(messages + [
        {"role": "assistant", "content": text},
        {"role": "user", "content": f"Rewrite the narration so it no longer contradicts hard canon. {violation} Keep the same scene and consequences that remain possible. Output only the corrected narration."},
    ], temperature=0.35, max_tokens=settings.llm_max_output_tokens)
    return repair.strip()


async def _action_notes(session: AsyncSession, campaign: Campaign, branch: Branch, action: str) -> list[dict[str, str]]:
    player = await session.scalar(select(Character).where(Character.branch_id == branch.id,
                                                          Character.name.ilike(campaign.protagonist_name)))
    if player is None:
        return []
    abilities = list((await session.scalars(select(Ability).where(Ability.character_id == player.id))).all())
    items = (await session.scalars(select(Item).where(Item.branch_id == branch.id))).all()
    inventory = [item.name for item in items if normalize_reference(item.owner_name) in {normalize_reference(player.name), "player"}]
    inventory += [alias for item in items for alias in (item.aliases or [])
                  if normalize_reference(item.owner_name) in {normalize_reference(player.name), "player"}]
    copying = any("copy" in ability.name.casefold() or "theft" in ability.name.casefold() or "steal" in ability.description.casefold()
                  for ability in abilities)
    return action_canon_notes(action, inventory_names=inventory, abilities=abilities, innate_copying=copying)


async def _reusable_attempt(session: AsyncSession, branch: Branch, parent_turn_id, action: str) -> Turn | None:
    return await session.scalar(select(Turn).where(
        Turn.branch_id == branch.id, Turn.canonical.is_(False), Turn.status.in_(["failed", "generating", "interpreting"]),
        Turn.player_action == action,
        (Turn.parent_turn_id == parent_turn_id) if parent_turn_id else Turn.parent_turn_id.is_(None),
    ).order_by(Turn.created_at.desc()).limit(1))


async def stream_turn(session: AsyncSession, campaign: Campaign, branch: Branch, action: str,
                      instruction: str | None = None, existing_turn: Turn | None = None):
    narrator = await route(session, "narrator", _factory)
    profile = narrator.profile
    provider = narrator.provider
    await ensure_hidden_canon(session, campaign, branch, provider)
    previous_state = deepcopy(branch.current_state or {})
    parent_turn_id = existing_turn.parent_turn_id if existing_turn else branch.head_turn_id
    parent_turn = await session.get(Turn, parent_turn_id) if parent_turn_id else None
    reused = False
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
        turn = await _reusable_attempt(session, branch, parent_turn_id, action)
        if turn is not None:
            reused = True
            turn.attempt = (turn.attempt or 1) + 1
            turn.status = "generating"
            turn.gm_response = ""
            turn.diagnostics = {}
            has_checkpoint = await session.scalar(select(Checkpoint.id).where(
                Checkpoint.branch_id == branch.id, Checkpoint.turn_id == turn.id).limit(1))
            if not has_checkpoint:
                session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
                                       turn_index=max(0, turn.turn_index - 1), state_snapshot=await capture_snapshot(session, branch)))
        else:
            turn_index = 1 if parent_turn_id is None else (await session.scalar(select(Turn.turn_index).where(Turn.id == parent_turn_id)) or 0) + 1
            turn = Turn(campaign_id=campaign.id, branch_id=branch.id, parent_turn_id=parent_turn_id,
                        turn_index=turn_index, player_action=action, status="generating", canonical=False, attempt=1)
            session.add(turn)
            await session.flush()
            session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
                                   turn_index=max(0, turn.turn_index - 1), state_snapshot=await capture_snapshot(session, branch)))
        await session.commit()

    local_narrator = not narrator.hosted
    context_state = {**(branch.current_state or {}), "player_status": "alive"} if player_says_alive(
        action, campaign.protagonist_name) else branch.current_state or {}
    notes = await _action_notes(session, campaign, branch, action)
    messages = await build_messages(session, campaign, branch.id, action, parent_turn_id, context_state, instruction,
                                    context_window=profile.context_window if profile and local_narrator else None,
                                    canon_notes=notes)
    full = ""
    diagnostics: dict[str, Any] = {"narrator": narrator.describe(), "canon_notes": notes, "attempt": turn.attempt,
                                   "reused_failed_attempt": reused}
    started = time.monotonic()
    try:
        async for piece in stream_narration(provider, messages,
                                            max_tokens=LENGTH_TOKENS.get(profile.response_length if profile else "standard", 1200),
                                            temperature=profile.temperature if profile else settings.llm_temperature):
            full += piece
        if not full.strip():
            raise ModelUnavailable("The model returned an empty response. Check its chat template and model profile.")
        diagnostics["narration_ms"] = round((time.monotonic() - started) * 1000)
        rules = list((await session.scalars(select(CanonRule).where(
            CanonRule.campaign_id == campaign.id,
            (CanonRule.branch_id.is_(None) | (CanonRule.branch_id == branch.id)),
        ))).all())
        rule_payload = [{"rule_type": row.rule_type, "strength": row.strength,
                         "statement": row.statement, "exceptions": row.exceptions} for row in rules]
        dead = list((await session.scalars(select(Character.name).where(
            Character.branch_id == branch.id, Character.status.ilike("dead%") | Character.status.ilike("deceased%")))).all())

        def narration_violation(candidate: str) -> str | None:
            return (check_narrative(candidate, campaign.constitution, rule_payload) or
                    check_player_agency(candidate, campaign.protagonist_name, turn.player_action,
                                        parent_turn.gm_response if parent_turn else "") or
                    dead_character_acts(candidate, dead))

        violation = narration_violation(full)
        if violation:
            repairer = await route(session, "canon_repair", _factory)
            diagnostics["canon_repair"] = {"violation": violation, "model": repairer.describe()}
            corrected = await _repair_canon(repairer.provider, messages, full, violation)
            if narration_violation(corrected):
                raise CanonViolation("The model could not keep the player's agency and established canon. Nothing from that response was saved.")
            full = corrected
        yield {"type": "delta", "text": full, "turn_id": str(turn.id)}
        turn.gm_response = full.strip()
        turn.status = "interpreting"
        yield {"type": "status", "stage": "interpreting", "turn_id": str(turn.id)}
        versions = list((await session.scalars(select(MessageVersion).where(
            MessageVersion.turn_id == turn.id, MessageVersion.role == "gm",
        ))).all())
        for row in versions:
            row.active = False
        session.add(MessageVersion(turn_id=turn.id, role="gm", version=max((row.version for row in versions), default=0) + 1,
                                   content=turn.gm_response, active=True))
        await session.flush()
        interpretation, interpreter_diagnostics = await interpret_turn(session, campaign, branch, turn, previous_state)
        log = StoreLog()
        if interpreter_diagnostics.get("salvage_used"):
            log.metrics["interpreter_salvage_used"] += 1
        if interpreter_diagnostics.get("failed"):
            log.metrics["interpreter_failed"] += 1
        await apply_interpretation(session, campaign, branch, turn, interpretation, log)
        await index_people_from_narration(session, campaign, branch, turn,
                                          str((branch.current_state or {}).get("current_location", "")), log)
        turn.suggested_actions = (await suggest_choices(provider, turn.gm_response, turn.player_action,
                                  campaign.protagonist_name)) if campaign.game_mode == "guided" else []
        turn.status = "complete"
        turn.canonical = True
        turn.diagnostics = {**diagnostics, "interpreter": interpreter_diagnostics, **log.as_dict()}
        branch.head_turn_id = turn.id
        # Replace any checkpoint from an earlier completion of this same turn (regenerate).
        await session.execute(delete(Checkpoint).where(Checkpoint.branch_id == branch.id, Checkpoint.turn_id == turn.id,
                                                       Checkpoint.turn_index == turn.turn_index))
        session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id,
                               turn_index=turn.turn_index, state_snapshot=await capture_snapshot(session, branch)))
        jobs = [enqueue(session, campaign_id=campaign.id, branch_id=branch.id, turn_id=turn.id, kind=kind)
                for kind in ("MEMORY_EMBEDDING", "SUMMARY_UPDATE", "PROFILE_EVOLUTION", "PORTRAIT_QUEUE")]
        await session.commit()
        yield {"type": "complete", "turn_id": str(turn.id), "turn_index": turn.turn_index,
               "branch_id": str(branch.id), "state_delta": turn.state_delta,
               "changes": (turn.state_delta or {}).get("changes", []), "metrics": log.as_dict()["metrics"],
               "jobs": [str(job.id) for job in jobs]}
    except (GeneratorExit, asyncio.CancelledError):
        turn_id = turn.id
        await session.rollback()
        if not existing_turn:
            saved = await session.get(Turn, turn_id)
            if saved:
                saved.status = "failed"
                saved.canonical = False
                saved.diagnostics = {**diagnostics, "error": "interrupted"}
                await session.commit()
        raise
    except Exception as exc:
        turn_id = turn.id
        await session.rollback()
        if not existing_turn:
            saved = await session.get(Turn, turn_id)
            if saved:
                saved.status = "failed"
                saved.canonical = False
                saved.diagnostics = {**diagnostics, "error": f"{type(exc).__name__}: {str(exc)[:400]}"}
                await session.commit()
        yield {"type": "error", "turn_id": str(turn_id), "message": str(exc)[:600]}
