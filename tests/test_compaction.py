"""Tests for context compaction strategies and their wiring into the engine."""

from __future__ import annotations

import json

import pytest

from sandcastle.engine.compaction import (
    STRATEGIES,
    CompactionResult,
    compact,
    compact_sync,
    estimate_tokens,
    normalize_strategy,
)


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_four_chars_per_token(self):
        assert estimate_tokens("a" * 400) == 100


class TestNoOp:
    """Text already inside the budget must come back untouched."""

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_short_text_unchanged(self, strategy):
        text = "short enough"
        r = compact_sync(text, 1000, strategy)
        assert r.text == text
        assert r.tokens_saved == 0
        assert r.applied is False

    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_zero_budget_disables_compaction(self, strategy):
        text = "x" * 10000
        assert compact_sync(text, 0, strategy).text == text


class TestTruncate:
    """The historical behaviour has to stay bit-for-bit available."""

    def test_keeps_head_and_marks_the_cut(self):
        text = "A" * 10000
        r = compact_sync(text, 500, "truncate")
        assert r.text.startswith("A" * 2000)
        assert r.text.endswith("[... truncated to fit context window ...]")

    def test_drops_the_tail(self):
        text = "START " + "x" * 8000 + " CONCLUSION"
        assert "CONCLUSION" not in compact_sync(text, 200, "truncate").text


class TestHeadTail:
    """The point of head_tail is that the conclusion survives."""

    def test_keeps_both_ends(self):
        text = "START-MARKER " + "x" * 8000 + " END-MARKER"
        r = compact_sync(text, 200, "head_tail")
        assert "START-MARKER" in r.text
        assert "END-MARKER" in r.text

    def test_respects_budget(self):
        text = "y" * 40000
        r = compact_sync(text, 300, "head_tail")
        assert len(r.text) <= 300 * 4

    def test_reports_savings(self):
        r = compact_sync("z" * 40000, 300, "head_tail")
        assert r.tokens_before > r.tokens_after
        assert r.tokens_saved == r.tokens_before - r.tokens_after
        assert r.applied is True

    def test_says_how_much_was_dropped(self):
        r = compact_sync("q" * 40000, 300, "head_tail")
        assert "omitted from the middle" in r.text


class TestPrune:
    def test_shortens_long_json_arrays_but_keeps_shape(self):
        payload = json.dumps(
            {"items": [{"id": i, "blob": "v" * 50} for i in range(300)], "verdict": "PASS"}
        )
        r = compact_sync(payload, 200, "prune")
        assert "verdict" in r.text
        assert r.tokens_after < r.tokens_before

    def test_collapses_repeated_lines(self):
        logs = "\n".join(["ERROR identical line"] * 200 + ["FINAL VERDICT: done"])
        r = compact_sync(logs, 40, "prune")
        assert "FINAL VERDICT: done" in r.text
        assert r.tokens_after < r.tokens_before

    def test_falls_back_to_head_tail_on_unstructured_text(self):
        text = "HEAD " + "".join(f"line {i} unique\n" for i in range(4000)) + " TAIL"
        r = compact_sync(text, 200, "prune")
        assert "HEAD" in r.text
        assert "TAIL" in r.text

    def test_invalid_json_does_not_raise(self):
        assert compact_sync("{not valid json" + "x" * 9000, 100, "prune").text


class TestSummarizeFallback:
    def test_sync_path_degrades_and_says_so(self):
        r = compact_sync("w" * 40000, 300, "summarize")
        assert r.strategy == "head_tail"
        assert r.fallback_from == "summarize"

    @pytest.mark.asyncio
    async def test_model_failure_falls_back_without_raising(self, monkeypatch):
        async def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr("sandcastle.engine.sandshore.query", boom, raising=False)
        r = await compact("m" * 40000, 300, "summarize")
        assert r.strategy == "head_tail"
        assert r.fallback_from == "summarize"
        assert r.text

    @pytest.mark.asyncio
    async def test_empty_summary_falls_back(self, monkeypatch):
        class _R:
            output = "   "
            total_cost_usd = 0.0

        async def empty(*a, **k):
            return _R()

        monkeypatch.setattr("sandcastle.engine.sandshore.query", empty, raising=False)
        r = await compact("n" * 40000, 300, "summarize")
        assert r.strategy == "head_tail"
        assert r.fallback_from == "summarize"

    @pytest.mark.asyncio
    async def test_successful_summary_is_used_and_costed(self, monkeypatch):
        class _R:
            output = "A short summary that fits."
            total_cost_usd = 0.002

        async def ok(*a, **k):
            return _R()

        monkeypatch.setattr("sandcastle.engine.sandshore.query", ok, raising=False)
        r = await compact("p" * 40000, 300, "summarize")
        assert r.strategy == "summarize"
        assert r.text == "A short summary that fits."
        assert r.cost_usd == 0.002
        assert r.tokens_saved > 0

    @pytest.mark.asyncio
    async def test_oversized_summary_is_trimmed(self, monkeypatch):
        class _R:
            output = "S" * 90000
            total_cost_usd = 0.0

        async def big(*a, **k):
            return _R()

        monkeypatch.setattr("sandcastle.engine.sandshore.query", big, raising=False)
        r = await compact("r" * 200000, 300, "summarize")
        assert len(r.text) <= 300 * 4


class TestNormalizeStrategy:
    @pytest.mark.parametrize("raw,expected", [
        ("head-tail", "head_tail"),
        ("HEAD_TAIL", "head_tail"),
        (" prune ", "prune"),
        ("nonsense", "truncate"),
        ("", "truncate"),
    ])
    def test_normalization(self, raw, expected):
        assert normalize_strategy(raw) == expected


class TestStepConfig:
    def test_default_is_truncate(self):
        from sandcastle.engine.dag import StepDefinition

        assert StepDefinition(id="s").context_strategy == "truncate"

    def test_parsed_from_yaml(self):
        from sandcastle.engine.dag import parse_yaml_string

        wf = parse_yaml_string("""
name: compaction-test
steps:
  - id: a
    type: llm
    prompt: hi
    output_max_tokens: 100
    context_strategy: head_tail
  - id: b
    type: llm
    prompt: hi
    context_strategy: summarize
    context_model: ollama
""")
        by_id = {s.id: s for s in wf.steps}
        assert by_id["a"].context_strategy == "head_tail"
        assert by_id["b"].context_strategy == "summarize"
        assert by_id["b"].context_model == "ollama"

    def test_unknown_strategy_falls_back_to_truncate(self):
        from sandcastle.engine.dag import parse_yaml_string

        wf = parse_yaml_string("""
name: bad-strategy
steps:
  - id: a
    type: llm
    prompt: hi
    context_strategy: nonsense
""")
        assert wf.steps[0].context_strategy == "truncate"


class TestEngineWiring:
    """The resolve path must apply the step's strategy and record the saving."""

    def _ctx(self, strategy: str):
        from sandcastle.engine.executor import RunContext

        ctx = RunContext(run_id="r1", input={})
        ctx.step_outputs["research"] = "START-MARKER " + "x" * 20000 + " END-MARKER"
        ctx._step_output_max_tokens = {"research": 200}
        ctx._step_context_strategy = {"research": strategy}
        return ctx

    def test_truncate_still_default_behaviour(self):
        from sandcastle.engine.executor import resolve_variable

        ctx = self._ctx("truncate")
        out = resolve_variable("steps.research.output", ctx)
        assert out.endswith("[... truncated to fit context window ...]")

    def test_head_tail_preserves_the_ending(self):
        from sandcastle.engine.executor import resolve_variable

        ctx = self._ctx("head_tail")
        out = resolve_variable("steps.research.output", ctx)
        assert "END-MARKER" in out

    def test_saving_is_recorded_for_the_step(self):
        from sandcastle.engine.executor import resolve_variable

        ctx = self._ctx("head_tail")
        resolve_variable("steps.research.output", ctx)
        assert ctx._compaction_saved.get("research", 0) > 0
        assert ctx._compaction_strategy_used.get("research") == "head_tail"

    def test_nothing_recorded_when_output_fits(self):
        from sandcastle.engine.executor import RunContext, resolve_variable

        ctx = RunContext(run_id="r1", input={})
        ctx.step_outputs["small"] = "tiny"
        ctx._step_output_max_tokens = {"small": 500}
        ctx._step_context_strategy = {"small": "head_tail"}
        resolve_variable("steps.small.output", ctx)
        assert ctx._compaction_saved.get("small", 0) == 0


class TestResultDataclass:
    def test_saved_never_negative(self):
        r = CompactionResult("x", tokens_before=10, tokens_after=50, strategy="prune")
        assert r.tokens_saved == 0
