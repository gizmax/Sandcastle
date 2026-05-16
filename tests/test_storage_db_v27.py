"""Comprehensive tests for storage backends and database models.

Covers LocalStorage (filesystem), S3Storage (mocked aioboto3), all ORM models,
and async session management.  Uses tmp_path for local I/O and in-memory SQLite
for database tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import inspect as sa_inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sandcastle.engine.storage import LocalStorage, S3Storage, _MAX_WRITE_SIZE
from sandcastle.models.db import (
    ApiKey,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    Base,
    EvolutionIteration,
    HubSubmission,
    PolicyViolation,
    Run,
    RunStatus,
    RunStep,
    Schedule,
    StepStatus,
    WorkflowEvolution,
    WorkflowVersion,
    WorkflowVersionStatus,
)


# ---------------------------------------------------------------------------
# Helpers: in-memory async engine / session for DB tests
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_engine():
    """Create a fresh in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    """Yield a session bound to the in-memory engine."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# 1. LocalStorage (12+ tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def storage(tmp_path):
    """Fresh LocalStorage rooted at a temporary directory."""
    return LocalStorage(base_dir=str(tmp_path))


class TestLocalStorageWriteRead:
    """Write and read operations."""

    @pytest.mark.asyncio
    async def test_write_and_read_back(self, storage):
        """Written content must be readable with identical bytes."""
        await storage.write("hello.txt", "world")
        assert await storage.read("hello.txt") == "world"

    @pytest.mark.asyncio
    async def test_write_unicode_filename(self, storage):
        """Filenames containing unicode characters work correctly."""
        name = "zprava_cz_\u010d\u0159\u017e.txt"
        await storage.write(name, "obsah")
        assert await storage.read(name) == "obsah"

    @pytest.mark.asyncio
    async def test_write_nested_directory_auto_creates(self, storage):
        """Intermediate directories are created on demand."""
        await storage.write("a/b/c/deep.json", '{"ok": true}')
        assert await storage.read("a/b/c/deep.json") == '{"ok": true}'

    @pytest.mark.asyncio
    async def test_read_nonexistent_returns_none(self, storage):
        """Reading a path that does not exist returns None."""
        assert await storage.read("missing.txt") is None

    @pytest.mark.asyncio
    async def test_list_files_in_directory(self, storage):
        """list() returns files under the specified prefix."""
        await storage.write("logs/a.log", "a")
        await storage.write("logs/b.log", "b")
        await storage.write("data/c.dat", "c")
        result = await storage.list("logs/")
        assert "logs/a.log" in result
        assert "logs/b.log" in result
        assert "data/c.dat" not in result

    @pytest.mark.asyncio
    async def test_list_with_prefix_filter(self, storage):
        """list() filters by the exact prefix string, not just directory."""
        await storage.write("data/alpha.txt", "1")
        await storage.write("data/beta.txt", "2")
        result = await storage.list("data/alpha")
        assert result == ["data/alpha.txt"]

    @pytest.mark.asyncio
    async def test_delete_file(self, storage):
        """Deleted file can no longer be read."""
        await storage.write("tmp.txt", "gone soon")
        await storage.delete("tmp.txt")
        assert await storage.read("tmp.txt") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_crash(self, storage):
        """Deleting a non-existent file must not raise."""
        await storage.delete("no-such-file.txt")

    def test_path_traversal_blocked(self, storage):
        """Attempts to escape the base directory raise ValueError."""
        with pytest.raises(ValueError, match="traversal"):
            storage._safe_path("../../etc/passwd")

    def test_path_traversal_with_inner_dots(self, storage):
        """Dotdot segments inside the path are also rejected."""
        with pytest.raises(ValueError, match="traversal"):
            storage._safe_path("subdir/../../etc/shadow")

    @pytest.mark.asyncio
    async def test_large_file_write_read(self, storage):
        """Writing and reading a ~10 MB payload works correctly."""
        big = "A" * (10 * 1024 * 1024)
        await storage.write("big.bin", big)
        result = await storage.read("big.bin")
        assert result == big

    @pytest.mark.asyncio
    async def test_oversized_file_rejected(self, storage):
        """Content larger than _MAX_WRITE_SIZE is rejected."""
        too_big = "X" * (_MAX_WRITE_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            await storage.write("huge.bin", too_big)

    @pytest.mark.asyncio
    async def test_concurrent_write_read(self, storage):
        """Concurrent writes to different files all persist correctly."""
        async def writer(key: str, value: str):
            await storage.write(key, value)
            return await storage.read(key)

        tasks = [writer(f"f{i}.txt", f"val{i}") for i in range(20)]
        results = await asyncio.gather(*tasks)
        for i, result in enumerate(results):
            assert result == f"val{i}"

    @pytest.mark.asyncio
    async def test_storage_root_isolation(self, tmp_path):
        """Two storages with different roots are completely isolated."""
        s1 = LocalStorage(base_dir=str(tmp_path / "s1"))
        s2 = LocalStorage(base_dir=str(tmp_path / "s2"))
        await s1.write("shared.txt", "from-s1")
        await s2.write("shared.txt", "from-s2")
        assert await s1.read("shared.txt") == "from-s1"
        assert await s2.read("shared.txt") == "from-s2"

    def test_empty_path_rejected(self, storage):
        """An empty string path raises ValueError."""
        with pytest.raises(ValueError, match="must not be empty"):
            storage._safe_path("")

    def test_control_char_path_rejected(self, storage):
        """Paths containing control characters are rejected."""
        with pytest.raises(ValueError, match="control characters"):
            storage._safe_path("file\x00name.txt")


# ---------------------------------------------------------------------------
# 2. S3Storage (10+ tests, mock aioboto3)
# ---------------------------------------------------------------------------

class _FakeBody:
    """Fake async S3 response body."""

    def __init__(self, data: bytes):
        self._data = data

    async def read(self):
        return self._data


class _FakeS3Client:
    """Minimal fake S3 client for unit tests."""

    def __init__(self):
        self._store: dict[str, bytes] = {}
        self.exceptions = MagicMock()
        self.exceptions.NoSuchKey = type("NoSuchKey", (Exception,), {})
        self._endpoint_url: str | None = None

    async def get_object(self, Bucket, Key):
        if Key not in self._store:
            raise self.exceptions.NoSuchKey(f"Key {Key} not found")
        return {"Body": _FakeBody(self._store[Key])}

    async def put_object(self, Bucket, Key, Body, ContentType=None):
        self._store[Key] = Body

    async def delete_object(self, Bucket, Key):
        self._store.pop(Key, None)

    def get_paginator(self, operation):
        client = self

        class _FakePaginator:
            async def paginate(self, Bucket, Prefix=""):
                contents = [
                    {"Key": k}
                    for k in sorted(client._store)
                    if k.startswith(Prefix)
                ]
                yield {"Contents": contents} if contents else {}
            def __aiter__(self_pag):
                return self_pag.paginate(Bucket="b", Prefix="")

        return _FakePaginator()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class _FakeSession:
    """Fake aioboto3.Session that returns our fake client."""

    def __init__(self, fake_client: _FakeS3Client, **kwargs):
        self._client = fake_client

    def client(self, service_name, endpoint_url=None, **kwargs):
        self._client._endpoint_url = endpoint_url
        return self._client


@pytest.fixture
def fake_s3_client():
    return _FakeS3Client()


@pytest.fixture
def s3_storage(fake_s3_client):
    """S3Storage with a mocked aioboto3 session.

    aioboto3 is imported lazily inside S3Storage.__init__, so we patch
    the import mechanism rather than a module-level attribute.
    """
    with patch.dict("sys.modules", {"aioboto3": MagicMock()}):
        import aioboto3 as _mocked
        _mocked.Session.return_value = _FakeSession(fake_s3_client)
        storage = S3Storage(
            bucket="test-bucket",
            endpoint_url="http://localhost:9000",
            aws_access_key_id="testkey",
            aws_secret_access_key="testsecret",
        )
        # Replace the real session with our fake
        storage._session = _FakeSession(fake_s3_client)
        return storage


class TestS3Storage:
    """S3-compatible storage backend tests with mocked aioboto3."""

    @pytest.mark.asyncio
    async def test_write_uploads_correct_key(self, s3_storage, fake_s3_client):
        """Write stores bytes in the correct bucket/key path."""
        await s3_storage.write("data/file.json", '{"a": 1}')
        assert fake_s3_client._store.get("data/file.json") == b'{"a": 1}'

    @pytest.mark.asyncio
    async def test_read_correct_key(self, s3_storage, fake_s3_client):
        """Read retrieves content from the correct key."""
        fake_s3_client._store["reports/r1.txt"] = b"content"
        result = await s3_storage.read("reports/r1.txt")
        assert result == "content"

    @pytest.mark.asyncio
    async def test_read_missing_key_returns_none(self, s3_storage):
        """Reading a key that does not exist returns None."""
        result = await s3_storage.read("nonexistent.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_with_prefix(self, s3_storage, fake_s3_client):
        """list() returns only keys matching the prefix."""
        fake_s3_client._store["logs/a.log"] = b"a"
        fake_s3_client._store["logs/b.log"] = b"b"
        fake_s3_client._store["data/c.dat"] = b"c"
        result = await s3_storage.list("logs/")
        assert "logs/a.log" in result
        assert "logs/b.log" in result
        assert "data/c.dat" not in result

    @pytest.mark.asyncio
    async def test_delete_object(self, s3_storage, fake_s3_client):
        """delete() removes the object from the store."""
        fake_s3_client._store["temp.txt"] = b"temp"
        await s3_storage.delete("temp.txt")
        assert "temp.txt" not in fake_s3_client._store

    def test_session_reuse_singleton(self, s3_storage):
        """_get_session always returns the same session instance."""
        s1 = s3_storage._get_session()
        s2 = s3_storage._get_session()
        assert s1 is s2

    @pytest.mark.asyncio
    async def test_network_failure_propagates(self, s3_storage, fake_s3_client):
        """Network errors during read are re-raised (after logging)."""
        original_get = fake_s3_client.get_object

        async def fail_get(**kwargs):
            raise ConnectionError("Network down")

        fake_s3_client.get_object = fail_get
        with pytest.raises(ConnectionError, match="Network down"):
            await s3_storage.read("any-key.txt")
        fake_s3_client.get_object = original_get

    def test_custom_endpoint_url(self, s3_storage):
        """Custom endpoint_url (MinIO) is stored in the instance."""
        assert s3_storage.endpoint_url == "http://localhost:9000"

    def test_path_traversal_s3_key_rejected(self):
        """S3 keys with path traversal sequences are rejected."""
        with pytest.raises(ValueError, match="traversal"):
            S3Storage._safe_key("../../etc/passwd")

    def test_s3_key_too_long_rejected(self):
        """S3 keys exceeding 1024 bytes are rejected."""
        long_key = "a" * 1025
        with pytest.raises(ValueError, match="maximum length"):
            S3Storage._safe_key(long_key)

    def test_empty_bucket_name_rejected(self):
        """Empty bucket name raises ValueError at construction time."""
        with patch.dict("sys.modules", {"aioboto3": MagicMock()}):
            with pytest.raises(ValueError, match="bucket name must not be empty"):
                S3Storage(bucket="")

    @pytest.mark.asyncio
    async def test_write_oversized_rejected(self, s3_storage):
        """Content exceeding _MAX_WRITE_SIZE is rejected."""
        too_big = "Z" * (_MAX_WRITE_SIZE + 1)
        with pytest.raises(ValueError, match="too large"):
            await s3_storage.write("big.bin", too_big)

    def test_s3_key_control_char_rejected(self):
        """S3 keys with control characters are rejected."""
        with pytest.raises(ValueError, match="control characters"):
            S3Storage._safe_key("file\x07name.txt")


# ---------------------------------------------------------------------------
# 3. Database models (12+ tests)
# ---------------------------------------------------------------------------

class TestRunModel:
    """Run model CRUD and relationships."""

    @pytest.mark.asyncio
    async def test_create_run(self, db_session: AsyncSession):
        """A new Run gets a UUID and defaults to QUEUED status."""
        run = Run(workflow_name="test-wf")
        db_session.add(run)
        await db_session.commit()
        await db_session.refresh(run)
        assert run.id is not None
        assert run.status == RunStatus.QUEUED
        assert run.total_cost_usd == 0.0

    @pytest.mark.asyncio
    async def test_read_run(self, db_session: AsyncSession):
        """A committed Run can be fetched by primary key."""
        run = Run(workflow_name="reader-wf")
        db_session.add(run)
        await db_session.commit()
        fetched = await db_session.get(Run, run.id)
        assert fetched is not None
        assert fetched.workflow_name == "reader-wf"

    @pytest.mark.asyncio
    async def test_update_run_status(self, db_session: AsyncSession):
        """Updating a Run status persists correctly."""
        run = Run(workflow_name="status-wf")
        db_session.add(run)
        await db_session.commit()
        run.status = RunStatus.RUNNING
        run.started_at = datetime.now(timezone.utc)
        await db_session.commit()
        await db_session.refresh(run)
        assert run.status == RunStatus.RUNNING
        assert run.started_at is not None

    @pytest.mark.asyncio
    async def test_run_completed_status(self, db_session: AsyncSession):
        """Run can transition to COMPLETED with output data."""
        run = Run(workflow_name="complete-wf", status=RunStatus.RUNNING)
        db_session.add(run)
        await db_session.commit()
        run.status = RunStatus.COMPLETED
        run.output_data = {"result": "ok"}
        run.completed_at = datetime.now(timezone.utc)
        await db_session.commit()
        await db_session.refresh(run)
        assert run.status == RunStatus.COMPLETED
        assert run.output_data == {"result": "ok"}


class TestRunStepModel:
    """RunStep model with cost tracking and Run relationship."""

    @pytest.mark.asyncio
    async def test_create_step_linked_to_run(self, db_session: AsyncSession):
        """A RunStep is linked to its parent Run via foreign key."""
        run = Run(workflow_name="step-wf")
        db_session.add(run)
        await db_session.commit()

        step = RunStep(
            run_id=run.id,
            step_id="generate",
            status=StepStatus.COMPLETED,
            cost_usd=0.0042,
            duration_seconds=1.5,
            model="claude-sonnet-4-6",
        )
        db_session.add(step)
        await db_session.commit()
        await db_session.refresh(step)
        assert step.run_id == run.id
        assert step.cost_usd == pytest.approx(0.0042)
        assert step.model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_step_default_status_pending(self, db_session: AsyncSession):
        """RunStep defaults to PENDING status."""
        run = Run(workflow_name="pending-wf")
        db_session.add(run)
        await db_session.commit()
        step = RunStep(run_id=run.id, step_id="init")
        db_session.add(step)
        await db_session.commit()
        await db_session.refresh(step)
        assert step.status == StepStatus.PENDING

    @pytest.mark.asyncio
    async def test_step_cost_tracking(self, db_session: AsyncSession):
        """Multiple steps accumulate cost independently."""
        run = Run(workflow_name="cost-wf")
        db_session.add(run)
        await db_session.commit()

        s1 = RunStep(run_id=run.id, step_id="s1", cost_usd=0.01)
        s2 = RunStep(run_id=run.id, step_id="s2", cost_usd=0.05)
        db_session.add_all([s1, s2])
        await db_session.commit()
        await db_session.refresh(s1)
        await db_session.refresh(s2)
        assert s1.cost_usd + s2.cost_usd == pytest.approx(0.06)


class TestWorkflowVersionModel:
    """WorkflowVersion model with version numbering."""

    @pytest.mark.asyncio
    async def test_create_version(self, db_session: AsyncSession):
        """WorkflowVersion stores YAML content, version number, and checksum."""
        wv = WorkflowVersion(
            workflow_name="my-wf",
            version=1,
            yaml_content="steps:\n  - id: greet",
            checksum=hashlib.sha256(b"steps:\n  - id: greet").hexdigest(),
            steps_count=1,
        )
        db_session.add(wv)
        await db_session.commit()
        await db_session.refresh(wv)
        assert wv.version == 1
        assert wv.status == WorkflowVersionStatus.DRAFT

    @pytest.mark.asyncio
    async def test_version_unique_constraint(self, db_session: AsyncSession):
        """Two versions with the same (workflow_name, version) violate uniqueness."""
        v1 = WorkflowVersion(
            workflow_name="dup-wf", version=1,
            yaml_content="v1", checksum="aaa",
        )
        v2 = WorkflowVersion(
            workflow_name="dup-wf", version=1,
            yaml_content="v2", checksum="bbb",
        )
        db_session.add(v1)
        await db_session.commit()
        db_session.add(v2)
        with pytest.raises(Exception):
            await db_session.commit()
        await db_session.rollback()


class TestApiKeyModel:
    """ApiKey model with HMAC hash and is_active flag."""

    @pytest.mark.asyncio
    async def test_create_api_key(self, db_session: AsyncSession):
        """ApiKey stores an HMAC hash, prefix, and tenant association."""
        key_hash = hashlib.sha256(b"sk-test-key-123").hexdigest()
        ak = ApiKey(
            key_hash=key_hash,
            key_prefix="sk-test-",
            tenant_id="tenant-42",
            name="Test Key",
        )
        db_session.add(ak)
        await db_session.commit()
        await db_session.refresh(ak)
        assert ak.is_active is True
        assert ak.key_hash == key_hash
        assert ak.tenant_id == "tenant-42"

    @pytest.mark.asyncio
    async def test_deactivate_api_key(self, db_session: AsyncSession):
        """Setting is_active=False disables the key."""
        ak = ApiKey(
            key_hash=hashlib.sha256(b"deactivate-me").hexdigest(),
            name="Temp Key",
        )
        db_session.add(ak)
        await db_session.commit()
        ak.is_active = False
        await db_session.commit()
        await db_session.refresh(ak)
        assert ak.is_active is False

    @pytest.mark.asyncio
    async def test_key_hash_unique(self, db_session: AsyncSession):
        """Duplicate key_hash values violate the unique constraint."""
        dup_hash = hashlib.sha256(b"unique-key").hexdigest()
        ak1 = ApiKey(key_hash=dup_hash, name="Key A")
        ak2 = ApiKey(key_hash=dup_hash, name="Key B")
        db_session.add(ak1)
        await db_session.commit()
        db_session.add(ak2)
        with pytest.raises(Exception):
            await db_session.commit()
        await db_session.rollback()


class TestScheduleModel:
    """Schedule model with cron expression storage."""

    @pytest.mark.asyncio
    async def test_create_schedule(self, db_session: AsyncSession):
        """A schedule stores cron expression and workflow name."""
        sched = Schedule(
            workflow_name="nightly-report",
            cron_expression="0 3 * * *",
            input_data={"format": "pdf"},
            enabled=True,
        )
        db_session.add(sched)
        await db_session.commit()
        await db_session.refresh(sched)
        assert sched.cron_expression == "0 3 * * *"
        assert sched.enabled is True

    @pytest.mark.asyncio
    async def test_disable_schedule(self, db_session: AsyncSession):
        """Disabling a schedule sets enabled=False."""
        sched = Schedule(
            workflow_name="hourly-check",
            cron_expression="0 * * * *",
        )
        db_session.add(sched)
        await db_session.commit()
        sched.enabled = False
        await db_session.commit()
        await db_session.refresh(sched)
        assert sched.enabled is False


class TestAuditEventModel:
    """AuditEvent model with hash chain integrity."""

    @pytest.mark.asyncio
    async def test_create_audit_event(self, db_session: AsyncSession):
        """An AuditEvent stores event_type, actor, and hash chain fields."""
        evt = AuditEvent(
            event_type="run.started",
            actor_id="system",
            prev_hash="0" * 64,
            entry_hash=hashlib.sha256(b"first-entry").hexdigest(),
            payload={"run_id": str(uuid.uuid4())},
        )
        db_session.add(evt)
        await db_session.commit()
        await db_session.refresh(evt)
        assert evt.event_type == "run.started"
        assert len(evt.entry_hash) == 64

    @pytest.mark.asyncio
    async def test_hash_chain_integrity(self, db_session: AsyncSession):
        """Two consecutive audit events form a valid hash chain."""
        genesis_hash = hashlib.sha256(b"genesis").hexdigest()
        e1 = AuditEvent(
            event_type="run.started",
            actor_id="system",
            prev_hash="0" * 64,
            entry_hash=genesis_hash,
        )
        db_session.add(e1)
        await db_session.commit()

        second_hash = hashlib.sha256(
            (genesis_hash + "run.completed").encode()
        ).hexdigest()
        e2 = AuditEvent(
            event_type="run.completed",
            actor_id="system",
            prev_hash=genesis_hash,
            entry_hash=second_hash,
        )
        db_session.add(e2)
        await db_session.commit()
        await db_session.refresh(e2)
        assert e2.prev_hash == e1.entry_hash


class TestHubSubmissionModel:
    """HubSubmission model with slug uniqueness."""

    @pytest.mark.asyncio
    async def test_create_hub_submission(self, db_session: AsyncSession):
        """HubSubmission stores a unique slug, name, and category."""
        sub = HubSubmission(
            slug="email-summarizer",
            name="Email Summarizer",
            yaml_content="steps:\n  - id: summarize",
            author="tester",
            category="productivity",
        )
        db_session.add(sub)
        await db_session.commit()
        await db_session.refresh(sub)
        assert sub.slug == "email-summarizer"
        assert sub.status == "pending"
        assert sub.downloads == 0

    @pytest.mark.asyncio
    async def test_slug_uniqueness(self, db_session: AsyncSession):
        """Duplicate slugs violate the unique constraint."""
        s1 = HubSubmission(
            slug="dup-slug", name="First",
            yaml_content="a", author="a", category="general_ai",
        )
        s2 = HubSubmission(
            slug="dup-slug", name="Second",
            yaml_content="b", author="b", category="general_ai",
        )
        db_session.add(s1)
        await db_session.commit()
        db_session.add(s2)
        with pytest.raises(Exception):
            await db_session.commit()
        await db_session.rollback()


class TestApprovalRequestModel:
    """ApprovalRequest model with status transitions."""

    @pytest.mark.asyncio
    async def test_create_pending_approval(self, db_session: AsyncSession):
        """A new approval request defaults to PENDING status."""
        run = Run(workflow_name="approval-wf")
        db_session.add(run)
        await db_session.commit()

        ar = ApprovalRequest(
            run_id=run.id,
            step_id="review",
            message="Please approve this output",
        )
        db_session.add(ar)
        await db_session.commit()
        await db_session.refresh(ar)
        assert ar.status == ApprovalStatus.PENDING

    @pytest.mark.asyncio
    async def test_approve_request(self, db_session: AsyncSession):
        """Transitioning to APPROVED sets reviewer info and resolved_at."""
        run = Run(workflow_name="approve-wf")
        db_session.add(run)
        await db_session.commit()

        ar = ApprovalRequest(run_id=run.id, step_id="check", message="ok?")
        db_session.add(ar)
        await db_session.commit()

        ar.status = ApprovalStatus.APPROVED
        ar.reviewer_id = "admin@example.com"
        ar.reviewer_comment = "Looks good"
        ar.resolved_at = datetime.now(timezone.utc)
        await db_session.commit()
        await db_session.refresh(ar)
        assert ar.status == ApprovalStatus.APPROVED
        assert ar.reviewer_id == "admin@example.com"

    @pytest.mark.asyncio
    async def test_reject_request(self, db_session: AsyncSession):
        """Transitioning to REJECTED is also valid."""
        run = Run(workflow_name="reject-wf")
        db_session.add(run)
        await db_session.commit()

        ar = ApprovalRequest(run_id=run.id, step_id="gate", message="verify")
        db_session.add(ar)
        await db_session.commit()

        ar.status = ApprovalStatus.REJECTED
        ar.reviewer_comment = "Output is incorrect"
        ar.resolved_at = datetime.now(timezone.utc)
        await db_session.commit()
        await db_session.refresh(ar)
        assert ar.status == ApprovalStatus.REJECTED


class TestPolicyViolationModel:
    """PolicyViolation model with severity levels."""

    @pytest.mark.asyncio
    async def test_create_violation(self, db_session: AsyncSession):
        """A policy violation records severity, action taken, and details."""
        run = Run(workflow_name="policy-wf")
        db_session.add(run)
        await db_session.commit()

        pv = PolicyViolation(
            run_id=run.id,
            step_id="generate",
            policy_id="no-pii",
            severity="high",
            trigger_details="SSN detected in output",
            action_taken="redact",
            output_modified=True,
        )
        db_session.add(pv)
        await db_session.commit()
        await db_session.refresh(pv)
        assert pv.severity == "high"
        assert pv.action_taken == "redact"
        assert pv.output_modified is True

    @pytest.mark.asyncio
    async def test_severity_levels(self, db_session: AsyncSession):
        """Multiple severity levels (low, medium, high, critical) persist."""
        run = Run(workflow_name="sev-wf")
        db_session.add(run)
        await db_session.commit()

        for sev in ("low", "medium", "high", "critical"):
            pv = PolicyViolation(
                run_id=run.id,
                step_id="step1",
                policy_id=f"rule-{sev}",
                severity=sev,
                action_taken="log",
            )
            db_session.add(pv)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(
            select(PolicyViolation).where(PolicyViolation.run_id == run.id)
        )
        violations = result.scalars().all()
        severities = {v.severity for v in violations}
        assert severities == {"low", "medium", "high", "critical"}


class TestEvolutionIterationModel:
    """EvolutionIteration model with score tracking."""

    @pytest.mark.asyncio
    async def test_create_iteration(self, db_session: AsyncSession):
        """An evolution iteration tracks mutation type, score, and status."""
        evo = WorkflowEvolution(workflow_name="evolve-wf")
        db_session.add(evo)
        await db_session.commit()

        it = EvolutionIteration(
            evolution_id=evo.id,
            iteration_number=1,
            mutation_type="prompt",
            mutation_description="Improved system prompt clarity",
            score=0.85,
            quality=0.9,
            cost_usd=0.003,
            status="keep",
        )
        db_session.add(it)
        await db_session.commit()
        await db_session.refresh(it)
        assert it.score == pytest.approx(0.85)
        assert it.status == "keep"

    @pytest.mark.asyncio
    async def test_score_tracking_across_iterations(self, db_session: AsyncSession):
        """Multiple iterations show score progression."""
        evo = WorkflowEvolution(workflow_name="score-wf")
        db_session.add(evo)
        await db_session.commit()

        scores = [0.6, 0.7, 0.75, 0.82]
        for i, score in enumerate(scores, 1):
            it = EvolutionIteration(
                evolution_id=evo.id,
                iteration_number=i,
                mutation_type="model",
                mutation_description=f"Iteration {i}",
                score=score,
                status="keep" if score > 0.65 else "discard",
            )
            db_session.add(it)
        await db_session.commit()

        from sqlalchemy import select
        result = await db_session.execute(
            select(EvolutionIteration)
            .where(EvolutionIteration.evolution_id == evo.id)
            .order_by(EvolutionIteration.iteration_number)
        )
        iterations = result.scalars().all()
        assert len(iterations) == 4
        # Scores should monotonically increase in this test data
        for j in range(1, len(iterations)):
            assert iterations[j].score >= iterations[j - 1].score


class TestIndexPresence:
    """Verify that all expected database indexes exist in the schema."""

    @pytest.mark.asyncio
    async def test_runs_indexes(self, db_engine):
        """Runs table has indexes on status, created_at, tenant_id, and workflow_name."""
        async with db_engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda c: sa_inspect(c).get_indexes("runs")
            )
        idx_names = {idx["name"] for idx in indexes}
        assert "ix_runs_status" in idx_names
        assert "ix_runs_created_at" in idx_names
        assert "ix_runs_tenant_id" in idx_names
        assert "ix_runs_workflow_name" in idx_names

    @pytest.mark.asyncio
    async def test_run_steps_indexes(self, db_engine):
        """run_steps table has indexes on run_id, model, and composite."""
        async with db_engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda c: sa_inspect(c).get_indexes("run_steps")
            )
        idx_names = {idx["name"] for idx in indexes}
        assert "ix_run_steps_run_id" in idx_names
        assert "ix_run_steps_model" in idx_names

    @pytest.mark.asyncio
    async def test_audit_events_indexes(self, db_engine):
        """audit_events table has indexes on run_id, actor_id, event_type."""
        async with db_engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda c: sa_inspect(c).get_indexes("audit_events")
            )
        idx_names = {idx["name"] for idx in indexes}
        assert "ix_audit_events_run_id" in idx_names
        assert "ix_audit_events_actor_id" in idx_names
        assert "ix_audit_events_event_type" in idx_names
        assert "ix_audit_events_created_at" in idx_names

    @pytest.mark.asyncio
    async def test_api_keys_indexes(self, db_engine):
        """api_keys table has indexes on tenant_id and is_active."""
        async with db_engine.connect() as conn:
            indexes = await conn.run_sync(
                lambda c: sa_inspect(c).get_indexes("api_keys")
            )
        idx_names = {idx["name"] for idx in indexes}
        assert "ix_api_keys_tenant_id" in idx_names
        assert "ix_api_keys_is_active" in idx_names


class TestForeignKeyRelationships:
    """Verify foreign key relationships between models."""

    @pytest.mark.asyncio
    async def test_run_to_steps_cascade(self, db_session: AsyncSession):
        """Deleting a Run cascades to its RunSteps."""
        run = Run(workflow_name="cascade-wf")
        db_session.add(run)
        await db_session.commit()

        step = RunStep(run_id=run.id, step_id="s1")
        db_session.add(step)
        await db_session.commit()
        step_id = step.id

        await db_session.delete(run)
        await db_session.commit()

        deleted_step = await db_session.get(RunStep, step_id)
        assert deleted_step is None

    @pytest.mark.asyncio
    async def test_parent_child_run_relationship(self, db_session: AsyncSession):
        """A Run can reference a parent_run_id forming a tree."""
        parent = Run(workflow_name="parent-wf")
        db_session.add(parent)
        await db_session.commit()

        child = Run(workflow_name="child-wf", parent_run_id=parent.id, depth=1)
        db_session.add(child)
        await db_session.commit()
        await db_session.refresh(child)
        assert child.parent_run_id == parent.id
        assert child.depth == 1

    @pytest.mark.asyncio
    async def test_approval_request_links_to_run(self, db_session: AsyncSession):
        """ApprovalRequest references a valid Run."""
        run = Run(workflow_name="fk-wf")
        db_session.add(run)
        await db_session.commit()

        ar = ApprovalRequest(run_id=run.id, step_id="gate", message="approve?")
        db_session.add(ar)
        await db_session.commit()
        await db_session.refresh(ar)
        assert ar.run_id == run.id


# ---------------------------------------------------------------------------
# 4. Database session management (5+ tests)
# ---------------------------------------------------------------------------

class TestSessionManagement:
    """Async session lifecycle and concurrency tests."""

    @pytest.mark.asyncio
    async def test_session_creates_working_connection(self, db_engine):
        """A freshly created session can execute queries."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_session_commit_persists(self, db_engine):
        """Data committed in one session is visible in another."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        run_id = None

        async with factory() as s1:
            run = Run(workflow_name="persist-wf")
            s1.add(run)
            await s1.commit()
            run_id = run.id

        async with factory() as s2:
            fetched = await s2.get(Run, run_id)
            assert fetched is not None
            assert fetched.workflow_name == "persist-wf"

    @pytest.mark.asyncio
    async def test_session_rollback_discards(self, db_engine):
        """Rolled-back changes are not visible afterwards."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)

        async with factory() as session:
            run = Run(workflow_name="rollback-wf")
            session.add(run)
            await session.flush()
            run_id = run.id
            await session.rollback()

        async with factory() as session2:
            fetched = await session2.get(Run, run_id)
            assert fetched is None

    @pytest.mark.asyncio
    async def test_concurrent_sessions_independent(self, db_engine):
        """Two concurrent sessions operating on different rows do not interfere."""
        factory = async_sessionmaker(db_engine, expire_on_commit=False)

        async def create_run(name: str) -> uuid.UUID:
            async with factory() as session:
                run = Run(workflow_name=name)
                session.add(run)
                await session.commit()
                return run.id

        id1, id2 = await asyncio.gather(
            create_run("concurrent-a"),
            create_run("concurrent-b"),
        )
        assert id1 != id2

        async with factory() as session:
            r1 = await session.get(Run, id1)
            r2 = await session.get(Run, id2)
            assert r1.workflow_name == "concurrent-a"
            assert r2.workflow_name == "concurrent-b"

    @pytest.mark.asyncio
    async def test_connection_pool_configuration(self):
        """PostgreSQL-style pool kwargs are generated correctly."""
        from sandcastle.models.db import _build_engine_kwargs
        kwargs = _build_engine_kwargs("postgresql+asyncpg://localhost/db")
        assert kwargs["pool_size"] == 20
        assert kwargs["max_overflow"] == 10
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_recycle"] == 3600

    @pytest.mark.asyncio
    async def test_sqlite_engine_kwargs(self):
        """SQLite-style engine kwargs include check_same_thread=False."""
        from sandcastle.models.db import _build_engine_kwargs
        kwargs = _build_engine_kwargs("sqlite+aiosqlite:///test.db")
        assert kwargs["connect_args"]["check_same_thread"] is False
        assert kwargs["connect_args"]["timeout"] == 30

    @pytest.mark.asyncio
    async def test_session_context_manager_cleanup(self, db_engine):
        """After the async context exits, session has no pending state.

        The session remains reusable (SQLAlchemy design) but its internal
        identity map is cleared and no dirty/new objects remain.
        """
        factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with factory() as session:
            run = Run(workflow_name="ctx-wf")
            session.add(run)
            # Before commit, the object is in the "new" set
            assert run in session.new
            await session.commit()
        # After exiting the context manager, new/dirty sets are empty
        assert len(session.new) == 0
        assert len(session.dirty) == 0
