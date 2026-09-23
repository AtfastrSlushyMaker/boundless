from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.services.image_provider import (
    ComfyUIImageProvider,
    importance_for,
    portrait_file,
    portrait_prompt,
    stable_seed,
    store_portrait,
    validate_endpoint,
    workflow_for,
)
from app.services.portrait_jobs import profile_dict


def character(**attributes):
    return SimpleNamespace(id=uuid4(), name="Aria", role="Companion", attributes=attributes)


def campaign(family="dark_fantasy"):
    return SimpleNamespace(theme_profile={"family": family, "accent_family": "iron red"})


def profile():
    return SimpleNamespace(checkpoint="portrait.safetensors", width=768, height=1024,
                           steps=28, cfg=6.5, sampler="dpmpp_2m", scheduler="karras")


def test_provider_disabled_defaults_and_endpoint_validation():
    assert profile_dict(None)["provider"] == "none"
    assert profile_dict(None)["enabled"] is False
    assert validate_endpoint("http://100.64.0.23:8188/") == "http://100.64.0.23:8188"
    for unsafe in ("file:///tmp/a", "http://user:pass@localhost:8188", "http://localhost:8188/view"):
        with pytest.raises(ValueError):
            validate_endpoint(unsafe)


def test_prompt_uses_canonical_visual_identity_and_campaign_mood():
    person = character(visual_identity={"hair": "long black hair", "eyes": "amber",
                                         "features": ["scar through left eyebrow"]},
                       current_appearance="black ceremonial robes")
    positive, negative = portrait_prompt(person, campaign())
    assert "long black hair" in positive
    assert "scar through left eyebrow" in positive
    assert "black ceremonial robes" in positive
    assert "aged oil painting" in positive
    assert "nudity" in negative
    assert importance_for(person) == "COMPANION"
    assert stable_seed(person.id) == stable_seed(person.id)


def test_workflow_is_reproducible_and_configurable():
    result = workflow_for(profile(), "hero portrait", "watermark", 42)
    assert result["1"]["inputs"]["ckpt_name"] == "portrait.safetensors"
    assert result["2"]["inputs"]["text"] == "hero portrait"
    assert result["5"]["inputs"]["seed"] == 42
    assert result["4"]["inputs"]["height"] == 1024
    assert result["7"]["class_type"] == "SaveImage"


def test_local_storage_rejects_invalid_bytes_and_path_traversal(tmp_path, monkeypatch):
    from app.services.image_provider import settings
    monkeypatch.setattr(settings, "portrait_storage_dir", str(tmp_path))
    campaign_id, character_id, portrait_id = uuid4(), uuid4(), uuid4()
    with pytest.raises(ValueError):
        store_portrait(b"not an image", campaign_id, character_id, portrait_id)
    relative = store_portrait(b"\x89PNG\r\n\x1a\nvalid", campaign_id, character_id, portrait_id)
    assert portrait_file(relative).read_bytes().startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        portrait_file("../../secrets/key.pem")


@pytest.mark.asyncio
async def test_comfy_client_health_queue_and_generation(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={"devices": [{"name": "NVIDIA GeForce RTX 4070 Ti"}]})
        if request.url.path == "/object_info/CheckpointLoaderSimple":
            return httpx.Response(200, json={"CheckpointLoaderSimple": {"input": {"required": {
                "ckpt_name": [["portrait.safetensors"]]}}}})
        if request.url.path == "/queue":
            return httpx.Response(200, json={"queue_running": [], "queue_pending": []})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "job-123"})
        if request.url.path == "/history/job-123":
            return httpx.Response(200, json={"job-123": {"status": {"completed": True},
                "outputs": {"7": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nportrait")
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        transport=httpx.MockTransport(handler), base_url="http://comfy.local", **{
            key: value for key, value in kwargs.items() if key != "base_url"}))
    provider = ComfyUIImageProvider("http://comfy.local")
    assert (await provider.health_check())["models"] == ["portrait.safetensors"]
    job_id = await provider.generate(workflow_for(profile(), "portrait", "bad", 7))
    assert job_id == "job-123"
    assert (await provider.result(job_id)).startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_offline_comfy_client(monkeypatch):
    def offline(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        transport=httpx.MockTransport(offline), **kwargs))
    with pytest.raises(httpx.ConnectError):
        await ComfyUIImageProvider("http://comfy.local").health_check()


@pytest.mark.asyncio
async def test_comfy_failure_and_missing_output(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/history/failed":
            return httpx.Response(200, json={"failed": {"status": {"status_str": "error"}}})
        if request.url.path == "/history/empty":
            return httpx.Response(200, json={"empty": {"status": {"completed": True}, "outputs": {}}})
        return httpx.Response(404)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        transport=httpx.MockTransport(handler), **kwargs))
    provider = ComfyUIImageProvider("http://comfy.local")
    with pytest.raises(ValueError, match="failed portrait workflow"):
        await provider.result("failed")
    with pytest.raises(ValueError, match="without an image"):
        await provider.result("empty")


@pytest.mark.asyncio
async def test_comfy_timeout(monkeypatch):
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("server timed out")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: real_client(
        transport=httpx.MockTransport(timeout), **kwargs))
    with pytest.raises(httpx.ReadTimeout):
        await ComfyUIImageProvider("http://comfy.local").generate({})
