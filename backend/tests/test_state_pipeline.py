"""End-to-end state integrity through the real turn pipeline, with a scripted model.

The scripted interpretations deliberately include the mistakes weak local models make:
creating a known person again, "unknown" roles, partial fact lists, duplicate objectives.
"""

import pytest
from sqlalchemy import select

from app.db.models import Character, CharacterAlias, Memory, Turn
from app.db.session import SessionLocal
from tests.conftest import character_id, detail, new_campaign, play

PROMPT = ("My name is Ada. I am a thief in the archive city of Velen. I was born with the ability to steal magic. "
          "When I touch a mage, I take their spells.")


def op(kind, name="", value=None, **extra):
    return {"kind": kind, "name": name, "value": value or {}, "certainty": "CONFIRMED", "visibility": "PLAYER_KNOWN", **extra}


def by_name(state: dict, name: str) -> list[dict]:
    return [person for person in state["characters"] if person["name"].casefold() == name.casefold()]


@pytest.mark.asyncio
async def test_mara_identity_survives_reveal_title_and_repeated_creation(client, scripted):
    campaign = await new_campaign(client, PROMPT)

    # 1. An unnamed woman in a dark coat.
    scripted.narrations.append("In the temple plaza, a woman in a dark coat watches you from beneath her hood. "
                               "The High Archivist stands on the temple steps.")
    scripted.interpretations.append({"state_changes": [
        op("MOVE_CHARACTER", "player", {"location": "temple plaza"}),
        op("CREATE_CHARACTER", "woman in a dark coat", {"role": "hooded watcher", "known_facts": ["Watches from beneath a hood"]}),
        op("CREATE_CHARACTER", "High Archivist", {"role": "High Archivist"}),
    ], "time_elapsed_seconds": 120})
    assert (event := await play(client, campaign, "I look around the plaza."))["type"] == "complete", event

    # 2. She says her name. A weak model creates "Mara" as a new person instead of revealing.
    scripted.narrations.append("The woman in the dark coat pushes back her hood. \"My name is Mara,\" she says. "
                               "\"I was the High Archivist before this one.\"")
    scripted.interpretations.append({"state_changes": [op("CREATE_CHARACTER", "Mara", {"gender": "woman", "role": "unknown"})]})
    assert (event := await play(client, campaign, "I ask who she is."))["type"] == "complete", event

    # 3. The model creates "Former High Archivist" and downgrades Mara's role with a partial fact list.
    scripted.narrations.append("Mara glances at the steps. \"She took my temple,\" Mara says. \"I hate what it has become.\"")
    scripted.interpretations.append(lambda request: {"state_changes": [
        op("CREATE_CHARACTER", "Former High Archivist", {"role": "former High Archivist", "known_facts": ["Ruled the temple before"]}),
        op("UPDATE_CHARACTER", "Mara", {"role": "unknown", "known_facts": ["Hates what the temple has become"]},
           character_id=character_id(request, "Mara")),
    ]})
    assert (event := await play(client, campaign, "I listen."))["type"] == "complete", event

    # 4. Another NPC: "Find the former High Archivist."
    scripted.narrations.append("A priest named Oren catches your sleeve. \"Find the former High Archivist,\" he whispers.")
    scripted.interpretations.append({"state_changes": [
        op("CREATE_CHARACTER", "Oren", {"role": "priest"}),
        op("CREATE_OBJECTIVE", "Find the former High Archivist", {"description": "Oren asked me to find her."}),
    ]})
    completed = await play(client, campaign, "I nod to the priest.")
    assert completed["type"] == "complete"

    # 5. "Find Mara." 6. more facts. 7. an NPC-NPC relationship.
    scripted.narrations.append("Oren adds, \"Find Mara before the High Archivist does.\" "
                               "On the steps, the High Archivist watches Mara with open contempt.")
    scripted.interpretations.append(lambda request: {
        "state_changes": [
            op("CREATE_OBJECTIVE", "Find Mara", {"description": "Oren wants Mara found."}),
            op("UPDATE_CHARACTER", "", {"known_facts": ["Has cropped gray hair"]}, character_id=character_id(request, "Mara")),
        ],
        "relationship_changes": [{"from_id": character_id(request, "High Archivist"), "to_id": character_id(request, "Mara"),
                                  "deltas": {"hostility": 30, "trust": -20},
                                  "reason": "The High Archivist sees Mara as a traitor to the temple"}],
    })
    assert (event := await play(client, campaign, "I ask Oren why."))["type"] == "complete", event

    state = await detail(client, campaign)
    mara = by_name(state, "Mara")
    assert len(mara) == 1, [person["name"] for person in state["characters"]]
    mara = mara[0]
    assert not by_name(state, "Former High Archivist")
    assert not by_name(state, "woman in a dark coat")
    aliases = {entry["alias"].casefold() for entry in mara["aliases"]}
    assert "woman in a dark coat" in aliases
    assert "former high archivist" in aliases
    assert mara["role"] == "former High Archivist"
    facts = {fact["content"] for fact in mara["facts"]}
    assert {"Watches from beneath a hood", "Hates what the temple has become", "Has cropped gray hair", "Ruled the temple before"} <= facts

    edge = next(relation for relation in state["relationships"] if relation["from"] == "High Archivist" and relation["to"] == "Mara")
    assert edge["dimensions"]["hostility"] == 30
    assert edge["dimensions"]["trust"] == 30
    reasons = {event["reason"] for event in edge["events"]}
    assert reasons == {"The High Archivist sees Mara as a traitor to the temple"}
    for relation in state["relationships"]:
        assert all("appeared in the story" not in event["reason"].casefold() for event in relation["events"])

    active = [objective["title"] for objective in state["objectives"] if objective["status"] == "active"]
    assert "Find Mara" not in active and "Find the former High Archivist" not in active
    finding = [objective for objective in state["objectives"] if objective["title"] == "Find the former High Archivist"]
    assert finding and finding[0]["status"] == "completed" and "Find Mara" in finding[0]["aliases"]

    # The interpreter was offered Mara with her id and aliases, which is what lets small models use IDs.
    last_request = scripted.requests[-1]
    card = next(card for card in last_request["known_characters"] if card["canonical_name"] == "Mara")
    assert "woman in a dark coat" in [alias.casefold() for alias in card["aliases"]]

    async with SessionLocal() as session:
        rows = (await session.scalars(select(Character).where(Character.branch_id == campaign["branch"]["id"]))).all()
        assert sum(1 for row in rows if row.name == "Mara") == 1
        alias_rows = (await session.scalars(select(CharacterAlias).where(CharacterAlias.character_id == mara["id"]))).all()
        assert any(row.alias_type == "CANONICAL_NAME" and row.alias == "Mara" for row in alias_rows)


@pytest.mark.asyncio
async def test_failed_generation_is_not_canonical_and_retry_reuses_the_turn(client, scripted):
    campaign = await new_campaign(client, PROMPT)
    scripted.fail_narration = True
    event = await play(client, campaign, "I pick the lock.")
    assert event["type"] == "error"
    assert (await detail(client, campaign))["turns"] == []
    scripted.fail_narration = False
    scripted.narrations.append("The lock clicks open.")
    event = await play(client, campaign, "I pick the lock.")
    assert event["type"] == "complete" and event["turn_index"] == 1
    state = await detail(client, campaign)
    assert [turn["turn_index"] for turn in state["turns"]] == [1]
    async with SessionLocal() as session:
        rows = (await session.scalars(select(Turn).where(Turn.campaign_id == campaign["id"]))).all()
        assert len(rows) == 1, "a retry must reuse the failed attempt instead of adding another turn 1"
        assert rows[0].canonical and rows[0].attempt == 2
    exported = (await client.get(f"/api/campaigns/{campaign['id']}/export")).json()
    assert exported["version"] == 2 and len(exported["turns"]) == 1


@pytest.mark.asyncio
async def test_summary_failure_never_rolls_back_turns_and_falls_back(client, scripted):
    campaign = await new_campaign(client, PROMPT)
    scripted.summary_fail = True
    for index in range(1, 10):
        scripted.narrations.append(f"Scene {index}: the alley stays wet and quiet.")
        assert (event := await play(client, campaign, f"I wait, beat {index}."))["type"] == "complete", event
    state = await detail(client, campaign)
    assert len(state["turns"]) == 9
    summary = state["summary_state"]
    assert summary["through_turn_index"] == 9 and summary["method"] == "deterministic"
    assert "SummaryError" in summary["last_error"] or summary["last_error"]
    assert "Scene 9" in state["summary"] or "beat 9" in state["summary"]
    scripted.summary_fail = False


@pytest.mark.asyncio
async def test_items_objectives_and_duplicates_reconcile(client, scripted):
    campaign = await new_campaign(client, PROMPT)
    scripted.narrations.append("The High Archivist keeps a debt ledger on the dais.")
    scripted.interpretations.append({"state_changes": [
        op("CREATE_OBJECTIVE", "Acquire the Ledger", {"description": "Take the ledger from the dais."}),
        op("CREATE_OBJECTIVE", "Acquire the debt ledger", {"description": "Steal the High Archivist's ledger of debts."}),
    ]})
    await play(client, campaign, "I study the dais.")
    state = await detail(client, campaign)
    assert [objective["title"] for objective in state["objectives"] if objective["status"] == "active"].count("Acquire the Ledger") == 1
    assert not [objective for objective in state["objectives"] if objective["title"] == "Acquire the debt ledger"]

    scripted.narrations.append("You snatch the Ledger from the dais.")
    scripted.interpretations.append({"state_changes": [op("ADD_ITEM", "The Ledger", {"owner": "player", "quantity": 1})]})
    event = await play(client, campaign, "I grab the ledger.")
    assert any("Objective completed" in change["text"] for change in event["changes"])
    scripted.narrations.append("The ledger is heavy in your arms.")
    scripted.interpretations.append({"state_changes": [op("ADD_ITEM", "ledger", {"owner": "Ada", "quantity": 1}),
                                                       op("UPDATE_OBJECTIVE", "Acquire the Ledger", {"status": "active"})]})
    await play(client, campaign, "I hold the ledger.")
    state = await detail(client, campaign)
    ledgers = [item for item in state["inventory"] if "ledger" in item["name"].casefold()]
    assert len(ledgers) == 1 and ledgers[0]["quantity"] == 1
    objective = next(objective for objective in state["objectives"] if objective["title"] == "Acquire the Ledger")
    assert objective["status"] == "completed", "a completed objective must not reactivate without a retcon"


@pytest.mark.asyncio
async def test_abilities_statuses_time_and_memories(client, scripted):
    campaign = await new_campaign(client, PROMPT)
    state = await detail(client, campaign)
    player = next(person for person in state["characters"] if person["name"] == "Ada")
    assert [ability["name"] for ability in player["abilities"]] == ["Magic theft"]

    scripted.narrations.append("At dusk you touch the priestess's palm and her small flame slides into you. "
                               "A roof tile slices your shoulder as you run.")
    scripted.interpretations.append({"state_changes": [
        op("GAIN_ABILITY", "Small flame", {"description": "A palm-sized white flame", "source": "copied from the High Archivist"}),
        op("CHANGE_CHARACTER_STATUS", "player", {"status": "standing, injured shoulder, hidden in the shadows of the roof",
                                                 "injuries": ["cut shoulder"]}),
    ], "new_memories": [
        {"content": "Ada copied the High Archivist's small flame.", "type": "ABILITY_GAINED", "importance": 0.9},
        {"content": "Ada copied the High Archivist's small flame!", "type": "ABILITY_GAINED"},
    ], "time_elapsed_seconds": 900})
    event = await play(client, campaign, "I touch her palm and steal the flame.")
    assert event["metrics"].get("memories_deduplicated", 0) >= 1
    state = await detail(client, campaign)
    player = next(person for person in state["characters"] if person["name"] == "Ada")
    assert {ability["name"] for ability in player["abilities"]} == {"Magic theft", "Small flame"}
    assert state["current_state"]["player_status"].startswith("standing, injured shoulder, hidden in the shadows")
    assert state["current_state"]["world_clock"]["time_of_day"] in {"evening", "night"}
    assert "Day 1" in state["current_state"]["world_time"]

    # The canon guard notices an ability the catalogue does not contain.
    scripted.narrations.append("You reach for a power you have never held.")
    await play(client, campaign, "I use my teleportation skill to vanish.")
    diagnostics = (await client.get(f"/api/campaigns/{campaign['id']}/diagnostics")).json()
    notes = diagnostics["turns"][-1]["diagnostics"]["canon_notes"]
    assert any(note["type"] == "UNKNOWN_ABILITY" for note in notes)

    async with SessionLocal() as session:
        rows = (await session.scalars(select(Memory).where(Memory.branch_id == campaign["branch"]["id"]))).all()
        assert all(row.embedding_model.startswith("hash-") for row in rows), "post-turn embedding job should cover all memories"


@pytest.mark.asyncio
async def test_weak_model_ids_invented_ids_rejected_offered_ids_used(client, scripted):
    campaign = await new_campaign(client, PROMPT)
    scripted.narrations.append("A priest named Oren blocks the door. \"Wait,\" Oren says.")

    def interpretation(request):
        offered = {row["mention"]: row["id"] for row in request["new_people"]}
        assert "Oren" in offered
        return {"state_changes": [
            op("UPDATE_CHARACTER", "", {"status": "suspicious"}, character_id="c1"),
            op("CREATE_CHARACTER", "", {"role": "priest"}, character_id=offered["Oren"]),
        ]}

    scripted.interpretations.append(interpretation)
    event = await play(client, campaign, "I stop.")
    assert event["metrics"].get("invented_ids") == 1 and event["metrics"].get("new_person_ids_used") == 1
    names = [person["name"] for person in (await detail(client, campaign))["characters"]]
    assert "Oren" in names and "c1" not in names
