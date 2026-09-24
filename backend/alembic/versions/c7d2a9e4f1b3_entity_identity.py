"""Entity identity, merge-safe facts, relationship events, abilities, and post-turn jobs.

Revision ID: c7d2a9e4f1b3
Revises: 5b1a7c02d914

Existing campaigns stay playable: every character name becomes its canonical alias,
stored known_facts become fact rows, and meaningful relationship history becomes
relationship events. Nothing is merged here; likely duplicates are reported by the
campaign repair tool, which asks before destructive changes.
"""

import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c7d2a9e4f1b3"
down_revision: str | None = "5b1a7c02d914"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOISE_REASONS = {"first appeared in the story", "appeared in the story"}


def _normalize(value: str) -> str:
    # Frozen copy of app.services.identity.normalize_reference at the time of this migration.
    text = str(value or "").casefold().replace("’", "'")
    text = re.sub(r"[^\w\s'()-]", " ", text)
    text = re.sub(r"\b(?:the|a|an)\b", " ", text)
    return " ".join(text.split())[:160]


def _uuid_columns(table: str, extra: list[sa.Column] | None = None) -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        *(extra or []),
    ]


def upgrade() -> None:
    op.add_column("turns", sa.Column("canonical", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("turns", sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("turns", sa.Column("diagnostics", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.create_index("ix_turns_canonical", "turns", ["canonical"])

    op.alter_column("characters", "status", type_=sa.String(160), existing_type=sa.String(32))
    op.add_column("characters", sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("characters", sa.Column("importance", sa.String(16), nullable=False, server_default="MINOR"))
    op.add_column("characters", sa.Column("first_seen_turn_index", sa.Integer(), nullable=True))
    op.add_column("characters", sa.Column("last_seen_turn_index", sa.Integer(), nullable=True))

    op.add_column("locations", sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("items", sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.alter_column("items", "condition", type_=sa.String(240), existing_type=sa.String(80))

    op.add_column("memories", sa.Column("normalized_hash", sa.String(64), nullable=False, server_default=""))
    op.add_column("memories", sa.Column("turn_index", sa.Integer(), nullable=True))
    op.add_column("memories", sa.Column("character_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))
    op.add_column("memories", sa.Column("embedding_model", sa.String(120), nullable=False, server_default=""))
    op.alter_column("memories", "embedding", type_=Vector(), existing_type=Vector(384), postgresql_using="embedding::vector")
    op.create_index("ix_memories_normalized_hash", "memories", ["normalized_hash"])

    for column in (
        sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("criteria", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("related_character_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("related_item_names", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("related_location_names", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_turn_index", sa.Integer(), nullable=True),
        sa.Column("completed_turn_index", sa.Integer(), nullable=True),
        sa.Column("failed_turn_index", sa.Integer(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=False, server_default=""),
    ):
        op.add_column("objectives", column)

    op.add_column("campaign_summaries", sa.Column("last_attempt_turn_index", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("campaign_summaries", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaign_summaries", sa.Column("last_error", sa.String(500), nullable=False, server_default=""))
    op.add_column("campaign_summaries", sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("campaign_summaries", sa.Column("method", sa.String(24), nullable=False, server_default="model"))

    op.add_column("model_profiles", sa.Column("role", sa.String(24), nullable=False, server_default="narrator"))
    op.create_index("ix_model_profiles_role", "model_profiles", ["role"])

    op.create_table("character_aliases", *_uuid_columns("character_aliases", [
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alias", sa.String(160), nullable=False),
        sa.Column("normalized", sa.String(160), nullable=False),
        sa.Column("alias_type", sa.String(24), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("first_seen_turn_index", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]), sa.UniqueConstraint("branch_id", "character_id", "normalized", name="uq_character_alias"))
    op.create_table("character_facts", *_uuid_columns("character_facts", [
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_type", sa.String(24), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("normalized", sa.Text(), nullable=False),
        sa.Column("certainty", sa.String(16), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("first_seen_turn_index", sa.Integer(), nullable=True),
        sa.Column("last_confirmed_turn_index", sa.Integer(), nullable=True),
        sa.Column("source_turn_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("supersedes_fact_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]))
    op.create_table("relationship_events", *_uuid_columns("relationship_events", [
        sa.Column("relationship_id", sa.Uuid(), sa.ForeignKey("character_relationships.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("dimension", sa.String(32), nullable=False),
        sa.Column("before_value", sa.Float(), nullable=True),
        sa.Column("after_value", sa.Float(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("location", sa.String(160), nullable=False),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]))
    op.create_table("abilities", *_uuid_columns("abilities", [
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("normalized", sa.String(160), nullable=False),
        sa.Column("aliases", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source", sa.String(240), nullable=False),
        sa.Column("acquired_turn_index", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("limitations", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    ]))
    op.create_table("post_turn_jobs", *_uuid_columns("post_turn_jobs", [
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.String(500), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]))
    for table, columns in (
        ("character_aliases", ("campaign_id", "branch_id", "character_id", "normalized")),
        ("character_facts", ("campaign_id", "branch_id", "character_id")),
        ("relationship_events", ("campaign_id", "branch_id", "relationship_id")),
        ("abilities", ("campaign_id", "branch_id", "character_id")),
        ("post_turn_jobs", ("campaign_id", "branch_id", "kind", "status")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])

    _backfill()


def _backfill() -> None:
    bind = op.get_bind()
    now = datetime.now(UTC)
    bind.execute(sa.text(
        "UPDATE turns SET canonical = false, status = 'failed' WHERE status IN ('error', 'interrupted', 'failed')"))

    aliases = sa.table("character_aliases", *[sa.column(name) for name in (
        "id", "campaign_id", "branch_id", "character_id", "alias", "normalized", "alias_type",
        "confidence", "first_seen_turn_index", "source", "created_at")])
    facts = sa.table("character_facts", *[sa.column(name) for name in (
        "id", "campaign_id", "branch_id", "character_id", "fact_type", "content", "normalized",
        "certainty", "provenance", "visibility", "first_seen_turn_index", "last_confirmed_turn_index",
        "source_turn_id", "active", "supersedes_fact_id", "created_at")])
    events = sa.table("relationship_events", *[sa.column(name) for name in (
        "id", "campaign_id", "branch_id", "relationship_id", "turn_id", "turn_index", "dimension",
        "before_value", "after_value", "delta", "reason", "location", "visibility", "created_at")])

    alias_rows, fact_rows, event_rows = [], [], []
    for row in bind.execute(sa.text(
            "SELECT id, campaign_id, branch_id, name, role, attributes FROM characters")).mappings():
        alias_rows.append({"id": uuid.uuid4(), "campaign_id": row["campaign_id"], "branch_id": row["branch_id"],
                           "character_id": row["id"], "alias": row["name"][:160], "normalized": _normalize(row["name"]),
                           "alias_type": "CANONICAL_NAME", "confidence": 1.0, "first_seen_turn_index": None,
                           "source": "migration", "created_at": now})
        attributes = row["attributes"] if isinstance(row["attributes"], dict) else json.loads(row["attributes"] or "{}")
        seen = set()
        for fact in attributes.get("known_facts") or []:
            if not isinstance(fact, str) or not fact.strip():
                continue
            key = _normalize(fact)
            if key in seen:
                continue
            seen.add(key)
            fact_rows.append({"id": uuid.uuid4(), "campaign_id": row["campaign_id"], "branch_id": row["branch_id"],
                              "character_id": row["id"], "fact_type": "general", "content": fact.strip()[:2000],
                              "normalized": key, "certainty": "CONFIRMED", "provenance": "DIRECT_OBSERVATION",
                              "visibility": "PLAYER_KNOWN", "first_seen_turn_index": None,
                              "last_confirmed_turn_index": None, "source_turn_id": None, "active": True,
                              "supersedes_fact_id": None, "created_at": now})
    for row in bind.execute(sa.text(
            "SELECT id, campaign_id, branch_id, dimensions, visibility FROM character_relationships")).mappings():
        dimensions = row["dimensions"] if isinstance(row["dimensions"], dict) else json.loads(row["dimensions"] or "{}")
        for entry in dimensions.get("history") or []:
            if not isinstance(entry, dict):
                continue
            reason = str(entry.get("reason") or "").strip()
            if not reason or reason.casefold() in NOISE_REASONS:
                continue
            turn_id = entry.get("turn_id")
            try:
                turn_uuid = uuid.UUID(str(turn_id)) if turn_id else None
            except ValueError:
                turn_uuid = None
            index = entry.get("turn_index")
            event_rows.append({"id": uuid.uuid4(), "campaign_id": row["campaign_id"], "branch_id": row["branch_id"],
                               "relationship_id": row["id"], "turn_id": turn_uuid,
                               "turn_index": index if isinstance(index, int) else None, "dimension": "note",
                               "before_value": None, "after_value": None, "delta": None, "reason": reason[:2000],
                               "location": str(entry.get("location") or "")[:160],
                               "visibility": row["visibility"] or "PLAYER_KNOWN", "created_at": now})
    if alias_rows:
        op.bulk_insert(aliases, alias_rows)
    if fact_rows:
        op.bulk_insert(facts, fact_rows)
    if event_rows:
        op.bulk_insert(events, event_rows)

    for row in bind.execute(sa.text(
            "SELECT m.id, m.content, t.turn_index FROM memories m LEFT JOIN turns t ON t.id = m.source_turn_id")).mappings():
        digest = hashlib.sha256(_normalize(row["content"]).encode()).hexdigest()
        bind.execute(sa.text("UPDATE memories SET normalized_hash = :hash, turn_index = :turn_index WHERE id = :id"),
                     {"hash": digest, "turn_index": row["turn_index"], "id": row["id"]})
    bind.execute(sa.text(
        "UPDATE objectives o SET created_turn_index = 0 WHERE created_turn_index IS NULL"))


def downgrade() -> None:
    for table in ("post_turn_jobs", "abilities", "relationship_events", "character_facts", "character_aliases"):
        op.drop_table(table)
    op.drop_index("ix_model_profiles_role", table_name="model_profiles")
    op.drop_column("model_profiles", "role")
    for column in ("method", "consecutive_failures", "last_error", "last_success_at", "last_attempt_turn_index"):
        op.drop_column("campaign_summaries", column)
    for column in ("resolution_note", "failed_turn_index", "completed_turn_index", "created_turn_index",
                   "related_location_names", "related_item_names", "related_character_ids", "criteria", "aliases"):
        op.drop_column("objectives", column)
    op.drop_index("ix_memories_normalized_hash", table_name="memories")
    op.execute("UPDATE memories SET embedding = NULL")
    op.alter_column("memories", "embedding", type_=Vector(384), existing_type=Vector(), postgresql_using="embedding::vector(384)")
    for column in ("embedding_model", "character_ids", "turn_index", "normalized_hash"):
        op.drop_column("memories", column)
    op.alter_column("items", "condition", type_=sa.String(80), existing_type=sa.String(240), postgresql_using="left(condition, 80)")
    op.drop_column("items", "aliases")
    op.drop_column("locations", "aliases")
    for column in ("last_seen_turn_index", "first_seen_turn_index", "importance", "provenance"):
        op.drop_column("characters", column)
    op.alter_column("characters", "status", type_=sa.String(32), existing_type=sa.String(160), postgresql_using="left(status, 32)")
    op.drop_index("ix_turns_canonical", table_name="turns")
    for column in ("diagnostics", "attempt", "canonical"):
        op.drop_column("turns", column)
