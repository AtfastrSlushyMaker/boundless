"""Faction kinds and aliases, for grouping people by faction, nation, guild, faith and so on.

Revision ID: f7a3c9d1e2b5
Revises: e5f1a2b3c4d6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f7a3c9d1e2b5"
down_revision: str | None = "e5f1a2b3c4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("factions", sa.Column("kind", sa.String(24), nullable=False, server_default="faction"))
    op.add_column("factions", sa.Column("aliases", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("factions", "aliases")
    op.drop_column("factions", "kind")
