"""Shared test setup. Tests never touch the player's real database."""

import json
import os
import re
from collections import deque

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://boundless:boundless_dev@127.0.0.1:54329/boundless_test")
os.environ["DB_NULL_POOL"] = "true"
os.environ.setdefault("EMBEDDING_PROVIDER", "hash")

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.main import app  # noqa: E402


class ScriptedModel:
    """A fake provider: narration and state interpretations are queued per turn.

    ``interpretations`` entries may be dicts (serialised to JSON), raw strings (to simulate
    malformed output), or callables that receive the interpreter request and return a dict.
    """

    def __init__(self, name: str = "scripted") -> None:
        self.name = name
        self.narrations: deque[str] = deque()
        self.interpretations: deque = deque()
        self.requests: list[dict] = []
        self.summary_calls = 0
        self.summary_fail = False
        self.fail_narration = False

    async def health(self):
        return {"status": "connected", "model": self.name}

    async def stream_chat(self, messages, **options):
        if self.fail_narration:
            from app.llm.base import ModelUnavailable
            raise ModelUnavailable("scripted narration failure")
        text = self.narrations.popleft() if self.narrations else "The street is quiet for a moment."
        for index in range(0, len(text), 40):
            yield text[index:index + 40]

    async def complete(self, messages, **options):
        return "The scene holds."

    async def complete_json(self, messages, **options):
        system = messages[0]["content"]
        if "saved state of a text role-playing game" in system:
            request = json.loads(messages[-1]["content"]) if messages[-1]["role"] == "user" and messages[-1]["content"].startswith("{") else {}
            self.requests.append(request)
            item = self.interpretations.popleft() if self.interpretations else {}
            if callable(item):
                item = item(request)
            return item if isinstance(item, str) else json.dumps(item)
        if "campaign summary" in system:
            self.summary_calls += 1
            if self.summary_fail:
                return "not json at all"
            return json.dumps({"summary": f"Summary call {self.summary_calls}: the story so far, with enough words to count."})
        if "suggest the player's next actions" in system:
            return json.dumps({"choices": ["I wait.", "I look around."]})
        return json.dumps({})


def character_id(request: dict, name: str) -> str:
    for card in request.get("known_characters", []):
        values = [card["canonical_name"], *card.get("aliases", [])]
        if any(re.sub(r"\W+", " ", value).strip().casefold() == name.casefold() for value in values):
            return card["id"]
    raise AssertionError(f"{name} not offered to the interpreter: {[c['canonical_name'] for c in request.get('known_characters', [])]}")


@pytest.fixture
def scripted(monkeypatch):
    from app.api import routes
    from app.services import turn_service

    model = ScriptedModel()
    monkeypatch.setattr(routes, "provider_for_profile", lambda profile: model)
    monkeypatch.setattr(turn_service, "provider_for_profile", lambda profile: model)
    return model


@pytest.fixture
async def client():
    created: list[str] = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", timeout=60) as http:
        http.created = created  # type: ignore[attr-defined]
        yield http
        for campaign_id in created:
            await http.delete(f"/api/campaigns/{campaign_id}")


async def new_campaign(client, prompt: str, **extra) -> dict:
    response = await client.post("/api/campaigns", json={"prompt": prompt, **extra})
    assert response.status_code == 201, response.text
    campaign = response.json()
    client.created.append(campaign["id"])
    return campaign


async def play(client, campaign: dict, action: str) -> dict:
    response = await client.post(f"/api/campaigns/{campaign['id']}/turns/stream",
                                 json={"action": action, "branch_id": campaign["branch"]["id"]})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
    return events[-1]


async def detail(client, campaign: dict) -> dict:
    response = await client.get(f"/api/campaigns/{campaign['id']}", params={"branch_id": campaign["branch"]["id"]})
    assert response.status_code == 200, response.text
    return response.json()
