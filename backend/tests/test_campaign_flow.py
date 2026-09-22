import json

import httpx
import pytest

from app.main import app


class FakeModel:
    async def health(self):
        return {"status": "connected", "model": "test-model"}

    async def stream_chat(self, messages, **options):
        yield "The envoy waits by the gate. "
        yield "A sealed letter rests in her hand."

    async def complete_json(self, messages, **options):
        return json.dumps({
            "events": [{"content": "An envoy arrived with a sealed letter.", "certainty": "CONFIRMED"}],
            "state_changes": [
                {"kind": "CREATE_CHARACTER", "name": "Envoy", "value": {"role": "Visitor"}},
                {"kind": "CREATE_LOCATION", "name": "Gatehouse", "value": {"description": "Fortress entrance"}},
                {"kind": "MOVE_CHARACTER", "subject": "Malachar", "value": {"location": "Gatehouse"}},
                {"kind": "ADD_ITEM", "name": "Sealed letter", "value": {"owner": "Malachar"}},
            ],
            "new_memories": [{"content": "The envoy brought a sealed letter to Malachar.", "importance": 0.8,
                              "characters": ["Envoy", "Malachar"], "keywords": ["letter"]}],
            "time_elapsed_seconds": 60,
        })

    async def complete(self, messages, **options):
        return "The envoy waits by the gate."


@pytest.mark.asyncio
async def test_campaign_turn_branch_and_export_round_trip(monkeypatch):
    from app.api import routes
    from app.services import turn_service

    fake = FakeModel()
    monkeypatch.setattr(routes, "provider_for_profile", lambda profile: fake)
    monkeypatch.setattr(turn_service, "provider_for_profile", lambda profile: fake)
    created = []
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        try:
            response = await client.post("/api/campaigns", json={"prompt": "I am Malachar, an immortal demon king. There is no exception to my immortality. My fortress is on the Mourning Coast."})
            assert response.status_code == 201, response.text
            campaign = response.json()
            created.append(campaign["id"])
            assert campaign["canon_rules"][0]["exceptions"] == []
            branch_id = campaign["branch"]["id"]

            response = await client.post(f"/api/campaigns/{campaign['id']}/turns/stream", json={"action": "Open the gate.", "branch_id": branch_id})
            assert response.status_code == 200, response.text
            assert '"type": "complete"' in response.text, response.text
            response = await client.get(f"/api/campaigns/{campaign['id']}")
            detail = response.json()
            assert len(detail["turns"]) == 1
            assert detail["current_location"] == "Gatehouse"
            assert any(item["name"] == "Sealed letter" for item in detail["inventory"])
            assert any(person["name"] == "Envoy" for person in detail["characters"])

            turn_id = detail["turns"][0]["id"]
            response = await client.patch(f"/api/turns/{turn_id}", json={"content": "The envoy now waits by the gate."})
            assert response.status_code == 200, response.text
            assert response.json()["turns"][0]["gm_response"] == "The envoy now waits by the gate."
            response = await client.post(f"/api/turns/{turn_id}/regenerate", json={"action": "regenerate"})
            assert response.status_code == 200, response.text
            assert '"type": "complete"' in response.text, response.text

            response = await client.post(f"/api/campaigns/{campaign['id']}/branches", params={"branch_id": branch_id}, json={"name": "The other gate", "turn_id": detail["turns"][0]["id"]})
            assert response.status_code == 201, response.text
            fork_id = response.json()["id"]
            response = await client.get(f"/api/campaigns/{campaign['id']}", params={"branch_id": fork_id})
            assert len(response.json()["turns"]) == 1
            assert response.json()["current_location"] == "Gatehouse"

            response = await client.post(f"/api/campaigns/{campaign['id']}/rewind", params={"branch_id": fork_id}, json={"turn_index": 0})
            assert response.status_code == 200, response.text
            assert response.json()["turns"] == []
            assert response.json()["inventory"] == []
            await client.post(f"/api/campaigns/{campaign['id']}/branches/{branch_id}/activate")

            exported = (await client.get(f"/api/campaigns/{campaign['id']}/export")).json()
            response = await client.post("/api/campaigns/import", files={"file": ("world.json", json.dumps(exported), "application/json")})
            assert response.status_code == 201, response.text
            imported = response.json()
            created.append(imported["id"])
            assert imported["id"] != campaign["id"]
            assert len(imported["turns"]) == 1
            assert len(imported["branches"]) == 2
            assert any(item["name"] == "Sealed letter" for item in imported["inventory"])
        finally:
            for campaign_id in created:
                await client.delete(f"/api/campaigns/{campaign_id}")
