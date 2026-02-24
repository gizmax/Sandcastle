"""Tests for memory API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sandcastle.main import app

client = TestClient(app)


# --- Fixtures ---


@pytest.fixture
def mock_memory_functions():
    """Mock all memory engine functions for API tests."""
    with (
        patch("sandcastle.engine.memory.load_memories", new_callable=AsyncMock) as mock_load,
        patch("sandcastle.engine.memory.save_memory", new_callable=AsyncMock) as mock_save,
        patch("sandcastle.engine.memory.delete_memory", new_callable=AsyncMock) as mock_delete,
        patch("sandcastle.engine.memory.delete_all_memories", new_callable=AsyncMock) as mock_del_all,
    ):
        mock_load.return_value = [
            {
                "id": "mem-1",
                "memory": "Test memory content",
                "metadata": {"step_id": "gather"},
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
            }
        ]
        mock_save.return_value = [{"id": "mem-2", "memory": "New memory"}]
        mock_delete.return_value = True
        mock_del_all.return_value = True
        yield {
            "load": mock_load,
            "save": mock_save,
            "delete": mock_delete,
            "delete_all": mock_del_all,
        }


# --- GET /api/memories ---


class TestListMemories:
    def test_list_memories(self, mock_memory_functions):
        resp = client.get("/api/memories", params={"scope_id": "workflow:test"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["memories"][0]["memory"] == "Test memory content"
        mock_memory_functions["load"].assert_called_once_with("workflow:test", limit=50)

    def test_list_memories_with_limit(self, mock_memory_functions):
        resp = client.get("/api/memories", params={"scope_id": "agent:bot", "limit": 10})
        assert resp.status_code == 200
        mock_memory_functions["load"].assert_called_once_with("agent:bot", limit=10)

    def test_list_memories_missing_scope(self, mock_memory_functions):
        resp = client.get("/api/memories")
        assert resp.status_code == 422  # Validation error - scope_id required


# --- POST /api/memories ---


class TestAddMemory:
    def test_add_memory(self, mock_memory_functions):
        resp = client.post("/api/memories", json={
            "scope_id": "workflow:test",
            "content": "Important fact to remember",
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["added"] == 1
        mock_memory_functions["save"].assert_called_once_with(
            scope_id="workflow:test",
            content="Important fact to remember",
            metadata=None,
        )

    def test_add_memory_with_metadata(self, mock_memory_functions):
        resp = client.post("/api/memories", json={
            "scope_id": "agent:bot",
            "content": "User likes summaries",
            "metadata": {"source": "manual"},
        })
        assert resp.status_code == 200
        mock_memory_functions["save"].assert_called_once_with(
            scope_id="agent:bot",
            content="User likes summaries",
            metadata={"source": "manual"},
        )


# --- POST /api/memories/search ---


class TestSearchMemories:
    def test_search_memories(self, mock_memory_functions):
        resp = client.post("/api/memories/search", json={
            "scope_id": "workflow:test",
            "query": "standup notes",
            "limit": 5,
        })
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "memories" in data
        mock_memory_functions["load"].assert_called_once_with(
            "workflow:test", query="standup notes", limit=5,
        )


# --- DELETE /api/memories/{memory_id} ---


class TestDeleteMemory:
    def test_delete_memory(self, mock_memory_functions):
        resp = client.delete("/api/memories/mem-1")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted"] is True
        mock_memory_functions["delete"].assert_called_once_with("mem-1")

    def test_delete_memory_not_found(self, mock_memory_functions):
        mock_memory_functions["delete"].return_value = False
        resp = client.delete("/api/memories/nonexistent")
        assert resp.status_code == 404


# --- DELETE /api/memories?scope_id= ---


class TestDeleteAllMemories:
    def test_delete_all_memories(self, mock_memory_functions):
        resp = client.delete("/api/memories", params={"scope_id": "workflow:test"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["deleted_all"] is True
        assert data["scope_id"] == "workflow:test"
        mock_memory_functions["delete_all"].assert_called_once_with("workflow:test")

    def test_delete_all_failure(self, mock_memory_functions):
        mock_memory_functions["delete_all"].return_value = False
        resp = client.delete("/api/memories", params={"scope_id": "workflow:test"})
        assert resp.status_code == 500
