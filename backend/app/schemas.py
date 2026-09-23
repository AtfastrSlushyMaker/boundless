from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CampaignCreate(BaseModel):
    prompt: str = Field(min_length=12, max_length=30_000)
    title: str | None = Field(default=None, max_length=180)


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
    original_prompt: str


class ThemeProfile(BaseModel):
    family: Literal["dark_fantasy", "cyberpunk", "survival", "cozy", "mystery", "neutral"] = "neutral"
    mood: str = "quietly expectant"
    accent_family: str = "iron red"
    surface_style: str = "ink"
    typography_style: str = "old-world"
    motion_style: str = "measured"
    atmosphere: str = ""
    background_effect: str = "engraved-map"
    contrast: Literal["standard", "high"] = "standard"


class StateOperation(BaseModel):
    kind: Literal[
        "CREATE_CHARACTER", "UPDATE_CHARACTER", "MOVE_CHARACTER", "CHANGE_CHARACTER_STATUS",
        "ADD_ITEM", "REMOVE_ITEM", "TRANSFER_ITEM", "CREATE_LOCATION", "UPDATE_LOCATION",
        "CREATE_EVENT", "CREATE_MEMORY", "CHANGE_RELATIONSHIP", "CREATE_FACTION",
        "CHANGE_FACTION_RELATIONSHIP", "ADVANCE_WORLD_TIME", "CREATE_SECRET", "REVEAL_SECRET",
        "CREATE_OBJECTIVE", "UPDATE_OBJECTIVE", "ADD_CANON_RULE", "MODIFY_CANON_RULE",
    ]
    subject: str = ""
    name: str = ""
    value: dict[str, Any] = Field(default_factory=dict)
    certainty: Literal["CONFIRMED", "OBSERVED", "INFERRED", "RUMOR", "BELIEF", "UNKNOWN"] = "CONFIRMED"
    visibility: Literal["PLAYER_KNOWN", "CHARACTER_KNOWN", "WORLD_SECRET", "GM_ONLY"] = "PLAYER_KNOWN"

    @field_validator("visibility", mode="before")
    @classmethod
    def normalize_visibility(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        aliases = {"WORLD": "PLAYER_KNOWN", "PUBLIC": "PLAYER_KNOWN", "PLAYER": "PLAYER_KNOWN",
                   "PRIVATE": "GM_ONLY", "HIDDEN": "GM_ONLY", "SECRET": "GM_ONLY"}
        normalized = value.strip().upper()
        return aliases.get(normalized, normalized)


class StateInterpretation(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    state_changes: list[StateOperation] = Field(default_factory=list)
    new_memories: list[dict[str, Any]] = Field(default_factory=list)
    knowledge_changes: list[dict[str, Any]] = Field(default_factory=list)
    relationship_changes: list[dict[str, Any]] = Field(default_factory=list)
    time_elapsed_seconds: int = Field(default=0, ge=0, le=31_536_000)


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
