"""Player notebook: notes and saved story passages.

Revision ID: e5f1a2b3c4d6
Revises: d3e8b1c2a7f4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f1a2b3c4d6"
down_revision: str | None = "d3e8b1c2a7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_notes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.id", ondelete="SET NULL"), nullable=True),
        sa.Column("turn_id", sa.Uuid(), sa.ForeignKey("turns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(160), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("tag", sa.String(24), nullable=False, server_default="note"),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_campaign_notes_campaign_id", "campaign_notes", ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_campaign_notes_campaign_id", table_name="campaign_notes")
    op.drop_table("campaign_notes")
