"""Repair persistence schema drift for runtime models.

Revision ID: 015
Revises: 014
Create Date: 2026-07-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _add_deploying_experimentstatus() -> None:
    """Extend the PostgreSQL enum without emitting unsupported SQLite DDL."""
    if _is_postgresql():
        op.execute("ALTER TYPE experimentstatus ADD VALUE IF NOT EXISTS 'deploying'")


def upgrade() -> None:
    # Columns added to models after their original migrations.
    op.add_column(
        "runs",
        sa.Column("risk_level", sa.String(50), nullable=True, server_default="minimal"),
    )
    op.add_column("runs", sa.Column("api_key_id", sa.Uuid(), nullable=True))
    if _is_postgresql():
        op.create_foreign_key(
            "fk_runs_api_key_id",
            "runs",
            "api_keys",
            ["api_key_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_runs_api_key_id", "runs", ["api_key_id"])

    op.add_column("api_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("api_keys", sa.Column("allowed_cidrs", sa.JSON(), nullable=True))
    op.add_column("api_keys", sa.Column("allowed_workflows", sa.JSON(), nullable=True))
    op.add_column("api_keys", sa.Column("rotated_from_id", sa.Uuid(), nullable=True))

    op.add_column(
        "workflow_versions",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("autopilot_experiments", sa.Column("rollout_stage", sa.String(20), nullable=True))
    op.add_column("run_steps", sa.Column("model", sa.String(100), nullable=True))
    op.create_index("ix_run_steps_model", "run_steps", ["model"])

    # The original type is PostgreSQL-only. SQLite stores enum values as text.
    _add_deploying_experimentstatus()

    eval_run_status = sa.Enum("RUNNING", "COMPLETED", "FAILED", name="evalrunstatus")

    op.create_table(
        "step_cache",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("cache_key", sa.String(64), nullable=False),
        sa.Column("workflow_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("step_id", sa.String(200), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("cost_usd >= 0", name="ck_step_cache_cost_non_negative"),
        sa.CheckConstraint("hit_count >= 0", name="ck_step_cache_hit_count_non_negative"),
    )
    op.create_index("ix_step_cache_cache_key", "step_cache", ["cache_key"], unique=True)
    op.create_index("ix_step_cache_expires_at", "step_cache", ["expires_at"])

    op.create_table(
        "tool_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("connection_name", sa.String(100), nullable=False),
        sa.Column("credentials", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tool_name", "connection_name", name="uq_tool_connection"),
    )

    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("suite_name", sa.String(255), nullable=False),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("status", eval_run_status, nullable=False, server_default="RUNNING"),
        sa.Column("total_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_cases", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pass_rate", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("total_duration_seconds", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("suite_yaml", sa.Text(), nullable=True),
        sa.Column("tenant_id", sa.String(255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("total_cost_usd >= 0", name="ck_eval_runs_total_cost_non_negative"),
        sa.CheckConstraint(
            "total_duration_seconds >= 0", name="ck_eval_runs_duration_non_negative"
        ),
        sa.CheckConstraint(
            "pass_rate >= 0 AND pass_rate <= 1", name="ck_eval_runs_pass_rate_range"
        ),
    )
    op.create_index("ix_eval_runs_status", "eval_runs", ["status"])
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"])
    op.create_index("ix_eval_runs_workflow_name", "eval_runs", ["workflow_name"])
    op.create_index("ix_eval_runs_tenant_id", "eval_runs", ["tenant_id"])

    op.create_table(
        "eval_case_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "eval_run_id",
            sa.Uuid(),
            sa.ForeignKey("eval_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_name", sa.String(255), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("assertions", sa.JSON(), nullable=True),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("cost_usd >= 0", name="ck_eval_case_cost_non_negative"),
        sa.CheckConstraint("duration_seconds >= 0", name="ck_eval_case_duration_non_negative"),
    )
    op.create_index("ix_eval_case_results_eval_run_id", "eval_case_results", ["eval_run_id"])
    op.create_index("ix_eval_case_results_run_id", "eval_case_results", ["run_id"])

    op.create_table(
        "golden_datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workflow_name",
            "name",
            "version",
            name="uq_golden_datasets_tenant_workflow_name_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_golden_datasets_version_positive"),
    )
    op.create_index(
        "ix_golden_datasets_tenant_workflow", "golden_datasets", ["tenant_id", "workflow_name"]
    )
    op.create_index("ix_golden_datasets_workflow_name", "golden_datasets", ["workflow_name"])
    op.create_index("ix_golden_datasets_is_active", "golden_datasets", ["is_active"])

    op.create_table(
        "golden_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.Uuid(),
            sa.ForeignKey("golden_datasets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_label", sa.String(255), nullable=False, server_default=""),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("expected_output", sa.JSON(), nullable=True),
        sa.Column("expected_score_min", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "expected_score_min >= 0 AND expected_score_min <= 1",
            name="ck_golden_cases_expected_score_range",
        ),
    )
    op.create_index("ix_golden_cases_dataset_id", "golden_cases", ["dataset_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("runs.id", ondelete="SET NULL")),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("actor_key_prefix", sa.String(8), nullable=True),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_audit_events_run_id", "audit_events", ["run_id"])
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    op.create_table(
        "hub_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("slug", sa.String(500), nullable=False, unique=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("yaml_content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(100), nullable=False, server_default="general_ai"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("author", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("models_used", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("tools_used", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("downloads", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("downloads >= 0", name="ck_hub_submissions_downloads_non_negative"),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1.0 AND rating <= 5.0)",
            name="ck_hub_submissions_rating_range",
        ),
        sa.CheckConstraint("step_count >= 0", name="ck_hub_submissions_step_count_non_negative"),
    )
    op.create_index("ix_hub_submissions_status", "hub_submissions", ["status"])
    op.create_index("ix_hub_submissions_category", "hub_submissions", ["category"])
    op.create_index("ix_hub_submissions_author", "hub_submissions", ["author"])
    op.create_index("ix_hub_submissions_created_at", "hub_submissions", ["created_at"])

    op.create_table(
        "workflow_evolutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="running"),
        sa.Column("strategy", sa.String(50), nullable=False, server_default="autoresearch"),
        sa.Column("optimize_for", sa.String(50), nullable=False, server_default="quality"),
        sa.Column("baseline_score", sa.Float(), nullable=True),
        sa.Column("baseline_quality", sa.Float(), nullable=True),
        sa.Column("baseline_cost", sa.Float(), nullable=True),
        sa.Column("best_score", sa.Float(), nullable=True),
        sa.Column("best_quality", sa.Float(), nullable=True),
        sa.Column("best_cost", sa.Float(), nullable=True),
        sa.Column("best_variant_yaml", sa.Text(), nullable=True),
        sa.Column("eval_suite_yaml", sa.Text(), nullable=True),
        sa.Column("max_iterations", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("budget_limit_usd", sa.Float(), nullable=True),
        sa.Column("current_iteration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_keeps", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_discards", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tenant_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_workflow_evolutions_workflow_name", "workflow_evolutions", ["workflow_name"]
    )
    op.create_index("ix_workflow_evolutions_status", "workflow_evolutions", ["status"])
    op.create_index("ix_workflow_evolutions_tenant_id", "workflow_evolutions", ["tenant_id"])

    op.create_table(
        "evolution_iterations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "evolution_id",
            sa.Uuid(),
            sa.ForeignKey("workflow_evolutions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("iteration_number", sa.Integer(), nullable=False),
        sa.Column("mutation_type", sa.String(50), nullable=False),
        sa.Column("mutation_description", sa.String(500), nullable=False),
        sa.Column("mutation_diff", sa.JSON(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("eval_pass_rate", sa.Float(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("variant_yaml", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_evolution_iterations_evolution_id", "evolution_iterations", ["evolution_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evolution_iterations_evolution_id", table_name="evolution_iterations")
    op.drop_table("evolution_iterations")
    op.drop_index("ix_workflow_evolutions_tenant_id", table_name="workflow_evolutions")
    op.drop_index("ix_workflow_evolutions_status", table_name="workflow_evolutions")
    op.drop_index("ix_workflow_evolutions_workflow_name", table_name="workflow_evolutions")
    op.drop_table("workflow_evolutions")
    op.drop_index("ix_hub_submissions_created_at", table_name="hub_submissions")
    op.drop_index("ix_hub_submissions_author", table_name="hub_submissions")
    op.drop_index("ix_hub_submissions_category", table_name="hub_submissions")
    op.drop_index("ix_hub_submissions_status", table_name="hub_submissions")
    op.drop_table("hub_submissions")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_index("ix_audit_events_run_id", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_golden_cases_dataset_id", table_name="golden_cases")
    op.drop_table("golden_cases")
    op.drop_index("ix_golden_datasets_is_active", table_name="golden_datasets")
    op.drop_index("ix_golden_datasets_workflow_name", table_name="golden_datasets")
    op.drop_index("ix_golden_datasets_tenant_workflow", table_name="golden_datasets")
    op.drop_table("golden_datasets")
    op.drop_index("ix_eval_case_results_run_id", table_name="eval_case_results")
    op.drop_index("ix_eval_case_results_eval_run_id", table_name="eval_case_results")
    op.drop_table("eval_case_results")
    op.drop_index("ix_eval_runs_tenant_id", table_name="eval_runs")
    op.drop_index("ix_eval_runs_workflow_name", table_name="eval_runs")
    op.drop_index("ix_eval_runs_created_at", table_name="eval_runs")
    op.drop_index("ix_eval_runs_status", table_name="eval_runs")
    op.drop_table("eval_runs")
    if _is_postgresql():
        sa.Enum("RUNNING", "COMPLETED", "FAILED", name="evalrunstatus").drop(
            op.get_bind(), checkfirst=True
        )
    op.drop_table("tool_connections")
    op.drop_index("ix_step_cache_expires_at", table_name="step_cache")
    op.drop_index("ix_step_cache_cache_key", table_name="step_cache")
    op.drop_table("step_cache")

    op.drop_index("ix_run_steps_model", table_name="run_steps")
    op.drop_column("run_steps", "model")
    op.drop_column("autopilot_experiments", "rollout_stage")
    op.drop_column("workflow_versions", "is_public")
    op.drop_column("api_keys", "rotated_from_id")
    op.drop_column("api_keys", "allowed_workflows")
    op.drop_column("api_keys", "allowed_cidrs")
    op.drop_column("api_keys", "expires_at")
    op.drop_index("ix_runs_api_key_id", table_name="runs")
    if _is_postgresql():
        op.drop_constraint("fk_runs_api_key_id", "runs", type_="foreignkey")
    op.drop_column("runs", "api_key_id")
    op.drop_column("runs", "risk_level")
    # PostgreSQL enum values cannot be removed without recreating the type.
