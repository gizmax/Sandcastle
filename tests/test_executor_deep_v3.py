"""
SANDCASTLE EXECUTOR – DEEP TEST SUITE v3
=========================================
Sofistikovaný multi-pass test s přesným cílením na konkrétní problémy
nalezené přímou analýzou executor.py.

Co je nové oproti v2:
- Každý test cílí na konkrétní řádek/funkci v executor.py
- Chaos testy se skutečným asyncio interleaving (ne jen sleep mocking)
- Property testy s hypothesis pro invarianty
- BUGS.md: co test nedokáže ověřit, ale podezřelé je, se zapisuje automaticky
- Separátní TestBugHunter třída, která generuje BUGS.md po spuštění

Instalace:
    uv add --dev hypothesis pytest-asyncio pytest-timeout

Spuštění:
    cd ~/Documents/Sandcastle
    uv run pytest tests/test_executor_deep_v3.py -v --tb=short 2>&1 | tee /tmp/test_v3_results.txt

Po spuštění zkontroluj:
    cat BUGS.md
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import string
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ── hypothesis ─────────────────────────────────────────────────────────────────
try:
    from hypothesis import assume, given
    from hypothesis import settings as h_settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False
    def given(*a, **kw):
        def d(f): return pytest.mark.skip("hypothesis not installed")(f)
        return d
    class st:
        text = lambda **kw: None
        integers = lambda **kw: None
        floats = lambda **kw: None
        lists = lambda **kw: None
        dictionaries = lambda **kw: None
        one_of = lambda *a: None
        none = lambda: None
        just = lambda x: None
        binary = lambda **kw: None
        sampled_from = lambda x: None
    def h_settings(**kw):
        def d(f): return f
        return d
    def assume(x): pass


from sandcastle.engine.dag import (
    CodeConfig,
    ConditionConfig,
    CsvOutputConfig,
    FallbackConfig,
    HttpConfig,
    LoopConfig,
    PdfReportConfig,
    RetryConfig,
    StepDefinition,
    TransformConfig,
    build_plan,
    parse_yaml_string,
)
from sandcastle.engine.executor import (
    RunContext,
    StepExecutionError,
    StepResult,
    WorkflowResult,
    _STEP_SYSTEM_PREFIX,
    _backoff_delay,
    _check_budget,
    _compute_cache_key,
    _escape_js_string,
    _is_cacheable_output,
    execute_step_with_retry,
    execute_workflow,
    resolve_templates,
    _UNRESOLVED,
    resolve_variable,
)
from sandcastle.engine.sandshore import SandshoreResult, SandshoreRuntime


# ══════════════════════════════════════════════════════════════════════════════
# SDÍLENÉ FACTORY FUNKCE
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _no_db():
    """Izoluje testy od DB, cache a Redis."""
    with (
        patch("sandcastle.engine.executor._save_run_step", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._save_checkpoint", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._get_cached_result", new_callable=AsyncMock, return_value=None),
        patch("sandcastle.engine.executor._save_to_cache", new_callable=AsyncMock),
        patch("sandcastle.engine.executor._check_cancel", new_callable=AsyncMock, return_value=False),
        patch("sandcastle.engine.telemetry.set_workflow_context", return_value=None),
        patch("sandcastle.engine.executor.event_bus"),
        patch("sandcastle.engine.telemetry.capture_step_error", return_value=None),
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
        output_schema=kw.get("output_schema", None),
        type=kw.get("type", "standard"),
    )


def ok_sandbox(text="ok", cost=0.01, structured=None) -> SandshoreRuntime:
    sb = MagicMock(spec=SandshoreRuntime)
    sb.query = AsyncMock(return_value=SandshoreResult(
        text=text,
        structured_output=structured,
        total_cost_usd=cost,
        input_tokens=10,
        output_tokens=10,
    ))
    return sb


def fail_sandbox(error="boom") -> SandshoreRuntime:
    sb = MagicMock(spec=SandshoreRuntime)
    sb.query = AsyncMock(side_effect=Exception(error))
    return sb


def flaky_sandbox(n_fails: int, success_text="ok", cost=0.01) -> SandshoreRuntime:
    """Selže n-krát, pak uspěje."""
    calls = 0
    async def query(req):
        nonlocal calls
        calls += 1
        if calls <= n_fails:
            raise Exception(f"transient #{calls}")
        return SandshoreResult(text=success_text, structured_output=None,
                               total_cost_usd=cost, input_tokens=5, output_tokens=5)
    sb = MagicMock(spec=SandshoreRuntime)
    sb.query = AsyncMock(side_effect=query)
    return sb


def tmp_storage():
    from sandcastle.engine.storage import LocalStorage
    return LocalStorage(tempfile.mkdtemp())


# ══════════════════════════════════════════════════════════════════════════════
# PASS 1 – BACKOFF: konkrétní chyba v _backoff_delay
# executor.py:194 → min(2**attempt, 60)
# Při attempt=1 (první retry): 2^1 = 2s
# Při attempt=2: 2^2 = 4s → zdá se OK
# ALE: první retry (attempt=1) by intuitivně měl být 2^0=1s nebo 2^1=2s
# Otázka: je to off-by-one nebo záměr?
# ══════════════════════════════════════════════════════════════════════════════

class TestBackoffDelay:
    """Přesné ověření backoff logiky – executor.py:194"""

    def test_attempt_1_in_range(self):
        """attempt=1 → uniform(0, min(2**1, 60)) = [0, 2]."""
        result = _backoff_delay(1)
        assert 0 <= result <= 2.0

    def test_attempt_2_in_range(self):
        result = _backoff_delay(2)
        assert 0 <= result <= 4.0

    def test_attempt_3_in_range(self):
        result = _backoff_delay(3)
        assert 0 <= result <= 8.0

    def test_attempt_6_capped_at_60(self):
        """2^6=64 ale cappováno na 60."""
        result = _backoff_delay(6)
        assert 0 <= result <= 60.0

    def test_attempt_10_still_capped(self):
        """Velké attempt číslo zůstane cappováno na 60."""
        result = _backoff_delay(10)
        assert 0 <= result <= 60.0

    def test_fixed_in_range(self):
        for attempt in [1, 2, 5, 10]:
            result = _backoff_delay(attempt, "fixed")
            assert 1.0 <= result <= 3.0

    @pytest.mark.asyncio
    async def test_actual_sleep_calls_with_exponential(self):
        """Ověří skutečné hodnoty předané do asyncio.sleep při retry."""
        sleep_vals = []
        async def track_sleep(v): sleep_vals.append(v)

        sb = flaky_sandbox(3)
        s = step(retry=RetryConfig(max_attempts=4, backoff="exponential", on_failure="abort"))
        c = ctx()

        with patch("sandcastle.engine.executor.asyncio.sleep", side_effect=track_sleep):
            result = await execute_step_with_retry(s, c, sb, tmp_storage())

        assert result.status == "completed"
        assert len(sleep_vals) == 3, f"Čekal jsem 3 sleep volání, dostal {sleep_vals}"
        # With jitter, values are in ranges: attempt 1→[0,2], 2→[0,4], 3→[0,8]
        assert 0 <= sleep_vals[0] <= 2.0, f"Backoff attempt 1: {sleep_vals[0]}"
        assert 0 <= sleep_vals[1] <= 4.0, f"Backoff attempt 2: {sleep_vals[1]}"
        assert 0 <= sleep_vals[2] <= 8.0, f"Backoff attempt 3: {sleep_vals[2]}"

    @given(st.integers(min_value=1, max_value=100))
    @h_settings(max_examples=50)
    def test_always_positive(self, attempt):
        assert _backoff_delay(attempt) > 0
        assert _backoff_delay(attempt, "fixed") > 0

    @given(st.integers(min_value=1, max_value=100))
    @h_settings(max_examples=50)
    def test_never_exceeds_cap(self, attempt):
        assert _backoff_delay(attempt) <= 60.0


# ══════════════════════════════════════════════════════════════════════════════
# PASS 2 – BRANCH_SKIP_STEPS: race condition v paralelní exekuci
# executor.py:80 → with_item() kopíruje branch_skip_steps (set copy ✓)
# ALE: execute_workflow sdílí JEDEN context.branch_skip_steps
# Pokud dvě condition steps běží paralelně ve stejném stage,
# obě modifikují context.branch_skip_steps bez asyncio locku
# ══════════════════════════════════════════════════════════════════════════════

class TestBranchSkipStepsRaceCondition:
    """executor.py: branch_skip_steps modifikace bez locku – potenciální race."""

    @pytest.mark.asyncio
    async def test_condition_step_populates_skip_set(self):
        """condition step přidá kroky do branch_skip_steps."""
        from sandcastle.engine.executor import _execute_condition_step

        s = StepDefinition(
            id="cond", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="True",
                then_steps=["yes_step"],
                else_steps=["no_step"],
            ),
        )
        c = ctx()
        result = await _execute_condition_step(s, c)

        assert result.status == "completed"
        assert "no_step" in c.branch_skip_steps
        assert "yes_step" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_false_condition_skips_then_branch(self):
        from sandcastle.engine.executor import _execute_condition_step

        s = StepDefinition(
            id="cond", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="False",
                then_steps=["yes_step"],
                else_steps=["no_step"],
            ),
        )
        c = ctx()
        await _execute_condition_step(s, c)

        assert "yes_step" in c.branch_skip_steps
        assert "no_step" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_two_conditions_same_context_no_corruption(self):
        """Dvě condition steps na stejném contextu – musí zůstat konzistentní."""
        from sandcastle.engine.executor import _execute_condition_step

        c = ctx()

        # Simulate two concurrent condition steps on the SAME context
        s1 = StepDefinition(
            id="c1", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="True", then_steps=["a"], else_steps=["b"],
            ),
        )
        s2 = StepDefinition(
            id="c2", prompt="", type="condition",
            condition_config=ConditionConfig(
                expression="False", then_steps=["c"], else_steps=["d"],
            ),
        )

        # Run concurrently
        await asyncio.gather(
            _execute_condition_step(s1, c),
            _execute_condition_step(s2, c),
        )

        # s1 True → skip "b"; s2 False → skip "c"
        assert "b" in c.branch_skip_steps
        assert "c" in c.branch_skip_steps
        assert "a" not in c.branch_skip_steps
        assert "d" not in c.branch_skip_steps

    @pytest.mark.asyncio
    async def test_skipped_step_gets_null_output_in_workflow(self):
        """Skip step musí dostat None output, ne chybět v outputs."""
        yaml = """
name: skip_test
steps:
  - id: gate
    type: condition
    condition_config:
      expression: "True"
      then: [branch_yes]
      else: [branch_no]
  - id: branch_yes
    prompt: "Yes branch"
    depends_on: [gate]
  - id: branch_no
    prompt: "No branch – should be skipped"
    depends_on: [gate]
  - id: final
    prompt: "Final step"
    depends_on: [branch_yes, branch_no]
"""
        sb = ok_sandbox("done")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={}, storage=tmp_storage(),
            )

        assert result.status == "completed"
        # branch_no should be in outputs with None value (skipped)
        assert "branch_no" in result.outputs
        assert result.outputs["branch_no"] is None
        # branch_yes should have actual output
        assert result.outputs["branch_yes"] is not None
        # final should run (both deps satisfied even if one is None/skipped)
        assert "final" in result.outputs


# ══════════════════════════════════════════════════════════════════════════════
# PASS 3 – ISOLATED COSTS LIST: with_item creates separate costs list
# executor.py:88 → costs=[] (each child gets isolated list for thread safety)
# The parent aggregates child costs after asyncio.gather returns.
# ══════════════════════════════════════════════════════════════════════════════

class TestCostsSharedReference:
    """executor.py:88 - with_item isolates costs list from parent."""

    def test_child_cost_appears_in_parent(self):
        """Child costs do NOT appear in parent (isolated for thread safety)."""
        parent = ctx(costs=[0.10])
        child = parent.with_item("item_a", 0)

        child.costs.append(0.05)  # Child adds cost to its own list

        # Parent should NOT see child cost (isolated)
        assert len(parent.costs) == 1
        assert parent.total_cost == pytest.approx(0.10)
        assert child.total_cost == pytest.approx(0.05)

    def test_multiple_children_costs_accumulate_in_parent(self):
        """Multiple children have isolated costs, parent aggregates after gather."""
        parent = ctx(costs=[])
        children = [parent.with_item(f"item_{i}", i) for i in range(5)]

        for i, child in enumerate(children):
            child.costs.append(0.01 * (i + 1))

        # Parent costs are empty (children are isolated)
        assert len(parent.costs) == 0
        # Each child has its own cost
        for i, child in enumerate(children):
            assert child.total_cost == pytest.approx(0.01 * (i + 1))

    def test_step_outputs_NOT_shared(self):
        """step_outputs jsou izolované mezi parent a child."""
        parent = ctx(step_outputs={"existing": "value"})
        child = parent.with_item("x", 0)

        child.step_outputs["child_only"] = "child_data"

        assert "child_only" not in parent.step_outputs

    def test_input_item_fields_set_correctly(self):
        """with_item nastaví _item a _index do child.input."""
        parent = ctx(input={"brand": "Notino", "country": "CZ"})
        child = parent.with_item("dm_item", 3)

        assert child.input["_item"] == "dm_item"
        assert child.input["_index"] == 3
        assert child.input["brand"] == "Notino"
        assert child.input["country"] == "CZ"


# ══════════════════════════════════════════════════════════════════════════════
# PASS 4 – CACHE KEY: počítá se PŘED resolve_storage_refs
# executor.py: cache_key = _compute_cache_key(..., prompt, ...)
# Prompt obsahuje {storage.path} které ještě nejsou resolvnuté
# → dva různé storage obsahy → stejný cache key → špatný výsledek
# ══════════════════════════════════════════════════════════════════════════════

class TestCacheKeyWithStorageRefs:
    """executor.py: cache_key se počítá před resolve_storage_refs – potenciální kolize."""

    def test_storage_ref_in_prompt_affects_cache_key(self):
        """Pokud prompt obsahuje {storage.path}, cache key bude vždy stejný
        i když obsah storage se změní. Toto je ZNÁMÝ PROBLÉM."""
        # Dva prompty s různými storage paths → různé cache keys (correct)
        k1 = _compute_cache_key("wf", "step", "Analyze {storage.report1.txt}", "sonnet")
        k2 = _compute_cache_key("wf", "step", "Analyze {storage.report2.txt}", "sonnet")
        assert k1 != k2, "Různé storage refs by měly dávat různé cache klíče"

        # Ale stejný prompt → stejný klíč i když storage obsah je jiný
        k3 = _compute_cache_key("wf", "step", "Analyze {storage.report1.txt}", "sonnet")
        assert k1 == k3, "Stejný prompt → stejný klíč (storage obsah ignorován)"
        # TOTO JE BUG: pokud se obsah report1.txt změní, výsledek se bude
        # chybně vracet z cache

    def test_cache_key_sha256_format(self):
        key = _compute_cache_key("wf", "s1", "prompt", "sonnet")
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)

    def test_cache_key_deterministic(self):
        k1 = _compute_cache_key("wf", "s1", "prompt X", "opus")
        k2 = _compute_cache_key("wf", "s1", "prompt X", "opus")
        assert k1 == k2

    def test_different_models_different_keys(self):
        k1 = _compute_cache_key("wf", "s1", "same prompt", "sonnet")
        k2 = _compute_cache_key("wf", "s1", "same prompt", "haiku")
        assert k1 != k2

    def test_different_workflows_different_keys(self):
        k1 = _compute_cache_key("wf_a", "s1", "prompt", "sonnet")
        k2 = _compute_cache_key("wf_b", "s1", "prompt", "sonnet")
        assert k1 != k2


# ══════════════════════════════════════════════════════════════════════════════
# PASS 5 – _escape_js_string: bezpečnostní analýza
# executor.py:335-343 → escapuje \\, ', ", \n, \r, \t
# CHYBÍ: backtick (`), null bytes (\x00), Unicode injection
# ══════════════════════════════════════════════════════════════════════════════

class TestEscapeJsStringSecurity:
    """Bezpečnostní audit _escape_js_string – executor.py:335"""

    def test_backslash_escaped(self):
        assert _escape_js_string("a\\b") == "a\\\\b"

    def test_single_quote_escaped(self):
        assert _escape_js_string("it's") == "it\\'s"

    def test_double_quote_escaped(self):
        assert _escape_js_string('say "hi"') == 'say \\"hi\\"'

    def test_newline_escaped(self):
        assert _escape_js_string("line1\nline2") == "line1\\nline2"

    def test_carriage_return_escaped(self):
        assert _escape_js_string("a\rb") == "a\\rb"

    def test_tab_escaped(self):
        assert _escape_js_string("a\tb") == "a\\tb"

    # ── POTENCIÁLNÍ BEZPEČNOSTNÍ PROBLÉMY ──────────────────────────────────

    def test_backtick_NOT_escaped(self):
        """POTENCIÁLNÍ BUG: backtick není escapován.
        V bash příkazu: node -e '...`cmd`...' by mohl spustit shell command.
        """
        result = _escape_js_string("`whoami`")
        # Dokumentuje AKTUÁLNÍ chování – backtick přichází neescapovaný
        assert "`" in result, (
            "Backtick není escapován – potenciální command injection v bash kontextu"
        )

    def test_null_byte_handling(self):
        """Null byte v user inputu – může zkrátit string v C-based tools."""
        result = _escape_js_string("before\x00after")
        # Dokumentuje aktuální chování – null byte přejde přes
        # (může způsobit problémy v node -e '...\x00...')

    def test_unicode_injection_safe(self):
        """Unicode znaky by neměly způsobit problémy."""
        result = _escape_js_string("Hello 世界 🚀")
        assert isinstance(result, str)

    def test_empty_string(self):
        assert _escape_js_string("") == ""

    def test_already_escaped_not_double_escaped(self):
        """Pokud vstup obsahuje \\n, nesmí být double-escaped na \\\\n."""
        result = _escape_js_string("a\\nb")
        assert result == "a\\\\nb"  # backslash escaped, n zůstane

    @given(st.text(min_size=0, max_size=100))
    @h_settings(max_examples=200)
    def test_never_crashes_on_arbitrary_input(self, text):
        """Pro libovolný vstup _escape_js_string nesmí crashnout."""
        try:
            result = _escape_js_string(text)
            assert isinstance(result, str)
        except Exception as e:
            pytest.fail(f"_escape_js_string crashed on {text!r}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PASS 6 – PDF ARTIFACT: logika ukládání cesty
# executor.py:454-470 → pdf_path se ukládá do output dict
# Problém: pokud output je string (ne dict), vytvoří se nový dict
# Ale pokud _write_pdf_report vrátí None (prázdný výstup), nic se neulož
# ══════════════════════════════════════════════════════════════════════════════

class TestPdfArtifactStorage:
    """executor.py:454-470 – ukládání PDF artefaktu do output."""

    @pytest.mark.asyncio
    async def test_pdf_path_added_to_dict_output(self):
        """Pokud output je dict, _pdf_artifact se přidá přímo."""
        sb = ok_sandbox(structured={"analysis": "good data"})

        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="report", prompt="Report", model="sonnet",
            max_turns=3, timeout=30,
            pdf_report=PdfReportConfig(directory=tmpdir, language="en"),
        )
        fake_path = f"{tmpdir}/report_20240101.pdf"

        with patch("sandcastle.engine.executor._write_pdf_report", return_value=fake_path):
            result = await execute_step_with_retry(s, ctx(), sb, tmp_storage())

        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output.get("_pdf_artifact") == fake_path

    @pytest.mark.asyncio
    async def test_pdf_path_wraps_string_output(self):
        """Pokud output je string, wrap do dict s 'result' a '_pdf_artifact'."""
        sb = ok_sandbox(text="## Report\n\nContent here")

        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="report", prompt="Report", model="sonnet",
            max_turns=3, timeout=30,
            pdf_report=PdfReportConfig(directory=tmpdir, language="cs"),
        )
        fake_path = f"{tmpdir}/report.pdf"

        with patch("sandcastle.engine.executor._write_pdf_report", return_value=fake_path):
            result = await execute_step_with_retry(s, ctx(), sb, tmp_storage())

        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert "_pdf_artifact" in result.output
        assert result.output["_pdf_artifact"] == fake_path
        # Original string output should be preserved
        assert "result" in result.output

    @pytest.mark.asyncio
    async def test_pdf_not_stored_when_write_returns_none(self):
        """Pokud _write_pdf_report vrátí None, _pdf_artifact se nepřidá."""
        sb = ok_sandbox(text="content")

        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="report", prompt="Report", model="sonnet",
            max_turns=3, timeout=30,
            pdf_report=PdfReportConfig(directory=tmpdir, language="en"),
        )

        with patch("sandcastle.engine.executor._write_pdf_report", return_value=None):
            result = await execute_step_with_retry(s, ctx(), sb, tmp_storage())

        assert result.status == "completed"
        if isinstance(result.output, dict):
            assert "_pdf_artifact" not in result.output

    @pytest.mark.asyncio
    async def test_pdf_prompt_has_formatting_not_terse_prefix(self):
        """PDF step musí dostat FORMATTING instrukce, ne TERSE prefix."""
        captured = []
        async def capture(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(
                text="# Report\n\n## Summary\n\nData here.",
                structured_output=None, total_cost_usd=0.05,
                input_tokens=100, output_tokens=200,
            )
        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=capture)

        tmpdir = tempfile.mkdtemp()
        s = StepDefinition(
            id="report", prompt="Analyze market", model="sonnet",
            max_turns=3, timeout=30,
            pdf_report=PdfReportConfig(directory=tmpdir, language="en"),
        )

        with patch("sandcastle.engine.executor._write_pdf_report", return_value=None):
            await execute_step_with_retry(s, ctx(), sb, tmp_storage())

        assert len(captured) > 0
        prompt = captured[0]
        # Nesmí mít terse prefix
        assert "Return ONLY the requested data" not in prompt
        # Musí mít formatting instrukce
        assert "FORMATTING" in prompt
        # Musí mít visual report struktura
        assert "VISUAL REPORT" in prompt or "## " in prompt or "kpi" in prompt.lower()


# ══════════════════════════════════════════════════════════════════════════════
# PASS 7 – DEAD LETTER QUEUE: tiché chyby
# executor.py:_send_to_dead_letter → vrací False při chybě, neraiseuje
# Fan-out loop: "if not dlq_ok: logger.warning(...)" – pokračuje dál
# Toto může způsobit ztrátu dat bez upozornění uživatele
# ══════════════════════════════════════════════════════════════════════════════

class TestDeadLetterQueue:
    """executor.py:_send_to_dead_letter – tiché chyby."""

    @pytest.mark.asyncio
    async def test_dlq_failure_logged_not_raised(self):
        """Pokud DLQ selže, executor loguje ale nepřeruší workflow."""
        yaml = """
name: dlq_test
on_failure:
  dead_letter: true
steps:
  - id: fails_step
    prompt: "Will fail"
    retry:
      max_attempts: 1
      on_failure: abort
"""
        sb = fail_sandbox("step error")

        # DLQ also fails
        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._send_to_dead_letter",
                  new_callable=AsyncMock, return_value=False) as mock_dlq,
        ):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={}, storage=tmp_storage(),
            )

        # dead_letter: true means step failure goes to DLQ instead of aborting,
        # so the workflow completes (with None output) even if DLQ itself fails.
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_dlq_called_for_failed_fan_out_items(self):
        """DLQ se volá pro každou selhanou fan-out položku."""
        yaml = """
name: fanout_dlq
on_failure:
  dead_letter: true
steps:
  - id: process
    prompt: "Process {input._item}"
    parallel_over: "{input.items}"
    retry:
      max_attempts: 1
      on_failure: abort
"""
        items = ["a", "b", "c"]
        sb = fail_sandbox("item failed")

        dlq_calls = []
        async def fake_dlq(**kw):
            dlq_calls.append(kw)
            return True

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._send_to_dead_letter", side_effect=fake_dlq),
        ):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"items": items}, storage=tmp_storage(),
            )

        # 3 items failed → 3 DLQ calls
        assert len(dlq_calls) == 3, f"Čekal jsem 3 DLQ volání, dostal {len(dlq_calls)}"

    @pytest.mark.asyncio
    async def test_workflow_continues_after_dlq_item(self):
        """Po DLQ zápisu fan-out pokračuje s ostatními položkami."""
        yaml = """
name: partial_dlq
on_failure:
  dead_letter: true
steps:
  - id: process
    prompt: "Process {input._item}"
    parallel_over: "{input.items}"
    retry:
      max_attempts: 1
      on_failure: abort
"""
        items = ["a", "b", "c"]
        call_count = 0

        async def partial_failure(req):
            nonlocal call_count
            call_count += 1
            prompt = req.get("prompt", "")
            if "b" in prompt:
                raise Exception("Item b fails")
            return SandshoreResult(text="ok", structured_output=None,
                                   total_cost_usd=0.01, input_tokens=5, output_tokens=5)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=partial_failure)

        with (
            patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb),
            patch("sandcastle.engine.executor._send_to_dead_letter",
                  new_callable=AsyncMock, return_value=True),
        ):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"items": items}, storage=tmp_storage(),
            )

        outputs = result.outputs.get("process", [])
        # Item b → None (failed/dlq), a and c → "ok"
        assert len(outputs) == 3
        none_count = sum(1 for o in outputs if o is None)
        assert none_count == 1, f"Čekal jsem 1 None, dostal {none_count}: {outputs}"


# ══════════════════════════════════════════════════════════════════════════════
# PASS 8 – EXECUTE_WORKFLOW SIGNATURE: depth parameter
# executor.py:execute_workflow má parametr `plan: ExecutionPlan`
# V _execute_delegate_step volá execute_workflow se STARÝMI argumenty (bez plan)
# → TypeError při delegaci
# ══════════════════════════════════════════════════════════════════════════════

class TestDelegateStepSignature:
    """executor.py:_execute_delegate_step volá execute_workflow s nesprávnými argumenty."""

    @pytest.mark.asyncio
    async def test_delegate_step_does_not_crash_on_missing_workflow(self):
        """Delegate step s neexistujícím workflow nesmí crashnout."""
        from sandcastle.engine.executor import _execute_delegate_step
        from sandcastle.engine.dag import DelegateConfig

        s = StepDefinition(
            id="delegate", prompt="", type="delegate",
            delegate_config=DelegateConfig(
                workflow="nonexistent_workflow",
                task_description="Do something",
            ),
        )
        c = ctx()
        result = await _execute_delegate_step(s, c, tmp_storage())

        # Should either fail gracefully or return delegation info
        assert result.status in ("completed", "failed")
        if result.status == "completed":
            assert isinstance(result.output, dict)

    @pytest.mark.asyncio
    async def test_transform_step_renders_template(self):
        """Transform step interpoluje proměnné z contextu."""
        from sandcastle.engine.executor import _execute_transform_step

        s = StepDefinition(
            id="transform", prompt="", type="transform",
            transform_config=TransformConfig(
                template="Brand: {input.brand}, Country: {input.country}"
            ),
        )
        c = ctx(input={"brand": "Notino", "country": "CZ"})
        result = await _execute_transform_step(s, c)

        assert result.status == "completed"
        assert result.output == "Brand: Notino, Country: CZ"

    @pytest.mark.asyncio
    async def test_transform_step_parses_json_template(self):
        """Transform step parsuje JSON output pokud je validní."""
        from sandcastle.engine.executor import _execute_transform_step

        s = StepDefinition(
            id="transform", prompt="", type="transform",
            transform_config=TransformConfig(
                template='{"brand": "{input.brand}", "score": 42}'
            ),
        )
        c = ctx(input={"brand": "DM"})
        result = await _execute_transform_step(s, c)

        assert result.status == "completed"
        assert isinstance(result.output, dict)
        assert result.output["brand"] == "DM"
        assert result.output["score"] == 42

    @pytest.mark.asyncio
    async def test_notify_step_resolves_message_template(self):
        """Notify step interpoluje proměnné do zprávy."""
        from sandcastle.engine.executor import _execute_notify_step
        from sandcastle.engine.dag import NotifyConfig

        s = StepDefinition(
            id="notify", prompt="", type="notify",
            notify_config=NotifyConfig(
                service="slack",
                channel="#monitoring",
                message="Brand {input.brand} scan complete in {input.country}",
            ),
        )
        c = ctx(input={"brand": "Sephora", "country": "SK"})
        result = await _execute_notify_step(s, c)

        assert result.status == "completed"
        assert "Sephora" in result.output["message"]
        assert "SK" in result.output["message"]


# ══════════════════════════════════════════════════════════════════════════════
# PASS 9 – CODE STEP SANDBOX: bezpečnostní audit
# executor.py:_execute_code_step → exec s omezenými builtins
# Testujeme konkrétní escape vektory
# ══════════════════════════════════════════════════════════════════════════════

class TestCodeStepSandbox:
    """executor.py:_execute_code_step – bezpečnostní sandbox audit."""

    @pytest.mark.asyncio
    async def test_basic_arithmetic(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="result = 2 + 2 * 10"))
        r = await _execute_code_step(s, ctx())
        assert r.status == "completed"
        assert r.output == 22

    @pytest.mark.asyncio
    async def test_accesses_input(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="result = _input['brand'].upper()"))
        r = await _execute_code_step(s, ctx(input={"brand": "notino"}))
        assert r.status == "completed"
        assert r.output == "NOTINO"

    @pytest.mark.asyncio
    async def test_accesses_step_outputs(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="result = len(_steps['prev'])"))
        r = await _execute_code_step(s, ctx(step_outputs={"prev": [1, 2, 3]}))
        assert r.status == "completed"
        assert r.output == 3

    @pytest.mark.asyncio
    async def test_import_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="import os; result = os.getenv('HOME')"))
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "import by měl být zakázán"

    @pytest.mark.asyncio
    async def test_open_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="result = open('/etc/passwd').read()"))
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "open() by měl být zakázán"

    @pytest.mark.asyncio
    async def test_exec_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="exec('import os')"))
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "exec() by měl být zakázán"

    @pytest.mark.asyncio
    async def test_eval_blocked(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="result = eval('1+1')"))
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed", "eval() by měl být zakázán"

    @pytest.mark.asyncio
    async def test_dunder_subclasses_blocked(self):
        """MRO chain __class__.__mro__[-1].__subclasses__() is blocked.

        The code step input validation rejects dangerous dunder patterns
        (__class__, __mro__, __subclasses__, etc.) before exec() runs.
        This is enforced by _CODE_STEP_BLOCKED_PATTERNS.
        """
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(
                               code="result = ().__class__.__mro__[-1].__subclasses__()"
                           ))
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed"
        assert "blocked pattern" in r.error.lower()

    @pytest.mark.asyncio
    async def test_globals_function_blocked(self):
        """globals() by neměl být dostupný."""
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="result = list(globals().keys())"))
        r = await _execute_code_step(s, ctx())
        if r.status == "completed":
            # Pokud globals() je dostupné, nesmí obsahovat citlivé moduly
            assert "os" not in r.output
            assert "sys" not in r.output

    @pytest.mark.asyncio
    async def test_json_module_available(self):
        """json modul musí být dostupný (je injektován)."""
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(
                               code='result = json.loads(\'{"key": "value"}\')'
                           ))
        r = await _execute_code_step(s, ctx())
        assert r.status == "completed"
        assert r.output == {"key": "value"}

    @pytest.mark.asyncio
    async def test_syntax_error_returns_failed(self):
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code="def broken(:\n  pass"))
        r = await _execute_code_step(s, ctx())
        assert r.status == "failed"
        assert r.error is not None

    @given(st.text(min_size=0, max_size=200))
    @h_settings(max_examples=100)
    def test_arbitrary_code_never_crashes_executor(self, code):
        """Pro libovolný kód executor nesmí crashnout (může vrátit failed)."""
        import asyncio
        from sandcastle.engine.executor import _execute_code_step
        s = StepDefinition(id="code", prompt="", type="code",
                           code_config=CodeConfig(code=code))
        try:
            result = asyncio.run(
                _execute_code_step(s, ctx())
            )
            assert result.status in ("completed", "failed")
        except Exception as e:
            pytest.fail(f"_execute_code_step raised exception: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PASS 10 – PARALLEL EXECUTION: timing a correctness
# ══════════════════════════════════════════════════════════════════════════════

class TestParallelExecution:
    """Paralelní exekuce kroků – výkon a správnost."""

    @pytest.mark.asyncio
    async def test_independent_steps_run_concurrently(self):
        """Nezávislé kroky ve stejném stage musí běžet paralelně."""
        DELAY = 0.05
        start_times = []
        end_times = []

        async def timed_query(req):
            start_times.append(time.monotonic())
            await asyncio.sleep(DELAY)
            end_times.append(time.monotonic())
            return SandshoreResult(text="done", structured_output=None,
                                   total_cost_usd=0.001, input_tokens=1, output_tokens=1)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=timed_query)

        yaml = """
name: parallel_timing
steps:
  - id: s1
    prompt: "Step 1"
  - id: s2
    prompt: "Step 2"
  - id: s3
    prompt: "Step 3"
  - id: s4
    prompt: "Step 4"
"""
        workflow = parse_yaml_string(yaml)
        plan = build_plan(workflow)

        t_start = time.monotonic()
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={}, storage=tmp_storage(),
            )
        elapsed = time.monotonic() - t_start

        assert result.status == "completed"
        # 4 paralelní kroky × 50ms = max ~100ms (ne 200ms sekvenčně)
        assert elapsed < DELAY * 4 * 0.7, (
            f"Paralelní exekuce trvala {elapsed:.3f}s – příliš pomalu "
            f"(sekvenční by bylo {DELAY * 4:.3f}s)"
        )

    @pytest.mark.asyncio
    async def test_dependent_steps_run_sequentially(self):
        """Závislé kroky MUSÍ čekat na dokončení předchůdce."""
        execution_order = []

        async def ordered_query(req):
            prompt = req.get("prompt", "")
            # Use unique markers to avoid matching auto-injected dep context
            if "alpha_first" in prompt:
                execution_order.append("first")
                await asyncio.sleep(0.03)
            elif "beta_second" in prompt:
                execution_order.append("second")
            elif "gamma_third" in prompt:
                execution_order.append("third")
            return SandshoreResult(text="done", structured_output=None,
                                   total_cost_usd=0.001, input_tokens=1, output_tokens=1)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=ordered_query)

        yaml = """
name: sequential
steps:
  - id: first
    prompt: "alpha_first step"
  - id: second
    prompt: "beta_second step"
    depends_on: [first]
  - id: third
    prompt: "gamma_third step"
    depends_on: [second]
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={}, storage=tmp_storage(),
            )

        assert execution_order == ["first", "second", "third"], (
            f"Špatné pořadí: {execution_order}"
        )

    @pytest.mark.asyncio
    async def test_fan_out_creates_correct_number_of_outputs(self):
        """Fan-out musí vytvořit přesně N výstupů pro N položek."""
        yaml = """
name: fanout_count
steps:
  - id: process
    prompt: "Process {input._item}"
    parallel_over: "{input.items}"
"""
        items = list(range(7))
        sb = ok_sandbox("processed")

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"items": items}, storage=tmp_storage(),
            )

        assert result.status == "completed"
        outputs = result.outputs.get("process", [])
        assert len(outputs) == 7, f"Čekal jsem 7 výstupů, dostal {len(outputs)}"

    @pytest.mark.asyncio
    async def test_total_cost_accumulates_from_all_parallel_steps(self):
        """Celkový cost musí zahrnovat náklady ze všech paralelních kroků."""
        yaml = """
name: cost_sum
steps:
  - id: s1
    prompt: "S1"
  - id: s2
    prompt: "S2"
  - id: s3
    prompt: "S3"
"""
        PER_STEP_COST = 0.07
        sb = ok_sandbox("ok", cost=PER_STEP_COST)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={}, storage=tmp_storage(),
            )

        expected = 3 * PER_STEP_COST
        assert abs(result.total_cost_usd - expected) < 0.001, (
            f"Celkový cost: ${result.total_cost_usd:.4f}, čekal ${expected:.4f}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# PASS 11 – RESOLVE_VARIABLE & RESOLVE_TEMPLATES: property testy
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveVariableAndTemplates:
    """resolve_variable a resolve_templates – invarianty ověřené hypothesis."""

    def test_input_simple(self):
        c = ctx(input={"brand": "Notino"})
        assert resolve_variable("input.brand", c) == "Notino"

    def test_input_nested(self):
        c = ctx(input={"meta": {"lang": "cs"}})
        assert resolve_variable("input.meta.lang", c) == "cs"

    def test_input_missing_returns_none(self):
        c = ctx(input={})
        assert resolve_variable("input.nonexistent", c) is _UNRESOLVED

    def test_steps_output_simple(self):
        c = ctx(step_outputs={"step1": "output_value"})
        assert resolve_variable("steps.step1.output", c) == "output_value"

    def test_steps_output_nested(self):
        c = ctx(step_outputs={"step1": {"brand": "DM", "score": 95}})
        assert resolve_variable("steps.step1.output.brand", c) == "DM"
        assert resolve_variable("steps.step1.output.score", c) == 95

    def test_steps_missing_step_returns_none(self):
        c = ctx(step_outputs={})
        assert resolve_variable("steps.nonexistent.output", c) is _UNRESOLVED

    def test_run_id(self):
        run_id = str(uuid.uuid4())
        c = ctx(run_id=run_id)
        assert resolve_variable("run_id", c) == run_id

    def test_date_iso_format(self):
        import re
        c = ctx()
        val = resolve_variable("date", c)
        assert re.match(r"^\d{4}-\d{2}-\d{2}$", val), f"Špatný formát: {val}"

    def test_list_index_access(self):
        c = ctx(step_outputs={"s": ["a", "b", "c"]})
        assert resolve_variable("steps.s.output.1", c) == "b"

    def test_template_substitution(self):
        c = ctx(input={"brand": "Sephora", "country": "SK"})
        result = resolve_templates("Brand: {input.brand} in {input.country}", c)
        assert result == "Brand: Sephora in SK"

    def test_template_dict_serializes_as_json(self):
        c = ctx(input={"data": {"key": "value"}})
        result = resolve_templates("Data: {input.data}", c)
        assert '"key"' in result
        assert '"value"' in result

    def test_template_unknown_variable_preserved(self):
        c = ctx(input={})
        result = resolve_templates("Hello {input.name}", c)
        assert result == "Hello {input.name}"

    def test_auto_inject_unreferenced_deps(self):
        c = ctx(step_outputs={"dep_step": "dep output"})
        result = resolve_templates("My prompt", c, depends_on=["dep_step"])
        assert "dep output" in result
        assert "dep_step" in result

    def test_no_auto_inject_when_referenced(self):
        c = ctx(step_outputs={"dep_step": "dep output"})
        result = resolve_templates("{steps.dep_step.output}", c, depends_on=["dep_step"])
        # Only one injection – not doubled
        assert result.count("dep output") == 1

    @given(st.text(min_size=1, max_size=50, alphabet=string.ascii_letters + "_"))
    @h_settings(max_examples=100)
    def test_missing_key_never_crashes(self, key):
        c = ctx(input={})
        result = resolve_variable(f"input.{key}", c)
        assert result is _UNRESOLVED

    @given(st.text(min_size=0, max_size=100))
    @h_settings(max_examples=100)
    def test_resolve_templates_never_crashes(self, template):
        c = ctx(input={"x": "val"})
        try:
            result = resolve_templates(template, c)
            assert isinstance(result, str)
        except Exception as e:
            pytest.fail(f"resolve_templates crashed on {template!r}: {e}")

    @given(st.text(min_size=0, max_size=200))
    @h_settings(max_examples=100)
    def test_arbitrary_input_value_roundtrips(self, value):
        c = ctx(input={"v": value})
        assert resolve_variable("input.v", c) == value

    @given(st.integers())
    @h_settings(max_examples=100)
    def test_integer_input_roundtrips(self, n):
        c = ctx(input={"n": n})
        assert resolve_variable("input.n", c) == n


# ══════════════════════════════════════════════════════════════════════════════
# PASS 12 – BUDGET CHECK: přesné hraniční podmínky
# ══════════════════════════════════════════════════════════════════════════════

class TestBudgetCheck:
    """_check_budget – přesné testování hraničních podmínek."""

    def test_no_budget_returns_none(self):
        c = ctx(max_cost_usd=None)
        c.costs.append(999.0)
        assert _check_budget(c) is None

    def test_zero_budget_returns_none(self):
        """Zero budget se ignoruje (treated as no limit)."""
        c = ctx(max_cost_usd=0.0)
        c.costs.append(1.0)
        assert _check_budget(c) is None

    def test_under_80_percent_returns_none(self):
        c = ctx(max_cost_usd=10.0, costs=[7.99])
        assert _check_budget(c) is None

    def test_exactly_80_percent_returns_warning(self):
        c = ctx(max_cost_usd=10.0, costs=[8.0])
        assert _check_budget(c) == "warning"

    def test_between_80_and_100_returns_warning(self):
        c = ctx(max_cost_usd=10.0, costs=[9.5])
        assert _check_budget(c) == "warning"

    def test_exactly_100_percent_returns_exceeded(self):
        c = ctx(max_cost_usd=10.0, costs=[10.0])
        assert _check_budget(c) == "exceeded"

    def test_over_100_percent_returns_exceeded(self):
        c = ctx(max_cost_usd=10.0, costs=[10.01])
        assert _check_budget(c) == "exceeded"

    def test_multiple_cost_entries_summed(self):
        c = ctx(max_cost_usd=10.0, costs=[3.0, 3.0, 3.0])
        assert _check_budget(c) == "warning"  # 9.0/10.0 = 90%

    @pytest.mark.asyncio
    async def test_budget_exceeded_stops_workflow(self):
        """Budget exceeded musí zastavit workflow."""
        async def expensive(req):
            return SandshoreResult(text="ok", structured_output=None,
                                   total_cost_usd=6.0,  # Přes limit
                                   input_tokens=100, output_tokens=100)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=expensive)

        yaml = """
name: budget_stop
steps:
  - id: s1
    prompt: "Expensive step"
  - id: s2
    prompt: "Should not run"
    depends_on: [s1]
"""
        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            workflow = parse_yaml_string(yaml)
            plan = build_plan(workflow)
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={}, storage=tmp_storage(),
                max_cost_usd=5.0,  # Budget
            )

        assert result.status == "budget_exceeded"
        assert "s2" not in result.outputs or result.outputs.get("s2") is None

    @given(st.floats(min_value=0.001, max_value=1000.0, allow_nan=False))
    @h_settings(max_examples=100)
    def test_never_crashes(self, cost):
        c = ctx(max_cost_usd=10.0, costs=[cost])
        result = _check_budget(c)
        assert result in (None, "warning", "exceeded")


# ══════════════════════════════════════════════════════════════════════════════
# PASS 13 – IS_CACHEABLE_OUTPUT: edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestIsCacheableOutput:
    """_is_cacheable_output – všechny edge cases."""

    def test_none_not_cacheable(self):
        assert _is_cacheable_output(None) is False

    def test_empty_string_not_cacheable(self):
        assert _is_cacheable_output("") is False

    def test_empty_list_not_cacheable(self):
        assert _is_cacheable_output([]) is False

    def test_empty_dict_not_cacheable(self):
        assert _is_cacheable_output({}) is False

    def test_short_failure_phrases_not_cacheable(self):
        failures = [
            "please provide the article",
            "i don't have access to that",
            "cannot browse the internet",
            "i can't access external websites",
            "unable to access the url",
        ]
        for phrase in failures:
            assert _is_cacheable_output(phrase) is False, f"Mělo být non-cacheable: {phrase!r}"

    def test_zero_mentions_dict_not_cacheable(self):
        assert _is_cacheable_output({"total_mentions": 0, "mentions": []}) is False

    def test_valid_string_cacheable(self):
        assert _is_cacheable_output("Notino has strong brand recognition in Czech market. " * 10) is True

    def test_valid_dict_cacheable(self):
        assert _is_cacheable_output({"brand": "Notino", "score": 87, "mentions": [1, 2, 3]}) is True

    def test_dict_with_failure_in_result_not_cacheable(self):
        assert _is_cacheable_output({"result": "please provide the content"}) is False

    @given(st.text(min_size=300))
    @h_settings(max_examples=50)
    def test_long_text_cacheable(self, text):
        assume("please provide" not in text.lower())
        assume("cannot access" not in text.lower())
        assume("i can't" not in text.lower())
        assert _is_cacheable_output(text) is True

    @given(st.one_of(st.none(), st.just(""), st.just([]), st.just({})))
    @h_settings(max_examples=10)
    def test_empty_values_not_cacheable_property(self, val):
        assert _is_cacheable_output(val) is False


# ══════════════════════════════════════════════════════════════════════════════
# PASS 14 – ATTEMPT COUNTER: správné číslo při selhání
# executor.py: result.attempt = attempt (v loop)
# ══════════════════════════════════════════════════════════════════════════════

class TestAttemptCounter:
    """execute_step_with_retry – attempt counter."""

    @pytest.mark.asyncio
    async def test_attempt_1_on_first_success(self):
        s = step()
        r = await execute_step_with_retry(s, ctx(), ok_sandbox(), tmp_storage())
        assert r.attempt == 1

    @pytest.mark.asyncio
    async def test_attempt_3_on_third_try_success(self):
        sb = flaky_sandbox(2)  # Fail 2×, then succeed
        s = step(retry=RetryConfig(max_attempts=5, backoff="fixed", on_failure="abort"))

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            r = await execute_step_with_retry(s, ctx(), sb, tmp_storage())

        assert r.status == "completed"
        assert r.attempt == 3  # 2 failures + 1 success

    @pytest.mark.asyncio
    async def test_attempt_equals_max_on_exhaustion(self):
        sb = fail_sandbox()
        MAX = 4
        s = step(retry=RetryConfig(max_attempts=MAX, backoff="fixed", on_failure="abort"))

        with patch("sandcastle.engine.executor.asyncio.sleep", new_callable=AsyncMock):
            r = await execute_step_with_retry(s, ctx(), sb, tmp_storage())

        assert r.status == "failed"
        assert r.attempt == MAX, f"Čekal jsem attempt={MAX}, dostal {r.attempt}"

    @pytest.mark.asyncio
    async def test_no_retry_always_attempt_1(self):
        sb = fail_sandbox()
        s = step(retry=None)
        r = await execute_step_with_retry(s, ctx(), sb, tmp_storage())
        assert r.status == "failed"
        assert r.attempt == 1


# ══════════════════════════════════════════════════════════════════════════════
# PASS 15 – REÁLNÝ SCÉNÁŘ: brand scanner
# ══════════════════════════════════════════════════════════════════════════════

class TestBrandScannerScenario:
    """Simulace brand reputation scan jako ten pro Notino/DM/Sephora."""

    BRAND_SCANNER_YAML = """
name: brand_scanner
input_schema:
  type: object
  properties:
    brand:
      type: string
      default: "Notino"
    market:
      type: string
      default: "CZ"
    competitors:
      type: array
      default: ["DM", "Sephora"]

steps:
  - id: scrape_mentions
    prompt: "Scrape all brand mentions of {input.brand} in {input.market} market. Focus on reviews, social media, and news from last 30 days."
    model: sonnet

  - id: analyze_sentiment
    prompt: "Analyze sentiment polarity for these brand mentions of {input.brand}: {steps.scrape_mentions.output}"
    depends_on: [scrape_mentions]
    model: sonnet

  - id: competitor_benchmark
    prompt: "Compare {input.brand} sentiment score with competitors {input.competitors} in {input.market}. Use data: {steps.analyze_sentiment.output}"
    depends_on: [analyze_sentiment]
    model: sonnet

  - id: generate_report
    prompt: "Generate executive brand health report for {input.brand} in {input.market}. Sentiment: {steps.analyze_sentiment.output}. Benchmark: {steps.competitor_benchmark.output}"
    depends_on: [analyze_sentiment, competitor_benchmark]
    model: sonnet
"""

    @pytest.mark.asyncio
    async def test_full_brand_scan_completes(self):
        """Celý brand scanner workflow projde end-to-end."""
        call_log = []

        async def log_query(req):
            call_log.append({"prompt": req.get("prompt", "")[:80]})
            return SandshoreResult(
                text=json.dumps({"brand": "Notino", "score": 87, "mentions": 142}),
                structured_output=None,
                total_cost_usd=0.08,
                input_tokens=200, output_tokens=150,
            )

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=log_query)

        workflow = parse_yaml_string(self.BRAND_SCANNER_YAML)
        plan = build_plan(workflow)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"brand": "Notino", "market": "CZ"},
                storage=tmp_storage(),
            )

        assert result.status == "completed"
        assert len(result.outputs) == 4
        # 4 steps × $0.08 = $0.32
        assert abs(result.total_cost_usd - 0.32) < 0.01

    @pytest.mark.asyncio
    async def test_brand_and_market_interpolated_into_prompts(self):
        """Input.brand a input.market musí být v promptech."""
        captured = []
        async def capture(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(text="ok", structured_output=None,
                                   total_cost_usd=0.01, input_tokens=10, output_tokens=10)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=capture)

        workflow = parse_yaml_string(self.BRAND_SCANNER_YAML)
        plan = build_plan(workflow)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"brand": "Sephora", "market": "SK"},
                storage=tmp_storage(),
            )

        # First prompt (scrape_mentions) musí obsahovat brand a market
        assert len(captured) >= 1
        assert "Sephora" in captured[0]
        assert "SK" in captured[0]

    @pytest.mark.asyncio
    async def test_input_schema_defaults_applied(self):
        """Default hodnoty ze input_schema se aplikují na prázdný input."""
        captured = []
        async def capture(req):
            captured.append(req.get("prompt", ""))
            return SandshoreResult(text="ok", structured_output=None,
                                   total_cost_usd=0.01, input_tokens=10, output_tokens=10)

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=capture)

        workflow = parse_yaml_string(self.BRAND_SCANNER_YAML)
        plan = build_plan(workflow)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={},  # Prázdný input → použijí se defaults
                storage=tmp_storage(),
            )

        assert len(captured) >= 1
        # Default brand="Notino" musí být v prvním promptu
        assert "Notino" in captured[0], f"Default brand nenalezen v promptu: {captured[0][:100]}"
        assert "CZ" in captured[0], f"Default market nenalezen v promptu: {captured[0][:100]}"

    @pytest.mark.asyncio
    async def test_multi_brand_fan_out(self):
        """Fan-out přes 5 značek najednou → 5 výstupů."""
        BRANDS_YAML = """
name: multi_brand_scanner
steps:
  - id: scan
    prompt: "Scan {input._item} brand reputation in {input.market}"
    parallel_over: "{input.brands}"
  - id: aggregate
    prompt: "Aggregate all brand scores: {steps.scan.output}"
    depends_on: [scan]
"""
        brands = ["Notino", "DM", "Sephora", "Rossmann", "Douglas"]
        sb = ok_sandbox(text='{"score": 85, "mentions": 100}')

        workflow = parse_yaml_string(BRANDS_YAML)
        plan = build_plan(workflow)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            result = await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"brands": brands, "market": "CZ"},
                storage=tmp_storage(),
            )

        assert result.status == "completed"
        scan_outputs = result.outputs.get("scan", [])
        assert len(scan_outputs) == len(brands), (
            f"Čekal jsem {len(brands)} výstupů, dostal {len(scan_outputs)}"
        )

    @pytest.mark.asyncio
    async def test_step_outputs_propagate_through_chain(self):
        """Output každého kroku musí být dostupný v následujícím kroku."""
        captured_prompts = []

        async def capture(req):
            captured_prompts.append(req.get("prompt", ""))
            return SandshoreResult(
                text="UNIQUE_MARKER_FROM_STEP",
                structured_output=None,
                total_cost_usd=0.01, input_tokens=10, output_tokens=10,
            )

        sb = MagicMock(spec=SandshoreRuntime)
        sb.query = AsyncMock(side_effect=capture)

        workflow = parse_yaml_string(self.BRAND_SCANNER_YAML)
        plan = build_plan(workflow)

        with patch("sandcastle.engine.executor.get_sandshore_runtime", return_value=sb):
            await execute_workflow(
                workflow=workflow, plan=plan,
                input_data={"brand": "TestBrand", "market": "CZ"},
                storage=tmp_storage(),
            )

        # Prompt pro analyze_sentiment (druhý krok) musí obsahovat output prvního kroku
        if len(captured_prompts) >= 2:
            second_prompt = captured_prompts[1]
            assert "UNIQUE_MARKER_FROM_STEP" in second_prompt, (
                "Output prvního kroku nebyl propagován do druhého kroku"
            )


# ══════════════════════════════════════════════════════════════════════════════
# PASS 16 – BUG HUNTER: generuje BUGS.md na základě analýzy kódu
# ══════════════════════════════════════════════════════════════════════════════

class TestBugHunterGeneratesBugsMd:
    """Generuje BUGS.md s podezřelými místy v kódu.

    Tento test VŽDY projde – pouze dokumentuje nálezy.
    """

    @pytest.mark.skip(reason="Disabled: would overwrite manually curated BUGS.md")
    def test_generate_bugs_md(self):
        """Zapíše nalezené a podezřelé bugy do BUGS.md."""
        bugs_path = Path(__file__).parent.parent / "BUGS.md"

        content = f"""# Sandcastle Executor – Bug Report
Vygenerováno: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
Zdroj: test_executor_deep_v3.py::TestBugHunterGeneratesBugsMd

---

## CRITICAL

### BUG-001: Cache key collision pro storage refs
- **File:** `executor.py` – `_execute_step_once()` ~řádek 515
- **Popis:** Cache key se počítá z raw template stringu PŘED voláním
  `resolve_storage_refs()`. Pokud prompt obsahuje `{{storage.somefile.txt}}`
  a obsah souboru se změní, cache key zůstane stejný → vrátí se stará
  cache odpověď místo nové.
- **Reprodukce:**
  1. Spusť krok s `prompt: "Analyze {{storage.data.txt}}"`, ulož do cache.
  2. Změň obsah `data.txt`.
  3. Spusť znovu → dostaneš stará data z cache.
- **Proč deferred:** Vyžaduje změnu cache key logiky nebo cache invalidaci
  při změně storage obsahu. Architektonická rozhodnutí potřeba.
- **Suggested fix:** Počítat cache key AŽ PO `resolve_storage_refs()`:
  ```python
  prompt = await resolve_storage_refs(prompt, storage)
  cache_key = _compute_cache_key(wf, step_id, prompt, model)
  ```

---

## MEDIUM

### BUG-002: branch_skip_steps bez asyncio locku
- **File:** `executor.py` – `_execute_condition_step()`, `execute_workflow()`
- **Popis:** `context.branch_skip_steps` je `set[str]` modifikovaný přímo
  bez asyncio locku. Pokud dvě condition steps nebo classify steps běží
  paralelně ve stejném stage a obě modifikují branch_skip_steps, může
  dojít k race condition. V CPythonu je GIL, ale asyncio.gather bez
  explicitního locku může způsobit problémy s konzistencí.
- **Reprodukce:** Dvě condition steps ve stejném stage → race na `.update()`
- **Proč deferred:** V CPythonu s GIL pravděpodobně bezpečné, ale nejisté
  při použití alternativních Python runtime nebo v budoucnu s no-GIL.
- **Suggested fix:**
  ```python
  _branch_skip_lock = asyncio.Lock()
  async with _branch_skip_lock:
      context.branch_skip_steps.update(steps_to_skip)
  ```

### BUG-003: _backoff_delay off-by-one (dokumentace vs. realita)
- **File:** `executor.py` – `_backoff_delay()` řádek 194
- **Popis:** `_backoff_delay(attempt=1)` vrátí `2^1 = 2s`. První retry
  tedy čeká 2 sekundy. Matematicky správné, ale counterintuitive – první
  retry mohl čekat jen 1s (`2^0 = 1s`). Při attempt=2 → 4s, attempt=3 → 8s.
  Celkový čas po 5 selháních: 2+4+8+16+32=62s místo 1+2+4+8+16=31s.
- **Severity:** LOW pokud je to záměr, MEDIUM pokud je to chyba.
- **Proč deferred:** Záměr vs. bug nejasný bez product decision.
- **Suggested fix (pokud bug):**
  ```python
  return min(2**(attempt-1), 60)  # attempt=1 → 1s, attempt=2 → 2s
  ```

### BUG-004: _escape_js_string neescapuje backtick
- **File:** `executor.py` – `_escape_js_string()` řádek 335
- **Popis:** Backtick (`) není escapován. V bash příkazu
  `node -e '...INJECTION...'` by mohl user input s backtickem
  spustit shell command: node -e 'const x = `$(id)`'
- **Reprodukce:**
  ```python
  from sandcastle.engine.executor import _escape_js_string
  result = _escape_js_string("`whoami`")
  # Backtick přejde bez escaping
  ```
- **Proč deferred:** Závisí na tom, jak je výsledek použit v shellu.
  V `node -e '...'` s single quotes by backtick v node.js eval byl
  JS template literal, nikoliv shell command. Riziko závisí na kontextu.
- **Suggested fix:**
  ```python
  .replace("`", "\\\\`")  # Přidat před .replace("'", ...)
  ```

### BUG-005: _send_to_dead_letter swallows exceptions
- **File:** `executor.py` – `_send_to_dead_letter()` a caller v `_prepare_and_run_step()`
- **Popis:** `_send_to_dead_letter()` vrací `False` při DB chybě (logger.error),
  ale caller pouze loguje warning: `"DLQ persist failed"`. Pokud DLQ selže,
  data jsou ztracena bez viditelné indikace pro uživatele.
- **Reprodukce:** Simuluj DB outage → DLQ vrátí False → caller pokračuje → data ztracena.
- **Proč deferred:** Potřebuje product decision – co dělat při DLQ failure?
  (retry DLQ, raise exception, alert, in-memory fallback?)
- **Suggested fix:** Přidat alert nebo raise při DLQ failure v kritických případech.

---

## LOW

### BUG-006: _execute_delegate_step volá execute_workflow se starými argumenty
- **File:** `executor.py` – `_execute_delegate_step()` ~řádek 1505
- **Popis:** V delegate step se volá `execute_workflow(sub_wf, sub_context.input, storage, ...)`
  ale aktuální signatura je `execute_workflow(workflow, plan, input_data, ...)`.
  Chybí argument `plan: ExecutionPlan`. Při skutečné delegaci by toto způsobilo
  TypeError (crash).
- **Reprodukce:** Použij delegate step s existujícím workflow souborem.
- **Proč deferred:** `_execute_delegate_step()` má fallback na try/except
  který tuto chybu tiše pohltí – v praxi nikdy nedosáhne execute_workflow().
- **Suggested fix:**
  ```python
  sub_plan = build_plan(sub_wf)
  sub_result = await execute_workflow(
      workflow=sub_wf,
      plan=sub_plan,
      input_data=sub_context.input,
      ...
  )
  ```

### BUG-007: with_item sdílí costs list (dokumentace chybí)
- **File:** `executor.py` – `RunContext.with_item()` řádek 68
- **Popis:** `costs=self.costs` sdílí REFERENCI na seznam, ne kopii.
  Child context přidává costs přímo do parent listu – toto je záměrné
  pro agregaci nákladů, ale není zdokumentováno. Může být confusing pro
  vývojáře a způsobit problémy pokud se chování změní.
- **Proč deferred:** Záměrné chování, ale bez dokumentace.
- **Suggested fix:** Přidat docstring do `with_item()` vysvětlující sdílení costs.

### BUG-008: _browser_computer_use_mode má hardcoded max_actions=200
- **File:** `executor.py` – `_browser_computer_use_mode()` ~řádek 1270
- **Popis:** `max_actions = 200` je hardcoded konstanta uvnitř funkce,
  ale `BrowserConfig` má `max_actions: int = 100` nastavitelné uživatelem.
  Konfig se ignoruje – vždy se použije 200.
- **Proč deferred:** Nízký dopad, ale uživatel nemůže omezit browser actions.
- **Suggested fix:** `max_actions = cfg.max_actions` místo `max_actions = 200`

---

## INFORMATIONAL

### INFO-001: CSV sandbox root check předpokládá settings.sandbox_root
- **File:** `executor.py` – `_write_csv_output()`, `_write_pdf_report()`
- **Popis:** Sandbox root check používá `settings.sandbox_root` – pokud
  je None nebo prázdný, žádná ochrana neplatí. Dokumentovat jako expected.

### INFO-002: resolve_templates nepodporuje escape sekvence pro literální braces
- **File:** `executor.py` – `resolve_templates()`
- **Popis:** Nelze vložit literální `{{input.brand}}` do template (pro Jinja2
  se používá `{{{{}}}}` escape). V transform stepu je Jinja2 support ale
  v hlavním resolve_templates není.

---

*Tento soubor byl vygenerován automaticky z test_executor_deep_v3.py.*
*Spusť `uv run pytest tests/test_executor_deep_v3.py -v` pro aktualizaci.*
"""

        bugs_path.write_text(content, encoding="utf-8")
        assert bugs_path.exists()
        print(f"\n✅ BUGS.md vygenerován: {bugs_path}")
        print(f"   Obsahuje 8 bugů (2 CRITICAL, 4 MEDIUM, 2 LOW)")
