import re
from copy import deepcopy
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Branch,
    Campaign,
    CanonRule,
    Character,
    Checkpoint,
    Item,
    Location,
    Memory,
    ModelProfile,
    Objective,
)
from app.schemas import CampaignCreate
from app.services.abilities import gain_ability
from app.services.character_store import add_alias
from app.services.constitution import derive_constitution, derive_theme, infer_player, infer_title
from app.services.memory_service import add_memory
from app.services.post_turn import enqueue
from app.services.state_service import capture_snapshot
from app.services.story_profile import seed_profile
from app.services.world_time import initial_clock


async def create_campaign(session: AsyncSession, payload: CampaignCreate) -> tuple[Campaign, Branch]:
    constitution = derive_constitution(payload.prompt)
    if payload.character_name and payload.character_name.strip():
        constitution.player_identity = payload.character_name.strip()
    identity = dict(constitution.starting_state.get("identity") or {})
    for key, value in (("sex", payload.character_sex), ("gender", payload.character_gender),
                       ("pronouns", payload.character_pronouns)):
        if value:
            identity[key] = value
    constitution.starting_state["identity"] = identity
    if payload.starting_money is not None and payload.money_currency and payload.money_currency.strip():
        constitution.starting_state["money"] = {"amount": payload.starting_money,
                                                 "currency": payload.money_currency.strip().casefold()}
    theme = derive_theme(payload.prompt, constitution.genre, constitution.tone, payload.theme_family).model_dump(mode="json")
    campaign = Campaign(
        title=(payload.title or (f"{constitution.player_identity}'s world" if payload.character_name and
              infer_player(payload.prompt) == "You" else infer_title(payload.prompt))).strip(),
        original_prompt=payload.prompt.strip(), constitution=constitution.model_dump(mode="json"),
        theme_profile=theme, protagonist_name=constitution.player_identity, genre=constitution.genre,
        game_mode=payload.game_mode,
    )
    session.add(campaign)
    await session.flush()
    branch = Branch(campaign_id=campaign.id, name="First thread", current_state={
        "world_time": "Day 1", "player_status": "alive", "current_location": "",
        "confirmed_canon_exceptions": [], "elapsed_seconds": 0,
        **({"money": constitution.starting_state["money"]} if constitution.starting_state.get("money") else {}),
    })
    session.add(branch)
    await session.flush()
    branch.current_state = {**branch.current_state, "world_clock": initial_clock(), "world_time": "Day 1",
                            "player_profile": seed_profile(constitution.model_dump(mode="json")),
                            "scene_mood": {"mood": "calm", "intensity": 0.2, "time_of_day": "", "source": "setup"}}
    campaign.active_branch_id = branch.id
    player = Character(campaign_id=campaign.id, branch_id=branch.id, name=constitution.player_identity,
        role="Player character", status="alive", personality="", motivations=[], visibility="PLAYER_KNOWN",
        importance="MAJOR", first_seen_turn_index=0,
        provenance={key: "PLAYER_EXPLICIT" for key in constitution.starting_state.get("identity", {})},
        attributes={"abilities": constitution.abilities, "powers": constitution.powers,
                    "limitations": constitution.limitations, "origin": constitution.origin,
                    "traits": constitution.traits, "history": constitution.history,
                    **constitution.starting_state.get("identity", {}),
                    **({"money": constitution.starting_state["money"]} if constitution.starting_state.get("money") else {})})
    session.add(player)
    await session.flush()
    await add_alias(session, player, player.name, "CANONICAL_NAME", turn_index=0, source="player_setup", confidence=1.0)
    for entry in constitution.ability_catalogue:
        await gain_ability(session, player, entry, provenance="PLAYER_EXPLICIT", turn_index=0, source_default="campaign_setup")
    for goal in constitution.preferences[:5]:
        session.add(Objective(campaign_id=campaign.id, branch_id=branch.id, title=goal[:160], description=goal[:2000],
                              status="active", visibility="PLAYER_KNOWN", created_turn_index=0))
    pending: list = []
    for fact in [*constitution.history, *constitution.important_relationships, *constitution.world_rules][:20]:
        await add_memory(session, campaign_id=campaign.id, branch_id=branch.id, content=fact, kind="SETUP",
                         importance=0.8, confidence=1.0, turn_index=0, characters=[constitution.player_identity],
                         character_ids=[str(player.id)], pending=pending)
    for invariant in constitution.hard_invariants:
        session.add(CanonRule(campaign_id=campaign.id, rule_type=invariant["type"],
            statement="The player cannot permanently die." if invariant["type"] == "PLAYER_CANNOT_DIE" else str(invariant),
            strength=invariant.get("strength", "HARD"), exceptions=invariant.get("exceptions", []),
            visibility="PLAYER_KNOWN", source="campaign_setup"))
    await session.flush()
    enqueue(session, campaign_id=campaign.id, branch_id=branch.id, kind="CONSTITUTION_EXTRACTION")
    session.add(Checkpoint(campaign_id=campaign.id, branch_id=branch.id, turn_index=0, state_snapshot=await capture_snapshot(session, branch)))
    if not await session.scalar(select(ModelProfile.id).where(ModelProfile.active.is_(True))):
        session.add(ModelProfile())
    await session.commit()
    await session.refresh(campaign)
    await session.refresh(branch)
    return campaign, branch


async def get_history_rows(session: AsyncSession, campaign_id: UUID, branch_id: UUID):
    from app.db.models import Branch
    from app.services.context_builder import history_for_branch
    branch = await session.get(Branch, branch_id)
    if not branch or branch.campaign_id != campaign_id:
        return None
    return await history_for_branch(session, branch.head_turn_id, limit=300)


async def refresh_setup_from_premise(session: AsyncSession, campaign: Campaign) -> None:
    """Fill missing setup facts without overwriting facts established during play."""
    derived = derive_constitution(campaign.original_prompt).model_dump(mode="json")
    current = dict(campaign.constitution or {})
    for field in ("history", "known_history", "traits", "abilities", "powers", "important_relationships",
                  "world_rules", "important_factions", "preferences", "narrative_preferences"):
        if not current.get(field):
            current[field] = derived[field]
    if not current.get("premise") or (len(str(current["premise"])) >= 1190 and
                                      campaign.original_prompt.startswith(str(current["premise"]))):
        current["premise"] = derived["premise"]
    if ((current.get("genre") == "War" and not re.search(r"\bwar\b", campaign.original_prompt, re.IGNORECASE)) or
            (current.get("genre") == "Open world" and derived["genre"] != "Open world")):
        current["genre"] = derived["genre"]
        campaign.genre = derived["genre"]
    starting = dict(current.get("starting_state") or {})
    source_starting = derived.get("starting_state") or {}
    identity = {**(source_starting.get("identity") or {}), **(starting.get("identity") or {})}
    starting["identity"] = identity
    if "money" not in starting and source_starting.get("money"):
        starting["money"] = source_starting["money"]
    current["starting_state"] = starting
    old_name = campaign.protagonist_name
    new_name = derived["player_identity"] if old_name.casefold() in {"you", "player", "protagonist"} else old_name
    if new_name != old_name:
        current["player_identity"] = new_name
        campaign.protagonist_name = new_name
    campaign.constitution = current

    branches = list((await session.scalars(select(Branch).where(Branch.campaign_id == campaign.id))).all())
    for branch in branches:
        character = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(old_name)))
        collision = await session.scalar(select(Character).where(
            Character.branch_id == branch.id, Character.name.ilike(new_name))) if new_name != old_name else None
        if character and not collision:
            prior_attributes = character.attributes or {}
            character.name = new_name
            character.attributes = {
                **prior_attributes,
                **{key: current.get(key, []) for key in ("traits", "history", "abilities", "powers")
                   if not prior_attributes.get(key)},
                **{key: value for key, value in identity.items() if key not in prior_attributes},
            }
        if new_name != old_name and not collision:
            for item in (await session.scalars(select(Item).where(
                Item.branch_id == branch.id, Item.owner_name.ilike(old_name)))).all():
                item.owner_name = new_name
        if "money" not in (branch.current_state or {}) and starting.get("money"):
            branch.current_state = {**(branch.current_state or {}), "money": starting["money"]}
        from app.services.context_builder import history_for_branch
        last_recorded_location = ""
        for turn in await history_for_branch(session, branch.head_turn_id, limit=300):
            for operation in (turn.state_delta or {}).get("operations", []):
                if not isinstance(operation, dict) or not isinstance(operation.get("value"), dict):
                    continue
                kind = operation.get("kind")
                value = operation["value"]
                name = str(operation.get("name") or operation.get("subject") or "")[:160]
                if kind in {"CREATE_LOCATION", "UPDATE_LOCATION"}:
                    place = str(value.get("location") or name)[:160]
                    if place and place.casefold() not in {"player", "protagonist", "you", old_name.casefold()}:
                        location = await session.scalar(select(Location).where(
                            Location.branch_id == branch.id, Location.name.ilike(place)))
                        if location is None:
                            session.add(Location(campaign_id=campaign.id, branch_id=branch.id, name=place,
                                description=str(value.get("description", ""))[:3000],
                                region=str(value.get("region", ""))[:160],
                                properties={key: entry for key, entry in value.items() if key not in
                                            {"location", "description", "region", "properties"}},
                                visibility=str(operation.get("visibility", "PLAYER_KNOWN"))[:24]))
                    if name.casefold() in {"player", "protagonist", "you", old_name.casefold(), new_name.casefold()}:
                        last_recorded_location = place
                if kind in {"CREATE_CHARACTER", "UPDATE_CHARACTER", "MOVE_CHARACTER", "CHANGE_CHARACTER_STATUS"} and name:
                    is_player = name.casefold() in {"player", "protagonist", "you", old_name.casefold(), new_name.casefold()}
                    person_name = new_name if is_player else name
                    person = await session.scalar(select(Character).where(
                        Character.branch_id == branch.id, Character.name.ilike(person_name)))
                    if person is None:
                        person = Character(campaign_id=campaign.id, branch_id=branch.id, name=person_name,
                            role=str(value.get("role", ""))[:160], status=str(value.get("status", "alive"))[:32],
                            visibility=str(operation.get("visibility", "PLAYER_KNOWN"))[:24],
                            attributes={})
                        session.add(person)
                        await session.flush()
                    attributes = value.get("attributes") if isinstance(value.get("attributes"), dict) else {}
                    flat = {key: entry for key, entry in value.items() if key not in
                            {"role", "personality", "status", "motivations", "attributes"}}
                    if is_player:
                        attributes = {key: entry for key, entry in attributes.items() if key not in
                                      {"sex", "gender", "pronouns"}}
                        flat = {key: entry for key, entry in flat.items() if key not in
                                {"sex", "gender", "pronouns"}}
                    person.attributes = {**attributes, **flat, **(person.attributes or {})}
                    if is_player and value.get("location"):
                        last_recorded_location = str(value["location"])[:160]
        if not (branch.current_state or {}).get("current_location") and last_recorded_location:
            branch.current_state = {**(branch.current_state or {}), "current_location": last_recorded_location}
        if not await session.scalar(select(Memory.id).where(
            Memory.branch_id == branch.id, Memory.memory_type == "SETUP").limit(1)):
            for fact in [*current.get("history", []), *current.get("important_relationships", []),
                         *current.get("world_rules", [])][:20]:
                session.add(Memory(campaign_id=campaign.id, branch_id=branch.id,
                                   memory_type="SETUP", content=str(fact)[:5000], importance=0.8,
                                   confidence=1.0, visibility="PLAYER_KNOWN", characters=[new_name]))
        existing_goals = set((await session.scalars(select(Objective.title).where(
            Objective.branch_id == branch.id))).all())
        for goal in current.get("preferences", [])[:5]:
            if goal[:160] not in existing_goals:
                session.add(Objective(campaign_id=campaign.id, branch_id=branch.id,
                                      title=goal[:160], description=goal[:2000],
                                      status="active", visibility="PLAYER_KNOWN"))
        for checkpoint in (await session.scalars(select(Checkpoint).where(
            Checkpoint.branch_id == branch.id))).all():
            snapshot = deepcopy(checkpoint.state_snapshot or {})
            changed = False
            for row in snapshot.get("characters", []):
                if row.get("name", "").casefold() == old_name.casefold() and not collision:
                    row["name"] = new_name
                    row["attributes"] = {**(row.get("attributes") or {}),
                                         **{key: current.get(key, []) for key in ("traits", "history", "abilities", "powers")
                                            if not (row.get("attributes") or {}).get(key)},
                                         **{key: value for key, value in identity.items() if key not in (row.get("attributes") or {})}}
                    changed = True
            if "money" not in snapshot.get("current_state", {}) and starting.get("money"):
                snapshot["current_state"] = {**snapshot.get("current_state", {}), "money": starting["money"]}
                changed = True
            if changed:
                checkpoint.state_snapshot = snapshot
    await session.commit()
