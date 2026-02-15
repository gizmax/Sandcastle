"""Add approval_requests table for human approval gates.

Revision ID: 005
Revises: 004
Create Date: 2026-02-15
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add AWAITING_APPROVAL to RunStatus enum
    op.execute("ALTER TYPE runstatus ADD VALUE IF NOT EXISTS 'awaiting_approval'")

    # Add AWAITING_APPROVAL to StepStatus enum
    op.execute("ALTER TYPE stepstatus ADD VALUE IF NOT EXISTS 'awaiting_approval'")

    # Create ApprovalStatus enum
    op.execute(
        "CREATE TYPE approvalstatus AS ENUM "
        "('pending', 'approved', 'rejected', 'skipped', 'timed_out')"
    )

    # Create approval_requests table
    op.create_table(
        "approval_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "approved", "rejected", "skipped", "timed_out",
                name="approvalstatus", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("request_data", JSONB, nullable=True),
        sa.Column("response_data", JSONB, nullable=True),
        sa.Column("message", sa.Text, nullable=False, server_default=""),
        sa.Column("reviewer_id", sa.String(255), nullable=True),
        sa.Column("reviewer_comment", sa.Text, nullable=True),
        sa.Column("timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("on_timeout", sa.String(50), nullable=False, server_default="abort"),
        sa.Column("allow_edit", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Index for listing pending approvals
    op.create_index(
        "ix_approval_requests_status",
        "approval_requests",
        ["status"],
    )
    op.create_index(
        "ix_approval_requests_run_id",
        "approval_requests",
        ["run_id"],
    )
    # Index for timeout checker
    op.create_index(
        "ix_approval_requests_timeout",
        "approval_requests",
        ["timeout_at"],
        postgresql_where=sa.text("status = 'pending' AND timeout_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_approval_requests_timeout", table_name="approval_requests")
    op.drop_index("ix_approval_requests_run_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.execute("DROP TYPE IF EXISTS approvalstatus")
