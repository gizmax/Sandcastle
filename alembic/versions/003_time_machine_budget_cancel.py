"""Add time machine (checkpoints, replay/fork), budget, cancel, idempotency.

Revision ID: 003
Revises: 002
Create Date: 2026-02-15
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- Run checkpoints table ---
    op.create_table(
        "run_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("stage_index", sa.Integer, nullable=False),
        sa.Column("context_snapshot", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_run_checkpoints_run_id", "run_checkpoints", ["run_id"])

    # --- New runs columns ---
    op.add_column("runs", sa.Column("idempotency_key", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("max_cost_usd", sa.Float, nullable=True))
    op.add_column(
        "runs",
        sa.Column(
            "parent_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("runs", sa.Column("replay_from_step", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("fork_changes", postgresql.JSONB, nullable=True))

    op.create_index(
        "ix_runs_idempotency_key",
        "runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index("ix_runs_parent_run_id", "runs", ["parent_run_id"])

    # --- Extend RunStatus enum with cancelled, budget_exceeded ---
    op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'cancelled'")
    op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'budget_exceeded'")

    # --- New api_keys columns ---
    op.add_column("api_keys", sa.Column("key_prefix", sa.String(8), server_default="", nullable=False))
    op.add_column("api_keys", sa.Column("max_cost_per_run_usd", sa.Float, nullable=True))

    # --- New dead_letter_queue column ---
    op.add_column("dead_letter_queue", sa.Column("parallel_index", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("dead_letter_queue", "parallel_index")
    op.drop_column("api_keys", "max_cost_per_run_usd")
    op.drop_column("api_keys", "key_prefix")
    op.drop_index("ix_runs_parent_run_id", "runs")
    op.drop_index("ix_runs_idempotency_key", "runs")
    op.drop_column("runs", "fork_changes")
    op.drop_column("runs", "replay_from_step")
    op.drop_column("runs", "parent_run_id")
    op.drop_column("runs", "max_cost_usd")
    op.drop_column("runs", "idempotency_key")
    op.drop_table("run_checkpoints")
    # Note: Cannot remove enum values in PostgreSQL without recreating the type
