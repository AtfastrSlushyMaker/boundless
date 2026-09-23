"""Optional image profile and durable portrait jobs.

Revision ID: 5b1a7c02d914
Revises: 9e47c1a5f2b8
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "5b1a7c02d914"
down_revision: str | None = "9e47c1a5f2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "image_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("base_url", sa.String(400), nullable=False),
        sa.Column("checkpoint", sa.String(240), nullable=False),
        sa.Column("workflow", sa.String(80), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("steps", sa.Integer(), nullable=False),
        sa.Column("cfg", sa.Float(), nullable=False),
        sa.Column("sampler", sa.String(80), nullable=False),
        sa.Column("scheduler", sa.String(80), nullable=False),
        sa.Column("auto_recurring", sa.Boolean(), nullable=False),
        sa.Column("auto_major", sa.Boolean(), nullable=False),
        sa.Column("auto_companion", sa.Boolean(), nullable=False),
        sa.Column("auto_minor", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "portrait_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_id", sa.Uuid(), sa.ForeignKey("characters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("remote_job_id", sa.String(160), nullable=False),
        sa.Column("image_path", sa.String(400), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("error", sa.String(500), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("campaign_id", "branch_id", "character_id", "status"):
        op.create_index(f"ix_portrait_jobs_{column}", "portrait_jobs", [column])


def downgrade() -> None:
    op.drop_table("portrait_jobs")
    op.drop_table("image_profiles")
