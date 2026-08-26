"""Bound crash-resume: count how often a run has been requeued after a crash.

0.45 gave the worker a durable step effect ledger; 0.46 lets it act on one.
``_recover_stuck_runs`` no longer buries a crashed run, it requeues it in the
same effect scope so the completed prefix memoizes at $0. The one thing that
turns that from a recovery into an infinite loop is a *poison* run - one that
dies on the same step every time (OOM, a segfaulting sandbox) - so the requeue
is bounded by ``settings.max_recovery_attempts`` and the counter has to survive
the crash it is counting. Hence a column and not a process-local dict.

Non-null with a server default of 0: every existing row reads as "never
recovered", which is exactly right, and no backfill is needed.

**Deploy order**: apply this migration *before* restarting workers. A worker
carrying the 0.46 code against a 022 schema cannot read ``recovery_attempts``,
so its recovery sweep raises and (by design, see ``_recover_stuck_runs``) leaves
the stuck runs alone rather than requeuing them unbounded.

Revision ID: 023
Revises: 022
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "recovery_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("runs", "recovery_attempts")
