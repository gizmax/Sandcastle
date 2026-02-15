"""Add hierarchical workflow support (sub_workflow_of_step, depth, sub_run_ids).

Revision ID: 007
Revises: 006
Create Date: 2026-02-16
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add hierarchical workflow columns to runs
    op.add_column("runs", sa.Column("sub_workflow_of_step", sa.String(255), nullable=True))
    op.add_column("runs", sa.Column("depth", sa.Integer, nullable=False, server_default="0"))

    # Add sub_run_ids to run_steps
    op.add_column("run_steps", sa.Column("sub_run_ids", JSONB, nullable=True))

    # Index for finding child runs
    op.create_index(
        "ix_runs_parent_run_id",
        "runs",
        ["parent_run_id"],
        postgresql_where=sa.text("parent_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_runs_parent_run_id", table_name="runs")
    op.drop_column("run_steps", "sub_run_ids")
    op.drop_column("runs", "depth")
    op.drop_column("runs", "sub_workflow_of_step")
