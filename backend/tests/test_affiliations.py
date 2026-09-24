"""Grouping people by faction, nation, faith and crew; player edits win; the API reports members."""

import json

import pytest

from app.db.models import Faction
from app.services.affiliations import (
    infer_from_text,
    kind_for,
    merge_affiliations,
    normalize_affiliations,
)
from tests.conftest import detail, new_campaign, play
from tests.test_state_pipeline import op


def faction(name: str, kind: str = "faction") -> Faction:
    return Faction(name=name, kind=kind, aliases=[])


def test_roles_and_facts_link_people_to_groups():
    found = infer_from_text("High Priestess of the Veiled Flame", [], [])
    assert found[0]["name"] == "Veiled Flame" and found[0]["status"] == "leader" and found[0]["kind"] == "religion"

    watch = [faction("City Watch", "military")]
    assert infer_from_text("courier", ["Fought the City Watch on the bridge"], watch) == []
    assert infer_from_text("courier", ["Serves the City Watch as a runner"], watch)[0]["name"] == "City Watch"
    assert infer_from_text("former captain of the City Watch", [], watch)[0]["status"] == "former"
    assert infer_from_text("companion of Mara", [], [], people_names={"mara"}) == []
    assert infer_from_text("merchant of Veln", [], [], places={"veln"})[0]["kind"] == "city"
    assert kind_for("Kingdom of Aster") == "nation" and kind_for("Iron Guild") == "guild"


def test_player_affiliations_are_never_overridden():
    player = normalize_affiliations(["Iron Guild"], source="player")
    merged = merge_affiliations(player, normalize_affiliations([{"name": "Iron Guild", "status": "former"}], source="inferred"))
    assert merged[0]["status"] == "member" and merged[0]["source"] == "player"
    merged = merge_affiliations(merged, normalize_affiliations(["Kingdom of Aster"]))
    assert [entry["name"] for entry in merged] == ["Iron Guild", "Kingdom of Aster"]


@pytest.mark.asyncio
async def test_story_affiliations_become_groups_with_members(client, scripted):
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    scripted.narrations.append("Captain Rhea of the Harbor Watch stops you. Beside her, Brother Oswin of the Tide Church frowns.")
    scripted.interpretations.append({"state_changes": [
        op("CREATE_FACTION", "Harbor Watch", {"description": "Keeps order on the docks", "kind": "military"}),
        op("CREATE_CHARACTER", "Rhea", {"role": "captain", "affiliations": [{"name": "Harbor Watch", "role": "captain", "status": "leader"}]}),
        op("CREATE_CHARACTER", "Oswin", {"role": "priest of the Tide Church"}),
        op("CHANGE_FACTION_RELATIONSHIP", "", {"from": "Harbor Watch", "to": "Tide Church", "relation": "rival"}),
    ]})
    assert (await play(client, campaign, "I show my courier seal."))["type"] == "complete"
    state = await detail(client, campaign)
    groups = {row["name"]: row for row in state["factions"]}
    people = {row["name"]: row["id"] for row in state["characters"]}
    assert groups["Harbor Watch"]["kind"] == "military"
    assert groups["Harbor Watch"]["members"] == [{"id": people["Rhea"], "role": "captain", "status": "leader"}]
    assert people["Oswin"] in [member["id"] for member in groups["Tide Church"]["members"]]
    assert groups["Tide Church"]["kind"] == "religion"
    assert {"to": "Tide Church", "relation": "rival", "details": ""} in groups["Harbor Watch"]["relations"]

    # The player edits Oswin's groups: removed ones go, added ones are created.
    response = await client.patch(f"/api/campaigns/{campaign['id']}/characters/{people['Oswin']}",
                                  params={"branch_id": campaign["branch"]["id"]},
                                  json={"role": "priest", "personality": "", "appearance": "", "sex": "", "gender": "", "pronouns": "",
                                        "affiliations": ["Kingdom of Ost"]})
    assert response.status_code == 200, response.text
    state = await detail(client, campaign)
    oswin = next(row for row in state["characters"] if row["name"] == "Oswin")
    assert [(entry["name"], entry["source"]) for entry in oswin["attributes"]["affiliations"]] == [("Kingdom of Ost", "player")]
    assert next(row for row in state["factions"] if row["name"] == "Kingdom of Ost")["kind"] == "nation"


@pytest.mark.asyncio
async def test_regroup_uses_the_state_model(client, scripted):
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    scripted.interpretations.append({"state_changes": [op("CREATE_CHARACTER", "masked man", {"role": "leader of the pursuers"}),
                                                       op("CREATE_CHARACTER", "thin blade", {"role": "subordinate to masked man"})]})
    await play(client, campaign, "I run across the roofs.")
    people = {row["name"]: row["id"] for row in (await detail(client, campaign))["characters"]}
    original = scripted.complete_json

    async def grouping(messages, **options):
        if "group the people of a role-playing story" in messages[0]["content"]:
            return json.dumps({"factions": [{"name": "Masked Crew", "kind": "crew", "description": "Rooftop pursuers"}],
                               "memberships": [{"character_id": people["masked man"], "faction": "Masked Crew", "status": "leader"},
                                               {"character_id": people["thin blade"], "faction": "Masked Crew"},
                                               {"character_id": "not-an-id", "faction": "Masked Crew"}]})
        return await original(messages, **options)

    scripted.complete_json = grouping
    response = await client.post(f"/api/campaigns/{campaign['id']}/factions/sync", params={"branch_id": campaign["branch"]["id"]})
    assert response.status_code == 200, response.text
    crew = next(row for row in response.json()["campaign"]["factions"] if row["name"] == "Masked Crew")
    assert crew["kind"] == "crew" and {member["id"] for member in crew["members"]} == {people["masked man"], people["thin blade"]}


@pytest.mark.asyncio
async def test_regroup_can_remove_a_wrong_story_membership_but_not_a_player_one(client, scripted):
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    scripted.interpretations.append({"state_changes": [
        op("CREATE_CHARACTER", "Tam", {"role": "merchant", "affiliations": ["Harbor Watch"]})]})
    await play(client, campaign, "I watch the docks.")
    tam = next(row for row in (await detail(client, campaign))["characters"] if row["name"] == "Tam")
    await client.patch(f"/api/campaigns/{campaign['id']}/characters/{tam['id']}", params={"branch_id": campaign["branch"]["id"]},
                       json={"role": "merchant", "personality": "", "appearance": "", "sex": "", "gender": "", "pronouns": "",
                             "affiliations": ["Harbor Watch", "Salt Guild"]})
    original = scripted.complete_json

    async def removing(messages, **options):
        if "group the people of a role-playing story" in messages[0]["content"]:
            return json.dumps({"remove": [{"character_id": tam["id"], "faction": "Harbor Watch"}]})
        return await original(messages, **options)

    scripted.complete_json = removing
    response = await client.post(f"/api/campaigns/{campaign['id']}/factions/sync", params={"branch_id": campaign["branch"]["id"]})
    groups = [entry["name"] for entry in next(row for row in response.json()["campaign"]["characters"] if row["name"] == "Tam")["attributes"]["affiliations"]]
    assert groups == ["Harbor Watch", "Salt Guild"], "player-set groups survive the model's corrections"
