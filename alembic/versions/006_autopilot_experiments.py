"""Add AutoPilot experiment and sample tables.

Revision ID: 006
Revises: 005
Create Date: 2026-02-15
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create ExperimentStatus enum
    op.execute(
        "CREATE TYPE experimentstatus AS ENUM ('running', 'completed', 'cancelled')"
    )

    # Create autopilot_experiments table
    op.create_table(
        "autopilot_experiments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running", "completed", "cancelled",
                name="experimentstatus", create_type=False,
            ),
            nullable=False,
            server_default="running",
        ),
        sa.Column("optimize_for", sa.String(50), nullable=False, server_default="quality"),
        sa.Column("config", JSONB, nullable=True),
        sa.Column("deployed_variant_id", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Index for finding active experiments
    op.create_index(
        "ix_autopilot_experiments_active",
        "autopilot_experiments",
        ["workflow_name", "step_id", "status"],
    )

    # Create autopilot_samples table
    op.create_table(
        "autopilot_samples",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "experiment_id", UUID(as_uuid=True),
            sa.ForeignKey("autopilot_experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id", UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("variant_id", sa.String(255), nullable=False),
        sa.Column("variant_config", JSONB, nullable=True),
        sa.Column("output_data", JSONB, nullable=True),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("duration_seconds", sa.Float, nullable=False, server_default="0.0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )

    op.create_index(
        "ix_autopilot_samples_experiment",
        "autopilot_samples",
        ["experiment_id"],
    )
    op.create_index(
        "ix_autopilot_samples_variant",
        "autopilot_samples",
        ["experiment_id", "variant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_autopilot_samples_variant", table_name="autopilot_samples")
    op.drop_index("ix_autopilot_samples_experiment", table_name="autopilot_samples")
    op.drop_table("autopilot_samples")
    op.drop_index("ix_autopilot_experiments_active", table_name="autopilot_experiments")
    op.drop_table("autopilot_experiments")
    op.execute("DROP TYPE IF EXISTS experimentstatus")
