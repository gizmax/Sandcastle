"""Durable step effect ledger so a replayed side effect fires only once.

Replay and approval-resume already skip steps whose output reached a
checkpoint, and the cassette already memoizes ``standard`` prompt steps. What
neither covers is the hybrid step that never reached a checkpoint (cancelled
when a sibling paused the run) or that sits downstream of the replay point:
those re-POST and re-spend. ``run_step_effects`` records a claim before the
call and the outcome after it, keyed on a hash of the *resolved* effect and
scoped to the replay lineage, so the three states - landed, did not land, do
not know - are distinguishable instead of guessed.

``runs.effect_scope_id`` names the lineage and is nullable with no backfill:
existing rows read as ``COALESCE(effect_scope_id, id)``, i.e. a pre-0.45 run is
its own scope. ``run_steps.replayed`` / ``original_cost_usd`` let the UI show
"replayed - $0.00 (originally $0.42)" without polluting the budget.

Revision ID: 022
Revises: 021
Create Date: 2026-08-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "run_step_effects",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # effect_key alone is UNIQUE: tenant and scope are already folded into
        # the hash, and the constraint is what makes INSERT-or-lose a lock that
        # behaves the same on SQLite and PostgreSQL.
        sa.Column("effect_key", sa.String(length=64), nullable=False),
        # Plain strings, not FKs to runs: the ledger must outlive the run row
        # that created it (deleting a run must not silently re-arm a POST), and
        # scopes also come from contexts with no run row - local CLI runs, mesh
        # sub-executions. Growth is bounded by expires_at, not by a cascade.
        sa.Column("effect_scope_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=255), nullable=True),
        sa.Column("step_id", sa.String(length=255), nullable=False),
        sa.Column("parallel_index", sa.Integer(), nullable=True),
        sa.Column("iteration_index", sa.Integer(), nullable=True),
        sa.Column("step_type", sa.String(length=50), nullable=False),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="in_flight"
        ),
        sa.Column("output_data", _JSONB, nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("effect_key", name="uq_run_step_effects_key"),
        sa.CheckConstraint("cost_usd >= 0", name="ck_run_step_effects_cost_non_negative"),
        sa.CheckConstraint(
            "status IN ('in_flight', 'committed', 'failed')",
            name="ck_run_step_effects_status",
        ),
    )
    op.create_index("ix_run_step_effects_scope", "run_step_effects", ["effect_scope_id"])
    op.create_index("ix_run_step_effects_run_id", "run_step_effects", ["run_id"])
    op.create_index("ix_run_step_effects_expires_at", "run_step_effects", ["expires_at"])
    op.create_index(
        "ix_run_step_effects_lease", "run_step_effects", ["status", "lease_expires_at"]
    )

    op.add_column("runs", sa.Column("effect_scope_id", sa.Uuid(), nullable=True))
    op.add_column(
        "run_steps",
        sa.Column("replayed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("run_steps", sa.Column("original_cost_usd", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("run_steps", "original_cost_usd")
    op.drop_column("run_steps", "replayed")
    op.drop_column("runs", "effect_scope_id")
    op.drop_index("ix_run_step_effects_lease", table_name="run_step_effects")
    op.drop_index("ix_run_step_effects_expires_at", table_name="run_step_effects")
    op.drop_index("ix_run_step_effects_run_id", table_name="run_step_effects")
    op.drop_index("ix_run_step_effects_scope", table_name="run_step_effects")
    op.drop_table("run_step_effects")
