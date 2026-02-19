"""SQLAlchemy 2.0 async models for runs, steps, and schedules."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.ext.asyncio import AsyncAttrs, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from sandcastle.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all models."""


class RunStatus(str, enum.Enum):
    """Possible statuses for a workflow run."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    BUDGET_EXCEEDED = "budget_exceeded"
    AWAITING_APPROVAL = "awaiting_approval"


class StepStatus(str, enum.Enum):
    """Possible statuses for a step within a run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class ApprovalStatus(str, enum.Enum):
    """Possible statuses for an approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"


class Run(Base):
    """A single workflow execution."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), nullable=False, default=RunStatus.QUEUED
    )
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    max_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    replay_from_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fork_changes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sub_workflow_of_step: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run", cascade="all, delete-orphan", foreign_keys="RunStep.run_id"
    )
    children: Mapped[list[Run]] = relationship(
        back_populates="parent", foreign_keys="Run.parent_run_id"
    )
    parent: Mapped[Run | None] = relationship(
        back_populates="children", remote_side="Run.id", foreign_keys="Run.parent_run_id"
    )


class RunStep(Base):
    """A single step execution within a run."""

    __tablename__ = "run_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parallel_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[StepStatus] = mapped_column(
        Enum(StepStatus), nullable=False, default=StepStatus.PENDING
    )
    input_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_run_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    policy_violations_count: Mapped[int] = mapped_column(Integer, default=0)
    policy_actions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="steps")


class Schedule(Base):
    """A scheduled workflow execution."""

    __tablename__ = "schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(255), nullable=False)
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notify: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ApiKey(Base):
    """API key for multi-tenant authentication."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    tenant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_cost_per_run_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DeadLetterItem(Base):
    """Failed step stored in the dead letter queue for retry/resolution."""

    __tablename__ = "dead_letter_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    parallel_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(50), nullable=True)


class ExperimentStatus(str, enum.Enum):
    """Possible statuses for an AutoPilot experiment."""

    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AutoPilotExperiment(Base):
    """An AutoPilot A/B experiment for a workflow step."""

    __tablename__ = "autopilot_experiments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus), nullable=False, default=ExperimentStatus.RUNNING
    )
    optimize_for: Mapped[str] = mapped_column(String(50), nullable=False, default="quality")
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    deployed_variant_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    samples: Mapped[list[AutoPilotSample]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class AutoPilotSample(Base):
    """A single sample from an AutoPilot experiment run."""

    __tablename__ = "autopilot_samples"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("autopilot_experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    variant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    experiment: Mapped[AutoPilotExperiment] = relationship(back_populates="samples")


class ApprovalRequest(Base):
    """Human approval gate for a workflow step."""

    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus), nullable=False, default=ApprovalStatus.PENDING
    )
    request_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reviewer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewer_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    on_timeout: Mapped[str] = mapped_column(String(50), nullable=False, default="abort")
    allow_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class RoutingDecision(Base):
    """Record of an optimizer routing decision."""

    __tablename__ = "routing_decisions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    selected_model: Mapped[str] = mapped_column(String(255), nullable=False)
    selected_variant_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_pressure: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.1)
    alternatives: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    slo: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class PolicyViolation(Base):
    """Record of a policy violation during workflow execution."""

    __tablename__ = "policy_violations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False, default="medium")
    trigger_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    action_taken: Mapped[str] = mapped_column(String(50), nullable=False)
    output_modified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    run: Mapped[Run] = relationship(foreign_keys=[run_id])


class RunCheckpoint(Base):
    """Snapshot of run context after each completed stage for replay/fork."""

    __tablename__ = "run_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    step_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stage_index: Mapped[int] = mapped_column(Integer, nullable=False)
    context_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class StepCache(Base):
    """Cached step results to avoid redundant sandbox executions."""

    __tablename__ = "step_cache"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    workflow_name: Mapped[str] = mapped_column(String(200), default="")
    step_id: Mapped[str] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    output_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Setting(Base):
    """Key-value configuration stored in the database."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class WorkflowVersionStatus(str, enum.Enum):
    """Possible statuses for a workflow version in the registry."""

    DRAFT = "draft"
    STAGING = "staging"
    PRODUCTION = "production"
    ARCHIVED = "archived"


class WorkflowVersion(Base):
    """A versioned workflow definition stored in the registry."""

    __tablename__ = "workflow_versions"
    __table_args__ = (
        UniqueConstraint("workflow_name", "version", name="uq_workflow_name_version"),
        Index("ix_workflow_versions_name_status", "workflow_name", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )
    workflow_name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[WorkflowVersionStatus] = mapped_column(
        Enum(WorkflowVersionStatus), nullable=False, default=WorkflowVersionStatus.DRAFT
    )
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    steps_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# Database engine and session factory

def _build_engine_url() -> str:
    """Build the database URL, defaulting to SQLite in local mode."""
    if settings.database_url:
        return settings.database_url
    # Local mode: SQLite in data_dir
    data_path = Path(settings.data_dir).resolve()
    data_path.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{data_path}/sandcastle.db"


def _build_engine_kwargs() -> dict:
    """Build engine kwargs based on database type."""
    url = _build_engine_url()
    kwargs: dict = {"echo": False}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return kwargs


def _sqlite_wal_mode(dbapi_conn, _connection_record):
    """Enable WAL mode for SQLite to allow concurrent reads during writes."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


engine = create_async_engine(_build_engine_url(), **_build_engine_kwargs())

# Enable WAL mode for SQLite connections
if _build_engine_url().startswith("sqlite"):
    from sqlalchemy import event

    event.listen(engine.sync_engine, "connect", _sqlite_wal_mode)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables and add missing columns for SQLite local mode.

    ``create_all`` only creates new tables - it never alters existing ones.
    For SQLite (local mode) we inspect each table and add any columns that
    the ORM model defines but the on-disk schema is missing.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Auto-add missing columns for SQLite (no Alembic in local mode)
        if _build_engine_url().startswith("sqlite"):
            await conn.run_sync(_add_missing_columns)


def _add_missing_columns(connection, **_kw) -> None:
    """Inspect SQLite tables and ALTER TABLE ADD COLUMN for any gaps."""
    import logging
    from sqlalchemy import inspect as sa_inspect, text

    log = logging.getLogger(__name__)
    inspector = sa_inspect(connection)

    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                col_type = col.type.compile(dialect=connection.dialect)
                stmt = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col_type}'
                log.info("Auto-migrating: %s", stmt)
                connection.execute(text(stmt))


async def get_session():
    """Dependency for FastAPI - yields an async session."""
    async with async_session() as session:
        yield session
