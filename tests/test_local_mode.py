"""Tests for local mode (SQLite + in-process queue + in-memory cancel)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


# Patch settings before importing anything that uses them
@pytest.fixture(autouse=True)
def local_mode_settings(tmp_path, monkeypatch):
    """Force local mode settings for all tests in this module."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


# --- Config ---


class TestLocalModeConfig:
    def test_is_local_mode_empty_db_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("REDIS_URL", "")
        from sandcastle.config import Settings

        s = Settings()
        assert s.is_local_mode is True

    def test_is_local_mode_sqlite_url(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/test.db")
        from sandcastle.config import Settings

        s = Settings()
        assert s.is_local_mode is True

    def test_is_production_mode(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql+asyncpg://user:pass@localhost:5432/sandcastle",
        )
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        from sandcastle.config import Settings

        s = Settings()
        assert s.is_local_mode is False


# --- Database ---


class TestSQLiteEngine:
    def test_engine_url_is_sqlite(self, tmp_path):
        from unittest.mock import patch

        from sandcastle.models.db import _build_engine_url

        with patch("sandcastle.models.db.settings") as mock_settings:
            mock_settings.database_url = ""
            mock_settings.data_dir = str(tmp_path / "data")
            url = _build_engine_url()

        assert url.startswith("sqlite+aiosqlite:///")
        assert "sandcastle.db" in url

    def test_engine_kwargs_sqlite(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        from sandcastle.models.db import _build_engine_kwargs

        kwargs = _build_engine_kwargs()
        assert "connect_args" in kwargs
        assert kwargs["connect_args"]["check_same_thread"] is False

    @pytest.mark.asyncio
    async def test_init_db_creates_tables(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from sandcastle.models.db import Base, _build_engine_kwargs, _build_engine_url

        url = _build_engine_url()
        eng = create_async_engine(url, **_build_engine_kwargs())
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Verify tables exist by running a simple query
        session_factory = async_sessionmaker(eng, expire_on_commit=False)
        async with session_factory() as session:
            from sqlalchemy import text

            result = await session.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
            tables = {row[0] for row in result.fetchall()}

        await eng.dispose()

        assert "runs" in tables
        assert "run_steps" in tables
        assert "schedules" in tables
        assert "api_keys" in tables
        assert "dead_letter_queue" in tables


# --- In-process Queue ---


class TestInProcessQueue:
    @pytest.mark.asyncio
    async def test_enqueue_runs_in_process(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "")
        executed = []

        async def fake_job(ctx, yaml, input_data, run_id, **kw):
            executed.append(run_id)
            return {"run_id": run_id, "status": "completed"}

        with patch("sandcastle.queue.worker.run_workflow_job", fake_job):
            from sandcastle.queue.worker import enqueue_workflow

            await enqueue_workflow(
                workflow_yaml="name: test\nsteps: []",
                input_data={},
                run_id="test-run-123",
            )
            # Give the background task time to execute
            await asyncio.sleep(0.1)

        assert "test-run-123" in executed


# --- In-memory Cancel ---


class TestInMemoryCancel:
    @pytest.mark.asyncio
    async def test_cancel_flag_in_memory(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "")
        from sandcastle.engine.executor import (
            _cancel_flags,
            _check_cancel,
            cancel_run_local,
        )

        run_id = "cancel-test-001"

        # Not cancelled yet
        assert await _check_cancel(run_id) is False

        # Set cancel flag
        cancel_run_local(run_id)
        assert await _check_cancel(run_id) is True

        # Cleanup
        _cancel_flags.discard(run_id)


# --- API Endpoints ---


class TestHealthLocalMode:
    def test_health_redis_is_null(self):
        with patch(
            "sandcastle.api.routes.SandshoreRuntime"
        ) as MockClient:
            mock = AsyncMock()
            mock.health.return_value = True
            mock.close = AsyncMock()
            MockClient.return_value = mock

            from sandcastle.main import app

            test_client = TestClient(app)
            response = test_client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        health = data["data"]
        assert health["redis"] is None
        assert health["runtime"] is True


class TestRuntimeEndpoint:
    def test_runtime_returns_local_mode(self):
        from sandcastle.main import app

        test_client = TestClient(app)
        response = test_client.get("/api/runtime")

        assert response.status_code == 200
        data = response.json()
        info = data["data"]
        assert info["mode"] == "local"
        assert info["database"] == "sqlite"
        assert info["queue"] == "in-process"
        assert info["storage"] == "local"
        assert info["data_dir"] is not None
