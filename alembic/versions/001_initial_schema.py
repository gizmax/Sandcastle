"""Initial schema - runs, run_steps, schedules.

Revision ID: 001
Revises: None
Create Date: 2026-02-15
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enums
    run_status = postgresql.ENUM(
        "queued", "running", "completed", "failed", "partial",
        name="runstatus", create_type=True,
    )
    step_status = postgresql.ENUM(
        "pending", "running", "completed", "failed", "skipped",
        name="stepstatus", create_type=True,
    )

    run_status.create(op.get_bind(), checkfirst=True)
    step_status.create(op.get_bind(), checkfirst=True)

    # Runs table
    op.create_table(
        "runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("status", run_status, nullable=False, server_default="queued"),
        sa.Column("input_data", postgresql.JSONB, nullable=True),
        sa.Column("output_data", postgresql.JSONB, nullable=True),
        sa.Column("total_cost_usd", sa.Float, server_default="0.0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("callback_url", sa.String(2048), nullable=True),
        sa.Column("tenant_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Run steps table
    op.create_table(
        "run_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("parallel_index", sa.Integer, nullable=True),
        sa.Column("status", step_status, nullable=False, server_default="pending"),
        sa.Column("input_prompt", sa.Text, nullable=True),
        sa.Column("output_data", postgresql.JSONB, nullable=True),
        sa.Column("cost_usd", sa.Float, server_default="0.0"),
        sa.Column("duration_seconds", sa.Float, server_default="0.0"),
        sa.Column("attempt", sa.Integer, server_default="1"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])

    # Schedules table
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("cron_expression", sa.String(255), nullable=False),
        sa.Column("input_data", postgresql.JSONB, nullable=True),
        sa.Column("notify", postgresql.JSONB, nullable=True),
        sa.Column("enabled", sa.Boolean, server_default="true"),
        sa.Column(
            "last_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Indexes for common queries
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_workflow_name", "runs", ["workflow_name"])
    op.create_index("ix_runs_tenant_id", "runs", ["tenant_id"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])


def downgrade() -> None:
    op.drop_table("schedules")
    op.drop_table("run_steps")
    op.drop_table("runs")
    op.execute("DROP TYPE IF EXISTS stepstatus")
    op.execute("DROP TYPE IF EXISTS runstatus")
