"""Add heal_attempts (Self-Healing) and mesh_nodes (Sandcastle Mesh) tables.

Revision ID: 014
Revises: 013
Create Date: 2026-06-11
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Self-Healing: a heal attempt per dead-letter failure.
    op.create_table(
        "heal_attempts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dead_letter_id", UUID(as_uuid=True),
            sa.ForeignKey("dead_letter_queue.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("step_id", sa.String(255), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("diff", sa.Text(), nullable=True),
        sa.Column("from_version", sa.Integer(), nullable=True),
        sa.Column("to_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="proposed"),
        sa.Column(
            "approval_id", UUID(as_uuid=True),
            sa.ForeignKey("approval_requests.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_heal_attempts_confidence_range",
        ),
    )
    op.create_index("ix_heal_attempts_dead_letter_id", "heal_attempts", ["dead_letter_id"])
    op.create_index("ix_heal_attempts_workflow_name", "heal_attempts", ["workflow_name"])
    op.create_index("ix_heal_attempts_status", "heal_attempts", ["status"])
    op.create_index("ix_heal_attempts_approval_id", "heal_attempts", ["approval_id"])

    # Sandcastle Mesh: a registered remote execution node.
    op.create_table(
        "mesh_nodes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=False),
        sa.Column("capabilities", JSONB, nullable=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="alive"),
        sa.Column(
            "registered_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("base_url", name="uq_mesh_nodes_base_url"),
    )
    op.create_index("ix_mesh_nodes_name", "mesh_nodes", ["name"])


def downgrade() -> None:
    op.drop_index("ix_mesh_nodes_name", table_name="mesh_nodes")
    op.drop_table("mesh_nodes")
    op.drop_index("ix_heal_attempts_approval_id", table_name="heal_attempts")
    op.drop_index("ix_heal_attempts_status", table_name="heal_attempts")
    op.drop_index("ix_heal_attempts_workflow_name", table_name="heal_attempts")
    op.drop_index("ix_heal_attempts_dead_letter_id", table_name="heal_attempts")
    op.drop_table("heal_attempts")
