"""Tests for agent memory engine (Mem0 wrapper)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine.memory import (
    _reset_client,
    delete_all_memories,
    delete_memory,
    format_memories_for_prompt,
    load_memories,
    resolve_scope_id,
    save_memory,
)


# --- Mock Mem0 client ---


class MockMem0Client:
    """Mock Mem0 Memory client for unit testing."""

    def __init__(self):
        self._store: dict[str, list[dict]] = {}
        self._counter = 0

    def add(self, content: str, user_id: str = "", metadata: dict | None = None):
        if user_id not in self._store:
            self._store[user_id] = []
        self._counter += 1
        entry = {
            "id": f"mem-{self._counter}",
            "memory": content,
            "metadata": metadata or {},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        }
        self._store[user_id].append(entry)
        return [entry]

    def get_all(self, user_id: str = ""):
        return self._store.get(user_id, [])

    def search(self, query: str, user_id: str = "", limit: int = 10):
        items = self._store.get(user_id, [])
        return {"results": items[:limit]}

    def delete(self, memory_id: str):
        for uid, items in self._store.items():
            self._store[uid] = [m for m in items if m["id"] != memory_id]

    def delete_all(self, user_id: str = ""):
        self._store.pop(user_id, None)


@pytest.fixture(autouse=True)
def _reset_memory_singleton():
    """Reset the memory client singleton before each test."""
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def mock_client():
    """Provide a MockMem0Client and patch _get_client to return it."""
    client = MockMem0Client()
    with patch("sandcastle.engine.memory._get_client", return_value=client):
        yield client


# --- resolve_scope_id ---


class TestResolveScopeId:
    def test_workflow_scope_default(self):
        cfg = MagicMock(scope="workflow", agent="")
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"

    def test_agent_scope(self):
        cfg = MagicMock(scope="agent", agent="standup-bot")
        assert resolve_scope_id(cfg, "daily-standup") == "agent:standup-bot"

    def test_global_scope(self):
        cfg = MagicMock(scope="global", agent="")
        assert resolve_scope_id(cfg, "any-wf") == "global"

    def test_none_config_defaults_to_workflow(self):
        assert resolve_scope_id(None, "my-wf") == "workflow:my-wf"

    def test_agent_scope_without_agent_name(self):
        cfg = MagicMock(scope="agent", agent="")
        # Falls through to workflow scope when agent name is empty
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"


# --- format_memories_for_prompt ---


class TestFormatMemoriesForPrompt:
    def test_empty_list(self):
        assert format_memories_for_prompt([]) == ""

    def test_single_memory(self):
        memories = [{"memory": "The user prefers JSON output"}]
        result = format_memories_for_prompt(memories)
        assert "[Agent Memory]" in result
        assert "- The user prefers JSON output" in result
        assert "[End of Agent Memory]" in result

    def test_multiple_memories(self):
        memories = [
            {"memory": "Fact one"},
            {"memory": "Fact two"},
            {"memory": "Fact three"},
        ]
        result = format_memories_for_prompt(memories)
        assert "- Fact one" in result
        assert "- Fact two" in result
        assert "- Fact three" in result

    def test_max_chars_truncation(self):
        memories = [
            {"memory": "A" * 100},
            {"memory": "B" * 100},
            {"memory": "C" * 100},
        ]
        result = format_memories_for_prompt(memories, max_chars=150)
        assert "- " + "A" * 100 in result
        # Second memory should fit (102 + ~102 = 204 > 150)
        assert "B" * 100 not in result

    def test_skips_empty_memories(self):
        memories = [{"memory": ""}, {"memory": "Real fact"}]
        result = format_memories_for_prompt(memories)
        assert "- Real fact" in result
        lines = result.strip().split("\n")
        # Header + 1 fact + footer = 3 lines
        assert len(lines) == 3


# --- load_memories ---


class TestLoadMemories:
    @pytest.mark.asyncio
    async def test_load_empty(self, mock_client):
        result = await load_memories("workflow:test")
        assert result == []

    @pytest.mark.asyncio
    async def test_load_after_save(self, mock_client):
        # Pre-populate
        mock_client.add("Test memory", user_id="workflow:test")
        result = await load_memories("workflow:test")
        assert len(result) == 1
        assert result[0]["memory"] == "Test memory"

    @pytest.mark.asyncio
    async def test_load_with_query(self, mock_client):
        mock_client.add("Standup notes from Monday", user_id="agent:bot")
        result = await load_memories("agent:bot", query="standup", limit=5)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_load_respects_limit(self, mock_client):
        for i in range(20):
            mock_client.add(f"Memory {i}", user_id="workflow:test")
        result = await load_memories("workflow:test", limit=5)
        assert len(result) == 5

    @pytest.mark.asyncio
    async def test_load_handles_error(self):
        """Errors should be caught and return empty list."""
        with patch("sandcastle.engine.memory._get_client", side_effect=Exception("fail")):
            result = await load_memories("workflow:test")
            assert result == []


# --- save_memory ---


class TestSaveMemory:
    @pytest.mark.asyncio
    async def test_save_basic(self, mock_client):
        result = await save_memory("workflow:test", "Important fact")
        assert len(result) == 1
        assert result[0]["memory"] == "Important fact"

    @pytest.mark.asyncio
    async def test_save_with_metadata(self, mock_client):
        result = await save_memory(
            "workflow:test", "Fact", metadata={"step_id": "gather"}, run_id="run-1"
        )
        assert len(result) == 1
        meta = result[0]["metadata"]
        assert meta["step_id"] == "gather"
        assert meta["run_id"] == "run-1"

    @pytest.mark.asyncio
    async def test_save_handles_error(self):
        with patch("sandcastle.engine.memory._get_client", side_effect=Exception("fail")):
            result = await save_memory("workflow:test", "fact")
            assert result == []


# --- delete_memory ---


class TestDeleteMemory:
    @pytest.mark.asyncio
    async def test_delete_existing(self, mock_client):
        mock_client.add("To delete", user_id="workflow:test")
        memories = mock_client.get_all(user_id="workflow:test")
        mem_id = memories[0]["id"]
        ok = await delete_memory(mem_id)
        assert ok is True
        assert len(mock_client.get_all(user_id="workflow:test")) == 0

    @pytest.mark.asyncio
    async def test_delete_handles_error(self):
        with patch("sandcastle.engine.memory._get_client", side_effect=Exception("fail")):
            ok = await delete_memory("nonexistent")
            assert ok is False


# --- delete_all_memories ---


class TestDeleteAllMemories:
    @pytest.mark.asyncio
    async def test_delete_all(self, mock_client):
        mock_client.add("Fact 1", user_id="workflow:test")
        mock_client.add("Fact 2", user_id="workflow:test")
        ok = await delete_all_memories("workflow:test")
        assert ok is True
        assert len(mock_client.get_all(user_id="workflow:test")) == 0

    @pytest.mark.asyncio
    async def test_scope_isolation(self, mock_client):
        mock_client.add("A", user_id="workflow:wf1")
        mock_client.add("B", user_id="workflow:wf2")
        await delete_all_memories("workflow:wf1")
        assert len(mock_client.get_all(user_id="workflow:wf1")) == 0
        assert len(mock_client.get_all(user_id="workflow:wf2")) == 1
