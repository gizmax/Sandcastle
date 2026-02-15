"""Add tenant_id to schedules, fix idempotency index to include tenant.

Revision ID: 004
Revises: 003
Create Date: 2026-02-15
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Schedule tenant isolation
    op.add_column("schedules", sa.Column("tenant_id", sa.String(255), nullable=True))

    # Fix idempotency index: scope to (tenant_id, idempotency_key)
    op.drop_index("ix_runs_idempotency_key", table_name="runs")
    op.create_index(
        "ix_runs_tenant_idempotency_key",
        "runs",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_tenant_idempotency_key", table_name="runs")
    op.create_index(
        "ix_runs_idempotency_key",
        "runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_column("schedules", "tenant_id")
