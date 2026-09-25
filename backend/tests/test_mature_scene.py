"""Mature scenes: the narrator plans, the user's chosen model writes; never for minors."""

import pytest

from app.services.mature_scene import DIRECTOR_PROMPT, WRITER_RULES, explicit_score, writer_messages
from app.services.portrait_focus import image_aspect
from tests.conftest import ScriptedModel, new_campaign, play
from tests.test_model_routing import clean_roles  # noqa: F401 - fixture


class Director(ScriptedModel):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.plans: list[list[dict]] = []

    async def complete(self, messages, **options):
        if DIRECTOR_PROMPT in messages[-1]["content"]:
            self.plans.append(messages)
            return "- She leads him upstairs.\n- The informant is waiting in the next room."
        return await super().complete(messages, **options)


class Writer(ScriptedModel):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.prompts: list[list[dict]] = []

    async def stream_chat(self, messages, **options):
        self.prompts.append(messages)
        async for piece in super().stream_chat(messages, **options):
            yield piece


@pytest.fixture
def models(monkeypatch):
    from app.api import routes
    from app.services import turn_service

    narrator, writer = Director("gm"), Writer("local-uncensored")

    def factory(profile):
        return writer if profile is not None and profile.role == "mature" else narrator

    monkeypatch.setattr(turn_service, "provider_for_profile", factory)
    monkeypatch.setattr(routes, "provider_for_profile", factory)
    return narrator, writer


async def enable_writer(client):
    response = await client.put("/api/settings/roles", json={"roles": [
        {"role": "mature", "inherit": False, "provider": "openai-compatible", "base_url": "http://127.0.0.1:1234/v1",
         "model": "local-uncensored"}]})
    assert response.status_code == 200, response.text


def test_explicit_detection_and_writer_prompt():
    assert explicit_score("I kiss her neck and undress her.", "") >= 2
    assert explicit_score("I ask the clerk about the parcel.", "The harbor is quiet.") == 0
    assert explicit_score("I drive the blade into his gut and twist.", "Blood soaks the floor; his wounds gape.") >= 2
    assert "gore and injuries" in WRITER_RULES and "only between adults" in WRITER_RULES
    assert len(WRITER_RULES.splitlines()) <= 5, "small models follow a short prompt better"
    messages = [{"role": "system", "content": "GM rules"}, {"role": "user", "content": "I kiss her."}]
    written = writer_messages(messages, "- beat one")
    assert WRITER_RULES in written[0]["content"] and "- beat one" in written[-1]["content"]
    assert image_aspect(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (832).to_bytes(4, "big") + (1216).to_bytes(4, "big") + b"\x08") == 0.6842


@pytest.mark.asyncio
async def test_mature_turn_is_planned_by_narrator_and_written_locally(client, clean_roles, models):  # noqa: F811
    narrator, writer = models
    await enable_writer(client)
    campaign = await new_campaign(client, "My name is Ada, a grown woman and a courier in the harbor city of Ost.")
    writer.narrations.append("The courtesan's robe slips from her shoulders as she draws you toward the bed.")
    event = await play(client, campaign, "I kiss the courtesan and undress her, then take her to bed.")
    assert event["type"] == "complete"
    assert narrator.plans, "the narrator still decides what happens"
    assert writer.prompts and WRITER_RULES in writer.prompts[-1][0]["content"]
    assert "informant is waiting" in writer.prompts[-1][-1]["content"]
    diagnostics = (await client.get(f"/api/campaigns/{campaign['id']}/diagnostics")).json()["turns"][-1]["diagnostics"]
    assert diagnostics["mature_scene"]["directed"] is True


@pytest.mark.asyncio
async def test_ordinary_turns_and_possible_minors_stay_with_the_narrator(client, clean_roles, models):  # noqa: F811
    narrator, writer = models
    await enable_writer(client)
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    assert (await play(client, campaign, "I hand the parcel to the clerk."))["type"] == "complete"
    assert (await play(client, campaign, "I kiss the teenage girl and undress her."))["type"] == "complete"
    assert writer.prompts == [] and narrator.plans == []


@pytest.mark.asyncio
async def test_without_a_mature_model_the_narrator_writes_everything(client, clean_roles, models):  # noqa: F811
    narrator, writer = models
    campaign = await new_campaign(client, "My name is Ada, a courier in the harbor city of Ost.")
    assert (await play(client, campaign, "I kiss the courtesan and undress her."))["type"] == "complete"
    assert writer.prompts == [] and narrator.plans == []


@pytest.mark.asyncio
async def test_graphic_violence_also_goes_to_the_mature_writer(client, clean_roles, models):  # noqa: F811
    narrator, writer = models
    await enable_writer(client)
    campaign = await new_campaign(client, "My name is Ada, a grown woman and a mercenary in the harbor city of Ost.")
    writer.narrations.append("The blade opens the smuggler's arm to the bone and blood sheets onto the planks.")
    event = await play(client, campaign, "I stab the smuggler and drive the blade until the wound tears open.")
    assert event["type"] == "complete"
    assert narrator.plans and writer.prompts, "violent turns are planned by the narrator and written by the mature model"


def test_minor_check_needs_a_clear_signal():
    from app.services.mature_scene import CLEAR_MINOR
    assert not CLEAR_MINOR.search("The girl at the bar is thirty, with a minor wound on her arm.")
    assert CLEAR_MINOR.search("a teenage courier") and CLEAR_MINOR.search("the child hides")
