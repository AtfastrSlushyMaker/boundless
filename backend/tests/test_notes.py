"""Player notebook: CRUD, saved passages, and pinned notes reaching the narrator as non-canon."""

import pytest

from tests.conftest import new_campaign, play


@pytest.mark.asyncio
async def test_notes_round_trip_and_pinned_notes_reach_the_narrator(client, scripted):
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    base = f"/api/campaigns/{campaign['id']}/notes"
    assert (await client.post(base, json={"title": "  "})).status_code == 422

    turn = await play(client, campaign, "I look for the harbor master.")
    created = await client.post(base, params={"branch_id": campaign["branch"]["id"]},
                                json={"quote": "The harbor master never sleeps.", "turn_id": turn["turn_id"]})
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["tag"] == "quote" and note["turn_index"] == turn["turn_index"]

    updated = await client.patch(f"{base}/{note['id']}", json={"body": "Ask about the ledger", "pinned": True})
    assert updated.json()["pinned"] is True
    listed = (await client.get(base)).json()["notes"]
    assert [row["id"] for row in listed] == [note["id"]]

    captured = {}
    original = scripted.stream_chat

    async def spy(messages, **options):
        captured["system"] = messages[0]["content"]
        async for piece in original(messages, **options):
            yield piece

    scripted.stream_chat = spy
    await play(client, campaign, "I wait by the docks.")
    assert "PLAYER'S PINNED NOTES" in captured["system"] and "Ask about the ledger" in captured["system"]
    assert "not established canon" in captured["system"]

    assert (await client.delete(f"{base}/{note['id']}")).status_code == 204
    assert (await client.get(base)).json()["notes"] == []


@pytest.mark.asyncio
async def test_wording_only_edit_keeps_later_turns(client, scripted):
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    first = await play(client, campaign, "I look for the harbor master.")
    await play(client, campaign, "I wait by the docks.")
    response = await client.patch(f"/api/turns/{first['turn_id']}", json={"content": "The harbor is fog and rope.", "wording_only": True})
    assert response.status_code == 200, response.text
    turns = response.json()["turns"]
    assert len(turns) == 2 and turns[0]["gm_response"] == "The harbor is fog and rope."
