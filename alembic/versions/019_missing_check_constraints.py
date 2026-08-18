"""Add the nine check constraints that exist in the model but in no migration.

`models/db.py` declares these on runs, run_steps, autopilot_samples and
routing_decisions, but no migration ever created them, so a database built by
alembic drifted from one built by `create_all`. `alembic check` fails on the
difference, which is how this surfaced.

They are added NOT VALID on PostgreSQL: that enforces the rule for every new
row immediately while skipping the full-table scan and the ACCESS EXCLUSIVE
lock that validating existing rows would take. Any historical row that violates
one stays put rather than failing the deploy - these are sanity bounds on cost
and duration, so a bad old row is a reporting curiosity, not a correctness
problem. Run VALIDATE CONSTRAINT later, off the deploy path, to check the
backlog.

Revision ID: 019
Revises: 018
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, constraint name, condition) - mirrors models/db.py exactly.
_CONSTRAINTS: list[tuple[str, str, str]] = [
    ("runs", "ck_runs_total_cost_non_negative", "total_cost_usd >= 0"),
    ("runs", "ck_runs_depth_non_negative", "depth >= 0"),
    ("run_steps", "ck_run_steps_cost_non_negative", "cost_usd >= 0"),
    ("run_steps", "ck_run_steps_duration_non_negative", "duration_seconds >= 0"),
    ("run_steps", "ck_run_steps_attempt_positive", "attempt >= 1"),
    ("autopilot_samples", "ck_autopilot_samples_cost_non_negative", "cost_usd >= 0"),
    (
        "autopilot_samples",
        "ck_autopilot_samples_duration_non_negative",
        "duration_seconds >= 0",
    ),
    (
        "routing_decisions",
        "ck_routing_budget_pressure_non_negative",
        "budget_pressure >= 0",
    ),
    (
        "routing_decisions",
        "ck_routing_confidence_range",
        "confidence >= 0 AND confidence <= 1",
    ),
]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    for table, name, condition in _CONSTRAINTS:
        if is_postgres:
            # NOT VALID is PostgreSQL-only; IF NOT EXISTS is not available for
            # ADD CONSTRAINT, so the name is what keeps this idempotent - a
            # rerun fails loudly rather than silently duplicating.
            op.execute(
                f'ALTER TABLE {table} ADD CONSTRAINT "{name}" '
                f"CHECK ({condition}) NOT VALID"
            )
        else:
            op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for table, name, _ in reversed(_CONSTRAINTS):
        op.drop_constraint(name, table, type_="check")
