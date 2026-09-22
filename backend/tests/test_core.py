from app.schemas import StateOperation
from app.services.canon_guard import CanonViolation, check_narrative, validate_state_operation
from app.services.constitution import derive_constitution, derive_theme
from app.services.retrieval import rank_memories
from app.services.turn_service import parse_json_response


def test_absolute_immortality_is_a_hard_rule():
    constitution = derive_constitution("I am Malachar, an immortal demon king. There is no exception to my immortality.")
    rule = constitution.hard_invariants[0]
    assert rule["type"] == "PLAYER_CANNOT_DIE"
    assert rule["exceptions"] == []
    assert check_narrative("You are dead.", constitution.model_dump(), [])
    try:
        validate_state_operation({"kind": "CHANGE_CHARACTER_STATUS", "subject": "Malachar", "value": {"status": "dead"}}, constitution.model_dump(), [], {})
    except CanonViolation:
        pass
    else:
        raise AssertionError("Hard canon allowed the protagonist to die")


def test_permitted_hidden_exception_is_distinct_from_absolute_rule():
    constitution = derive_constitution("I am Malachar, immortal. There may be a hidden way to end me.")
    assert constitution.hidden_canon_permissions
    assert derive_theme("cyberpunk neon street", "Cyberpunk").family == "cyberpunk"


def test_relevant_memory_ranks_above_unrelated_memory():
    memories = [
        {"content": "A quiet morning at the harbor", "importance": 0.8, "keywords": []},
        {"content": "The envoy hid the iron seal in the gatehouse", "importance": 0.6, "keywords": ["envoy"], "characters": ["Envoy"]},
    ]
    assert rank_memories(memories, "Where did the envoy hide the iron seal?", {"Envoy"})[0] is memories[1]


def test_structured_response_can_recover_fenced_json():
    assert parse_json_response('```json\n{"events": []}\n```') == {"events": []}


def test_interpreter_accepts_common_visibility_alias():
    operation = StateOperation.model_validate({"kind": "CREATE_LOCATION", "name": "Gatehouse", "visibility": "WORLD"})
    assert operation.visibility == "PLAYER_KNOWN"
