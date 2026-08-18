"""Persist workflow evolution failure details.

Revision ID: 018
Revises: 017
Create Date: 2026-07-26
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workflow_evolutions",
        sa.Column("error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_evolutions", "error")
