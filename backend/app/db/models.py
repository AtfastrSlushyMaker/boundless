from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(180), default="Untitled world")
    original_prompt: Mapped[str] = mapped_column(Text)
    constitution: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    theme_profile: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    protagonist_name: Mapped[str] = mapped_column(String(120), default="You")
    genre: Mapped[str] = mapped_column(String(80), default="Open world")
    game_mode: Mapped[str] = mapped_column(String(16), default="freeform")
    active_branch_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Branch(Base):
    __tablename__ = "branches"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), default="First thread")
    parent_branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.id", ondelete="SET NULL"))
    forked_from_turn_id: Mapped[UUID | None] = mapped_column(Uuid)
    head_turn_id: Mapped[UUID | None] = mapped_column(Uuid)
    current_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Turn(Base):
    __tablename__ = "turns"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    parent_turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    turn_index: Mapped[int] = mapped_column(Integer, default=1)
    player_action: Mapped[str] = mapped_column(Text, default="")
    gm_response: Mapped[str] = mapped_column(Text, default="")
    suggested_actions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(24), default="complete")
    in_world_time: Mapped[str] = mapped_column(String(120), default="")
    state_delta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageVersion(Base):
    __tablename__ = "message_versions"
    __table_args__ = (UniqueConstraint("turn_id", "role", "version", name="uq_message_version"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(ForeignKey("turns.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="gm")
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Checkpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CanonRule(Base):
    __tablename__ = "canon_rules"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID | None] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    rule_type: Mapped[str] = mapped_column(String(64), index=True)
    statement: Mapped[str] = mapped_column(Text)
    strength: Mapped[str] = mapped_column(String(16), default="HARD")
    exceptions: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")
    source: Mapped[str] = mapped_column(String(64), default="campaign_setup")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (UniqueConstraint("campaign_id", "branch_id", "name", name="uq_character_branch_name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(32), default="alive")
    personality: Mapped[str] = mapped_column(Text, default="")
    motivations: Mapped[list[str]] = mapped_column(JSONB, default=list)
    knowledge: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CharacterRelationship(Base):
    __tablename__ = "character_relationships"
    __table_args__ = (UniqueConstraint("campaign_id", "branch_id", "from_character_id", "to_character_id", name="uq_relationship_pair"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    from_character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"))
    to_character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"))
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String(160), default="")
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")


class Item(Base):
    __tablename__ = "items"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    owner_name: Mapped[str] = mapped_column(String(120), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    condition: Mapped[str] = mapped_column(String(80), default="intact")
    properties: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    significance: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")


class Event(Base):
    __tablename__ = "events"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    content: Mapped[str] = mapped_column(Text)
    certainty: Mapped[str] = mapped_column(String(16), default="CONFIRMED")
    in_world_time: Mapped[str] = mapped_column(String(120), default="")
    participants: Mapped[list[str]] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    source_turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))
    memory_type: Mapped[str] = mapped_column(String(40), default="EVENT")
    content: Mapped[str] = mapped_column(Text)
    importance: Mapped[float] = mapped_column(Float, default=0.4)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")
    in_world_time: Mapped[str] = mapped_column(String(120), default="")
    characters: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    locations: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    factions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    items: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    keywords: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(24), default="GM_ONLY")
    discovered_by: Mapped[list[str]] = mapped_column(JSONB, default=list)
    source_turn_id: Mapped[UUID | None] = mapped_column(ForeignKey("turns.id", ondelete="SET NULL"))


class Objective(Base):
    __tablename__ = "objectives"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(24), default="active")
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")


class CampaignSummary(Base):
    __tablename__ = "campaign_summaries"
    __table_args__ = (UniqueConstraint("branch_id", "summary_type", name="uq_branch_summary_type"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    summary_type: Mapped[str] = mapped_column(String(32), default="campaign")
    content: Mapped[str] = mapped_column(Text, default="")
    through_turn_index: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ModelProfile(Base):
    __tablename__ = "model_profiles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(80), default="Balanced")
    provider: Mapped[str] = mapped_column(String(32), default="mlx")
    base_url: Mapped[str] = mapped_column(String(400), default="http://127.0.0.1:8088/v1")
    model: Mapped[str] = mapped_column(String(240), default="lukey03/Qwen3.5-9B-abliterated-MLX-4bit")
    context_window: Mapped[int] = mapped_column(Integer, default=131072)
    response_length: Mapped[str] = mapped_column(String(24), default="standard")
    temperature: Mapped[float] = mapped_column(Float, default=0.82)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ImageProfile(Base):
    __tablename__ = "image_profiles"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), default="none")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    base_url: Mapped[str] = mapped_column(String(400), default="")
    checkpoint: Mapped[str] = mapped_column(String(240), default="")
    workflow: Mapped[str] = mapped_column(String(80), default="boundless_portrait_v1")
    width: Mapped[int] = mapped_column(Integer, default=768)
    height: Mapped[int] = mapped_column(Integer, default=1024)
    steps: Mapped[int] = mapped_column(Integer, default=28)
    cfg: Mapped[float] = mapped_column(Float, default=6.5)
    sampler: Mapped[str] = mapped_column(String(80), default="dpmpp_2m")
    scheduler: Mapped[str] = mapped_column(String(80), default="karras")
    auto_recurring: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_major: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_companion: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_minor: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PortraitJob(Base):
    __tablename__ = "portrait_jobs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[UUID] = mapped_column(ForeignKey("characters.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="QUEUED", index=True)
    remote_job_id: Mapped[str] = mapped_column(String(160), default="")
    image_path: Mapped[str] = mapped_column(String(400), default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    error: Mapped[str] = mapped_column(String(500), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Faction(Base):
    __tablename__ = "factions"
    __table_args__ = (UniqueConstraint("campaign_id", "branch_id", "name", name="uq_faction_branch_name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    motives: Mapped[list[str]] = mapped_column(JSONB, default=list)
    visibility: Mapped[str] = mapped_column(String(24), default="PLAYER_KNOWN")


class FactionRelationship(Base):
    __tablename__ = "faction_relationships"
    __table_args__ = (UniqueConstraint("campaign_id", "branch_id", "from_faction", "to_faction", name="uq_faction_relationship"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    branch_id: Mapped[UUID] = mapped_column(ForeignKey("branches.id", ondelete="CASCADE"), index=True)
    from_faction: Mapped[str] = mapped_column(String(160))
    to_faction: Mapped[str] = mapped_column(String(160))
    relation: Mapped[str] = mapped_column(String(80), default="unknown")
    details: Mapped[str] = mapped_column(Text, default="")
