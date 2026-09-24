"""Deterministic identity, fact, objective, canon, time, and memory rules (no database)."""

import pytest

from app.llm.structured import parse_json_response, salvage_interpretation
from app.services import world_time
from app.services.canon_guard import (
    action_canon_notes,
    dead_character_acts,
    validate_general_operation,
)
from app.services.constitution import derive_constitution
from app.services.embeddings import HashedEmbedding
from app.services.entity_resolver import (
    CharacterCandidate,
    EntityResolver,
    detect_identity_reveals,
    resolve_named,
)
from app.services.identity import is_unknown, merge_scalar, same_statement
from app.services.objectives import same_objective, verb_class
from app.services.retrieval import cosine_similarity, score_memory


def resolver(*people, location="temple interior"):
    return EntityResolver([CharacterCandidate("p", "Ada", is_player=True), *people], "Ada", location)


def test_alias_title_and_canonical_name_resolve_to_one_person():
    mara = CharacterCandidate("s", "Mara", [("Mara", "CANONICAL_NAME"), ("woman in the dark coat", "DESCRIPTION")],
                               role="former High Archivist")
    current = CharacterCandidate("hp", "High Archivist", role="High Archivist")
    world = resolver(mara, current)
    for reference in ("Mara", "mara", "Former High Archivist", "the woman in the dark coat", "Mara (former High Archivist)"):
        result = world.resolve(reference)
        assert (result.status, result.character_id) == ("RESOLVED", "s"), reference
    assert world.resolve("High Archivist").character_id == "hp"
    assert world.resolve("player").status == "PLAYER"
    assert world.resolve("Oren").status == "NEW"


def test_generic_descriptors_do_not_auto_merge():
    guards = [CharacterCandidate("g1", "City Guard"), CharacterCandidate("g2", "temple guard"),
              CharacterCandidate("g3", "Unnamed Guard")]
    world = resolver(*guards)
    assert world.resolve("the guard").status in {"AMBIGUOUS", "NEW"}
    assert world.resolve("Guard 2").status == "NEW"
    assert world.resolve("second guard").status == "NEW"
    two_men = resolver(CharacterCandidate("a", "masked man"), CharacterCandidate("b", "Burning man"))
    assert two_men.resolve("burning man").character_id == "b"
    assert two_men.resolve("bleeding masked man").character_id == "a"


def test_descriptor_variant_uses_story_evidence():
    boy = CharacterCandidate("b", "Boy", first_meeting_place="Tanner's Row")
    world = resolver(boy, location="Tanner's Row")
    assert world.resolve("Street boy").character_id == "b"
    coat = CharacterCandidate("d", "man in the dark coat", in_scene=False)
    assert resolver(coat).resolve("woman in the dark coat").status == "AMBIGUOUS"
    coat.in_scene = True
    assert resolver(coat).resolve("woman in the dark coat").character_id == "d"


def test_identity_reveal_in_narration():
    coat = CharacterCandidate("d", "man in the dark coat", in_scene=True, last_seen=10)
    narration = ("His hood blows back. Underneath it is a woman's face. The woman in the dark coat does not look away. "
                 "\"My name is Mara,\" she says to you. \"I was the High Archivist before this one.\"")
    reveals = detect_identity_reveals(narration, resolver(coat), {"d"})
    assert [(reveal.character_id, reveal.name, reveal.former_role) for reveal in reveals] == [
        ("d", "Mara", "former High Archivist")]
    assert detect_identity_reveals("\"I am not here to stop you,\" she says.", resolver(coat), {"d"}) == []


def test_unknown_or_weaker_values_never_overwrite_known_values():
    assert merge_scalar("former High Archivist", "DIRECT_OBSERVATION", "unknown", "DIRECT_OBSERVATION")[0] == "former High Archivist"
    assert merge_scalar("former High Archivist", "DIRECT_OBSERVATION", "", "PLAYER_EXPLICIT")[0] == "former High Archivist"
    assert merge_scalar("former High Archivist", "DIRECT_OBSERVATION", "High Archivist", "DIRECT_OBSERVATION", field="role")[0] \
        == "former High Archivist"
    assert merge_scalar("smuggler", "DIRECT_OBSERVATION", "spy", "RUMOR")[0] == "smuggler"
    assert merge_scalar("smuggler", "RUMOR", "spy", "DIRECT_OBSERVATION")[0] == "spy"
    assert merge_scalar("smuggler", "PLAYER_EXPLICIT", "spy", "DIRECT_OBSERVATION")[0] == "smuggler"
    assert merge_scalar("unknown", None, "priest", "INFERRED") == ("priest", "INFERRED", "filled")
    assert is_unknown("Unknown man") and is_unknown("") and not is_unknown("Mara")


def test_fact_deduplication_is_wording_tolerant():
    assert same_statement("Mara revealed she was the former High Archivist.",
                          "Mara revealed that she was the former High Archivist")
    assert not same_statement("Mara hates the temple.", "Mara owns a boat.")


def test_objective_identity():
    assert same_objective("Acquire the Ledger", [], "Acquire the debt ledger")
    assert not same_objective("Find Mara", [], "Avoid the pleasure district tonight")
    assert not same_objective("Find the key", [], "Use the key")
    assert verb_class("Find Mara") == "meet"


def test_item_and_location_identity():
    items = [("1", "Ledger", []), ("2", "Raw dark matter bars", [])]
    assert resolve_named("the debt ledger", items).character_id == "1"
    assert resolve_named("The Ledger", items).character_id == "1"
    assert resolve_named("dark matter", items).character_id == "2"
    places = [("a", "temple plaza", []), ("b", "Plaza of Chains", []), ("c", "temple interior", [])]
    assert resolve_named("Plaza of Chains", places, min_subset_tokens=2).character_id == "b"
    assert resolve_named("plaza", places, min_subset_tokens=2).status == "NEW"


def test_constitution_captures_magic_theft_as_an_ability():
    constitution = derive_constitution(
        "My name is Ada. I was born with a power no other mage possesses: the ability to steal magic. "
        "When I touch a mage's skin, I take their magic. Their fireball. Their teleportation. I catalog every stolen spell.")
    [ability] = constitution.ability_catalogue
    assert ability["name"] == "Magic theft" and ability["source"] == "innate"
    assert "fireball" in ability["description"].casefold()
    assert any(rule["type"] == "ABILITY" and rule["strength"] == "HARD" for rule in constitution.rules)


class Ability:
    def __init__(self, name, description=""):
        self.name, self.description, self.aliases, self.status = name, description, [], "ACTIVE"


def test_canon_guard_general_checks():
    notes = action_canon_notes("I use my teleportation skill and draw my silver dagger.",
                               inventory_names=["Ledger"], abilities=[Ability("Magic theft", "steal magic")],
                               innate_copying=True)
    assert {note["type"] for note in notes} == {"UNKNOWN_ABILITY", "UNOWNED_ITEM"}
    assert not action_canon_notes("I use the white flame.", inventory_names=[],
                                  abilities=[Ability("white flame")], innate_copying=False)
    assert dead_character_acts("Oren smiles at you.", ["Oren"])
    assert not dead_character_acts("You remember how Oren smiles.", ["Oren"])
    assert validate_general_operation({"kind": "CHANGE_CHARACTER_STATUS", "value": {"status": "alive"}},
                                      current_status="dead", resurrection=False, player_name="Ada")
    assert not validate_general_operation({"kind": "CHANGE_CHARACTER_STATUS", "value": {"status": "alive", "resurrected": True}},
                                          current_status="dead", resurrection=True, player_name="Ada")


def test_world_time_advances_into_readable_labels():
    clock = world_time.advance({}, 60, "Torchlight flickers across the plaza.")
    assert clock["label"] == "Night, Day 1"
    clock = world_time.advance({"world_clock": clock}, 8 * 3600, "Dawn breaks over the docks.")
    assert clock["day"] == 2 and clock["time_of_day"] == "dawn"
    quoted = world_time.advance({"world_clock": clock}, 60, "\"You will be dead by morning,\" he says.")
    assert quoted["time_of_day"] == "dawn"


@pytest.mark.asyncio
async def test_hashed_embeddings_support_semantic_style_retrieval():
    model = HashedEmbedding()
    query, related, unrelated = await model.embed(["Where is the stolen ledger hidden?",
                                                   "Ada hid the ledger he stole beneath the east pier warehouse.",
                                                   "The priests chant at dawn in the temple."])
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)
    important = {"content": "Mara revealed she was the former High Archivist.", "importance": 0.8,
                 "memory_type": "DISCOVERY", "characters": ["Mara"], "embedding": related}
    transcript = {"content": "Player: I look at Mara. World: Mara looks back.", "importance": 0.25,
                  "memory_type": "TURN", "characters": ["Mara"], "embedding": unrelated}
    assert score_memory(important, "What do I know about Mara?", {"mara"}) > \
        score_memory(transcript, "What do I know about Mara?", {"mara"})


def test_local_model_output_salvage():
    raw = '```json\n{"state_changes":[{"kind":"move_player","value":"docks"},{"kind":"CREATE_CHARACTER","name":"Mara",' \
          '"value":{"role":"smuggler"},"visibility":"LOW","certainty":"high"},{"kind":"FLY"},],"time_elapsed_seconds":"90"}\n```'
    interpretation, dropped = salvage_interpretation(parse_json_response(raw))
    kinds = [operation.kind for operation in interpretation.state_changes]
    assert kinds == ["MOVE_CHARACTER", "CREATE_CHARACTER"] and dropped == 1
    assert interpretation.state_changes[1].visibility == "PLAYER_KNOWN"
    assert interpretation.time_elapsed_seconds == 90


def test_living_profile_merges_without_losing_history():
    from app.services.mood import read_mood
    from app.services.story_profile import merge_changes, seed_profile

    profile = seed_profile({"traits": ["People call me a thief."], "goals": ["I want the High Archivist's throne."],
                            "world_rules": []})
    goal_id = profile["goals"][0]["id"]
    trait_id = profile["traits"][0]["id"]
    notes = merge_changes(profile, {
        "add": {"traits": ["Keeps his word to those who help him."], "world_rules": ["The Pale Lantern chooses what it burns."],
                "reputation": ["The temple wants him dead."]},
        "retire": [{"id": goal_id, "reason": "He took the throne"}],
        "update": [{"id": trait_id, "text": "Known across the city as the thief who burned the temple."}],
    }, 30)
    assert len(notes) == 5
    assert profile["goals"][0]["status"] == "past" and profile["goals"][0]["ended_turn_index"] == 30
    assert [row["status"] for row in profile["traits"]] == ["past", "active", "active"]
    assert profile["traits"][0]["source"] == "premise"
    assert any(row.get("replaces") == trait_id for row in profile["traits"])
    assert not merge_changes(profile, {"add": {"traits": ["Keeps his word to those who help him"]}}, 31)
    assert read_mood("Blood sprays as the blade strikes. He lunges again, sword raised.")["mood"] in {"combat", "danger"}
    assert read_mood("\"Blood and fire!\" she laughs softly. The tea is warm and the room is quiet.")["mood"] == "calm"
