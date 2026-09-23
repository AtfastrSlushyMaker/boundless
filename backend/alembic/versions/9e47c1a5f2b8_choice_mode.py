"""Add campaign play style and saved turn choices.

Revision ID: 9e47c1a5f2b8
Revises: 85491e23155a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "9e47c1a5f2b8"
down_revision: str | None = "85491e23155a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("game_mode", sa.String(length=16), nullable=False, server_default="freeform"))
    op.add_column("turns", sa.Column("suggested_actions", postgresql.JSONB(), nullable=False,
                                     server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("turns", "suggested_actions")
    op.drop_column("campaigns", "game_mode")
