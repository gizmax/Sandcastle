"""Reconcile PostgreSQL enum labels with SQLAlchemy enum member names.

Revision ID: 016
Revises: 015
Create Date: 2026-07-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENUM_COLUMNS = (
    (
        "runstatus",
        (
            "QUEUED",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "PARTIAL",
            "CANCELLED",
            "BUDGET_EXCEEDED",
            "AWAITING_APPROVAL",
        ),
        "runs",
        "status",
        "QUEUED",
    ),
    (
        "stepstatus",
        ("PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED", "AWAITING_APPROVAL"),
        "run_steps",
        "status",
        "PENDING",
    ),
    (
        "approvalstatus",
        ("PENDING", "APPROVED", "REJECTED", "SKIPPED", "TIMED_OUT"),
        "approval_requests",
        "status",
        "PENDING",
    ),
    (
        "experimentstatus",
        ("RUNNING", "DEPLOYING", "COMPLETED", "CANCELLED"),
        "autopilot_experiments",
        "status",
        "RUNNING",
    ),
)

_NOT_NULL_COLUMNS = (
    ("runs", "total_cost_usd", "0.0"),
    ("runs", "created_at", "CURRENT_TIMESTAMP"),
    ("run_steps", "cost_usd", "0.0"),
    ("run_steps", "duration_seconds", "0.0"),
    ("run_steps", "attempt", "1"),
    ("schedules", "enabled", "true"),
    ("schedules", "created_at", "CURRENT_TIMESTAMP"),
    ("api_keys", "is_active", "true"),
    ("api_keys", "created_at", "CURRENT_TIMESTAMP"),
    ("dead_letter_queue", "attempts", "1"),
    ("dead_letter_queue", "created_at", "CURRENT_TIMESTAMP"),
    ("run_checkpoints", "created_at", "CURRENT_TIMESTAMP"),
    ("workflow_versions", "created_at", "CURRENT_TIMESTAMP"),
)

# Partial indexes whose predicates reference enum literals. PostgreSQL cannot
# rewrite the predicate across an enum type swap (no cross-type operator), so
# they must be dropped before the conversion and recreated against the new
# type with the relabeled literal.
_ENUM_PARTIAL_INDEXES: dict[str, tuple[tuple[str, str, list[str], str, str], ...]] = {
    "stepstatus": (
        (
            "ix_run_steps_perf",
            "run_steps",
            ["step_id", "cost_usd", "duration_seconds"],
            "status = '{lit}'",
            "completed",
        ),
    ),
    "approvalstatus": (
        (
            "ix_approval_requests_timeout",
            "approval_requests",
            ["timeout_at"],
            "status = '{lit}' AND timeout_at IS NOT NULL",
            "pending",
        ),
    ),
}


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _recreate_enum(
    enum_name: str,
    labels: tuple[str, ...],
    table_name: str,
    column_name: str,
    default: str,
    transform: str,
) -> None:
    """Replace an enum type while preserving its values and column default."""
    # Drop partial indexes with enum-literal predicates first: PostgreSQL has
    # no operator between the old and new types to rewrite the predicate.
    partial_indexes = _ENUM_PARTIAL_INDEXES.get(enum_name, ())
    for index_name, index_table, _columns, _where, _lit in partial_indexes:
        op.drop_index(index_name, table_name=index_table)

    old_name = f"{enum_name}_old"
    label_sql = ", ".join(f"'{label}'" for label in labels)
    op.execute(f"ALTER TYPE {enum_name} RENAME TO {old_name}")
    op.execute(f"CREATE TYPE {enum_name} AS ENUM ({label_sql})")
    op.execute(f"ALTER TABLE {table_name} ALTER COLUMN {column_name} DROP DEFAULT")
    op.execute(
        f"ALTER TABLE {table_name} ALTER COLUMN {column_name} TYPE {enum_name} "
        f"USING {transform}({column_name}::text)::{enum_name}"
    )
    op.execute(
        f"ALTER TABLE {table_name} ALTER COLUMN {column_name} SET DEFAULT '{default}'::{enum_name}"
    )
    op.execute(f"DROP TYPE {old_name}")

    # Recreate the partial indexes with the literal in the new type's casing.
    relabel = str.upper if transform == "UPPER" else str.lower
    for index_name, index_table, columns, where_template, literal in partial_indexes:
        op.create_index(
            index_name,
            index_table,
            columns,
            postgresql_where=sa.text(where_template.format(lit=relabel(literal))),
        )


def _convert_workflow_version_status_to_enum() -> None:
    op.execute(
        "CREATE TYPE workflowversionstatus AS ENUM ('DRAFT', 'STAGING', 'PRODUCTION', 'ARCHIVED')"
    )
    op.execute("ALTER TABLE workflow_versions ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN status TYPE workflowversionstatus "
        "USING UPPER(status)::workflowversionstatus"
    )
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN status "
        "SET DEFAULT 'DRAFT'::workflowversionstatus"
    )


def _convert_workflow_version_status_to_string() -> None:
    op.execute("ALTER TABLE workflow_versions ALTER COLUMN status DROP DEFAULT")
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN status TYPE VARCHAR(20) "
        "USING LOWER(status::text)::VARCHAR(20)"
    )
    op.execute("ALTER TABLE workflow_versions ALTER COLUMN status SET DEFAULT 'draft'")
    op.execute("DROP TYPE workflowversionstatus")


def _drop_obsolete_uuid_server_defaults() -> None:
    """Use the ORM UUID factories rather than PostgreSQL-only UUID defaults."""
    op.alter_column("policy_violations", "id", server_default=None)
    op.alter_column("routing_decisions", "id", server_default=None)


def _restore_uuid_server_defaults() -> None:
    op.alter_column("policy_violations", "id", server_default=sa.text("gen_random_uuid()"))
    op.alter_column("routing_decisions", "id", server_default=sa.text("gen_random_uuid()"))


def _reconcile_not_null_columns() -> None:
    """Backfill nullable legacy values before enforcing the ORM contract."""
    for table_name, column_name, replacement in _NOT_NULL_COLUMNS:
        op.execute(
            f"UPDATE {table_name} SET {column_name} = {replacement} WHERE {column_name} IS NULL"
        )
        op.alter_column(table_name, column_name, nullable=False)


def _restore_nullable_columns() -> None:
    for table_name, column_name, _replacement in _NOT_NULL_COLUMNS:
        op.alter_column(table_name, column_name, nullable=True)


def upgrade() -> None:
    """Use native PostgreSQL enums with the uppercase ORM member names."""
    if not _is_postgresql():
        return

    for enum_name, labels, table_name, column_name, default in _ENUM_COLUMNS:
        _recreate_enum(
            enum_name,
            labels,
            table_name,
            column_name,
            default,
            "UPPER",
        )

    _convert_workflow_version_status_to_enum()
    _drop_obsolete_uuid_server_defaults()
    _reconcile_not_null_columns()
    op.add_column(
        "runs",
        sa.Column("admin_trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Restore the lowercase enum labels used before this reconciliation."""
    if not _is_postgresql():
        return

    op.drop_column("runs", "admin_trusted")
    _restore_nullable_columns()
    _restore_uuid_server_defaults()
    _convert_workflow_version_status_to_string()

    for enum_name, labels, table_name, column_name, default in _ENUM_COLUMNS:
        _recreate_enum(
            enum_name,
            tuple(label.lower() for label in labels),
            table_name,
            column_name,
            default.lower(),
            "LOWER",
        )
