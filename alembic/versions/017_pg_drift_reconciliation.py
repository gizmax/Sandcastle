"""Reconcile PostgreSQL persistence schema with ORM metadata.

Revision ID: 017
Revises: 016
Create Date: 2026-07-21
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_JSON_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("api_keys", "allowed_cidrs", None),
    ("api_keys", "allowed_workflows", None),
    ("audit_events", "payload", None),
    ("eval_case_results", "assertions", None),
    ("evolution_iterations", "mutation_diff", None),
    ("golden_cases", "input_data", None),
    ("golden_cases", "expected_output", None),
    ("hub_submissions", "tags", "'[]'"),
    ("hub_submissions", "models_used", "'[]'"),
    ("hub_submissions", "tools_used", "'[]'"),
    ("step_cache", "output_data", None),
    ("tool_connections", "credentials", "'{}'"),
)

_NEW_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("ix_api_keys_is_active", "api_keys", "is_active"),
    ("ix_autopilot_samples_run_id", "autopilot_samples", "run_id"),
    (
        "ix_autopilot_samples_experiment_id",
        "autopilot_samples",
        "experiment_id",
    ),
    (
        "ix_autopilot_experiments_workflow_step",
        "autopilot_experiments",
        "workflow_name, step_id",
    ),
    (
        "ix_approval_requests_status_timeout",
        "approval_requests",
        "status, timeout_at",
    ),
    ("ix_dead_letter_resolved_at", "dead_letter_queue", "resolved_at"),
    ("ix_dead_letter_run_id", "dead_letter_queue", "run_id"),
    (
        "ix_routing_decisions_created_model",
        "routing_decisions",
        "created_at, selected_model",
    ),
    ("ix_routing_decisions_step_id", "routing_decisions", "step_id"),
    ("ix_run_steps_run_id_status", "run_steps", "run_id, status"),
    ("ix_run_steps_run_step_parallel", "run_steps", "run_id, step_id, parallel_index"),
    ("ix_runs_tenant_status_created", "runs", "tenant_id, status, created_at"),
    ("ix_schedules_enabled", "schedules", "enabled"),
    ("ix_schedules_last_run_id", "schedules", "last_run_id"),
    ("ix_schedules_tenant_id", "schedules", "tenant_id"),
    ("ix_schedules_workflow_name", "schedules", "workflow_name"),
)


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _convert_json_columns(target_type: str) -> None:
    for table_name, column_name, default in _JSON_COLUMNS:
        if default is not None:
            op.execute(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT")
        op.execute(
            f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE {target_type} "
            f"USING {column_name}::{target_type}"
        )
        if default is not None:
            op.execute(
                f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
                f"SET DEFAULT {default}::{target_type}"
            )


def upgrade() -> None:
    """Use jsonb and create indexes that are part of the ORM contract."""
    if not _is_postgresql():
        return

    _convert_json_columns("JSONB")
    for index_name, table_name, column_name in _NEW_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})")


def downgrade() -> None:
    """Restore the PostgreSQL schema emitted by revision 015."""
    if not _is_postgresql():
        return

    for index_name, _table_name, _column_name in _NEW_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
    _convert_json_columns("JSON")
