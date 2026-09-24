from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

ThemeFamily = Literal["dark_fantasy", "cyberpunk", "survival", "cozy", "mystery", "neutral", "horror", "romance", "sci_fi", "modern"]


class CampaignCreate(BaseModel):
    prompt: str = Field(min_length=12, max_length=30_000)
    title: str | None = Field(default=None, max_length=180)
    game_mode: Literal["freeform", "guided"] = "freeform"
    theme_family: ThemeFamily | None = None
    character_name: str | None = Field(default=None, max_length=120)
    character_sex: Literal["male", "female", "intersex", "other"] | None = None
    character_gender: Literal["man", "woman", "nonbinary", "other"] | None = None
    character_pronouns: Literal["he/him", "she/her", "they/them"] | None = None
    starting_money: int | None = Field(default=None, ge=0, le=1_000_000_000)
    money_currency: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_money(self):
        if self.starting_money is not None and not (self.money_currency or "").strip():
            raise ValueError("Choose a currency when adding starting money.")
        return self


class CampaignModeUpdate(BaseModel):
    game_mode: Literal["freeform", "guided"]


class CampaignThemeUpdate(BaseModel):
    theme_family: ThemeFamily


class WorldEnhanceRequest(BaseModel):
    prompt: str = Field(min_length=12, max_length=30_000)
    direction: str = Field(min_length=1, max_length=2_000)

    @field_validator("direction")
    @classmethod
    def trim_direction(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Add a detail or direction for the enhancement.")
        return value


class CampaignRename(BaseModel):
    title: str = Field(min_length=1, max_length=180)


class CharacterUpdate(BaseModel):
    role: str = Field(max_length=160)
    personality: str = Field(max_length=2_000)
    appearance: str = Field(max_length=1_000)
    sex: str = Field(max_length=60)
    gender: str = Field(max_length=60)
    pronouns: str = Field(max_length=60)


class RelationshipUpdate(BaseModel):
    trust: int | None = Field(default=None, ge=0, le=100)
    respect: int | None = Field(default=None, ge=0, le=100)
    fear: int | None = Field(default=None, ge=0, le=100)
    hostility: int | None = Field(default=None, ge=0, le=100)
    status: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=2_000)


class ImageSettingsUpdate(BaseModel):
    provider: Literal["none", "comfyui", "ai_horde", "perchance_assisted"] = "none"
    enabled: bool = False
    base_url: str = Field(default="", max_length=400)
    checkpoint: str = Field(default="", max_length=240)
    workflow: Literal["boundless_portrait_v1"] = "boundless_portrait_v1"
    width: int = Field(default=768, ge=512, le=1536, multiple_of=64)
    height: int = Field(default=1024, ge=512, le=1536, multiple_of=64)
    steps: int = Field(default=28, ge=10, le=60)
    cfg: float = Field(default=6.5, ge=1, le=15)
    sampler: str = Field(default="dpmpp_2m", max_length=80)
    scheduler: str = Field(default="karras", max_length=80)
    auto_recurring: bool = True
    auto_major: bool = True
    auto_companion: bool = True
    auto_minor: bool = False
    allow_mature: bool = False

    @model_validator(mode="after")
    def validate_provider(self):
        if self.enabled and self.provider == "none":
            raise ValueError("Choose an image provider before enabling portraits.")
        if self.provider == "comfyui" and self.enabled:
            from app.services.image_provider import validate_endpoint
            self.base_url = validate_endpoint(self.base_url)
            if not self.checkpoint.strip():
                raise ValueError("Choose a ComfyUI checkpoint.")
        return self


class PortraitRequest(BaseModel):
    new_seed: bool = False


class TurnCreate(BaseModel):
    action: str = Field(min_length=1, max_length=12_000)
    branch_id: UUID | None = None
    instruction: str | None = Field(default=None, max_length=2_000)


class TurnEdit(BaseModel):
    content: str = Field(min_length=1, max_length=30_000)


class RewindRequest(BaseModel):
    turn_id: UUID | None = None
    turn_index: int | None = Field(default=None, ge=0)


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    turn_id: UUID | None = None


class DeepSeekModelsRequest(BaseModel):
    api_key: str | None = Field(default=None, max_length=1024, exclude=True, repr=False)


class MLXStartRequest(BaseModel):
    model: str = Field(min_length=1, max_length=240)


class ModelRoleUpdate(BaseModel):
    """One AI role. ``inherit`` means "use the model this role falls back to"."""

    role: Literal["state", "summary", "canon_repair", "state_fallback"]
    inherit: bool = True
    provider: Literal["mlx", "openai-compatible", "ollama", "deepseek"] = "mlx"
    base_url: str = Field(default="http://127.0.0.1:8088/v1", max_length=400)
    model: str = Field(default="", max_length=240)
    temperature: float = Field(default=0.1, ge=0, le=2)
    context_window: int = Field(default=32_768, ge=2_048, le=1_000_000)
    api_key: str | None = Field(default=None, max_length=1024, exclude=True, repr=False)

    @model_validator(mode="after")
    def validate_endpoint(self):
        if not self.inherit:
            if not (self.base_url.startswith("http://") or self.base_url.startswith("https://")):
                raise ValueError("Endpoint must begin with http:// or https://")
            if not self.model.strip():
                raise ValueError("Choose a model for this role.")
            self.base_url = self.base_url.rstrip("/")
        return self


class ModelRolesUpdate(BaseModel):
    roles: list[ModelRoleUpdate] = Field(default_factory=list, max_length=4)
    hosted_fallback_enabled: bool = False


class RepairApply(BaseModel):
    finding_ids: list[str] = Field(default_factory=list, max_length=500)
    include_high_confidence: bool = True


class ModelSettingsUpdate(BaseModel):
    provider: Literal["mlx", "openai-compatible", "ollama", "deepseek"]
    base_url: str = Field(min_length=8, max_length=400)
    model: str = Field(min_length=1, max_length=240)
    context_window: int = Field(default=131_072, ge=2_048, le=1_000_000)
    response_length: Literal["concise", "standard", "detailed", "novelistic"] = "standard"
    temperature: float = Field(default=0.82, ge=0, le=2)
    api_key: str | None = Field(default=None, max_length=1024, exclude=True, repr=False)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            raise ValueError("Endpoint must begin with http:// or https://")
        return value.rstrip("/")


class CampaignConstitution(BaseModel):
    premise: str = ""
    genre: str = "Open world"
    tone: str = ""
    narrative_preferences: list[str] = Field(default_factory=list)
    player_identity: str = "You"
    origin: str = ""
    history: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    abilities: list[str] = Field(default_factory=list)
    powers: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    mortality_rules: list[str] = Field(default_factory=list)
    important_relationships: list[str] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    physics_rules: list[str] = Field(default_factory=list)
    magic_rules: list[str] = Field(default_factory=list)
    technology_rules: list[str] = Field(default_factory=list)
    known_history: list[str] = Field(default_factory=list)
    important_factions: list[str] = Field(default_factory=list)
    starting_state: dict[str, Any] = Field(default_factory=dict)
    hard_invariants: list[dict[str, Any]] = Field(default_factory=list)
    soft_rules: list[str] = Field(default_factory=list)
    preferences: list[str] = Field(default_factory=list)
    hidden_canon_permissions: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    # Structured, auditable versions of the above. Each rule keeps statement/type/strength/source.
    ability_catalogue: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    extraction: dict[str, Any] = Field(default_factory=dict)
    original_prompt: str


class ThemeProfile(BaseModel):
    family: ThemeFamily = "neutral"
    mood: str = "quietly expectant"
    accent_family: str = "iron red"
    surface_style: str = "ink"
    typography_style: str = "old-world"
    motion_style: str = "measured"
    atmosphere: str = ""
    background_effect: str = "engraved-map"
    contrast: Literal["standard", "high"] = "standard"


OPERATION_KINDS = (
    "CREATE_CHARACTER", "UPDATE_CHARACTER", "MOVE_CHARACTER", "CHANGE_CHARACTER_STATUS",
    "REVEAL_CHARACTER_IDENTITY", "MERGE_CHARACTERS", "ADD_CHARACTER_ALIAS", "ADD_CHARACTER_FACT",
    "ADD_ITEM", "REMOVE_ITEM", "TRANSFER_ITEM", "UPDATE_ITEM", "CREATE_LOCATION", "UPDATE_LOCATION",
    "CREATE_EVENT", "CREATE_MEMORY", "CHANGE_RELATIONSHIP", "CREATE_FACTION",
    "CHANGE_FACTION_RELATIONSHIP", "ADVANCE_WORLD_TIME", "CREATE_SECRET", "REVEAL_SECRET",
    "CREATE_OBJECTIVE", "UPDATE_OBJECTIVE", "COMPLETE_OBJECTIVE", "FAIL_OBJECTIVE",
    "ADD_CANON_RULE", "MODIFY_CANON_RULE", "UPDATE_MONEY", "GAIN_ABILITY", "LOSE_ABILITY",
)
KIND_ALIASES = {
    "ADD_CHARACTER": "CREATE_CHARACTER", "NEW_CHARACTER": "CREATE_CHARACTER", "UPDATE_NPC": "UPDATE_CHARACTER",
    "CREATE_NPC": "CREATE_CHARACTER", "SET_CHARACTER_STATUS": "CHANGE_CHARACTER_STATUS", "MOVE_PLAYER": "MOVE_CHARACTER",
    "GAIN_ITEM": "ADD_ITEM", "LOSE_ITEM": "REMOVE_ITEM", "GIVE_ITEM": "TRANSFER_ITEM", "ADD_LOCATION": "CREATE_LOCATION",
    "ADD_MEMORY": "CREATE_MEMORY", "ADD_EVENT": "CREATE_EVENT", "UPDATE_RELATIONSHIP": "CHANGE_RELATIONSHIP",
    "ADD_OBJECTIVE": "CREATE_OBJECTIVE", "ADD_ABILITY": "GAIN_ABILITY", "LEARN_ABILITY": "GAIN_ABILITY",
    "REMOVE_ABILITY": "LOSE_ABILITY", "REVEAL_IDENTITY": "REVEAL_CHARACTER_IDENTITY", "ADD_ALIAS": "ADD_CHARACTER_ALIAS",
    "ADD_FACT": "ADD_CHARACTER_FACT", "ADVANCE_TIME": "ADVANCE_WORLD_TIME",
}
CERTAINTY_ALIASES = {"LOW": "INFERRED", "MEDIUM": "OBSERVED", "HIGH": "CONFIRMED", "CERTAIN": "CONFIRMED",
                     "LIKELY": "INFERRED", "POSSIBLE": "RUMOR", "RUMOUR": "RUMOR"}


class StateOperation(BaseModel):
    """One proposed change. Known entities should be referenced by ID; names are a fallback."""

    kind: Literal[OPERATION_KINDS]  # type: ignore[valid-type]
    subject: str = ""
    name: str = ""
    character_id: str | None = None
    target_id: str | None = None
    item_id: str | None = None
    location_id: str | None = None
    objective_id: str | None = None
    value: dict[str, Any] = Field(default_factory=dict)
    certainty: Literal["CONFIRMED", "OBSERVED", "INFERRED", "RUMOR", "BELIEF", "UNKNOWN"] = "CONFIRMED"
    visibility: Literal["PLAYER_KNOWN", "CHARACTER_KNOWN", "WORLD_SECRET", "GM_ONLY"] = "PLAYER_KNOWN"

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        kind = str(data.get("kind") or data.get("type") or data.get("op") or "").strip().upper().replace(" ", "_")
        data["kind"] = KIND_ALIASES.get(kind, kind)
        changes = data.pop("changes", None)
        value = data.get("value")
        if isinstance(changes, dict):
            value = {**changes, **(value if isinstance(value, dict) else {})}
        data["value"] = value if isinstance(value, dict) else {}
        for key in ("subject", "name"):
            if data.get(key) is None:
                data[key] = ""
            elif not isinstance(data.get(key), str):
                data[key] = str(data[key])
        for key in ("character_id", "target_id", "item_id", "location_id", "objective_id",
                    "source_character_id", "target_character_id"):
            if key in data and data[key] is not None and not isinstance(data[key], str):
                data[key] = str(data[key])
        if data.get("source_character_id") and not data.get("character_id"):
            data["character_id"] = data.pop("source_character_id")
        if data.get("target_character_id") and not data.get("target_id"):
            data["target_id"] = data.pop("target_character_id")
        certainty = str(data.get("certainty") or "CONFIRMED").strip().upper()
        data["certainty"] = CERTAINTY_ALIASES.get(certainty, certainty) if certainty else "CONFIRMED"
        if data["certainty"] not in {"CONFIRMED", "OBSERVED", "INFERRED", "RUMOR", "BELIEF", "UNKNOWN"}:
            data["certainty"] = "OBSERVED"
        return data

    @field_validator("visibility", mode="before")
    @classmethod
    def normalize_visibility(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return "PLAYER_KNOWN" if value is None else value
        aliases = {"WORLD": "PLAYER_KNOWN", "PUBLIC": "PLAYER_KNOWN", "PLAYER": "PLAYER_KNOWN",
                   "PRIVATE": "GM_ONLY", "HIDDEN": "GM_ONLY", "SECRET": "GM_ONLY", "LOW": "PLAYER_KNOWN",
                   "MEDIUM": "PLAYER_KNOWN", "HIGH": "PLAYER_KNOWN", "": "PLAYER_KNOWN", "CHARACTER": "CHARACTER_KNOWN"}
        normalized = value.strip().upper()
        return aliases.get(normalized, normalized)


class StateInterpretation(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    state_changes: list[StateOperation] = Field(default_factory=list)
    new_memories: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_changes: list[dict[str, Any]] = Field(default_factory=list)
    relationship_changes: list[dict[str, Any]] = Field(default_factory=list)
    time_elapsed_seconds: int = Field(default=0, ge=0, le=31_536_000)
    time_of_day: str = ""
    scene_mood: str = ""
    # Filled by the server, not the model: temporary ids offered for people not saved yet.
    mention_ids: dict[str, str] = Field(default_factory=dict, exclude=True)

    @field_validator("time_elapsed_seconds", mode="before")
    @classmethod
    def coerce_seconds(cls, value: Any) -> Any:
        try:
            return max(0, min(int(float(value)), 31_536_000))
        except (TypeError, ValueError):
            return 0


class CampaignImport(BaseModel):
    format: Literal["boundless-campaign"]
    version: int = Field(ge=1)
    campaign: dict[str, Any]
    branches: list[dict[str, Any]] = Field(default_factory=list)
    turns: list[dict[str, Any]] = Field(default_factory=list)
    canon_rules: list[dict[str, Any]] = Field(default_factory=list)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    locations: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    memories: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    secrets: list[dict[str, Any]] = Field(default_factory=list)
    objectives: list[dict[str, Any]] = Field(default_factory=list)
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    summaries: list[dict[str, Any]] = Field(default_factory=list)
