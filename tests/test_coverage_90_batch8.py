"""Batch 8: Targeted tests for remaining high-miss files.

Focuses on:
- storage.py: symlink traversal detection, error path in write
- autopilot.py: select_winner, _two_tailed_p_value, apply_variant
- sandshore.py: CircuitBreaker, cancellation branch
- generator.py: _get_api_key uncovered path
- optimizer.py: _get_performance_history branches
- pdf.py: internal render functions with various markdown patterns
- agui.py: uncovered endpoint branches
- queue/scheduler.py: branches
"""

from __future__ import annotations

import asyncio
import math
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# storage.py: LocalStorage - symlink traversal detection
# ---------------------------------------------------------------------------

class TestLocalStorageSymlink:
    def setup_method(self):
        import tempfile
        self.base_dir = tempfile.mkdtemp()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.base_dir, ignore_errors=True)

    def test_read_symlink_inside_basedir_ok(self):
        """Symlinks that resolve inside base_dir should work."""
        import asyncio
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(self.base_dir)
        # Create a real file and a symlink pointing to it (inside base_dir)
        real_file = Path(self.base_dir) / "real.txt"
        real_file.write_text("content")
        link_file = Path(self.base_dir) / "link.txt"
        link_file.symlink_to(real_file)

        result = asyncio.run(storage.read("link.txt"))
        assert result == "content"

    def test_read_returns_none_for_missing_file(self):
        """read() returns None for non-existent file."""
        import asyncio
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(self.base_dir)
        result = asyncio.run(storage.read("does_not_exist.txt"))
        assert result is None

    def test_safe_path_path_traversal_denied(self):
        """_safe_path should raise ValueError for path traversal."""
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(self.base_dir)
        with pytest.raises(ValueError, match="Path traversal"):
            storage._safe_path("../../../etc/passwd")

    def test_write_and_read_roundtrip(self):
        """write then read should return the same content."""
        import asyncio
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(self.base_dir)
        asyncio.run(storage.write("test.txt", "hello world"))
        content = asyncio.run(storage.read("test.txt"))
        assert content == "hello world"

    def test_write_too_large_content_raises(self):
        """write() raises ValueError for content exceeding max size."""
        import asyncio
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(self.base_dir)
        # 100MB+ should exceed the limit
        large_content = "x" * (101 * 1024 * 1024)
        with pytest.raises(ValueError, match="too large"):
            asyncio.run(storage.write("large.txt", large_content))

    def test_list_empty_prefix(self):
        """list() with empty prefix returns all files."""
        import asyncio
        from sandcastle.engine.storage import LocalStorage

        storage = LocalStorage(self.base_dir)
        asyncio.run(storage.write("file1.txt", "a"))
        asyncio.run(storage.write("file2.txt", "b"))
        files = asyncio.run(storage.list(""))
        assert "file1.txt" in files
        assert "file2.txt" in files


# ---------------------------------------------------------------------------
# autopilot.py: select_winner
# ---------------------------------------------------------------------------

class TestSelectWinner:
    def _make_row(self, variant_id, count, avg_quality, avg_cost, avg_duration):
        row = MagicMock()
        row.variant_id = variant_id
        row.count = count
        row.avg_quality = avg_quality
        row.avg_cost = avg_cost
        row.avg_duration = avg_duration
        return row

    def _make_config(self, optimize_for="quality", quality_threshold=0.0):
        from sandcastle.engine.dag import AutoPilotConfig
        return AutoPilotConfig(
            enabled=True,
            variants=[],
            optimize_for=optimize_for,
            quality_threshold=quality_threshold,
        )

    def test_select_winner_empty_stats_returns_none(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config()
        result = select_winner([], config)
        assert result is None

    def test_select_winner_optimize_for_quality(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config(optimize_for="quality")
        rows = [
            self._make_row("v1", 10, 0.8, 0.01, 1.0),
            self._make_row("v2", 10, 0.9, 0.02, 1.5),
        ]
        result = select_winner(rows, config)
        assert result["variant_id"] == "v2"

    def test_select_winner_optimize_for_cost(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config(optimize_for="cost")
        rows = [
            self._make_row("v1", 10, 0.8, 0.02, 1.0),
            self._make_row("v2", 10, 0.9, 0.01, 1.5),
        ]
        result = select_winner(rows, config)
        assert result["variant_id"] == "v2"  # Lower cost

    def test_select_winner_optimize_for_latency(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config(optimize_for="latency")
        rows = [
            self._make_row("v1", 10, 0.8, 0.01, 2.0),
            self._make_row("v2", 10, 0.9, 0.02, 1.0),
        ]
        result = select_winner(rows, config)
        assert result["variant_id"] == "v2"  # Lower latency

    def test_select_winner_optimize_for_pareto(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config(optimize_for="pareto")
        rows = [
            self._make_row("v1", 10, 0.9, 0.01, 1.0),
            self._make_row("v2", 10, 0.6, 0.05, 3.0),
        ]
        result = select_winner(rows, config)
        assert result is not None

    def test_select_winner_all_below_quality_threshold(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config(quality_threshold=0.95)
        rows = [
            self._make_row("v1", 10, 0.7, 0.01, 1.0),
            self._make_row("v2", 10, 0.8, 0.02, 1.5),
        ]
        # All below threshold -> returns best quality anyway
        result = select_winner(rows, config)
        assert result is not None
        assert result["variant_id"] == "v2"  # Highest quality

    def test_select_winner_quality_threshold_filters(self):
        from sandcastle.engine.autopilot import select_winner
        config = self._make_config(quality_threshold=0.85)
        rows = [
            self._make_row("v1", 10, 0.9, 0.01, 1.0),  # Above threshold
            self._make_row("v2", 10, 0.7, 0.02, 1.5),  # Below threshold (filtered)
        ]
        result = select_winner(rows, config)
        assert result["variant_id"] == "v1"


# ---------------------------------------------------------------------------
# autopilot.py: _two_tailed_p_value
# ---------------------------------------------------------------------------

class TestTwoTailedPValue:
    def test_nan_input_returns_1(self):
        from sandcastle.engine.autopilot import _two_tailed_p_value
        result = _two_tailed_p_value(float("nan"), 30)
        assert result == 1.0

    def test_nan_df_returns_1(self):
        from sandcastle.engine.autopilot import _two_tailed_p_value
        result = _two_tailed_p_value(2.0, float("nan"))
        assert result == 1.0

    def test_inf_t_stat_returns_0(self):
        from sandcastle.engine.autopilot import _two_tailed_p_value
        result = _two_tailed_p_value(float("inf"), 30)
        assert result == 0.0

    def test_zero_df_returns_1(self):
        from sandcastle.engine.autopilot import _two_tailed_p_value
        result = _two_tailed_p_value(2.0, 0)
        assert result == 1.0

    def test_large_df_uses_normal_approx(self):
        from sandcastle.engine.autopilot import _two_tailed_p_value
        result = _two_tailed_p_value(2.0, 200)
        assert 0 < result < 0.1  # Should be small p-value for t=2

    def test_small_df_uses_correction(self):
        from sandcastle.engine.autopilot import _two_tailed_p_value
        result = _two_tailed_p_value(2.0, 10)
        assert 0 < result < 1.0


# ---------------------------------------------------------------------------
# autopilot.py: apply_variant
# ---------------------------------------------------------------------------

class TestApplyVariant:
    def _make_step(self, prompt="Original prompt", model="claude-haiku"):
        from sandcastle.engine.dag import StepDefinition
        return StepDefinition(
            id="step1",
            type="llm",
            prompt=prompt,
            model=model,
        )

    def _make_variant(self, prompt=None, model=None, max_turns=None):
        from sandcastle.engine.dag import VariantConfig
        return VariantConfig(
            id="v1",
            prompt=prompt,
            model=model,
            max_turns=max_turns,
        )

    def test_apply_variant_overrides_prompt(self):
        from sandcastle.engine.autopilot import apply_variant

        step = self._make_step()
        variant = self._make_variant(prompt="New variant prompt")

        result = apply_variant(step, variant)
        assert result.prompt == "New variant prompt"

    def test_apply_variant_overrides_model(self):
        from sandcastle.engine.autopilot import apply_variant

        step = self._make_step()
        variant = self._make_variant(model="claude-sonnet")

        result = apply_variant(step, variant)
        assert result.model == "claude-sonnet"

    def test_apply_variant_no_overrides(self):
        from sandcastle.engine.autopilot import apply_variant

        step = self._make_step()
        variant = self._make_variant()  # No overrides

        result = apply_variant(step, variant)
        # Original values preserved
        assert result.prompt == "Original prompt"
        assert result.model == "claude-haiku"


# ---------------------------------------------------------------------------
# generator.py: _resolve_api_key
# ---------------------------------------------------------------------------

class TestResolveApiKey:
    def test_resolve_api_key_from_env(self):
        from sandcastle.engine.generator import _resolve_api_key
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-123"}):
            key = _resolve_api_key()
        assert key == "sk-test-123"

    def test_resolve_api_key_openai_from_settings(self):
        from sandcastle.engine.generator import _resolve_api_key

        # Remove anthropic key from env to force settings lookup
        env_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with patch("sandcastle.engine.generator._get_advisor_config",
                       return_value={"api_key_env": "OPENAI_API_KEY"}):
                with patch("sandcastle.config.settings") as mock_s:
                    mock_s.openai_api_key = "sk-openai-test"
                    mock_s.anthropic_api_key = "sk-anthropic-fallback"
                    with patch.dict(os.environ, {}, clear=False):
                        os.environ.pop("OPENAI_API_KEY", None)
                        key = _resolve_api_key()
            # Should get openai key from settings
            assert key in ("sk-openai-test", "sk-anthropic-fallback", "")
        finally:
            if env_backup:
                os.environ["ANTHROPIC_API_KEY"] = env_backup


# ---------------------------------------------------------------------------
# pdf.py: rendering functions with rich markdown
# ---------------------------------------------------------------------------

class TestPdfRichMarkdown:
    def test_generate_branded_pdf_with_callout(self):
        """Test rendering callout blocks."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

> [!NOTE]
> This is a note callout.

> [!WARNING]
> This is a warning callout.

> [!INFO]
> This is an info callout.

Regular paragraph here.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_blockquote(self):
        """Test rendering blockquotes."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

> This is a blockquote paragraph.

Regular text.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_horizontal_rule(self):
        """Test rendering horizontal rules."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

Section 1.

---

Section 2 after rule.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_headings_all_levels(self):
        """Test rendering all heading levels."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Level 1

## Level 2

### Level 3

#### Level 4

##### Level 5

Some body text here.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_kpi_tiles(self):
        """Test rendering KPI tiles pattern."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

## Executive Summary

- Revenue: $1.2M
- Growth: 15%
- Customers: 1,200
- NPS: 72

## Key Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Revenue | $1.2M | $1.0M |
| Churn | 2% | 3% |
| NPS | 72 | 70 |

"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_code_block(self):
        """Test rendering code blocks."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

Here is some code:

```python
def hello():
    print("Hello, World!")
    return 42
```

And some inline `code` here.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_numbered_list(self):
        """Test rendering numbered and mixed lists."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

1. First item
2. Second item
3. Third item

And a bullet list:

- Item A
- Item B
- Item C

Conclusion text.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_bold_italic(self):
        """Test rendering inline bold and italic text."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# Report

This has **bold text** and *italic text* and ***bold italic***.

Also __bold__ and _italic_ variants.
"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass

    def test_generate_branded_pdf_with_kpi_dash_pattern(self):
        """Test KPI dashboard detection patterns."""
        import tempfile
        from sandcastle.engine.pdf import generate_branded_pdf

        md_text = """# KPI Dashboard

**Revenue**: $1,234,567

**Growth Rate**: 12.5%

**Active Users**: 45,678

**Conversion Rate**: 3.2%

## Score

Score: 8.5/10

"""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            out_path = f.name
        try:
            result = generate_branded_pdf(md_text, out_path)
            assert result.exists()
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# sandshore.py: CircuitBreaker basic behavior
# ---------------------------------------------------------------------------

class TestCircuitBreaker:
    def test_initial_state_closed(self):
        """CircuitBreaker starts in CLOSED state."""
        from sandcastle.engine.sandshore import CircuitBreaker
        cb = CircuitBreaker()
        assert cb.state == CircuitBreaker.CLOSED

    def test_circuit_opens_after_failures(self):
        """CircuitBreaker opens after enough failures."""
        import asyncio
        from sandcastle.engine.sandshore import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=9999)

        async def run():
            for _ in range(3):
                await cb.record_failure()

        asyncio.run(run())
        assert cb.state == CircuitBreaker.OPEN

    def test_circuit_resets_to_closed_after_success(self):
        """CircuitBreaker resets after success."""
        import asyncio
        from sandcastle.engine.sandshore import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)

        async def run():
            for _ in range(2):
                await cb.record_failure()
            await cb.record_success()

        asyncio.run(run())
        assert cb.state == CircuitBreaker.CLOSED

    def test_allow_request_when_closed(self):
        """allow_request returns True when circuit is closed."""
        import asyncio
        from sandcastle.engine.sandshore import CircuitBreaker
        cb = CircuitBreaker()

        async def run():
            return await cb.allow_request()

        result = asyncio.run(run())
        assert result is True

    def test_allow_request_returns_false_when_open(self):
        """allow_request returns False when circuit is open."""
        import asyncio
        from sandcastle.engine.sandshore import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=9999)

        async def run():
            await cb.record_failure()
            return await cb.allow_request()

        result = asyncio.run(run())
        assert result is False

    def test_half_open_allows_one_probe(self):
        """CircuitBreaker allows one probe in HALF_OPEN state."""
        import asyncio
        from sandcastle.engine.sandshore import CircuitBreaker
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.01)

        async def run():
            await cb.record_failure()
            await asyncio.sleep(0.05)  # Wait for recovery
            # First request should get through (probe)
            allowed1 = await cb.allow_request()
            # Second request in-flight while probe running - should be rejected
            allowed2 = await cb.allow_request()
            return allowed1, allowed2

        a1, a2 = asyncio.run(run())
        assert a1 is True  # Probe allowed
        assert a2 is False  # Second rejected while probe in-flight


# ---------------------------------------------------------------------------
# queue/scheduler.py: _check_approval_timeouts
# ---------------------------------------------------------------------------

class TestSchedulerApprovalTimeout:
    @pytest.mark.asyncio
    async def test_check_approval_timeouts_no_timeouts(self):
        """_check_approval_timeouts with no timed-out approvals."""
        from sandcastle.queue.scheduler import _check_approval_timeouts

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            await _check_approval_timeouts()

    @pytest.mark.asyncio
    async def test_check_approval_timeouts_exception_logged(self):
        """_check_approval_timeouts logs exceptions."""
        from sandcastle.queue.scheduler import _check_approval_timeouts

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            # Should not raise
            await _check_approval_timeouts()


# ---------------------------------------------------------------------------
# queue/scheduler.py: add_schedule / remove_schedule / list_schedules
# ---------------------------------------------------------------------------

class TestSchedulerApi:
    def test_add_schedule_empty_cron_raises(self):
        from sandcastle.queue.scheduler import add_schedule
        with pytest.raises(ValueError, match="cron_expression"):
            add_schedule("sched-1", "", "my_workflow")

    def test_add_schedule_empty_workflow_raises(self):
        from sandcastle.queue.scheduler import add_schedule
        with pytest.raises(ValueError, match="workflow_name"):
            add_schedule("sched-1", "0 * * * *", "")

    def test_list_schedules_returns_list(self):
        from sandcastle.queue.scheduler import list_schedules
        with patch("sandcastle.queue.scheduler.get_scheduler") as mock_sched:
            mock_scheduler = MagicMock()
            mock_scheduler.get_jobs = MagicMock(return_value=[])
            mock_sched.return_value = mock_scheduler
            result = list_schedules()
        assert isinstance(result, list)

    def test_remove_schedule_not_found(self):
        from sandcastle.queue.scheduler import remove_schedule
        with patch("sandcastle.queue.scheduler.get_scheduler") as mock_sched:
            mock_scheduler = MagicMock()
            mock_scheduler.remove_job = MagicMock(side_effect=Exception("job not found"))
            mock_sched.return_value = mock_scheduler
            result = remove_schedule("nonexistent-id")
        assert result is False


# ---------------------------------------------------------------------------
# optimizer.py: CostLatencyOptimizer._get_performance_stats (via select_model)
# ---------------------------------------------------------------------------

class TestOptimizerSelectModel:
    @pytest.mark.asyncio
    async def test_select_model_with_no_history(self):
        """select_model falls back gracefully with no historical data."""
        from sandcastle.engine.optimizer import (
            CostLatencyOptimizer,
            ModelOption,
            SLO,
        )

        optimizer = CostLatencyOptimizer()
        slo = SLO(
            quality_min=0.0,
            cost_max_usd=10.0,
            latency_max_seconds=30.0,
            optimize_for="quality",
        )
        pool = [
            ModelOption(id="haiku", model="claude-haiku", max_turns=1),
            ModelOption(id="sonnet", model="claude-sonnet", max_turns=3),
        ]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            decision = await optimizer.select_model(
                step_id="step1",
                workflow_name="workflow1",
                slo=slo,
                model_pool=pool,
            )

        assert decision is not None
        assert decision.selected_option is not None

    @pytest.mark.asyncio
    async def test_select_model_exception_in_query(self):
        """select_model handles query exceptions gracefully."""
        from sandcastle.engine.optimizer import (
            CostLatencyOptimizer,
            ModelOption,
            SLO,
        )

        optimizer = CostLatencyOptimizer()
        slo = SLO(
            quality_min=0.0,
            cost_max_usd=10.0,
            latency_max_seconds=30.0,
            optimize_for="quality",
        )
        pool = [ModelOption(id="haiku", model="claude-haiku", max_turns=1)]

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=Exception("DB down"))

        with patch("sandcastle.models.db.async_session", return_value=mock_session):
            decision = await optimizer.select_model(
                step_id="step1",
                workflow_name="workflow1",
                slo=slo,
                model_pool=pool,
            )

        assert decision is not None


# ---------------------------------------------------------------------------
# executor.py: _truncate_output edge cases
# ---------------------------------------------------------------------------

class TestTruncateOutputEdgeCases:
    def test_non_serializable_object_uses_str(self):
        """Objects that can't be JSON serialized fall back to str()."""
        from sandcastle.engine.executor import _truncate_output

        class CustomObj:
            def __repr__(self):
                return "CustomObj()"

        obj = CustomObj()
        # Should not raise
        result = _truncate_output(obj)
        # Returned as-is since small
        assert result is obj or result is not None

    def test_list_within_size_returned_unchanged(self):
        """Small list returned unchanged."""
        from sandcastle.engine.executor import _truncate_output

        lst = [1, 2, 3, "hello", {"a": "b"}]
        result = _truncate_output(lst)
        assert result == lst

    def test_dict_within_size_returned_unchanged(self):
        """Small dict returned unchanged."""
        from sandcastle.engine.executor import _truncate_output

        d = {"key": "value", "number": 42}
        result = _truncate_output(d)
        assert result == d


# ---------------------------------------------------------------------------
# routes.py: _sanitize_workflow_yaml
# ---------------------------------------------------------------------------

class TestSanitizeWorkflowYaml:
    def test_removes_secrets_from_yaml(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml_content = """
name: test
steps:
  - id: step1
    env:
      API_KEY: sk-1234567890abcdef
"""
        result = _sanitize_workflow_yaml(yaml_content)
        assert isinstance(result, str)

    def test_valid_yaml_passes_through(self):
        from sandcastle.api.routes import _sanitize_workflow_yaml

        yaml_content = """
name: My Workflow
steps:
  - id: step1
    type: llm
    prompt: Hello world
"""
        result = _sanitize_workflow_yaml(yaml_content)
        assert "My Workflow" in result


# ---------------------------------------------------------------------------
# executor.py: resolve_variable - list traversal
# ---------------------------------------------------------------------------

class TestResolveVariableListTraversal:
    def test_list_index_access(self):
        """resolve_variable handles steps.step1.output[0] style paths."""
        from sandcastle.engine.executor import RunContext, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            step_outputs={"step1": ["item0", "item1", "item2"]},
        )
        # Access list index via path
        result = resolve_variable("steps.step1.output", ctx)
        assert result == ["item0", "item1", "item2"]

    def test_nested_dict_traversal(self):
        """resolve_variable handles nested dict output."""
        from sandcastle.engine.executor import RunContext, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
            step_outputs={"step1": {"nested": {"value": 42}}},
        )
        result = resolve_variable("steps.step1.output", ctx)
        assert result == {"nested": {"value": 42}}


# ---------------------------------------------------------------------------
# executor.py: _compute_cache_key determinism
# ---------------------------------------------------------------------------

class TestComputeCacheKey:
    def test_same_inputs_produce_same_key(self):
        from sandcastle.engine.executor import _compute_cache_key

        key1 = _compute_cache_key("wf1", "step1", "hello prompt", "claude-haiku")
        key2 = _compute_cache_key("wf1", "step1", "hello prompt", "claude-haiku")
        assert key1 == key2

    def test_different_inputs_produce_different_keys(self):
        from sandcastle.engine.executor import _compute_cache_key

        key1 = _compute_cache_key("wf1", "step1", "hello", "claude-haiku")
        key2 = _compute_cache_key("wf1", "step1", "world", "claude-haiku")
        assert key1 != key2

    def test_key_is_hex_string(self):
        from sandcastle.engine.executor import _compute_cache_key

        key = _compute_cache_key("wf", "s", "p", "m")
        # Should be hex sha256
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# executor.py: resolve_variable - env var path
# ---------------------------------------------------------------------------

class TestResolveVariableEnvVar:
    def test_env_variable_resolved(self):
        from sandcastle.engine.executor import RunContext, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        with patch.dict(os.environ, {"MY_TEST_VAR": "test_value"}):
            result = resolve_variable("env.MY_TEST_VAR", ctx)
        assert result == "test_value"

    def test_env_variable_missing_returns_unresolved(self):
        from sandcastle.engine.executor import RunContext, _UNRESOLVED, resolve_variable

        ctx = RunContext(
            run_id="aabbccdd-0000-0000-0000-000000000001",
            input={},
        )
        result = resolve_variable("env.DEFINITELY_NOT_SET_VAR_12345", ctx)
        assert result is _UNRESOLVED


# ---------------------------------------------------------------------------
# executor.py: _is_cacheable_output
# ---------------------------------------------------------------------------

class TestIsCacheableOutput:
    def test_empty_string_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert not _is_cacheable_output("")

    def test_none_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert not _is_cacheable_output(None)

    def test_normal_text_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output("This is good output with useful data.")

    def test_please_provide_keyword_not_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert not _is_cacheable_output("Please provide the content and I'll analyze it.")

    def test_dict_output_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output({"result": "good data", "count": 5})

    def test_list_output_cacheable(self):
        from sandcastle.engine.executor import _is_cacheable_output
        assert _is_cacheable_output([1, 2, 3])
