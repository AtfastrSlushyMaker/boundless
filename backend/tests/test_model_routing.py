"""Separate narrator/state/summary models, inheritance, and explicit hosted fallback."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from app.db.models import ModelProfile
from app.db.session import SessionLocal
from app.llm.router import is_hosted, route, visual_model
from tests.conftest import ScriptedModel, detail, new_campaign, play


@pytest.fixture
async def clean_roles():
    async with SessionLocal() as session:
        await session.execute(delete(ModelProfile).where(ModelProfile.role != "narrator"))
        await session.commit()
    yield
    async with SessionLocal() as session:
        await session.execute(delete(ModelProfile).where(ModelProfile.role != "narrator"))
        await session.commit()


@pytest.mark.asyncio
async def test_roles_inherit_the_narrator_until_overridden(client, clean_roles):
    response = await client.get("/api/settings/roles")
    assert response.status_code == 200
    roles = {row["role"]: row for row in response.json()["roles"]}
    assert roles["state"]["inherit"] and roles["summary"]["inherit"]
    assert response.json()["hosted_fallback_enabled"] is False

    response = await client.put("/api/settings/roles", json={"roles": [
        {"role": "state", "inherit": False, "provider": "openai-compatible", "base_url": "http://127.0.0.1:9999/v1",
         "model": "state-model"},
    ]})
    assert response.status_code == 200, response.text
    async with SessionLocal() as session:
        state = await route(session, "state", lambda profile: profile)
        summary = await route(session, "summary", lambda profile: profile)
        canon = await route(session, "canon_repair", lambda profile: profile)
        assert state.source_role == "state" and state.model_name == "state-model" and not state.hosted
        assert summary.source_role == "state", "summary falls back to the state model before the narrator"
        assert canon.source_role in {"narrator", "default"}
        assert await route(session, "state_fallback", lambda profile: profile) is None


@pytest.mark.asyncio
async def test_visual_description_prefers_mature_role_over_state(monkeypatch):
    from app.llm import router

    mature = SimpleNamespace(role="mature", provider="openai-compatible",
                             base_url="http://127.0.0.1:1234/v1", model="uncensored-local")

    async def profile_for_role(_session, role):
        assert role == "mature", "the state model must not be used when a mature role is configured"
        return mature

    monkeypatch.setattr(router, "role_profile", profile_for_role)
    routed = await visual_model(None, lambda profile: profile)
    assert routed.source_role == "mature"
    assert routed.model_name == "uncensored-local"


@pytest.mark.asyncio
async def test_hosted_fallback_requires_explicit_enable(client, clean_roles, monkeypatch):
    from app.services import turn_service

    narrator = ScriptedModel("narrator")
    broken_state = ScriptedModel("local-state")
    hosted = ScriptedModel("hosted-state")
    broken_state.interpretations.extend(["this is not json"] * 4)

    def factory(profile):
        if profile is None or profile.role == "narrator":
            return narrator
        return {"state": broken_state, "state_fallback": hosted}[profile.role]

    monkeypatch.setattr(turn_service, "provider_for_profile", factory)
    from app.api import routes
    monkeypatch.setattr(routes, "provider_for_profile", factory)
    await client.put("/api/settings/roles", json={"roles": [
        {"role": "state", "inherit": False, "provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen"},
        {"role": "state_fallback", "inherit": False, "provider": "deepseek", "base_url": "https://api.deepseek.com",
         "model": "deepseek-flash"},
    ], "hosted_fallback_enabled": False})
    campaign = await new_campaign(client, "My name is Ada. I am a courier in the harbor city of Ost.")
    narrator.narrations.append("A clerk named Pell hands you a sealed parcel.")
    event = await play(client, campaign, "I take the parcel.")
    assert event["type"] == "complete"
    assert hosted.requests == [], "campaign content must not reach a hosted model unless fallback is enabled"
    diagnostics = (await client.get(f"/api/campaigns/{campaign['id']}/diagnostics")).json()["turns"][-1]["diagnostics"]
    assert diagnostics["interpreter"]["failed"] and diagnostics["interpreter"]["model"]["model"] == "qwen"
    assert diagnostics["narrator"]["model"] != "qwen"

    await client.put("/api/settings/roles", json={"roles": [
        {"role": "state", "inherit": False, "provider": "ollama", "base_url": "http://127.0.0.1:11434", "model": "qwen"},
        {"role": "state_fallback", "inherit": False, "provider": "deepseek", "base_url": "https://api.deepseek.com",
         "model": "deepseek-flash"},
    ], "hosted_fallback_enabled": True})
    broken_state.interpretations.extend(["still not json"] * 4)
    hosted.interpretations.append({"state_changes": [{"kind": "CREATE_CHARACTER", "name": "Pell", "value": {"role": "clerk"}}]})
    narrator.narrations.append("Pell tips his cap.")
    assert (await play(client, campaign, "I thank Pell."))["type"] == "complete"
    assert len(hosted.requests) == 1
    state = await detail(client, campaign)
    assert any(person["name"] == "Pell" for person in state["characters"])
    roles = (await client.get("/api/settings/roles")).json()
    assert roles["hosted_fallback_enabled"] is True
    assert next(row for row in roles["roles"] if row["role"] == "state_fallback")["hosted"] is True


def test_hosted_detection():
    assert is_hosted("deepseek", "https://api.deepseek.com")
    assert not is_hosted("mlx", "http://127.0.0.1:8088/v1")
    assert not is_hosted("openai-compatible", "http://192.168.1.20:1234/v1")
    assert is_hosted("openai-compatible", "https://api.example.com/v1")


def test_mac_docker_maps_ollama_loopback_without_changing_explicit_endpoints(monkeypatch):
    from app.core.config import settings
    from app.llm import gateway

    monkeypatch.setattr(gateway.platform, "system", lambda: "Linux")
    monkeypatch.setattr(settings, "host_mlx_supported", True)
    assert gateway.ollama_base_url("http://127.0.0.1:11434") == "http://host.docker.internal:11434"
    assert gateway.ollama_base_url("http://localhost:11435") == "http://host.docker.internal:11435"
    assert gateway.ollama_base_url("http://host.docker.internal:11434") == "http://host.docker.internal:11434"
    monkeypatch.setattr(settings, "host_mlx_supported", False)
    assert gateway.ollama_base_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"


@pytest.mark.asyncio
async def test_health_reports_state_and_inherited_summary_offline(client, clean_roles, monkeypatch):
    from app.api import routes

    class OfflineState(ScriptedModel):
        async def health(self):
            return {"status": "offline", "model": self.name, "detail": "State model unavailable"}

    narrator, state = ScriptedModel("narrator"), OfflineState("local-state")
    monkeypatch.setattr(routes, "provider_for_profile",
                        lambda profile: state if profile and profile.role == "state" else narrator)
    response = await client.put("/api/settings/roles", json={"roles": [
        {"role": "state", "inherit": False, "provider": "ollama", "base_url": "http://127.0.0.1:11434",
         "model": "local-state"},
    ], "hosted_fallback_enabled": False})
    assert response.status_code == 200
    health = (await client.get("/api/health")).json()
    assert health["status"] == "degraded"
    assert health["model"]["status"] == "connected"
    assert health["roles"]["narrator"]["status"] == "connected"
    assert health["roles"]["state"]["status"] == "offline"
    assert health["roles"]["summary"]["status"] == "offline"
    assert health["roles"]["summary"]["source_role"] == "state"


@pytest.mark.asyncio
async def test_relationship_repeats_and_noise_are_rejected(client, scripted):
    campaign = await new_campaign(client, "My name is Ada. I am a courier in the harbor city of Ost.")
    change = {"from": "Pell", "to": "Ada", "deltas": {"trust": 10}, "reason": "Ada delivered the parcel on time"}
    scripted.narrations.append("Pell the clerk smiles as Ada hands over the parcel.")
    scripted.interpretations.append({"state_changes": [{"kind": "CREATE_CHARACTER", "name": "Pell", "value": {"role": "clerk"}}],
                                     "relationship_changes": [change, {"from": "Pell", "to": "Ada", "reason": "Appeared in the story"}]})
    await play(client, campaign, "I deliver the parcel.")
    scripted.narrations.append("Pell nods again.")
    scripted.interpretations.append({"relationship_changes": [change]})
    await play(client, campaign, "I wait.")
    state = await detail(client, campaign)
    edge = next(relation for relation in state["relationships"] if relation["from"] == "Pell")
    assert edge["dimensions"]["trust"] == 60
    assert [event["reason"] for event in edge["events"]] == ["Ada delivered the parcel on time"]
    assert json.dumps(edge).count("Appeared in the story") == 0
