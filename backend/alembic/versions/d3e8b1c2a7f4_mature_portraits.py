"""Opt-in mature portraits for confirmed adults.

Revision ID: d3e8b1c2a7f4
Revises: c7d2a9e4f1b3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e8b1c2a7f4"
down_revision: str | None = "c7d2a9e4f1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("image_profiles", sa.Column("allow_mature", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("image_profiles", "allow_mature")
