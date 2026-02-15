"""Add real-time cost-latency optimizer support - routing_decisions table and indexes.

Revision ID: 009
Revises: 008
Create Date: 2026-02-16
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create routing_decisions table
    op.create_table(
        "routing_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("selected_model", sa.String(255), nullable=False),
        sa.Column("selected_variant_id", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("budget_pressure", sa.Float, nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0.1"),
        sa.Column("alternatives", JSONB, nullable=True),
        sa.Column("slo", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Indexes for routing decisions
    op.create_index("ix_routing_decisions_run_id", "routing_decisions", ["run_id"])
    op.create_index("ix_routing_decisions_created_at", "routing_decisions", ["created_at"])

    # Performance index on run_steps for optimizer queries
    op.create_index(
        "ix_run_steps_perf",
        "run_steps",
        ["step_id", "cost_usd", "duration_seconds"],
        postgresql_where=sa.text("status = 'completed'"),
    )

    # Performance index on autopilot_samples for optimizer queries
    op.create_index(
        "ix_autopilot_perf",
        "autopilot_samples",
        ["experiment_id", "variant_id", "quality_score", "cost_usd"],
    )


def downgrade() -> None:
    op.drop_index("ix_autopilot_perf", table_name="autopilot_samples")
    op.drop_index("ix_run_steps_perf", table_name="run_steps")
    op.drop_index("ix_routing_decisions_created_at", table_name="routing_decisions")
    op.drop_index("ix_routing_decisions_run_id", table_name="routing_decisions")
    op.drop_table("routing_decisions")
