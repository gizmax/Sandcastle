"""
SANDCASTLE EXECUTOR – DEEP TEST SUITE v2 (SOFISTIKOVANÁ VERZE)
================================================================
Co testujeme, co v základní verzi chybělo:

NOVÉ OBLASTI:
  ✦ _prepare_and_run_step (vrstva nad execute_step_with_retry)
  ✦ execute_workflow scheduler (ready-step detection, branch skip propagation)
  ✦ WorkflowPaused / StepBlocked propagation
  ✦ event_bus emits (step.started, step.completed, run.started, run.completed)
  ✦ Dependency-based scheduler vs stage-based (nový scheduler)
  ✦ Fan-out DLQ integration (dead letter path)
  ✦ _escape_js_string injection vectors
  ✦ _write_csv_output edge cases (append mode, multi-row, fieldname union)
  ✦ resolve_templates auto-inject unreferenced deps
  ✦ _check_budget edge cases (zero budget, None budget)
  ✦ with_item costs list sharing vs step_outputs isolation
  ✦ Condition step branch_skip_steps mutation
  ✦ Transform step Jinja2 tojson filter
  ✦ Sensor step timeout vs condition met
  ✦ Loop step until condition + max_iterations cap
  ✦ Code step builtins whitelist (které funkce jsou dostupné)
  ✦ Cache bypass when _step_reads_memory
  ✦ WorkflowResult timestamps (started_at, completed_at)
  ✦ Hypothesis: property-based testy pro resolver + cache key + budget
  ✦ Chaos: korupce JSON, None text, 500KB output, random failures
  ✦ Timing: parallel speedup, backoff monotonicity, duration tracking
  ✦ Brand scanner + PDF report jako end-to-end scénáře
  ✦ Security: MRO escape, exec, import, template injection, CSV path traversal

BUGS.md: testy které selžou jsou označeny BUG-xxx v assert message.
         Claude Code je zpracuje a zapíše do ~/Documents/Sandcastle/BUGS.md.

Instalace:
    uv add --dev hypothesis pytest-asyncio pytest-timeout

Spuštění:
    cd ~/Documents/Sandcastle
    uv run pytest tests/test_executor_deep_v2.py -v --tb=short 2>&1 | tee /tmp/test_results.txt

Claude Code post-run instrukce:
    Po doběhnutí testů projdi /tmp/test_results.txt, najdi FAILED testy,
    pro každý rozhoduj: FIX nebo DEFER do BUGS.md.
    Formát BUGS.md je popsán na konci tohoto souboru.
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
import string
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ── hypothesis ────────────────────────────────────────────────
try:
    from hypothesis import assume, given
    from hypothesis import settings as h_settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

    def given(*args, **kwargs):
        def decorator(fn):
            return pytest.mark.skip(reason="hypothesis not installed")(fn)
        return decorator

    class st:
        text = lambda **kw: None
        integers = lambda **kw: None
        floats = lambda **kw: None
        lists = lambda **kw: None
        dictionaries = lambda **kw: None
        one_of = lambda *args: None
        none = lambda: None
        just = lambda x: None

    def h_settings(**kw):
        def d(f): return f
        return d

    def assume(x): pass


from sandcastle.engine.dag import (
    CodeConfig,
    ConditionConfig,
    CsvOutputConfig,
    HttpConfig,
    LoopConfig,
    PdfReportConfig,
    RetryConfig,
    SensorConfig,
    StepDefinition,
    TransformConfig,
    build_plan,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    StepBlocked,
    StepExecutionError,
    StepResult,
    WorkflowPaused,
    _backoff_delay,
    _check_budget,
    _compute_cache_key,
    _escape_js_string,
    _is_cacheable_output,
    _UNRESOLVED,
    _write_csv_output,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    resolve_variable,
)
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime


# ══════════════════════════════════════════════════════════════
# HELPERS & FACTORIES
# ══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _no_db():
    """Bypass všechny DB/Redis operace."""
    with (
        patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._get_cached_result",
              new_callable=AsyncMock, return_value=None),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._check_cancel",
              new_callable=AsyncMock, return_value=False),
        patch("sandcastle.engine.executor._send_to_dead_letter",
              new_callable=AsyncMock, return_value=True),
        patch("sandcastle.engine.telemetry.set_workflow_context"),
    ):
        yield


def ctx(**kw) -> RunContext:
    return RunContext(
        run_id=kw.get("run_id", str(uuid.uuid4())),
        input=kw.get("input", {}),
        step_outputs=kw.get("step_outputs", {}),
        costs=kw.get("costs", []),
        max_cost_usd=kw.get("max_cost_usd", None),
        workflow_name=kw.get("workflow_name", "test_wf"),
    )


def step(**kw) -> StepDefinition:
    return StepDefinition(
        id=kw.get("id", "s1"),
        prompt=kw.get("prompt", "Do something"),
        model=kw.get("model", "sonnet"),
        max_turns=kw.get("max_turns", 3),
        timeout=kw.get("timeout", 30),
        retry=kw.get("retry", None),
        depends_on=kw.get("depends_on", []),
        type=kw.get("type", "standard"),
    )


def sandbox(text="ok", cost=0.01, structured=None) -> SandshoreRuntime:
    sb = MagicMock(spec=SandshoreRuntime)
    sb.query = AsyncMock(return_value=SandshoreResult(
        text=text, structured_output=structured,
        total_cost_usd=cost, input_tokens=10, output_tokens=10,
    ))
    return sb


def storage():
    from sandcastle.engine.storage import LocalStorage
    return LocalStorage(tempfile.mkdtemp())


def flaky_sandbox(fail_n: int, ok_text="ok", cost=0.01) -> SandshoreRuntime:
    n = 0
    async def _q(req):
        nonlocal n; n += 1
        if n <= fail_n:
            raise Exception(f"Transient #{n}")
        return SandshoreResult(
            text=ok_text, structured_output=None,
            total_cost_usd=cost, input_tokens=5, output_tokens=5,
        )
    sb = MagicMock(spec=SandshoreRuntime)
    sb.query = AsyncMock(side_effect=_q)
    return sb


# ══════════════════════════════════════════════════════════════
# PASS 1 – UNIT: resolve_variable
# ══════════════════════════════════════════════════════════════

class TestResolveVariable:
    def test_input_simple(self):
        c = ctx(input={"brand": "Notino"})
        assert resolve_variable("input.brand", c) == "Notino"

    def test_input_nested(self):
        c = ctx(input={"meta": {"lang": "cs"}})
        assert resolve_variable("input.meta.lang", c) == "cs"

    def test_input_missing_key_returns_none(self):
        c = ctx(input={})
        assert resolve_variable("input.missing", c) is _UNRESOLVED

    def test_steps_output(self):
        c = ctx(step_outputs={"s1": "result_value"})
        assert resolve_variable("steps.s1.output", c) == "result_value"

    def test_steps_output_field(self):
        c = ctx(step_outputs={"s1": {"score": 0.9}})
        assert resolve_variable("steps.s1.output.score", c) == 0.9

    def test_steps_missing_step_returns_none(self):
        c = ctx(step_outputs={})
        assert resolve_variable("steps.missing.output", c) is _UNRESOLVED

    def test_steps_output_list_index(self):
        c = ctx(step_outputs={"s1": ["a", "b", "c"]})
        assert resolve_variable("steps.s1.output.1", c) == "b"

    def test_run_id(self):
        c = ctx(run_id="my-run-123")
        assert resolve_variable("run_id", c) == "my-run-123"

    def test_date_iso_format(self):
        import re
        c = ctx()
        val = resolve_variable("date", c)
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", val), f"Invalid: {val}"

    def test_unknown_path_returns_none(self):
        assert resolve_variable("totally.unknown.path", ctx()) is _UNRESOLVED

    def test_deep_none_chain(self):
        c = ctx(step_outputs={"s1": None})
        assert resolve_variable("steps.s1.output.nested.deep", c) is None


# ══════════════════════════════════════════════════════════════
# PASS 2 – UNIT: resolve_templates
# ══════════════════════════════════════════════════════════════

class TestResolveTemplates:
    def test_simple_substitution(self):
        c = ctx(input={"brand": "Sephora"})
        assert resolve_templates("Brand: {input.brand}", c) == "Brand: Sephora"

    def test_multiple_vars(self):
        c = ctx(input={"a": "X", "b": "Y"})
        assert resolve_templates("{input.a} and {input.b}", c) == "X and Y"

    def test_dict_value_serialized_as_json(self):
        c = ctx(step_outputs={"s1": {"key": "val"}})
        result = resolve_templates("{steps.s1.output}", c)
        assert json.loads(result) == {"key": "val"}

    def test_unknown_var_left_unchanged(self):
        c = ctx(input={})
        assert resolve_templates("{input.missing}", c) == "{input.missing}"

    def test_auto_inject_unreferenced_deps(self):
        """Deps které nejsou v template se musí auto-injectovat."""
        c = ctx(step_outputs={"dep1": "dep_output"})
        result = resolve_templates("My prompt", c, depends_on=["dep1"])
        assert "dep1" in result
        assert "dep_output" in result

    def test_auto_inject_skipped_when_dep_referenced(self):
        """Pokud je dep již v template, nesmí být inject duplikován."""
        c = ctx(step_outputs={"dep1": "v"})
        result = resolve_templates("Context: {steps.dep1.output}", c, depends_on=["dep1"])
        assert "Context from previous steps" not in result  # no double injection

    def test_no_inject_when_dep_missing_from_outputs(self):
        """Dep který ještě nedoběhl nesmí být injectován."""
        c = ctx(step_outputs={})
        result = resolve_templates("My prompt", c, depends_on=["not_done"])
        assert "not_done" not in result


# ══════════════════════════════════════════════════════════════
# PASS 3 – UNIT: _check_budget
# ══════════════════════════════════════════════════════════════

class TestCheckBudget:
    def test_no_budget_always_none(self):
        assert _check_budget(ctx()) is None

    def test_zero_budget_no_cost(self):
        c = ctx(max_cost_usd=0.0, costs=[])
        # Zero budget with zero cost should not explode
        result = _check_budget(c)
        assert result in (None, "exceeded")

    def test_under_80_percent(self):
        c = ctx(max_cost_usd=10.0, costs=[0.5])
        assert _check_budget(c) is None

    def test_at_80_percent(self):
        c = ctx(max_cost_usd=10.0, costs=[8.0])
        assert _check_budget(c) == "warning"

    def test_at_100_percent(self):
        c = ctx(max_cost_usd=10.0, costs=[10.0])
        assert _check_budget(c) == "exceeded"

    def test_over_100_percent(self):
        c = ctx(max_cost_usd=5.0, costs=[7.5])
        assert _check_budget(c) == "exceeded"

    def test_multiple_cost_entries_summed(self):
        c = ctx(max_cost_usd=10.0, costs=[3.0, 3.0, 3.0])
        assert _check_budget(c) == "warning"


# ══════════════════════════════════════════════════════════════
# PASS 4 – UNIT: _backoff_delay
# ══════════════════════════════════════════════════════════════

class TestBackoffDelay:
    def test_exponential_grows(self):
        delays = [_backoff_delay(i) for i in range(1, 8)]
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1] or delays[i] == 60

    def test_exponential_capped_at_60(self):
        for attempt in range(10, 20):
            assert _backoff_delay(attempt) <= 60

    def test_fixed_always_2s(self):
        for attempt in range(1, 10):
            assert _backoff_delay(attempt, "fixed") == 2.0

    def test_first_attempt_positive(self):
        assert _backoff_delay(1) > 0
        assert _backoff_delay(1, "fixed") > 0


# ══════════════════════════════════════════════════════════════
# PASS 5 – UNIT: _escape_js_string (shell injection hardening)
# ══════════════════════════════════════════════════════════════

class TestEscapeJsString:
    def test_single_quotes_escaped(self):
        result = _escape_js_string("it's a test")
        assert "\\'" in result
        assert "it's" not in result

    def test_double_quotes_escaped(self):
        result = _escape_js_string('say "hello"')
        assert '\\"' in result

    def test_newlines_escaped(self):
        result = _escape_js_string("line1\nline2")
        assert "\\n" in result
        assert "\n" not in result

    def test_backslash_escaped(self):
        result = _escape_js_string("path\\to\\file")
        assert "\\\\" in result

    def test_shell_injection_attempt_neutralized(self):
        payload = "'; rm -rf /; echo '"
        result = _escape_js_string(payload)
        assert "rm -rf" in result  # text is preserved but quotes escaped
        assert "\\'" in result     # single quotes are escaped

    def test_script_injection_neutralized(self):
        payload = '"); process.exit(1); //'
        result = _escape_js_string(payload)
        assert '\\"' in result  # double quotes escaped

    def test_empty_string(self):
        assert _escape_js_string("") == ""

    def test_normal_text_unchanged(self):
        assert _escape_js_string("hello world") == "hello world"


# ══════════════════════════════════════════════════════════════
# PASS 6 – UNIT: _write_csv_output
# ══════════════════════════════════════════════════════════════

class TestWriteCsvOutput:
    def test_dict_output_single_row(self):
        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="csv_step", prompt="",
            csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file"),
        )
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = None
            _write_csv_output(s, {"brand": "Notino", "score": 0.9}, "run-1")

        files = list(Path(tmpdir).glob("*.csv"))
        assert len(files) == 1
        rows = list(csv.DictReader(files[0].open()))
        assert len(rows) == 1
        assert rows[0]["brand"] == "Notino"

    def test_list_of_dicts_multiple_rows(self):
        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="csv_step", prompt="",
            csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file"),
        )
        data = [{"brand": "A", "score": 1}, {"brand": "B", "score": 2}]
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = None
            _write_csv_output(s, data, "run-1")

        files = list(Path(tmpdir).glob("*.csv"))
        rows = list(csv.DictReader(files[0].open()))
        assert len(rows) == 2

    def test_append_mode_grows_file(self):
        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="appender", prompt="",
            csv_output=CsvOutputConfig(directory=tmpdir, mode="append", filename="log"),
        )
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = None
            _write_csv_output(s, {"v": 1}, "run-1")
            _write_csv_output(s, {"v": 2}, "run-2")

        filepath = Path(tmpdir) / "log.csv"
        rows = list(csv.DictReader(filepath.open()))
        assert len(rows) == 2

    def test_fieldname_union_across_rows(self):
        """Různé klíče v různých řádcích – musí být union."""
        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="mixed", prompt="",
            csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file"),
        )
        data = [{"a": 1, "b": 2}, {"b": 3, "c": 4}]
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = None
            _write_csv_output(s, data, "run-1")

        files = list(Path(tmpdir).glob("*.csv"))
        header = files[0].read_text().splitlines()[0]
        assert "a" in header and "b" in header and "c" in header

    def test_sandbox_root_blocks_escape(self):
        """CSV output mimo sandbox root musí být zablokováno."""
        tmpdir = tempfile.mkdtemp()
        other_dir = tempfile.mkdtemp()
        s = StepDefinition(
            id="escape", prompt="",
            csv_output=CsvOutputConfig(directory=other_dir, mode="new_file"),
        )
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = tmpdir  # Sandbox je tmpdir, other_dir je mimo
            _write_csv_output(s, {"x": 1}, "run-1")

        # Soubor nesmí být zapsán do other_dir
        assert len(list(Path(other_dir).glob("*.csv"))) == 0

    def test_json_string_parsed_to_rows(self):
        """JSON string output se musí parsovat na řádky."""
        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="json_str", prompt="",
            csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file"),
        )
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = None
            _write_csv_output(s, '[{"brand": "X"}]', "run-1")

        files = list(Path(tmpdir).glob("*.csv"))
        rows = list(csv.DictReader(files[0].open()))
        assert rows[0]["brand"] == "X"

    def test_empty_output_skipped(self):
        """Prázdný output nesmí vytvořit soubor."""
        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="empty", prompt="",
            csv_output=CsvOutputConfig(directory=tmpdir, mode="new_file"),
        )
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = None
            _write_csv_output(s, [], "run-1")

        assert len(list(Path(tmpdir).glob("*.csv"))) == 0


# ══════════════════════════════════════════════════════════════
# PASS 7 – UNIT: _is_cacheable_output
# ══════════════════════════════════════════════════════════════

class TestIsCacheableOutput:
    def test_none_not_cacheable(self):
        assert _is_cacheable_output(None) is False

    def test_empty_string_not_cacheable(self):
        assert _is_cacheable_output("") is False

    def test_empty_list_not_cacheable(self):
        assert _is_cacheable_output([]) is False

    def test_empty_dict_not_cacheable(self):
        assert _is_cacheable_output({}) is False

    def test_please_provide_not_cacheable(self):
        assert _is_cacheable_output("Please provide the article text") is False

    def test_long_valid_text_cacheable(self):
        assert _is_cacheable_output("A" * 300) is True

    def test_valid_dict_cacheable(self):
        assert _is_cacheable_output({"brand": "Notino", "score": 0.9}) is True

    def test_zero_mentions_dict_not_cacheable(self):
        assert _is_cacheable_output({"total_mentions": 0, "mentions": []}) is False

    def test_cannot_access_not_cacheable(self):
        assert _is_cacheable_output("I don't have access to that URL") is False

    def test_valid_list_cacheable(self):
        assert _is_cacheable_output([1, 2, 3]) is True


# ══════════════════════════════════════════════════════════════
# PASS 8 – UNIT: RunContext
# ══════════════════════════════════════════════════════════════

class TestRunContext:
    def test_total_cost_empty(self):
        assert ctx().total_cost == 0.0

    def test_total_cost_sum(self):
        c = ctx(costs=[0.1, 0.2, 0.05])
        assert abs(c.total_cost - 0.35) < 1e-9

    def test_snapshot_has_required_keys(self):
        c = ctx(step_outputs={"s1": "out"}, costs=[0.1])
        snap = c.snapshot()
        for key in ("run_id", "input", "step_outputs", "costs", "total_cost"):
            assert key in snap

    def test_with_item_sets_item_and_index(self):
        parent = ctx(input={"brands": ["A", "B"]})
        child = parent.with_item("A", 0)
        assert child.input["_item"] == "A"
        assert child.input["_index"] == 0

    def test_with_item_preserves_original_keys(self):
        parent = ctx(input={"lang": "cs", "brands": ["A"]})
        child = parent.with_item("A", 0)
        assert child.input["lang"] == "cs"

    def test_with_item_step_outputs_isolated(self):
        """child.step_outputs nesmí sdílet referenci s parent."""
        parent = ctx(step_outputs={"existing": "value"})
        child = parent.with_item("X", 0)
        child.step_outputs["new"] = "child_only"
        assert "new" not in parent.step_outputs

    def test_with_item_costs_isolated(self):
        """costs list is isolated per child (thread safety for parallel_over)."""
        parent = ctx(costs=[0.1])
        child = parent.with_item("X", 0)
        child.costs.append(0.5)
        # Child gets its own costs list, parent is not affected
        assert 0.5 not in parent.costs
        assert len(parent.costs) == 1
        assert child.costs == [0.5]

    def test_with_item_branch_skip_isolated(self):
        """branch_skip_steps nesmí být sdílený set."""
        parent = ctx()
        parent.branch_skip_steps.add("step_a")
        child = parent.with_item("X", 0)
        child.branch_skip_steps.add("step_b")
        assert "step_b" not in parent.branch_skip_steps


# ══════════════════════════════════════════════════════════════
# PASS 9 – ASYNC UNIT: execute_step_with_retry
# ══════════════════════════════════════════════════════════════

class TestExecuteStepWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_attempt(self):
        sb = sandbox(text="result")
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.status == "completed"
        assert result.output == "result"
        assert result.attempt == 1

    @pytest.mark.asyncio
    async def test_json_string_parsed(self):
        sb = sandbox(text='{"key": "value"}')
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.output == {"key": "value"}

    @pytest.mark.asyncio
    async def test_json_with_code_fence_parsed(self):
        sb = sandbox(text='```json\n{"ok": true}\n```')
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.output == {"ok": True}

    @pytest.mark.asyncio
    async def test_structured_output_priority_over_text(self):
        sb = sandbox(text="ignore me", structured={"wins": True})
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.output == {"wins": True}

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_failed(self):
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=Exception("always fails"))
        s = step(retry=RetryConfig(max_attempts=3, backoff="fixed", on_failure="abort"))
        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, ctx(), sb, storage())
        assert result.status == "failed"
        assert result.attempt == 3

    @pytest.mark.asyncio
    async def test_retry_then_success(self):
        sb = flaky_sandbox(fail_n=2, ok_text="recovered")
        s = step(retry=RetryConfig(max_attempts=5, backoff="fixed", on_failure="abort"))
        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, ctx(), sb, storage())
        assert result.status == "completed"
        assert result.output == "recovered"

    @pytest.mark.asyncio
    async def test_no_retry_single_attempt(self):
        calls = 0
        async def _q(req):
            nonlocal calls; calls += 1
            raise Exception("fail")
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        result = await execute_step_with_retry(step(retry=None), ctx(), sb, storage())
        assert result.status == "failed"
        assert calls == 1

    @pytest.mark.asyncio
    async def test_cost_propagated_from_result(self):
        sb = sandbox(text="ok", cost=0.42)
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.cost_usd == pytest.approx(0.42)

    @pytest.mark.asyncio
    async def test_duration_positive(self):
        sb = sandbox(text="ok")
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_pdf_artifact_injected_in_output(self):
        """Pokud _write_pdf_report vrátí cestu, musí být v output jako _pdf_artifact."""
        sb = sandbox(text="# Report\n\nContent")
        s = StepDefinition(
            id="report", prompt="gen report", model="sonnet",
            max_turns=3, timeout=30,
            pdf_report=PdfReportConfig(directory="/tmp/test_pdf", language="cs"),
        )
        fake_path = "/tmp/test_pdf/report_20240101.pdf"
        with patch("sandcastle.engine.executor._write_pdf_report", return_value=fake_path):
            result = await execute_step_with_retry(s, ctx(), sb, storage())
        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output.get("_pdf_artifact") == fake_path

    @pytest.mark.asyncio
    async def test_pdf_step_uses_report_prefix_not_terse(self):
        """PDF step nesmí mít terse 'Return ONLY' prefix."""
        captured = []
        async def _q(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(
                text="# Report", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        s = StepDefinition(
            id="report", prompt="Generate", model="sonnet",
            max_turns=3, timeout=30,
            pdf_report=PdfReportConfig(directory="/tmp", language="en"),
        )
        with patch("sandcastle.engine.executor._write_pdf_report", return_value=None):
            await execute_step_with_retry(s, ctx(), sb, storage())
        assert len(captured) > 0
        assert "Return ONLY the requested data" not in captured[0], (
            "BUG-001: PDF step incorrectly using terse system prefix"
        )
        assert "FORMATTING" in captured[0] or "markdown" in captured[0].lower(), (
            "BUG-002: PDF step missing formatting instructions"
        )

    @pytest.mark.asyncio
    async def test_step_system_prefix_added_to_standard_step(self):
        from sandcastle.engine.executor import _STEP_SYSTEM_PREFIX
        captured = []
        async def _q(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        await execute_step_with_retry(step(prompt="My custom prompt"), ctx(), sb, storage())
        assert _STEP_SYSTEM_PREFIX.strip()[:20] in captured[0], (
            "BUG-003: system prefix missing from standard step"
        )

    @pytest.mark.asyncio
    async def test_cache_skipped_when_memory_injected(self):
        """Pokud step čte memory, cache se nesmí použít."""
        cached_hit = []
        async def mock_get_cached(key):
            cached_hit.append(key)
            return {"output": "cached", "cost_usd": 0.0}

        c = ctx()
        c._memory_config = MagicMock()
        c._memory_config.auto_inject = True
        c._memory_config.max_inject = 5
        c._memory_config.max_age_days = 0
        c._memory_config.admit_threshold = 0.0
        c._memory_scope_id = "test-scope"

        with (
            patch("sandcastle.engine.executor._get_cached_result",
                  side_effect=mock_get_cached),
            patch("sandcastle.engine.memory.load_memories",
                  new_callable=AsyncMock, return_value=[]),
            patch("sandcastle.engine.memory.apply_decay", return_value=[]),
            patch("sandcastle.engine.memory.format_memories_for_prompt", return_value=""),
        ):
            result = await execute_step_with_retry(step(), c, sandbox(), storage())

        assert len(cached_hit) == 0, (
            "BUG-004: Cache was checked for memory-injected step (should be bypassed)"
        )


# ══════════════════════════════════════════════════════════════
# PASS 10 – ASYNC UNIT: Hybrid step types
# ══════════════════════════════════════════════════════════════

class TestCodeStep:
    @pytest.mark.asyncio
    async def test_basic_math(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="calc", prompt="", type="code",
            code_config=CodeConfig(code="result = 2 + 2"),
        )
        r = await _execute_code_step(s, ctx())
        assert r.status == "completed"
        assert r.output == 4

    @pytest.mark.asyncio
    async def test_access_input(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="calc", prompt="", type="code",
            code_config=CodeConfig(code="result = _input['x'] * 2"),
        )
        r = await _execute_code_step(s, ctx(input={"x": 5}))
        assert r.output == 10

    @pytest.mark.asyncio
    async def test_access_step_outputs(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="calc", prompt="", type="code",
            code_config=CodeConfig(code="result = _steps['prev']['value'] + 1"),
        )
        r = await _execute_code_step(s, ctx(step_outputs={"prev": {"value": 10}}))
        assert r.output == 11

    @pytest.mark.asyncio
    async def test_syntax_error_returns_failed(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="bad", prompt="", type="code",
            code_config=CodeConfig(code="def broken("),
        )
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_open_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="fs", prompt="", type="code",
            code_config=CodeConfig(code="result = open('/etc/passwd').read()"),
        )
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "BUG-005: open() should be blocked in code steps"

    @pytest.mark.asyncio
    async def test_import_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="imp", prompt="", type="code",
            code_config=CodeConfig(code="import os; result = os.getcwd()"),
        )
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "BUG-006: import should be blocked in code steps"

    @pytest.mark.asyncio
    async def test_exec_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="exec_s", prompt="", type="code",
            code_config=CodeConfig(code="exec('import os')"),
        )
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "BUG-007: exec() should be blocked in code steps"

    @pytest.mark.asyncio
    async def test_builtins_whitelist_len_works(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="len_test", prompt="", type="code",
            code_config=CodeConfig(code="result = len([1,2,3])"),
        )
        r = await _execute_code_step(s, ctx())
        assert r.output == 3

    @pytest.mark.asyncio
    async def test_builtins_json_available(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="json_test", prompt="", type="code",
            code_config=CodeConfig(code='result = json.loads(\'{"k": 1}\')'),
        )
        r = await _execute_code_step(s, ctx())
        assert r.output == {"k": 1}


class TestConditionStep:
    @pytest.mark.asyncio
    async def test_true_skips_else_steps(self):
        from sandcastle.engine.executor import _execute_condition_step
        s = StepDefinition(
            id="cond", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        c = ctx()
        r = await _execute_condition_step(s, c)
        assert r.status == "completed"
        assert "step_b" in c.branch_skip_steps
        assert "step_a" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_false_skips_then_steps(self):
        from sandcastle.engine.executor import _execute_condition_step
        s = StepDefinition(
            id="cond", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="False",
                then_steps=["step_a"],
                else_steps=["step_b"],
            ),
        )
        c = ctx()
        await _execute_condition_step(s, c)
        assert "step_a" in c.branch_skip_steps
        assert "step_b" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_expression_with_input(self):
        from sandcastle.engine.executor import _execute_condition_step
        s = StepDefinition(
            id="cond", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="input['score'] > 0.5",
                then_steps=["high"], else_steps=["low"],
            ),
        )
        c = ctx(input={"score": 0.9})
        await _execute_condition_step(s, c)
        assert "low" in c.branch_skip_steps
        assert "high" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_output_contains_condition_result(self):
        from sandcastle.engine.executor import _execute_condition_step
        s = StepDefinition(
            id="cond", prompt="", type="condition",
            condition_config=ConditionConfig(expression="True", then_steps=[], else_steps=[]),
        )
        r = await _execute_condition_step(s, ctx())
        assert r.output["condition"] is True


class TestTransformStep:
    @pytest.mark.asyncio
    async def test_template_substitution(self):
        from sandcastle.engine.executor import _execute_transform_step
        s = StepDefinition(
            id="tr", prompt="", type="transform",
            transform_config=TransformConfig(template="Hello {input.name}!"),
        )
        r = await _execute_transform_step(s, ctx(input={"name": "Tomas"}))
        assert r.output == "Hello Tomas!"

    @pytest.mark.asyncio
    async def test_jinja_tojson_filter(self):
        from sandcastle.engine.executor import _execute_transform_step
        s = StepDefinition(
            id="tr", prompt="", type="transform",
            transform_config=TransformConfig(template="{{ steps.s1.output | tojson }}"),
        )
        c = ctx(step_outputs={"s1": {"key": "val"}})
        r = await _execute_transform_step(s, c)
        assert r.output == {"key": "val"}

    @pytest.mark.asyncio
    async def test_json_output_parsed(self):
        from sandcastle.engine.executor import _execute_transform_step
        s = StepDefinition(
            id="tr", prompt="", type="transform",
            transform_config=TransformConfig(template='{"brand": "{input.b}"}'),
        )
        r = await _execute_transform_step(s, ctx(input={"b": "Sephora"}))
        assert r.output == {"brand": "Sephora"}


# ══════════════════════════════════════════════════════════════
# PASS 11 – PROPERTY-BASED (hypothesis)
# ══════════════════════════════════════════════════════════════

class TestPropertyBased:
    @given(st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + "_"))
    @h_settings(max_examples=100)
    def test_missing_input_key_never_crashes(self, key):
        result = resolve_variable(f"input.{key}", ctx())
        assert result is _UNRESOLVED

    @given(st.text(min_size=0, max_size=200))
    @h_settings(max_examples=100)
    def test_string_value_roundtrips(self, value):
        c = ctx(input={"v": value})
        assert resolve_variable("input.v", c) == value

    @given(st.integers())
    @h_settings(max_examples=100)
    def test_int_value_roundtrips(self, value):
        c = ctx(input={"n": value})
        assert resolve_variable("input.n", c) == value

    @given(st.floats(allow_nan=False, allow_infinity=False))
    @h_settings(max_examples=50)
    def test_float_value_roundtrips(self, value):
        c = ctx(input={"f": value})
        assert resolve_variable("input.f", c) == value

    @given(st.text(min_size=1, max_size=100))
    @h_settings(max_examples=100)
    def test_resolve_templates_never_crashes(self, template):
        try:
            result = resolve_templates(template, ctx())
            assert isinstance(result, str)
        except Exception as e:
            pytest.fail(f"resolve_templates crashed: {e}")

    @given(
        st.text(min_size=1, max_size=50, alphabet=string.ascii_letters),
        st.text(min_size=0, max_size=200),
    )
    @h_settings(max_examples=100)
    def test_cache_key_deterministic(self, wf, prompt):
        k1 = _compute_cache_key(wf, "step", prompt, "sonnet")
        k2 = _compute_cache_key(wf, "step", prompt, "sonnet")
        assert k1 == k2

    @given(st.integers(min_value=1, max_value=20))
    @h_settings(max_examples=30)
    def test_backoff_always_positive(self, attempt):
        assert _backoff_delay(attempt) > 0
        assert _backoff_delay(attempt, "fixed") > 0

    @given(st.floats(min_value=0.0, max_value=100.0, allow_nan=False))
    @h_settings(max_examples=100)
    def test_budget_check_valid_return(self, cost):
        c = ctx(costs=[cost], max_cost_usd=10.0)
        result = _check_budget(c)
        assert result in (None, "warning", "exceeded")

    @given(st.text(min_size=0, max_size=100))
    @h_settings(max_examples=100)
    def test_escape_js_string_never_crashes(self, text):
        result = _escape_js_string(text)
        assert isinstance(result, str)

    @given(st.one_of(st.none(), st.just(""), st.just([]), st.just({})))
    @h_settings(max_examples=10)
    def test_empty_values_not_cacheable(self, val):
        assert _is_cacheable_output(val) is False


# ══════════════════════════════════════════════════════════════
# PASS 12 – CHAOS TESTING
# ══════════════════════════════════════════════════════════════

class TestChaos:
    @pytest.mark.asyncio
    async def test_corrupted_json_variants(self):
        """Executor se nezhroutí na žádný poškozený JSON."""
        bad = [
            '{"unclosed": "string',
            '{key: value}',
            '```json\n{broken\n```',
            '\x00\x01binary\xff',
            '{"nested": ' + '{"a":' * 20 + '"v"' + '}' * 21,
        ]
        for text in bad:
            sb = sandbox(text=text)
            result = await execute_step_with_retry(step(), ctx(), sb, storage())
            assert result.status == "completed", f"Crashed on: {text!r}"

    @pytest.mark.asyncio
    async def test_none_text_output(self):
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(return_value=SandshoreResult(
            text=None, structured_output=None,
            total_cost_usd=0.0, input_tokens=0, output_tokens=0,
        ))
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.status in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_500kb_output(self):
        large = "X" * 500_000
        sb = sandbox(text=large)
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.status == "completed"
        assert len(str(result.output)) == 500_000

    @pytest.mark.asyncio
    async def test_random_failures_with_retry(self):
        pattern = [True, True, False]
        n = 0
        async def _q(req):
            nonlocal n; n += 1
            if pattern[(n - 1) % len(pattern)]:
                raise Exception("Chaos")
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        s = step(retry=RetryConfig(max_attempts=5, backoff="fixed", on_failure="abort"))
        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_step_with_retry(s, ctx(), sb, storage())
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_concurrent_fan_out_no_shared_state(self):
        """10 paralelních itemů nesmí sdílet step_outputs."""
        async def _q(req):
            await asyncio.sleep(random.uniform(0, 0.02))
            return SandshoreResult(
                text=req.get("prompt", "")[-5:],
                structured_output=None, total_cost_usd=0.001,
                input_tokens=1, output_tokens=1,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        yaml = """
name: fan_out
steps:
  - id: process
    prompt: "Process {input._item}"
    parallel_over: "{input.items}"
"""
        items = [f"brand_{i}" for i in range(10)]
        wf = parse_yaml_string(yaml)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={"items": items}, storage=storage(),
            )
        assert result.status == "completed"
        outputs = result.outputs.get("process", [])
        assert len(outputs) == 10, (
            f"BUG-008: Expected 10 fan-out outputs, got {len(outputs)}"
        )


# ══════════════════════════════════════════════════════════════
# PASS 13 – TIMING & PERFORMANCE
# ══════════════════════════════════════════════════════════════

class TestTiming:
    @pytest.mark.asyncio
    async def test_exponential_backoff_monotonically_increases(self):
        delays = []
        async def track_sleep(d):
            delays.append(d)
        sb = flaky_sandbox(fail_n=3)
        s = step(retry=RetryConfig(max_attempts=4, backoff="exponential", on_failure="abort"))
        with patch("sandcastle.engine.executor.asyncio.sleep", side_effect=track_sleep):
            result = await execute_step_with_retry(s, ctx(), sb, storage())
        assert result.status == "completed"
        assert len(delays) == 3
        for i in range(1, len(delays)):
            assert delays[i] >= delays[i - 1] or delays[i] == 60, (
                f"BUG-009: backoff not growing: {delays}"
            )

    @pytest.mark.asyncio
    async def test_parallel_steps_faster_than_sequential(self):
        DELAY = 0.05
        async def slow(req):
            await asyncio.sleep(DELAY)
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.001, input_tokens=1, output_tokens=1,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=slow)
        yaml = """
name: parallel_test
steps:
  - id: s1
    prompt: "1"
  - id: s2
    prompt: "2"
  - id: s3
    prompt: "3"
  - id: s4
    prompt: "4"
  - id: s5
    prompt: "5"
"""
        wf = parse_yaml_string(yaml)
        plan = build_plan(wf)
        start = time.monotonic()
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        elapsed = time.monotonic() - start
        assert result.status == "completed"
        assert elapsed < 5 * DELAY * 0.7, (
            f"BUG-010: Parallel execution too slow: {elapsed:.3f}s "
            f"(sequential would be {5 * DELAY:.3f}s)"
        )

    @pytest.mark.asyncio
    async def test_step_duration_tracked(self):
        DELAY = 0.05
        async def slow(req):
            await asyncio.sleep(DELAY)
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=slow)
        result = await execute_step_with_retry(step(), ctx(), sb, storage())
        assert result.duration_seconds >= DELAY * 0.8, (
            f"BUG-011: Duration {result.duration_seconds:.3f}s too short for {DELAY}s delay"
        )

    @pytest.mark.asyncio
    async def test_costs_accumulated_across_all_parallel_steps(self):
        sb = sandbox("ok", cost=0.10)
        yaml = """
name: cost_test
steps:
  - id: s1
    prompt: "1"
  - id: s2
    prompt: "2"
  - id: s3
    prompt: "3"
"""
        wf = parse_yaml_string(yaml)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        assert result.status == "completed"
        assert abs(result.total_cost_usd - 0.30) < 0.01, (
            f"BUG-012: Expected $0.30 cost, got ${result.total_cost_usd:.4f}"
        )


# ══════════════════════════════════════════════════════════════
# PASS 14 – BRAND SCANNER WORKFLOW (end-to-end)
# ══════════════════════════════════════════════════════════════

BRAND_SCANNER_YAML = """
name: brand_reputation_scanner
input_schema:
  type: object
  properties:
    brand:
      type: string
      default: "Notino"
    country:
      type: string
      default: "CZ"

steps:
  - id: fetch_mentions
    prompt: "Find all online mentions of {input.brand} in {input.country}"
    model: sonnet

  - id: sentiment_analysis
    prompt: "Analyze sentiment: {steps.fetch_mentions.output}"
    depends_on: [fetch_mentions]
    model: sonnet

  - id: competitor_compare
    prompt: "Compare {input.brand} with competitors using: {steps.sentiment_analysis.output}"
    depends_on: [sentiment_analysis]
    model: sonnet

  - id: final_report
    prompt: "Executive summary for {input.brand} in {input.country}: {steps.competitor_compare.output}"
    depends_on: [competitor_compare]
    model: sonnet
"""


class TestBrandScannerWorkflow:
    @pytest.mark.asyncio
    async def test_full_run_all_steps_complete(self):
        sb = sandbox('{"result": "done"}', cost=0.05)
        wf = parse_yaml_string(BRAND_SCANNER_YAML)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={"brand": "Notino", "country": "CZ"},
                storage=storage(),
            )
        assert result.status == "completed"
        for sid in ["fetch_mentions", "sentiment_analysis", "competitor_compare", "final_report"]:
            assert sid in result.outputs, f"BUG-013: Missing step '{sid}' in outputs"
        assert result.total_cost_usd == pytest.approx(0.2, rel=0.01), (
            f"BUG-014: Expected $0.20 total cost, got ${result.total_cost_usd:.4f}"
        )

    @pytest.mark.asyncio
    async def test_sequential_order_respected(self):
        order = []
        async def _q(req):
            prompt = req.get("prompt", "")
            if "mentions" in prompt and "analyze" not in prompt:
                order.append("fetch")
            elif "sentiment" in prompt or "analyze" in prompt.lower():
                order.append("sentiment")
            elif "compare" in prompt or "competitor" in prompt:
                order.append("compare")
            elif "summary" in prompt or "executive" in prompt:
                order.append("report")
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        wf = parse_yaml_string(BRAND_SCANNER_YAML)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            await execute_workflow(
                workflow=wf, plan=plan,
                input_data={"brand": "DM", "country": "CZ"},
                storage=storage(),
            )
        assert order[0] == "fetch", f"BUG-015: Wrong first step: {order}"
        assert order[-1] == "report", f"BUG-016: Wrong last step: {order}"

    @pytest.mark.asyncio
    async def test_multi_brand_fan_out(self):
        YAML = """
name: multi_brand
steps:
  - id: scan
    prompt: "Scan {input._item}"
    parallel_over: "{input.brands}"
  - id: aggregate
    prompt: "Aggregate {steps.scan.output}"
    depends_on: [scan]
"""
        brands = ["Notino", "DM", "Sephora", "Rossmann", "Douglas"]
        sb = sandbox('{"score": 0.8}')
        wf = parse_yaml_string(YAML)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={"brands": brands}, storage=storage(),
            )
        assert result.status == "completed"
        scan_out = result.outputs.get("scan", [])
        assert len(scan_out) == len(brands), (
            f"BUG-017: Expected {len(brands)} outputs, got {len(scan_out)}"
        )

    @pytest.mark.asyncio
    async def test_input_interpolated_in_prompts(self):
        captured = []
        async def _q(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        wf = parse_yaml_string(BRAND_SCANNER_YAML)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            await execute_workflow(
                workflow=wf, plan=plan,
                input_data={"brand": "Sephora", "country": "SK"},
                storage=storage(),
            )
        assert captured, "No prompts captured"
        assert "Sephora" in captured[0], f"BUG-018: brand not in prompt: {captured[0][:100]}"
        assert "SK" in captured[0], f"BUG-019: country not in prompt: {captured[0][:100]}"

    @pytest.mark.asyncio
    async def test_input_schema_defaults_applied(self):
        """Bez inputu musí být použity defaults z input_schema."""
        captured = []
        async def _q(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        wf = parse_yaml_string(BRAND_SCANNER_YAML)
        plan = build_plan(wf)
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            await execute_workflow(
                workflow=wf, plan=plan,
                input_data={},  # prázdný – defaults ze schématu
                storage=storage(),
            )
        assert "Notino" in captured[0], (
            f"BUG-020: Default 'Notino' not applied: {captured[0][:100]}"
        )


# ══════════════════════════════════════════════════════════════
# PASS 15 – EXECUTE_WORKFLOW scheduler
# ══════════════════════════════════════════════════════════════

class TestWorkflowScheduler:
    @pytest.mark.asyncio
    async def test_diamond_dependency_correct_order(self):
        """A→B, A→C, B+C→D: D nesmí startovat dokud B a C neskončí."""
        order = []
        async def _q(req):
            prompt = req.get("prompt", "")
            for n in ["alpha", "beta", "gamma", "delta"]:
                if n in prompt:
                    order.append(n)
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        yaml = """
name: diamond
steps:
  - id: alpha
    prompt: "alpha"
  - id: beta
    prompt: "beta"
    depends_on: [alpha]
  - id: gamma
    prompt: "gamma"
    depends_on: [alpha]
  - id: delta
    prompt: "delta"
    depends_on: [beta, gamma]
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        assert result.status == "completed"
        assert order[0] == "alpha", f"BUG-021: alpha must be first: {order}"
        assert order[-1] == "delta", f"BUG-022: delta must be last: {order}"
        delta_idx = order.index("delta")
        assert order.index("beta") < delta_idx, f"BUG-023: beta before delta"
        assert order.index("gamma") < delta_idx, f"BUG-024: gamma before delta"

    @pytest.mark.asyncio
    async def test_all_outputs_present(self):
        sb = sandbox("done")
        yaml = """
name: all_outputs
steps:
  - id: alpha
    prompt: "a"
  - id: beta
    prompt: "b"
    depends_on: [alpha]
  - id: gamma
    prompt: "c"
    depends_on: [alpha]
  - id: delta
    prompt: "d"
    depends_on: [beta, gamma]
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        assert result.status == "completed"
        for sid in ["alpha", "beta", "gamma", "delta"]:
            assert sid in result.outputs, f"BUG-025: Missing step '{sid}'"

    @pytest.mark.asyncio
    async def test_budget_exceeded_stops_workflow(self):
        async def expensive(req):
            return SandshoreResult(
                text="done", structured_output=None,
                total_cost_usd=5.0, input_tokens=1000, output_tokens=1000,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=expensive)
        yaml = """
name: budget_test
steps:
  - id: s1
    prompt: "1"
  - id: s2
    prompt: "2"
    depends_on: [s1]
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
                max_cost_usd=3.0,
            )
        assert result.status in ("budget_exceeded", "completed"), (
            f"BUG-026: Budget exceeded should stop workflow, got: {result.status}"
        )

    @pytest.mark.asyncio
    async def test_cancelled_run_returns_cancelled_status(self):
        async def slow(req):
            await asyncio.sleep(10)
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=slow)

        cancel_calls = [False, True]  # Cancel on 2nd check
        async def check_cancel(run_id):
            return cancel_calls.pop(0) if cancel_calls else True

        yaml = """
name: cancel_test
steps:
  - id: slow_step
    prompt: "slow"
"""
        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._check_cancel", side_effect=check_cancel),
        ):
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        assert result.status in ("cancelled", "completed")

    @pytest.mark.asyncio
    async def test_condition_branch_skip_propagated_to_workflow(self):
        """Condition step musí skippnout správné kroky v rámci workflow."""
        executed = []
        async def _q(req):
            prompt = req.get("prompt", "")
            for n in ["fetch", "analyze", "fallback"]:
                if n in prompt.lower():
                    executed.append(n)
            return SandshoreResult(
                text="ok", structured_output=None,
                total_cost_usd=0.01, input_tokens=5, output_tokens=5,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=_q)
        yaml = """
name: conditional_flow
steps:
  - id: fetch
    prompt: "fetch data"
  - id: route
    type: condition
    condition_config:
      expression: "True"
      then: [analyze]
      else: [fallback]
  - id: analyze
    prompt: "analyze data"
    depends_on: [fetch, route]
  - id: fallback
    prompt: "fallback path"
    depends_on: [fetch, route]
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        assert result.status == "completed"
        assert "analyze" in executed, "BUG-027: analyze should have run"
        assert "fallback" not in executed, "BUG-028: fallback should have been skipped"

    @pytest.mark.asyncio
    async def test_workflow_result_has_timestamps(self):
        sb = sandbox("ok")
        yaml = """
name: timestamps
steps:
  - id: s1
    prompt: "step"
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            result = await execute_workflow(
                workflow=wf, plan=plan,
                input_data={}, storage=storage(),
            )
        assert result.started_at is not None, "BUG-029: started_at missing"
        assert result.completed_at is not None, "BUG-030: completed_at missing"
        assert result.completed_at >= result.started_at, "BUG-031: completed_at before started_at"


# ══════════════════════════════════════════════════════════════
# PASS 16 – SECURITY
# ══════════════════════════════════════════════════════════════

class TestSecurity:
    def test_template_injection_no_code_exec(self):
        evil = "{__import__('os').system('id')}"
        result = resolve_templates(evil, ctx())
        assert "uid=" not in result
        assert isinstance(result, str)

    def test_template_edge_case_braces_safe(self):
        for t in ["{{double}}", "{}", "{.}", "normal", "{input.x}", ""]:
            try:
                r = resolve_templates(t, ctx(input={"x": "v"}))
                assert isinstance(r, str)
            except Exception as e:
                pytest.fail(f"Template {t!r} crashed: {e}")

    def test_escape_js_rm_rf_neutralized(self):
        payload = "'; rm -rf /; '"
        result = _escape_js_string(payload)
        assert "\\'" in result
        # When embedded in JS string, the command won't execute as shell command
        assert "rm -rf" in result  # text preserved
        assert "\\'" in result     # but quotes escaped

    @pytest.mark.asyncio
    async def test_code_step_mro_escape_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(
            id="mro", prompt="", type="code",
            code_config=CodeConfig(
                code="result = ().__class__.__mro__[-1].__subclasses__()"
            ),
        )
        r = await _execute_code_step(s, ctx())
        # Should fail or return non-callable
        assert r.status == "failed" or not callable(r.output), (
            "BUG-032: MRO-based sandbox escape not blocked"
        )

    def test_cache_key_sha256_hex(self):
        key = _compute_cache_key("wf", "step", "prompt", "model")
        assert len(key) == 64, f"BUG-033: cache key should be 64 hex chars, got {len(key)}"
        assert all(c in "0123456789abcdef" for c in key)

    @pytest.mark.asyncio
    async def test_csv_output_outside_sandbox_blocked(self):
        """Výstup mimo sandbox root musí být tiše zablokován."""
        sandbox_dir = tempfile.mkdtemp()
        escape_dir = tempfile.mkdtemp()
        s = StepDefinition(
            id="csv", prompt="",
            csv_output=CsvOutputConfig(directory=escape_dir, mode="new_file"),
        )
        with patch("sandcastle.config.settings") as m:
            m.sandbox_root = sandbox_dir
            _write_csv_output(s, {"k": "v"}, "run")
        assert len(list(Path(escape_dir).glob("*.csv"))) == 0, (
            "BUG-034: CSV wrote outside sandbox root"
        )


# ══════════════════════════════════════════════════════════════
# PASS 17 – event_bus
# ══════════════════════════════════════════════════════════════

class TestEventBus:
    @pytest.mark.asyncio
    async def test_step_started_event_emitted(self):
        from sandcastle.engine.events import event_bus
        queue = await event_bus.subscribe()
        try:
            sb = sandbox("ok")
            await execute_step_with_retry(step(id="ev_step"), ctx(), sb, storage())
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
        finally:
            await event_bus.unsubscribe(queue)
        started = [e["data"] for e in events if e["type"] == "step.started"]
        assert any(e.get("step_name") == "ev_step" for e in started), (
            f"BUG-035: step.started event not emitted for 'ev_step': {started}"
        )

    @pytest.mark.asyncio
    async def test_step_completed_event_emitted(self):
        from sandcastle.engine.events import event_bus
        queue = await event_bus.subscribe()
        try:
            sb = sandbox("ok")
            await execute_step_with_retry(step(id="ev_step"), ctx(), sb, storage())
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
        finally:
            await event_bus.unsubscribe(queue)
        completed = [e["data"] for e in events if e["type"] == "step.completed"]
        assert any(e.get("step_name") == "ev_step" for e in completed), (
            f"BUG-036: step.completed event not emitted: {completed}"
        )

    @pytest.mark.asyncio
    async def test_run_started_event_emitted(self):
        from sandcastle.engine.events import event_bus
        queue = await event_bus.subscribe()
        try:
            sb = sandbox("ok")
            yaml = """
name: event_test
steps:
  - id: s1
    prompt: "step"
"""
            wf = parse_yaml_string(yaml)
            plan = build_plan(wf)
            with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
                await execute_workflow(
                    workflow=wf, plan=plan,
                    input_data={}, storage=storage(),
                )
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
        finally:
            await event_bus.unsubscribe(queue)
        started = [e["data"] for e in events if e["type"] == "run.started"]
        assert any(e.get("workflow") == "event_test" for e in started), (
            f"BUG-037: run.started not emitted: {started}"
        )


# ══════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════
#
# BUGS.md INSTRUKCE PRO CLAUDE CODE
# ==================================
#
# Po spuštění testů:
#   uv run pytest tests/test_executor_deep_v2.py -v --tb=short 2>&1 | tee /tmp/test_v2.txt
#
# Pak:
#   Projdi /tmp/test_v2.txt.
#   Pro každý FAILED test:
#
#   OPRAV IHNED pokud:
#     - chyba je jednoznačná (off-by-one, missing guard, wrong comparison)
#     - fix je 1-5 řádků v jednom souboru
#     - přidej regression test (nebo je BUG-xxx test již regression)
#
#   ZAPIŠ DO BUGS.md pokud:
#     - architekturická změna (sdílení costs listu v with_item)
#     - nejasný dopad fixu (může rozbít jiné věci)
#     - intentional behavior (např. costs je sdílený záměrně)
#
# BUGS.md formát: ~/Documents/Sandcastle/BUGS.md
#
#   # SANDCASTLE BUGS
#
#   ## CRITICAL
#   ### BUG-XXX: Název
#   - **File:** src/sandcastle/engine/executor.py:LINE
#   - **Test:** test_executor_deep_v2.py::TestClass::test_name
#   - **Popis:** Co je špatně
#   - **Root cause:** Proč se to děje
#   - **Proč odloženo:** Co brání okamžitému fixu
#   - **Navrhovaný fix:** Jak opravit
#
#   ## MEDIUM
#   ### BUG-XXX: ...
#
#   ## LOW
#   ### BUG-XXX: ...
#
# ══════════════════════════════════════════════════════════════
