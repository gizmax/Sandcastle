"""Add a public share_token to runs for shareable run permalinks.

Revision ID: 013
Revises: 012
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("share_token", sa.String(64), nullable=True))
    op.create_index(
        "ix_runs_share_token", "runs", ["share_token"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_runs_share_token", table_name="runs")
    op.drop_column("runs", "share_token")
