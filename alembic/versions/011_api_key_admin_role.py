"""Make api_keys.tenant_id nullable to support admin keys.

Revision ID: 011
Revises: 010
Create Date: 2026-02-16
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(255),
            nullable=True,
        )


def downgrade() -> None:
    # Set any NULL tenant_ids to empty string before making NOT NULL
    op.execute("UPDATE api_keys SET tenant_id = '' WHERE tenant_id IS NULL")
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.alter_column(
            "tenant_id",
            existing_type=sa.String(255),
            nullable=False,
        )
