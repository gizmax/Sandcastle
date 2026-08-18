"""Add started_at to workflow_evolutions.

The stuck-job reaper keyed on created_at, which is when the evolution was
queued, not when a worker picked it up. Running on every worker startup with no
ownership check, it therefore failed evolutions that were merely waiting in the
queue - or running on a different worker - and the job carried on regardless,
finishing by setting status="completed" while the error text stayed. The result
was a row permanently reading completed with "Worker crashed or evolution job
timed out" beside it.

Revision ID: 020
Revises: 019
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill on purpose: rows created before this migration
    # have no honest start time, and NULL says so. The reaper skips them rather
    # than inventing one, which is the safe direction - a missed reap leaves a
    # row to clean up by hand, a wrong one kills a live job.
    op.add_column(
        "workflow_evolutions",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workflow_evolutions", "started_at")
