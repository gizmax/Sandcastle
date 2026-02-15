"""Add policy engine support - policy_violations table and run_steps columns.

Revision ID: 008
Revises: 007
Create Date: 2026-02-16
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create policy_violations table
    op.create_table(
        "policy_violations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("policy_id", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(50), nullable=False, server_default="medium"),
        sa.Column("trigger_details", sa.Text, nullable=True),
        sa.Column("action_taken", sa.String(50), nullable=False),
        sa.Column("output_modified", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # Indexes for common queries
    op.create_index("ix_policy_violations_run_id", "policy_violations", ["run_id"])
    op.create_index("ix_policy_violations_severity", "policy_violations", ["severity"])
    op.create_index("ix_policy_violations_created_at", "policy_violations", ["created_at"])

    # Add policy tracking columns to run_steps
    op.add_column(
        "run_steps",
        sa.Column("policy_violations_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "run_steps",
        sa.Column("policy_actions", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("run_steps", "policy_actions")
    op.drop_column("run_steps", "policy_violations_count")
    op.drop_index("ix_policy_violations_created_at", table_name="policy_violations")
    op.drop_index("ix_policy_violations_severity", table_name="policy_violations")
    op.drop_index("ix_policy_violations_run_id", table_name="policy_violations")
    op.drop_table("policy_violations")
