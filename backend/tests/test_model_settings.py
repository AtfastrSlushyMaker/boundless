import stat

import httpx
import pytest

from app.api import routes
from app.core import secrets
from app.llm.gateway import DeepSeekProvider
from app.main import app


def test_deepseek_key_stays_in_private_file(tmp_path, monkeypatch):
    key_path = tmp_path / ".secrets" / "deepseek_api_key"
    monkeypatch.setattr(secrets, "DEEPSEEK_KEY_PATH", key_path)

    secrets.save_deepseek_api_key("sk-local-test")

    assert secrets.get_deepseek_api_key() == "sk-local-test"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700


@pytest.mark.asyncio
async def test_deepseek_model_list_uses_bearer_key(monkeypatch):
    actual_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.deepseek.com/models"
        assert request.headers["Authorization"] == "Bearer sk-local-test"
        return httpx.Response(200, json={"data": [{"id": "deepseek-flash"}, {"id": "deepseek-v4-pro"}]})

    def fake_client(*args, **kwargs):
        return actual_client(*args, transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr("app.llm.gateway.httpx.AsyncClient", fake_client)
    provider = DeepSeekProvider(api_key="sk-local-test")

    assert await provider.list_models() == ["deepseek-flash", "deepseek-v4-pro"]


@pytest.mark.asyncio
async def test_deepseek_models_route_does_not_echo_key(monkeypatch):
    async def fake_list_models(self):
        assert self.headers["Authorization"] == "Bearer sk-local-test"
        return ["deepseek-flash"]

    monkeypatch.setattr(routes.DeepSeekProvider, "list_models", fake_list_models)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/settings/deepseek/models", json={"api_key": "sk-local-test"})

    assert response.status_code == 200
    assert response.json() == {"models": ["deepseek-flash"]}
    assert "sk-local-test" not in response.text
