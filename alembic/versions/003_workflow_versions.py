"""Add workflow_versions table and workflow_version column to runs.

Revision ID: 003
Revises: 002
Create Date: 2026-02-17
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
    # Workflow versions table
    op.create_table(
        "workflow_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("yaml_content", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("steps_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column("promoted_by", sa.String(255), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        "uq_workflow_name_version",
        "workflow_versions",
        ["workflow_name", "version"],
    )
    op.create_index(
        "ix_workflow_versions_name_status",
        "workflow_versions",
        ["workflow_name", "status"],
    )

    # Add workflow_version column to runs table
    op.add_column("runs", sa.Column("workflow_version", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "workflow_version")
    op.drop_table("workflow_versions")
