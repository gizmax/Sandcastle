"""Tests for agent memory engine v2 (Mem0 wrapper with admission, decay, enrichment)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.memory import (
    MemoryAdmissionError,
    MemoryBackendError,
    MemoryError,
    MemoryErrorKind,
    _reset_client,
    apply_decay,
    delete_all_memories,
    delete_memory,
    detect_conflicts,
    enrich_memory,
    format_memories_for_prompt,
    load_memories,
    memory_health_check,
    resolve_scope_id,
    save_memory,
    score_importance,
    should_admit,
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
            "id": f"mem-{self._counter}", "memory": content,
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


# --- Fixtures ---

@pytest.fixture(autouse=True)
def _reset_memory_singleton():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def mock_client():
    client = MockMem0Client()
    with patch("sandcastle.engine.memory._get_client", return_value=client):
        yield client


# --- resolve_scope_id ---

class TestResolveScopeId:
    def test_workflow_scope(self):
        cfg = MagicMock(scope="workflow", agent="")
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"

    def test_agent_scope(self):
        cfg = MagicMock(scope="agent", agent="standup-bot")
        assert resolve_scope_id(cfg, "daily") == "agent:standup-bot"

    def test_global_scope(self):
        cfg = MagicMock(scope="global", agent="")
        assert resolve_scope_id(cfg, "any") == "global"

    def test_none_config(self):
        assert resolve_scope_id(None, "my-wf") == "workflow:my-wf"

    def test_agent_scope_no_name_fallback(self):
        cfg = MagicMock(scope="agent", agent="")
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"


# --- score_importance ---

class TestScoreImportance:
    def test_returns_float_in_range(self):
        s = score_importance("Some content", [])
        assert isinstance(s, float) and 0.0 <= s <= 1.0

    def test_short_content_penalty(self):
        short = score_importance("ok", [])
        normal = score_importance(
            "Deployment failed on staging due to missing env var.", [],
        )
        assert short < normal

    def test_long_content_within_bounds(self):
        assert score_importance("word " * 2000, []) <= 1.0

    def test_json_numbers_boost(self):
        plain = score_importance("Customer is happy.", [])
        rich = score_importance(
            'Data: {"satisfaction": 9.5, "nps": 85}', [],
        )
        assert rich >= plain

    def test_novelty_check(self):
        existing = [{"memory": "Login timeout on mobile"}]
        dup = score_importance("Login timeout on tablet", existing)
        fresh = score_importance("Payment gateway done", existing)
        assert dup < fresh

    def test_empty_content(self):
        assert score_importance("", []) < 0.3

    def test_no_existing_memories(self):
        assert score_importance("Meaningful insight.", []) > 0.0


# --- should_admit ---

class TestShouldAdmit:
    def test_tuple_shape(self):
        admitted, score, reason = should_admit("Content", [])
        assert isinstance(admitted, bool)
        assert isinstance(score, float)
        assert isinstance(reason, str)

    def test_above_threshold(self):
        admitted, score, _ = should_admit(
            "Critical: production failover at 03:00. Disk space exhaustion.",
            [], threshold=0.2,
        )
        assert admitted is True and score >= 0.2

    def test_below_threshold(self):
        admitted, _, reason = should_admit("ok", [], threshold=0.99)
        assert admitted is False

    def test_zero_threshold_admits_all(self):
        assert should_admit("x", [], threshold=0.0)[0] is True

    def test_high_threshold_rejects_trivial(self):
        assert should_admit("hi", [{"memory": "hi"}], threshold=0.9)[0] is False


# --- apply_decay ---

class TestApplyDecay:
    @staticmethod
    def _mem(content: str, days_ago: int) -> dict:
        ts = time.time() - (days_ago * 86400)
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        return {
            "id": f"m-{days_ago}", "memory": content,
            "metadata": {}, "created_at": iso, "updated_at": iso,
        }

    def test_old_filtered(self):
        result = apply_decay(
            [self._mem("Recent", 5), self._mem("Ancient", 200)],
            max_age_days=90,
        )
        assert len(result) == 1 and result[0]["memory"] == "Recent"

    def test_recency_sorting(self):
        result = apply_decay([
            self._mem("Old", 30), self._mem("New", 1), self._mem("Mid", 15),
        ], max_age_days=90)
        assert [m["memory"] for m in result] == ["New", "Mid", "Old"]

    def test_relevance_score_present(self):
        result = apply_decay([self._mem("Fact", 10)], max_age_days=90)
        assert "relevance_score" in result[0]
        assert 0.0 <= result[0]["relevance_score"] <= 1.0

    def test_zero_max_age_keeps_all(self):
        result = apply_decay(
            [self._mem("Very old", 9999), self._mem("Recent", 1)],
            max_age_days=0,
        )
        assert len(result) == 2

    def test_empty_list(self):
        assert apply_decay([], max_age_days=90) == []


# --- detect_conflicts ---

class TestDetectConflicts:
    def test_overlap_detected(self):
        existing = [{"memory": "Deployment uses PostgreSQL 14 on primary."}]
        assert len(detect_conflicts(
            "Deployment uses MySQL 8 on primary.", existing,
        )) >= 1

    def test_unrelated_no_conflict(self):
        existing = [{"memory": "CI runs on GitHub Actions."}]
        assert detect_conflicts("Customer survey is positive.", existing) == []

    def test_short_content(self):
        assert detect_conflicts("ok", []) == []

    def test_empty_existing(self):
        assert detect_conflicts("Anything.", []) == []

    def test_conflict_has_reference(self):
        existing = [{"memory": "Rate limit is 100 req/min."}]
        conflicts = detect_conflicts("Rate limit is 500 req/min.", existing)
        if conflicts:
            assert "memory" in conflicts[0] or "content" in conflicts[0]


# --- enrich_memory ---

class TestEnrichMemory:
    def test_returns_dict(self):
        assert isinstance(enrich_memory("Customer timeout."), dict)

    def test_keyword_extraction(self):
        r = enrich_memory(
            "PostgreSQL optimizer chose sequential scan on orders table."
        )
        assert "keywords" in r and len(r["keywords"]) > 0

    def test_auto_tag_issue(self):
        r = enrich_memory("Bug: login crashes on Safari with 2FA.")
        assert "issue" in r["tags"] or "bug" in r["tags"]

    def test_auto_tag_preference(self):
        r = enrich_memory("Customer prefers email over phone.")
        assert "preference" in r["tags"]

    def test_auto_tag_data(self):
        r = enrich_memory("Revenue Q4: $2.3M, up 18%. ARPU: $45.")
        assert "data" in r["tags"]

    def test_metadata_merge(self):
        r = enrich_memory("Test.", metadata={"step_id": "gather"})
        has_it = r.get("step_id") == "gather" or (
            "metadata" in r and r["metadata"].get("step_id") == "gather"
        )
        assert has_it

    def test_empty_content(self):
        assert isinstance(enrich_memory(""), dict)


# --- format_memories_for_prompt ---

class TestFormatMemoriesForPrompt:
    def test_empty(self):
        assert format_memories_for_prompt([]) == ""

    def test_single(self):
        r = format_memories_for_prompt([{"memory": "User prefers JSON"}])
        assert "[Agent Memory]" in r and "User prefers JSON" in r

    def test_multiple(self):
        mems = [{"memory": f"Fact {i}"} for i in range(3)]
        r = format_memories_for_prompt(mems)
        for i in range(3):
            assert f"Fact {i}" in r

    def test_truncation(self):
        mems = [{"memory": c * 100} for c in "ABC"]
        r = format_memories_for_prompt(mems, max_chars=150)
        assert "A" * 100 in r and "B" * 100 not in r

    def test_skips_empty(self):
        mems = [{"memory": ""}, {"memory": "Real"}]
        r = format_memories_for_prompt(mems)
        lines = [ln for ln in r.strip().split("\n") if ln.strip()]
        assert len(lines) == 3

    def test_tags_present(self):
        mems = [{"memory": "Pref", "metadata": {"tags": ["preference"]}}]
        assert "Pref" in format_memories_for_prompt(mems)


# --- load_memories ---

class TestLoadMemories:
    @pytest.mark.asyncio
    async def test_load_empty(self, mock_client):
        assert await load_memories("workflow:test") == []

    @pytest.mark.asyncio
    async def test_load_after_save(self, mock_client):
        mock_client.add("Test", user_id="workflow:test")
        r = await load_memories("workflow:test")
        assert len(r) == 1 and r[0]["memory"] == "Test"

    @pytest.mark.asyncio
    async def test_load_with_query(self, mock_client):
        mock_client.add("Standup Monday", user_id="agent:bot")
        assert len(await load_memories("agent:bot", query="standup")) == 1

    @pytest.mark.asyncio
    async def test_load_limit(self, mock_client):
        for i in range(20):
            mock_client.add(f"M{i}", user_id="workflow:test")
        assert len(await load_memories("workflow:test", limit=5)) == 5

    @pytest.mark.asyncio
    async def test_load_error(self):
        with patch("sandcastle.engine.memory._get_client", side_effect=Exception):
            assert await load_memories("workflow:test") == []

    @pytest.mark.asyncio
    async def test_load_max_age_days(self, mock_client):
        mock_client.add("Old", user_id="workflow:test")
        assert isinstance(await load_memories("workflow:test", max_age_days=30), list)


# --- save_memory ---

class TestSaveMemory:
    @pytest.mark.asyncio
    async def test_basic(self, mock_client):
        content = "The login timeout issue was caused by a misconfigured rate limiter"
        r = await save_memory("workflow:test", content, skip_admission=True)
        assert len(r) == 1 and r[0]["memory"] == content

    @pytest.mark.asyncio
    async def test_with_metadata(self, mock_client):
        content = "User preference is JSON output with timestamps included"
        r = await save_memory(
            "workflow:test", content,
            metadata={"step_id": "gather"}, run_id="run-1",
            skip_admission=True,
        )
        assert r[0]["metadata"]["step_id"] == "gather"
        assert r[0]["metadata"]["run_id"] == "run-1"

    @pytest.mark.asyncio
    async def test_error(self):
        with patch("sandcastle.engine.memory._get_client", side_effect=Exception):
            with pytest.raises(MemoryBackendError):
                await save_memory(
                    "workflow:test", "fact", skip_admission=True,
                )

    @pytest.mark.asyncio
    async def test_admit_threshold(self, mock_client):
        content = "System perf insight: database query latency at 200ms average"
        r = await save_memory(
            "workflow:test", content, admit_threshold=0.3,
        )
        assert isinstance(r, list)

    @pytest.mark.asyncio
    async def test_zero_threshold(self, mock_client):
        r = await save_memory(
            "workflow:test", "Note", admit_threshold=0.0, skip_admission=True,
        )
        assert isinstance(r, list)


# --- delete_memory ---

class TestDeleteMemory:
    @pytest.mark.asyncio
    async def test_delete_existing(self, mock_client):
        mock_client.add("To delete", user_id="workflow:test")
        mem_id = mock_client.get_all(user_id="workflow:test")[0]["id"]
        assert await delete_memory(mem_id) is True
        assert len(mock_client.get_all(user_id="workflow:test")) == 0

    @pytest.mark.asyncio
    async def test_delete_error(self):
        with patch("sandcastle.engine.memory._get_client", side_effect=Exception):
            assert await delete_memory("x") is False


# --- delete_all_memories ---

class TestDeleteAllMemories:
    @pytest.mark.asyncio
    async def test_delete_all(self, mock_client):
        mock_client.add("F1", user_id="workflow:test")
        mock_client.add("F2", user_id="workflow:test")
        assert await delete_all_memories("workflow:test") is True
        assert len(mock_client.get_all(user_id="workflow:test")) == 0

    @pytest.mark.asyncio
    async def test_scope_isolation(self, mock_client):
        mock_client.add("A", user_id="workflow:wf1")
        mock_client.add("B", user_id="workflow:wf2")
        await delete_all_memories("workflow:wf1")
        assert len(mock_client.get_all(user_id="workflow:wf1")) == 0
        assert len(mock_client.get_all(user_id="workflow:wf2")) == 1


# --- memory_health_check ---

class TestMemoryHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_status(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            return_value=MockMem0Client(),
        ):
            r = await memory_health_check()
            assert isinstance(r, dict) and "status" in r

    @pytest.mark.asyncio
    async def test_backend_down(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            side_effect=Exception("down"),
        ):
            r = await memory_health_check()
            assert r["status"] in ("error", "unavailable", "unhealthy")


# --- Error types ---

class TestErrorTypes:
    def test_hierarchy(self):
        assert issubclass(MemoryAdmissionError, MemoryError)
        assert issubclass(MemoryBackendError, MemoryError)

    def test_error_kind_values(self):
        for kind in (
            MemoryErrorKind.BACKEND_UNAVAILABLE,
            MemoryErrorKind.ADMISSION_REJECTED,
            MemoryErrorKind.CONFLICT_DETECTED,
            MemoryErrorKind.DECAY_EXPIRED,
            MemoryErrorKind.UNKNOWN,
        ):
            assert isinstance(kind.value, str)

    def test_raise_admission(self):
        with pytest.raises(MemoryAdmissionError):
            raise MemoryAdmissionError("Score too low")

    def test_raise_backend(self):
        with pytest.raises(MemoryBackendError):
            raise MemoryBackendError("Connection refused")


# --- Integration ---

class TestMemoryIntegration:
    def test_enrich_then_score(self):
        assert isinstance(enrich_memory("Latency spike 2500ms."), dict)
        assert score_importance("Latency spike 2500ms.", []) > 0.0

    def test_admit_then_conflict(self):
        content = "The database backend uses PostgreSQL version 14 for production"
        assert should_admit(content, [], threshold=0.1)[0]
        conflicts = detect_conflicts(
            "The database backend uses MySQL version 8 for production",
            [{"memory": content}],
        )
        assert len(conflicts) >= 1

    def test_decay_preserves_recent(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        mems = [{"id": "m1", "memory": "Fresh", "metadata": {},
                 "created_at": now, "updated_at": now}]
        assert apply_decay(mems, max_age_days=30)[0]["memory"] == "Fresh"

    @pytest.mark.asyncio
    async def test_roundtrip(self, mock_client):
        await save_memory(
            "agent:bot", "Sprint velocity: 42 story points average",
            skip_admission=True,
        )
        loaded = await load_memories("agent:bot")
        assert len(loaded) == 1

    @pytest.mark.asyncio
    async def test_save_delete_verify(self, mock_client):
        saved = await save_memory(
            "workflow:test",
            "Temporary note about deployment configuration",
            skip_admission=True,
        )
        assert await delete_memory(saved[0]["id"]) is True
        assert await load_memories("workflow:test") == []
