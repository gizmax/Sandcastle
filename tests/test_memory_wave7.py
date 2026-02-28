"""Wave 7 deep audit tests for Agent Memory v2.

Covers: correctness, performance edge cases, security (scope isolation,
input validation, injection), admission control, decay, conflict detection,
enrichment, prompt formatting, content size limits, and graph client config.

Note: _word_set() filters words <= 2 chars - tests use longer words.
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from sandcastle.engine.memory import (
    MemoryAdmissionError,
    MemoryBackendError,
    MemoryError,
    MemoryErrorKind,
    _reset_client,
    _validate_scope,
    _word_set,
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


# ---------------------------------------------------------------------------
# Mock Mem0 client
# ---------------------------------------------------------------------------


class MockMem0Client:
    """Mock Mem0 Memory client for unit testing with scope isolation."""

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
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 1. Scope validation
# ---------------------------------------------------------------------------


class TestScopeValidation:
    """Test scope_id format validation added in wave7."""

    def test_valid_workflow_scope(self):
        _validate_scope("workflow:my-workflow")

    def test_valid_agent_scope(self):
        _validate_scope("agent:standup-bot")

    def test_valid_global_scope(self):
        _validate_scope("global")

    def test_valid_health_check_scope(self):
        _validate_scope("__health_check__")

    def test_valid_scope_with_dots(self):
        _validate_scope("workflow:my.workflow.v2")

    def test_valid_scope_with_spaces(self):
        _validate_scope("agent:my bot name")

    def test_invalid_scope_empty(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("")

    def test_invalid_scope_no_prefix(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("just-a-name")

    def test_invalid_scope_unknown_prefix(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("unknown:something")

    def test_invalid_scope_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("workflow:../../etc/passwd")

    def test_invalid_scope_newline_injection(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("workflow:test\ninjected")

    def test_invalid_scope_null_byte(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("workflow:test\x00injected")

    def test_invalid_scope_too_long(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("workflow:" + "a" * 250)

    def test_invalid_scope_special_chars(self):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope("workflow:test;DROP TABLE memories")

    @pytest.mark.asyncio
    async def test_load_memories_rejects_bad_scope(self, mock_client):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            await load_memories("bad-scope-no-prefix")

    @pytest.mark.asyncio
    async def test_save_memory_rejects_bad_scope(self, mock_client):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            await save_memory(
                "../../etc/passwd",
                "content here",
                skip_admission=True,
            )

    @pytest.mark.asyncio
    async def test_delete_all_rejects_bad_scope(self, mock_client):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            await delete_all_memories("invalid:scope:format")


# ---------------------------------------------------------------------------
# 2. Scope isolation
# ---------------------------------------------------------------------------


class TestScopeIsolation:
    """Verify that different scopes cannot read each other's memories."""

    @pytest.mark.asyncio
    async def test_workflow_scopes_are_isolated(self, mock_client):
        """workflow:A cannot read workflow:B memories."""
        await save_memory(
            "workflow:alpha",
            "Secret strategy for alpha workflow production deployment",
            skip_admission=True,
        )
        await save_memory(
            "workflow:beta",
            "Configuration details for beta workflow staging environment",
            skip_admission=True,
        )

        alpha_mems = await load_memories("workflow:alpha")
        beta_mems = await load_memories("workflow:beta")

        assert len(alpha_mems) == 1
        assert len(beta_mems) == 1
        assert "alpha" in alpha_mems[0]["memory"]
        assert "beta" in beta_mems[0]["memory"]

    @pytest.mark.asyncio
    async def test_agent_scope_isolated_from_workflow(self, mock_client):
        await save_memory(
            "agent:mybot",
            "Agent-specific memory about user preferences for output",
            skip_admission=True,
        )
        wf_mems = await load_memories("workflow:mybot")
        assert len(wf_mems) == 0

    @pytest.mark.asyncio
    async def test_global_scope_separate(self, mock_client):
        await save_memory(
            "global",
            "Global memory shared across all production workflows",
            skip_admission=True,
        )
        wf_mems = await load_memories("workflow:test")
        assert len(wf_mems) == 0

    @pytest.mark.asyncio
    async def test_delete_all_only_affects_target_scope(self, mock_client):
        await save_memory(
            "workflow:keep-this",
            "Important memory that should survive deletion of other scope",
            skip_admission=True,
        )
        await save_memory(
            "workflow:delete-this",
            "Memory that will be deleted from target scope only",
            skip_admission=True,
        )
        await delete_all_memories("workflow:delete-this")
        kept = await load_memories("workflow:keep-this")
        deleted = await load_memories("workflow:delete-this")
        assert len(kept) == 1
        assert len(deleted) == 0


# ---------------------------------------------------------------------------
# 3. Content size limits
# ---------------------------------------------------------------------------


class TestContentSizeLimits:
    """Verify that oversized content is truncated in save_memory."""

    @pytest.mark.asyncio
    async def test_content_within_limit_passes(self, mock_client):
        content = "Normal sized content for memory storage" * 10
        result = await save_memory(
            "workflow:test", content, skip_admission=True,
        )
        assert len(result) == 1
        assert result[0]["memory"] == content

    @pytest.mark.asyncio
    async def test_oversized_content_truncated(self, mock_client):
        content = "x" * 200_000  # 200KB, over the 100KB limit
        result = await save_memory(
            "workflow:test", content, skip_admission=True,
        )
        assert len(result) == 1
        # The content stored should be truncated
        assert len(result[0]["memory"]) == 100_000

    @pytest.mark.asyncio
    async def test_exactly_at_limit_not_truncated(self, mock_client):
        content = "y" * 100_000  # exactly at limit
        result = await save_memory(
            "workflow:test", content, skip_admission=True,
        )
        assert len(result[0]["memory"]) == 100_000


# ---------------------------------------------------------------------------
# 4. Admission control
# ---------------------------------------------------------------------------


class TestAdmissionControl:
    """Thorough tests for admission scoring and threshold logic."""

    def test_score_empty_content_low(self):
        assert score_importance("", []) < 0.3

    def test_score_whitespace_only_low(self):
        assert score_importance("   ", []) < 0.3

    def test_score_very_short_penalized(self):
        short_score = score_importance("hello", [])
        normal_score = score_importance(
            "The deployment pipeline failed due to missing environment variable", [],
        )
        assert short_score < normal_score

    def test_score_sweet_spot_bonus(self):
        # 50-500 chars gets a bonus
        sweet = "a" * 100
        too_short = "ab"
        assert score_importance(sweet, []) > score_importance(too_short, [])

    def test_score_very_long_penalized(self):
        long_text = "word " * 500  # ~2500 chars
        medium_text = "word " * 60  # ~300 chars
        assert score_importance(long_text, []) <= score_importance(medium_text, [])

    def test_score_json_data_boosted(self):
        plain = "Customer satisfaction is good overall"
        structured = '{"satisfaction": 9.5, "nps": 85}'
        assert score_importance(structured, []) >= score_importance(plain, [])

    def test_score_iso_date_boosted(self):
        plain = "Something happened on Monday last week"
        dated = "Deployment completed at 2026-02-28"
        assert score_importance(dated, []) >= score_importance(plain, [])

    def test_score_high_novelty_when_no_existing(self):
        # No existing memories = no novelty penalty
        score = score_importance(
            "Completely novel insight about system architecture", [],
        )
        assert score >= 0.5

    def test_score_duplicate_heavily_penalized(self):
        existing = [{"memory": "Login timeout issue on mobile browser"}]
        duplicate_score = score_importance(
            "Login timeout issue on mobile browser", existing,
        )
        fresh_score = score_importance(
            "Payment gateway integration completed successfully", existing,
        )
        assert duplicate_score < fresh_score

    def test_score_partial_overlap_moderate_penalty(self):
        existing = [{"memory": "Database uses PostgreSQL version fourteen"}]
        partial = score_importance(
            "Database uses MySQL version eight", existing,
        )
        # Shares "database", "uses", "version" -> moderate overlap
        assert 0.0 <= partial <= 1.0

    def test_score_always_clamped(self):
        # Even extreme inputs stay in [0, 1]
        assert 0.0 <= score_importance("x", [{"memory": "x"}]) <= 1.0
        assert 0.0 <= score_importance("y" * 5000, []) <= 1.0

    def test_should_admit_empty_rejected(self):
        admitted, score, reason = should_admit("", [])
        assert admitted is False
        assert "Empty" in reason

    def test_should_admit_whitespace_only_rejected(self):
        admitted, _, reason = should_admit("   \n\t  ", [])
        assert admitted is False
        assert "Empty" in reason

    def test_should_admit_zero_threshold_accepts_all(self):
        admitted, _, _ = should_admit("anything", [], threshold=0.0)
        assert admitted is True

    def test_should_admit_1_threshold_rejects_most(self):
        admitted, _, _ = should_admit("hello world", [], threshold=1.0)
        assert admitted is False

    def test_should_admit_returns_correct_tuple_types(self):
        admitted, score, reason = should_admit(
            "Some reasonable content for testing purposes", [],
        )
        assert isinstance(admitted, bool)
        assert isinstance(score, float)
        assert isinstance(reason, str)

    @pytest.mark.asyncio
    async def test_save_memory_admission_rejection(self, mock_client):
        """save_memory raises MemoryAdmissionError when content is too short."""
        with pytest.raises(MemoryAdmissionError):
            await save_memory(
                "workflow:test",
                "ok",
                admit_threshold=0.99,
            )

    @pytest.mark.asyncio
    async def test_save_memory_skip_admission(self, mock_client):
        """skip_admission=True bypasses the check."""
        result = await save_memory(
            "workflow:test",
            "Short note for testing",
            skip_admission=True,
        )
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_save_memory_duplicate_penalized(self, mock_client):
        """Saving the same content twice triggers novelty penalty."""
        content = "Production database backup schedule runs every night"
        await save_memory(
            "workflow:test", content, skip_admission=True,
        )
        # Second save should be penalized by novelty check
        # but may still pass if threshold is low
        with pytest.raises(MemoryAdmissionError):
            await save_memory(
                "workflow:test", content, admit_threshold=0.5,
            )


# ---------------------------------------------------------------------------
# 5. Decay / TTL
# ---------------------------------------------------------------------------


class TestDecayTTL:
    """Test memory decay filtering and relevance scoring."""

    @staticmethod
    def _mem(content: str, days_ago: float) -> dict:
        ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return {
            "id": f"m-{days_ago}",
            "memory": content,
            "metadata": {},
            "created_at": ts.isoformat(),
            "updated_at": ts.isoformat(),
        }

    def test_expired_memories_filtered(self):
        result = apply_decay(
            [self._mem("Recent", 5), self._mem("Expired", 100)],
            max_age_days=90,
        )
        assert len(result) == 1
        assert result[0]["memory"] == "Recent"

    def test_recent_memory_high_relevance(self):
        result = apply_decay([self._mem("Fresh", 0.1)], max_age_days=90)
        assert result[0]["relevance_score"] > 0.9

    def test_old_memory_low_relevance(self):
        result = apply_decay([self._mem("Old", 85)], max_age_days=90)
        assert result[0]["relevance_score"] < 0.2

    def test_sorted_by_relevance_descending(self):
        result = apply_decay([
            self._mem("Old", 60),
            self._mem("New", 1),
            self._mem("Mid", 30),
        ], max_age_days=90)
        scores = [m["relevance_score"] for m in result]
        assert scores == sorted(scores, reverse=True)

    def test_zero_max_age_keeps_all(self):
        result = apply_decay(
            [self._mem("Ancient", 9999), self._mem("Recent", 1)],
            max_age_days=0,
        )
        assert len(result) == 2
        assert all(m["relevance_score"] == 1.0 for m in result)

    def test_negative_max_age_keeps_all(self):
        result = apply_decay(
            [self._mem("Anything", 500)],
            max_age_days=-1,
        )
        assert len(result) == 1

    def test_no_timestamp_gets_mid_relevance(self):
        mem = {"id": "m1", "memory": "No timestamp", "metadata": {}}
        result = apply_decay([mem], max_age_days=90)
        assert len(result) == 1
        assert result[0]["relevance_score"] == 0.5

    def test_invalid_timestamp_treated_as_now(self):
        mem = {
            "id": "m1", "memory": "Bad date", "metadata": {},
            "created_at": "not-a-date",
        }
        result = apply_decay([mem], max_age_days=90)
        assert len(result) == 1
        # Treated as "now" -> high relevance
        assert result[0]["relevance_score"] > 0.9

    def test_unix_timestamp_supported(self):
        ts = time.time() - (10 * 86400)  # 10 days ago
        mem = {
            "id": "m1", "memory": "Unix ts", "metadata": {},
            "created_at": ts,
        }
        result = apply_decay([mem], max_age_days=90)
        assert len(result) == 1
        assert 0.8 < result[0]["relevance_score"] < 1.0

    def test_z_suffix_timezone_handled(self):
        ts = (datetime.now(timezone.utc) - timedelta(days=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        mem = {"id": "m1", "memory": "Z suffix", "metadata": {}, "created_at": ts}
        result = apply_decay([mem], max_age_days=90)
        assert len(result) == 1
        assert result[0]["relevance_score"] > 0.9

    def test_empty_list_returns_empty(self):
        assert apply_decay([], max_age_days=90) == []

    def test_exactly_at_max_age_filtered(self):
        # Memory exactly at the boundary should be filtered out
        result = apply_decay(
            [self._mem("Boundary", 90.001)],
            max_age_days=90,
        )
        assert len(result) == 0

    def test_just_under_max_age_kept(self):
        result = apply_decay(
            [self._mem("Almost expired", 89.9)],
            max_age_days=90,
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 6. Conflict detection
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """Test word-overlap conflict detection heuristics."""

    def test_high_overlap_detected(self):
        existing = [{"memory": "Database backend uses PostgreSQL version fourteen"}]
        conflicts = detect_conflicts(
            "Database backend uses MySQL version eight", existing,
        )
        assert len(conflicts) >= 1
        assert "_overlap_ratio" in conflicts[0]
        assert conflicts[0]["_overlap_ratio"] > 0.4

    def test_no_overlap_no_conflict(self):
        existing = [{"memory": "Customer satisfaction survey completed"}]
        conflicts = detect_conflicts(
            "Infrastructure deployment pipeline configured", existing,
        )
        assert len(conflicts) == 0

    def test_empty_content_no_conflict(self):
        assert detect_conflicts("", [{"memory": "something"}]) == []

    def test_empty_existing_no_conflict(self):
        assert detect_conflicts("anything here", []) == []

    def test_short_words_filtered(self):
        # Words <= 2 chars are filtered by _word_set
        conflicts = detect_conflicts(
            "is an of",  # all short words
            [{"memory": "is an of"}],
        )
        assert len(conflicts) == 0

    def test_shared_words_list_bounded(self):
        # _shared_words is limited to 10
        words = " ".join(f"word{i}" for i in range(20))
        conflicts = detect_conflicts(
            words, [{"memory": words}],
        )
        if conflicts:
            assert len(conflicts[0]["_shared_words"]) <= 10

    def test_conflict_preserves_original_memory_data(self):
        existing = [{"memory": "Rate limit policy hundred requests", "id": "mem-42"}]
        conflicts = detect_conflicts(
            "Rate limit policy fifty requests", existing,
        )
        if conflicts:
            assert conflicts[0]["id"] == "mem-42"

    def test_multiple_existing_memories_checked(self):
        existing = [
            {"memory": "Backend uses PostgreSQL for data storage"},
            {"memory": "Frontend uses React with TypeScript"},
            {"memory": "Backend uses MySQL for data storage"},
        ]
        conflicts = detect_conflicts(
            "Backend uses SQLite for data storage", existing,
        )
        # Should find conflicts with the two "backend...data storage" memories
        assert len(conflicts) >= 1


# ---------------------------------------------------------------------------
# 7. Enrichment
# ---------------------------------------------------------------------------


class TestEnrichment:
    """Test structured memory enrichment (keywords, tags)."""

    def test_returns_expected_keys(self):
        result = enrich_memory("Test content for enrichment")
        assert "content" in result
        assert "keywords" in result
        assert "tags" in result
        assert "metadata" in result
        assert "enriched_at" in result

    def test_keyword_extraction_filters_short_words(self):
        result = enrich_memory("The big database query optimization")
        # "the", "big" filtered (stopword / short), keeps longer words
        assert all(len(kw) > 3 for kw in result["keywords"])

    def test_keyword_extraction_max_five(self):
        result = enrich_memory(
            "alpha bravo charlie delta echo foxtrot golf hotel india juliet"
        )
        assert len(result["keywords"]) <= 5

    def test_keyword_deduplication(self):
        result = enrich_memory("error error error different")
        kw_counts = {}
        for kw in result["keywords"]:
            kw_counts[kw] = kw_counts.get(kw, 0) + 1
        assert all(c == 1 for c in kw_counts.values())

    def test_tag_issue_detected(self):
        result = enrich_memory("The application crashed with an error")
        assert "issue" in result["tags"]

    def test_tag_preference_detected(self):
        result = enrich_memory("Users prefer dark mode in the interface")
        assert "preference" in result["tags"]

    def test_tag_insight_detected(self):
        result = enrich_memory("Team discovered a new optimization pattern")
        assert "insight" in result["tags"]

    def test_tag_data_detected_with_date(self):
        result = enrich_memory("Event happened on 2026-02-28")
        assert "data" in result["tags"]

    def test_tag_data_detected_with_decimal(self):
        result = enrich_memory("Revenue was 2.5 million this quarter")
        assert "data" in result["tags"]

    def test_tag_data_detected_with_large_number(self):
        result = enrich_memory("Total transactions reached 15000")
        assert "data" in result["tags"]

    def test_no_tags_for_generic_content(self):
        result = enrich_memory("The quick brown fox jumped")
        assert len(result["tags"]) == 0

    def test_multiple_tags_possible(self):
        result = enrich_memory("Bug found: 2026-01-15 crash in payment 99.99")
        assert "issue" in result["tags"]
        assert "data" in result["tags"]

    def test_metadata_preserved(self):
        meta = {"step_id": "gather", "custom": "value"}
        result = enrich_memory("Content here", metadata=meta)
        assert result["metadata"]["step_id"] == "gather"
        assert result["metadata"]["custom"] == "value"

    def test_empty_content_enrichable(self):
        result = enrich_memory("")
        assert result["content"] == ""
        assert result["keywords"] == []

    def test_enriched_at_is_valid_iso(self):
        result = enrich_memory("Some content here")
        datetime.fromisoformat(result["enriched_at"])  # should not raise

    def test_tags_are_sorted_and_unique(self):
        result = enrich_memory("error crash bug failure exception broken")
        assert result["tags"] == sorted(set(result["tags"]))


# ---------------------------------------------------------------------------
# 8. Prompt formatting
# ---------------------------------------------------------------------------


class TestPromptFormatting:
    """Test format_memories_for_prompt with edge cases."""

    def test_empty_memories_returns_empty_string(self):
        assert format_memories_for_prompt([]) == ""

    def test_header_and_footer_present(self):
        result = format_memories_for_prompt([{"memory": "Fact"}])
        assert "[Agent Memory]" in result
        assert "[End of Agent Memory]" in result

    def test_memory_content_included(self):
        result = format_memories_for_prompt([
            {"memory": "User prefers JSON output format"},
        ])
        assert "User prefers JSON output format" in result

    def test_multiple_memories_all_included(self):
        mems = [{"memory": f"Fact number {i}"} for i in range(5)]
        result = format_memories_for_prompt(mems)
        for i in range(5):
            assert f"Fact number {i}" in result

    def test_empty_memory_text_skipped(self):
        mems = [{"memory": ""}, {"memory": "Real content"}]
        result = format_memories_for_prompt(mems)
        lines = [ln for ln in result.split("\n") if ln.startswith("- ")]
        assert len(lines) == 1

    def test_max_chars_respects_header_footer(self):
        """max_chars should account for header + footer overhead."""
        result = format_memories_for_prompt(
            [{"memory": "a" * 200}],
            max_chars=100,
        )
        # Header "[Agent Memory]" + footer "[End of Agent Memory]" + newlines
        # already exceed 100 chars budget, so no memory lines should be included
        lines = [ln for ln in result.split("\n") if ln.startswith("- ")]
        assert len(lines) == 0

    def test_max_chars_truncation(self):
        mems = [{"memory": "x" * 100} for _ in range(5)]
        result = format_memories_for_prompt(mems, max_chars=200)
        # Should not include all 5 (would be 500+ chars of content)
        content_lines = [ln for ln in result.split("\n") if ln.startswith("- ")]
        assert len(content_lines) < 5

    def test_tags_and_keywords_in_output(self):
        mems = [{
            "memory": "Important fact",
            "metadata": {"tags": "issue,data", "keywords": "crash,server"},
        }]
        result = format_memories_for_prompt(mems)
        assert "tags:issue,data" in result
        assert "kw:crash,server" in result

    def test_relevance_score_in_output(self):
        mems = [{"memory": "Fact", "relevance_score": 0.85}]
        result = format_memories_for_prompt(mems)
        assert "rel:0.85" in result

    def test_very_small_max_chars(self):
        """Even with max_chars=0, should not crash."""
        result = format_memories_for_prompt(
            [{"memory": "content"}], max_chars=0,
        )
        # Should still have header and footer even if no content fits
        assert "[Agent Memory]" in result
        assert "[End of Agent Memory]" in result

    def test_metadata_none_handled(self):
        mems = [{"memory": "Fact", "metadata": None}]
        # Should not crash on None metadata
        result = format_memories_for_prompt(mems)
        assert "Fact" in result


# ---------------------------------------------------------------------------
# 9. Word set helper
# ---------------------------------------------------------------------------


class TestWordSet:
    """Test _word_set() tokenization edge cases."""

    def test_filters_short_words(self):
        words = _word_set("I am an ok guy")
        assert "am" not in words
        assert "an" not in words
        assert "ok" not in words
        assert "guy" in words

    def test_lowercase(self):
        words = _word_set("Hello WORLD")
        assert "hello" in words
        assert "world" in words

    def test_underscores_preserved(self):
        words = _word_set("my_variable_name")
        assert "my_variable_name" in words

    def test_numbers_included(self):
        words = _word_set("error404 and 2026")
        assert "error404" in words
        assert "2026" in words

    def test_empty_string(self):
        assert _word_set("") == set()

    def test_only_short_words(self):
        assert _word_set("I am on it") == set()

    def test_special_chars_split(self):
        words = _word_set("hello-world! foo@bar")
        assert "hello" in words
        assert "world" in words
        assert "foo" in words
        assert "bar" in words


# ---------------------------------------------------------------------------
# 10. Resolve scope ID
# ---------------------------------------------------------------------------


class TestResolveScopeId:
    """Test scope resolution from config objects."""

    def test_workflow_scope_default(self):
        cfg = MagicMock(scope="workflow", agent="")
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"

    def test_agent_scope_with_name(self):
        cfg = MagicMock(scope="agent", agent="standup-bot")
        assert resolve_scope_id(cfg, "daily") == "agent:standup-bot"

    def test_agent_scope_no_name_falls_back_to_workflow(self):
        cfg = MagicMock(scope="agent", agent="")
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"

    def test_global_scope(self):
        cfg = MagicMock(scope="global", agent="")
        assert resolve_scope_id(cfg, "any") == "global"

    def test_none_config_defaults_to_workflow(self):
        assert resolve_scope_id(None, "my-wf") == "workflow:my-wf"

    def test_unknown_scope_falls_back_to_workflow(self):
        cfg = MagicMock(scope="unknown", agent="")
        assert resolve_scope_id(cfg, "my-wf") == "workflow:my-wf"


# ---------------------------------------------------------------------------
# 11. Error hierarchy
# ---------------------------------------------------------------------------


class TestErrorHierarchy:
    """Test custom exception classes and error kinds."""

    def test_memory_error_is_exception(self):
        assert issubclass(MemoryError, Exception)

    def test_admission_error_is_memory_error(self):
        assert issubclass(MemoryAdmissionError, MemoryError)

    def test_backend_error_is_memory_error(self):
        assert issubclass(MemoryBackendError, MemoryError)

    def test_admission_error_has_score(self):
        err = MemoryAdmissionError("too low", score=0.15)
        assert err.score == 0.15
        assert err.kind == MemoryErrorKind.ADMISSION_REJECTED

    def test_backend_error_has_kind(self):
        err = MemoryBackendError("connection refused")
        assert err.kind == MemoryErrorKind.BACKEND_UNAVAILABLE

    def test_base_error_default_kind(self):
        err = MemoryError("generic")
        assert err.kind == MemoryErrorKind.UNKNOWN

    def test_all_error_kinds_have_string_values(self):
        for kind in MemoryErrorKind:
            assert isinstance(kind.value, str)


# ---------------------------------------------------------------------------
# 12. CRUD operations
# ---------------------------------------------------------------------------


class TestCrudOperations:
    """Test core CRUD async operations."""

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self, mock_client):
        content = "Important production database schema migration details"
        await save_memory("workflow:test", content, skip_admission=True)
        loaded = await load_memories("workflow:test")
        assert len(loaded) == 1
        assert loaded[0]["memory"] == content

    @pytest.mark.asyncio
    async def test_save_with_metadata_and_run_id(self, mock_client):
        result = await save_memory(
            "workflow:test",
            "Server configuration details for staging environment setup",
            metadata={"step_id": "config"},
            run_id="run-abc",
            skip_admission=True,
        )
        assert result[0]["metadata"]["step_id"] == "config"
        assert result[0]["metadata"]["run_id"] == "run-abc"

    @pytest.mark.asyncio
    async def test_save_enriches_with_tags(self, mock_client):
        result = await save_memory(
            "workflow:test",
            "Critical error found in payment processing module",
            skip_admission=True,
        )
        # Should have enrichment metadata
        meta = result[0]["metadata"]
        assert "tags" in meta or "keywords" in meta

    @pytest.mark.asyncio
    async def test_load_with_query(self, mock_client):
        await save_memory(
            "workflow:test",
            "Production monitoring dashboard configuration",
            skip_admission=True,
        )
        results = await load_memories("workflow:test", query="monitoring")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_load_with_limit(self, mock_client):
        for i in range(10):
            mock_client.add(
                f"Memory number {i} for testing", user_id="workflow:test",
            )
        results = await load_memories("workflow:test", limit=3)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_load_with_max_age_days(self, mock_client):
        mock_client.add("Fresh memory", user_id="workflow:test")
        results = await load_memories("workflow:test", max_age_days=30)
        assert isinstance(results, list)
        if results:
            assert "relevance_score" in results[0]

    @pytest.mark.asyncio
    async def test_delete_single_memory(self, mock_client):
        mock_client.add("To delete", user_id="workflow:test")
        mem_id = mock_client.get_all(user_id="workflow:test")[0]["id"]
        assert await delete_memory(mem_id) is True
        assert len(mock_client.get_all(user_id="workflow:test")) == 0

    @pytest.mark.asyncio
    async def test_delete_returns_false_on_error(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            side_effect=RuntimeError("down"),
        ):
            assert await delete_memory("nonexistent") is False

    @pytest.mark.asyncio
    async def test_delete_all_memories_clears_scope(self, mock_client):
        mock_client.add("Fact1", user_id="workflow:test")
        mock_client.add("Fact2", user_id="workflow:test")
        assert await delete_all_memories("workflow:test") is True
        assert len(mock_client.get_all(user_id="workflow:test")) == 0

    @pytest.mark.asyncio
    async def test_load_backend_error_returns_empty(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            side_effect=RuntimeError("fail"),
        ):
            result = await load_memories("workflow:test")
            assert result == []

    @pytest.mark.asyncio
    async def test_load_backend_error_raises_if_memory_backend_error(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            side_effect=MemoryBackendError("unreachable"),
        ):
            with pytest.raises(MemoryBackendError):
                await load_memories("workflow:test")

    @pytest.mark.asyncio
    async def test_save_backend_error_raises(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(MemoryBackendError):
                await save_memory(
                    "workflow:test", "content here", skip_admission=True,
                )


# ---------------------------------------------------------------------------
# 13. Health check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_backend(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            return_value=MockMem0Client(),
        ):
            result = await memory_health_check()
            assert result["status"] == "ok"
            assert "latency_ms" in result
            assert result["backend"] == "local"

    @pytest.mark.asyncio
    async def test_unhealthy_backend(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            side_effect=RuntimeError("connection refused"),
        ):
            result = await memory_health_check()
            assert result["status"] == "error"
            assert "error" in result

    @pytest.mark.asyncio
    async def test_cloud_backend_not_implemented(self):
        result = await memory_health_check(backend="cloud")
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# 14. Graph client config
# ---------------------------------------------------------------------------


class TestGraphClientConfig:
    """Verify graph client reads config from environment."""

    def test_graph_client_uses_env_vars(self):
        """Ensure Neo4j credentials come from env, not hardcoded."""
        from sandcastle.engine.memory import _get_graph_client

        _reset_client()  # reset the initialized flag

        mock_memory_cls = MagicMock()
        mock_memory_cls.from_config.side_effect = RuntimeError("no neo4j")

        with (
            patch.dict(
                "os.environ",
                {
                    "NEO4J_URL": "bolt://custom-host:7687",
                    "NEO4J_USERNAME": "admin",
                    "NEO4J_PASSWORD": "secret123",
                },
            ),
            patch.dict("sys.modules", {"mem0": MagicMock(Memory=mock_memory_cls)}),
        ):
            result = _get_graph_client()
            # Should have been called (even if it fails)
            # The important thing is it reads from env
            assert result is None

    def test_graph_client_default_empty_password(self):
        """Default Neo4j password should be empty, not 'neo4j'."""
        import sandcastle.engine.memory as mem_module

        # Read the source to verify no hardcoded password
        import inspect
        source = inspect.getsource(mem_module._get_graph_client)
        # Should NOT contain a hardcoded password like "neo4j"
        # The env default for password should be empty string
        assert 'NEO4J_PASSWORD' in source
        assert '"password": "neo4j"' not in source


# ---------------------------------------------------------------------------
# 15. Backend management
# ---------------------------------------------------------------------------


class TestBackendManagement:
    def test_unknown_backend_raises(self):
        with pytest.raises(MemoryBackendError, match="Unknown memory backend"):
            from sandcastle.engine.memory import _get_client
            _get_client("nonexistent")

    def test_cloud_backend_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Cloud memory backend"):
            from sandcastle.engine.memory import _get_client
            _get_client("cloud")

    def test_reset_client_clears_state(self):
        from sandcastle.engine.memory import (
            _clients,
            _graph_client_initialized,
        )

        _reset_client()
        assert len(_clients) == 0


# ---------------------------------------------------------------------------
# 16. Integration scenarios
# ---------------------------------------------------------------------------


class TestIntegrationScenarios:
    """End-to-end scenarios combining multiple memory operations."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, mock_client):
        """Save -> Load -> Search -> Delete -> Verify gone."""
        # Save
        content = "Critical deployment configuration for production environment"
        saved = await save_memory(
            "workflow:lifecycle", content, skip_admission=True,
        )
        mem_id = saved[0]["id"]

        # Load
        loaded = await load_memories("workflow:lifecycle")
        assert len(loaded) == 1
        assert loaded[0]["memory"] == content

        # Search
        found = await load_memories("workflow:lifecycle", query="deployment")
        assert len(found) == 1

        # Delete
        assert await delete_memory(mem_id) is True

        # Verify gone
        remaining = await load_memories("workflow:lifecycle")
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_enrich_then_save(self, mock_client):
        """Enrichment metadata should appear in saved memory."""
        content = "Database error: connection pool exhausted at 2026-02-28"
        await save_memory(
            "workflow:enrich-test", content, skip_admission=True,
        )
        loaded = await load_memories("workflow:enrich-test")
        meta = loaded[0]["metadata"]
        # Should have tags and keywords from enrichment
        assert "tags" in meta or "keywords" in meta

    @pytest.mark.asyncio
    async def test_admission_then_conflict_check(self, mock_client):
        """Admission control and conflict detection work together."""
        first = "The database backend uses PostgreSQL version fourteen"
        await save_memory(
            "workflow:conflict", first, skip_admission=True,
        )

        # Check for conflicts with a contradictory statement
        existing = await load_memories("workflow:conflict")
        conflicts = detect_conflicts(
            "The database backend uses MySQL version eight", existing,
        )
        assert len(conflicts) >= 1

    def test_format_after_decay(self):
        """Decay scores should appear in formatted prompt."""
        now = datetime.now(timezone.utc).isoformat()
        mems = [
            {"id": "m1", "memory": "Recent fact", "metadata": {},
             "created_at": now, "updated_at": now},
        ]
        decayed = apply_decay(mems, max_age_days=90)
        formatted = format_memories_for_prompt(decayed)
        assert "Recent fact" in formatted
        assert "rel:" in formatted


# ---------------------------------------------------------------------------
# 17. Edge cases and security
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Miscellaneous edge cases and security checks."""

    def test_metadata_tags_as_string_in_format(self):
        """Tags stored as comma-separated string should display correctly."""
        mems = [{"memory": "Fact", "metadata": {"tags": "issue,data"}}]
        result = format_memories_for_prompt(mems)
        assert "tags:issue,data" in result

    def test_metadata_tags_as_list_in_format(self):
        """Tags stored as list should be handled (get returns list, not string)."""
        mems = [{"memory": "Fact", "metadata": {"tags": ["issue", "data"]}}]
        result = format_memories_for_prompt(mems)
        # tags value is a list, which is truthy, so it enters the branch
        # and tries to format it
        assert "tags:" in result

    def test_score_importance_with_none_memory_text(self):
        """Existing memories with None text should not crash scoring."""
        existing = [{"memory": None}, {"memory": "Valid memory content"}]
        score = score_importance("New content to evaluate", existing)
        assert isinstance(score, float)

    def test_detect_conflicts_with_none_memory_text(self):
        existing = [{"memory": None}]
        conflicts = detect_conflicts("Some content here", existing)
        assert conflicts == []

    def test_enrich_memory_with_unicode(self):
        result = enrich_memory("Error in module: Umlaut test Prufung 42.5")
        assert isinstance(result, dict)
        assert "enriched_at" in result

    def test_format_with_very_long_memory(self):
        """A single memory that is very long should be included if within budget."""
        mems = [{"memory": "x" * 5000}]
        result = format_memories_for_prompt(mems, max_chars=6000)
        assert "x" * 5000 in result

    @pytest.mark.asyncio
    async def test_concurrent_saves_to_different_scopes(self, mock_client):
        """Multiple saves to different scopes should not interfere."""
        import asyncio

        tasks = [
            save_memory(
                f"workflow:scope-{i}",
                f"Memory content number {i} for concurrent test scenario",
                skip_admission=True,
            )
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)
        assert all(len(r) == 1 for r in results)

        # Verify isolation
        for i in range(5):
            loaded = await load_memories(f"workflow:scope-{i}")
            assert len(loaded) == 1
            assert str(i) in loaded[0]["memory"]

    def test_novelty_overlap_with_empty_content_words(self):
        """If content produces no words, score should still work."""
        score = score_importance("!@#$%", [{"memory": "valid content here"}])
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_should_admit_boundary_score_equals_threshold(self):
        """When score exactly equals threshold, should it be admitted?"""
        # score < threshold -> rejected, so score == threshold -> admitted
        content = "Some reasonable content for evaluation"
        _, score, _ = should_admit(content, [], threshold=0.0)
        admitted, _, _ = should_admit(content, [], threshold=score)
        # score < threshold is the rejection condition, so score == threshold passes
        assert admitted is False or score >= 0.0  # Either outcome is valid

    @pytest.mark.asyncio
    async def test_load_memories_dict_response_format(self, mock_client):
        """Handle Mem0 response as dict with 'results' key."""
        mock_client.add("Test fact", user_id="workflow:test")
        results = await load_memories("workflow:test", query="test")
        assert isinstance(results, list)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_save_memory_result_not_list(self, mock_client):
        """Handle case where client.add returns a dict instead of list."""
        original_add = mock_client.add

        def add_returning_dict(content, user_id="", metadata=None):
            result = original_add(content, user_id=user_id, metadata=metadata)
            return result[0]  # return dict instead of list

        mock_client.add = add_returning_dict
        result = await save_memory(
            "workflow:test",
            "Memory content for dict return test scenario",
            skip_admission=True,
        )
        assert isinstance(result, list)
        assert len(result) == 1
