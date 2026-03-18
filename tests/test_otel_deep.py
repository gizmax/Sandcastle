"""Deep tests for OpenTelemetry instrumentation in sandcastle/engine/otel.py.

Covers:
1. No-dep graceful degradation (ImportError for opentelemetry)
2. Full lifecycle mock (TracerProvider, spans, attributes)
3. Thread safety (5 concurrent init_otel calls)
4. Span attribute types (all must be str for OTLP)
5. Config validation (otel_enabled=True but empty endpoint)
"""

from __future__ import annotations

import concurrent.futures
import sys
import threading
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

# Always reset module-level _tracer state before and after tests
import sandcastle.engine.otel as otel_module


@pytest.fixture(autouse=True)
def reset_tracer():
    """Reset the module-level _tracer before and after each test."""
    otel_module._reset()
    yield
    otel_module._reset()


# ---------------------------------------------------------------------------
# Part 1: No-dep graceful degradation
# ---------------------------------------------------------------------------

class TestNoDependencyDegradation:
    """All otel functions must be no-ops when opentelemetry is not installed."""

    def test_init_otel_import_error_no_crash(self):
        """init_otel silently degrades when opentelemetry is not installed."""
        with patch.dict(sys.modules, {
            "opentelemetry": None,
            "opentelemetry.trace": None,
            "opentelemetry.exporter": None,
            "opentelemetry.exporter.otlp": None,
            "opentelemetry.exporter.otlp.proto": None,
            "opentelemetry.exporter.otlp.proto.http": None,
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": None,
            "opentelemetry.sdk": None,
            "opentelemetry.sdk.resources": None,
            "opentelemetry.sdk.trace": None,
            "opentelemetry.sdk.trace.export": None,
        }):
            # Should not raise
            otel_module.init_otel("sandcastle-test", "http://localhost:4318")
        # _tracer remains None after failed init
        assert otel_module._tracer is None

    def test_workflow_span_no_tracer_no_crash(self):
        """workflow_span yields None when _tracer is None."""
        assert otel_module._tracer is None
        with otel_module.workflow_span("run-1", "my-workflow", extra="val") as span:
            assert span is None

    def test_step_span_no_tracer_no_crash(self):
        """step_span yields None when _tracer is None."""
        assert otel_module._tracer is None
        with otel_module.step_span("run-1", "step-1", "llm", model="sonnet") as span:
            assert span is None

    def test_record_step_result_no_span_no_crash(self):
        """record_step_result is a no-op when span is None."""
        # Should not raise
        otel_module.record_step_result(
            span=None,
            status="completed",
            cost=0.01,
            duration=1.5,
            tokens_in=100,
            tokens_out=200,
        )

    def test_workflow_span_body_executes_without_tracer(self):
        """Code inside workflow_span context manager executes normally."""
        executed = []
        with otel_module.workflow_span("run-2", "wf") as span:
            executed.append("ran")
            assert span is None
        assert executed == ["ran"]

    def test_step_span_body_executes_without_tracer(self):
        """Code inside step_span context manager executes normally."""
        executed = []
        with otel_module.step_span("run-2", "s-1", "bash") as span:
            executed.append("ran")
            assert span is None
        assert executed == ["ran"]


# ---------------------------------------------------------------------------
# Part 2: Full lifecycle mock
# ---------------------------------------------------------------------------

class TestFullLifecycleMock:
    """Verify the full OTEL lifecycle when deps are present."""

    def _build_mocks(self):
        """Build a complete mock hierarchy for opentelemetry SDK."""
        mock_span = MagicMock()
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        mock_trace_module = MagicMock()
        mock_trace_module.get_tracer.return_value = mock_tracer

        mock_resource = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create.return_value = mock_resource

        mock_provider = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)

        mock_exporter = MagicMock()
        mock_exporter_cls = MagicMock(return_value=mock_exporter)

        mock_processor = MagicMock()
        mock_processor_cls = MagicMock(return_value=mock_processor)

        return {
            "span": mock_span,
            "tracer": mock_tracer,
            "trace": mock_trace_module,
            "resource_cls": mock_resource_cls,
            "resource": mock_resource,
            "provider_cls": mock_provider_cls,
            "provider": mock_provider,
            "exporter_cls": mock_exporter_cls,
            "exporter": mock_exporter,
            "processor_cls": mock_processor_cls,
            "processor": mock_processor,
        }

    def test_init_creates_tracer_provider(self):
        """init_otel creates a TracerProvider and registers it."""
        mocks = self._build_mocks()

        # Patch via import inside init_otel
        with patch.dict("sys.modules", {
            "opentelemetry": MagicMock(trace=mocks["trace"]),
            "opentelemetry.trace": mocks["trace"],
            "opentelemetry.sdk.resources": MagicMock(Resource=mocks["resource_cls"]),
            "opentelemetry.sdk.trace": MagicMock(TracerProvider=mocks["provider_cls"]),
            "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=mocks["processor_cls"]),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                OTLPSpanExporter=mocks["exporter_cls"]
            ),
        }):
            otel_module.init_otel("sandcastle", "http://otel-collector:4318")

        # Tracer should be set
        assert otel_module._tracer is not None

    def test_workflow_span_correct_attributes(self):
        """workflow_span creates span with run_id, workflow name and extra attrs."""
        mocks = self._build_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.workflow_span("run-abc", "extract-data", env="prod") as span:
            assert span is mocks["span"]

        mocks["tracer"].start_as_current_span.assert_called_once()
        call_kwargs = mocks["tracer"].start_as_current_span.call_args
        span_name = call_kwargs[0][0]
        assert span_name == "workflow:extract-data"
        attrs = call_kwargs[1]["attributes"]
        assert attrs["sandcastle.run_id"] == "run-abc"
        assert attrs["sandcastle.workflow"] == "extract-data"
        assert attrs["sandcastle.env"] == "prod"

    def test_step_span_nests_with_model_attribute(self):
        """step_span creates span with run_id, step_id, step_type, and model."""
        mocks = self._build_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-abc", "step-1", "llm", model="claude-sonnet") as span:
            assert span is mocks["span"]

        call_kwargs = mocks["tracer"].start_as_current_span.call_args
        span_name = call_kwargs[0][0]
        assert span_name == "step:step-1"
        attrs = call_kwargs[1]["attributes"]
        assert attrs["sandcastle.run_id"] == "run-abc"
        assert attrs["sandcastle.step_id"] == "step-1"
        assert attrs["sandcastle.step_type"] == "llm"
        assert attrs["sandcastle.model"] == "claude-sonnet"

    def test_step_span_no_model_omits_model_attr(self):
        """step_span omits model attribute when model is None."""
        mocks = self._build_mocks()
        otel_module._tracer = mocks["tracer"]

        with otel_module.step_span("run-1", "s-1", "bash", model=None) as span:
            pass

        attrs = mocks["tracer"].start_as_current_span.call_args[1]["attributes"]
        assert "sandcastle.model" not in attrs

    def test_record_step_result_sets_all_attributes(self):
        """record_step_result sets all expected attributes on the span."""
        mock_span = MagicMock()
        otel_module.record_step_result(
            span=mock_span,
            status="completed",
            cost=0.042,
            duration=3.14,
            tokens_in=512,
            tokens_out=1024,
        )
        calls = {c[0][0]: c[0][1] for c in mock_span.set_attribute.call_args_list}
        assert calls["sandcastle.status"] == "completed"
        assert calls["sandcastle.cost_usd"] == 0.042
        assert calls["sandcastle.duration_s"] == 3.14
        assert calls["sandcastle.tokens_in"] == 512
        assert calls["sandcastle.tokens_out"] == 1024

    def test_record_step_result_skips_zero_tokens(self):
        """record_step_result does not set tokens_in/out when they are 0."""
        mock_span = MagicMock()
        otel_module.record_step_result(
            span=mock_span,
            status="failed",
            cost=0.0,
            duration=0.1,
            tokens_in=0,
            tokens_out=0,
        )
        attr_names = [c[0][0] for c in mock_span.set_attribute.call_args_list]
        assert "sandcastle.tokens_in" not in attr_names
        assert "sandcastle.tokens_out" not in attr_names


# ---------------------------------------------------------------------------
# Part 3: Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    """init_otel called from multiple threads must not cause errors."""

    def test_concurrent_init_otel_no_race(self):
        """5 concurrent init_otel calls should all complete without exception."""
        errors = []

        def call_init():
            try:
                # Without real otel deps this will hit the ImportError path
                # which is the safe degradation - just no crash
                otel_module.init_otel("sandcastle", "http://localhost:4318")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_init) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

    def test_concurrent_init_with_mock_otel(self):
        """5 threads calling init_otel with mocked SDK all succeed."""
        errors = []
        results = []

        def build_mocks():
            mock_span = MagicMock()
            mock_tracer = MagicMock()
            mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
            mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)
            mock_trace = MagicMock()
            mock_trace.get_tracer.return_value = mock_tracer
            return mock_trace, mock_tracer

        mock_trace, _ = build_mocks()

        def call_init():
            try:
                with patch.dict("sys.modules", {
                    "opentelemetry": MagicMock(trace=mock_trace),
                    "opentelemetry.trace": mock_trace,
                    "opentelemetry.sdk.resources": MagicMock(Resource=MagicMock()),
                    "opentelemetry.sdk.trace": MagicMock(TracerProvider=MagicMock()),
                    "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=MagicMock()),
                    "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                        OTLPSpanExporter=MagicMock()
                    ),
                }):
                    otel_module.init_otel("sandcastle", "http://localhost:4318")
                results.append(True)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_init) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"
        assert len(results) == 5

    def test_concurrent_span_creation_no_crash(self):
        """Multiple threads creating spans concurrently must not crash."""
        mock_tracer = MagicMock()
        mock_span = MagicMock()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=mock_span)
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx_mgr

        otel_module._tracer = mock_tracer

        errors = []

        def use_spans():
            try:
                with otel_module.workflow_span(f"run-{threading.get_ident()}", "wf"):
                    with otel_module.step_span(f"run-{threading.get_ident()}", "s", "llm"):
                        pass
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=use_spans) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Span errors: {errors}"


# ---------------------------------------------------------------------------
# Part 4: Span attribute types
# ---------------------------------------------------------------------------

class TestSpanAttributeTypes:
    """OTLP requires all custom attributes to be strings (or numeric primitives)."""

    def test_workflow_span_extra_attrs_are_strings(self):
        """Extra kwargs passed to workflow_span are converted to str."""
        mock_tracer = MagicMock()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=MagicMock())
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx_mgr
        otel_module._tracer = mock_tracer

        with otel_module.workflow_span("run-1", "wf", count=42, flag=True, ratio=3.14):
            pass

        attrs = mock_tracer.start_as_current_span.call_args[1]["attributes"]
        # run_id and workflow are always str
        assert isinstance(attrs["sandcastle.run_id"], str)
        assert isinstance(attrs["sandcastle.workflow"], str)
        # Extra attrs must also be str
        assert isinstance(attrs["sandcastle.count"], str)
        assert attrs["sandcastle.count"] == "42"
        assert isinstance(attrs["sandcastle.flag"], str)
        assert attrs["sandcastle.flag"] == "True"
        assert isinstance(attrs["sandcastle.ratio"], str)
        assert attrs["sandcastle.ratio"] == "3.14"

    def test_step_span_extra_attrs_are_strings(self):
        """Extra kwargs passed to step_span are converted to str."""
        mock_tracer = MagicMock()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=MagicMock())
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx_mgr
        otel_module._tracer = mock_tracer

        with otel_module.step_span("run-1", "s-1", "llm", model="sonnet", retry_count=2):
            pass

        attrs = mock_tracer.start_as_current_span.call_args[1]["attributes"]
        assert isinstance(attrs["sandcastle.retry_count"], str)
        assert attrs["sandcastle.retry_count"] == "2"

    def test_step_span_core_attrs_are_strings(self):
        """Core step_span attributes (run_id, step_id, step_type) are str."""
        mock_tracer = MagicMock()
        ctx_mgr = MagicMock()
        ctx_mgr.__enter__ = MagicMock(return_value=MagicMock())
        ctx_mgr.__exit__ = MagicMock(return_value=False)
        mock_tracer.start_as_current_span.return_value = ctx_mgr
        otel_module._tracer = mock_tracer

        with otel_module.step_span("run-xyz", "step-abc", "bash"):
            pass

        attrs = mock_tracer.start_as_current_span.call_args[1]["attributes"]
        for key in ("sandcastle.run_id", "sandcastle.step_id", "sandcastle.step_type"):
            assert isinstance(attrs[key], str), f"{key} must be str"


# ---------------------------------------------------------------------------
# Part 5: Config validation edge cases
# ---------------------------------------------------------------------------

class TestConfigValidation:
    """Edge cases around init_otel configuration."""

    def test_empty_endpoint_does_not_crash(self):
        """init_otel with empty endpoint string should not raise (degrades gracefully)."""
        # With empty endpoint the OTLPSpanExporter will receive an empty URL.
        # The init may fail on exporter creation - but must not propagate the exception.
        # We mock the SDK to make it raise to simulate this
        mock_exporter_cls = MagicMock(side_effect=ValueError("Empty endpoint"))
        mock_trace = MagicMock()

        with patch.dict("sys.modules", {
            "opentelemetry": MagicMock(trace=mock_trace),
            "opentelemetry.trace": mock_trace,
            "opentelemetry.sdk.resources": MagicMock(Resource=MagicMock()),
            "opentelemetry.sdk.trace": MagicMock(TracerProvider=MagicMock()),
            "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=MagicMock()),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                OTLPSpanExporter=mock_exporter_cls
            ),
        }):
            # Should not raise - wraps in try/except Exception
            otel_module.init_otel("sandcastle", "")

    def test_init_otel_with_special_service_name(self):
        """init_otel handles service names with special characters."""
        # Should not raise even if otel is not installed
        otel_module.init_otel("my-service/v2.0 (test)", "http://localhost:4318")
        # Still None because otel not installed
        assert otel_module._tracer is None

    def test_reset_clears_tracer(self):
        """_reset() sets _tracer back to None."""
        mock_tracer = MagicMock()
        otel_module._tracer = mock_tracer
        assert otel_module._tracer is not None
        otel_module._reset()
        assert otel_module._tracer is None

    def test_multiple_init_calls_override_tracer(self):
        """Multiple init_otel calls replace the previous tracer."""
        mock_tracer1 = MagicMock()
        mock_tracer2 = MagicMock()

        mock_trace1 = MagicMock()
        mock_trace1.get_tracer.return_value = mock_tracer1

        mock_trace2 = MagicMock()
        mock_trace2.get_tracer.return_value = mock_tracer2

        # First init
        with patch.dict("sys.modules", {
            "opentelemetry": MagicMock(trace=mock_trace1),
            "opentelemetry.trace": mock_trace1,
            "opentelemetry.sdk.resources": MagicMock(Resource=MagicMock()),
            "opentelemetry.sdk.trace": MagicMock(TracerProvider=MagicMock()),
            "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=MagicMock()),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                OTLPSpanExporter=MagicMock()
            ),
        }):
            otel_module.init_otel("svc", "http://host1:4318")

        first_tracer = otel_module._tracer

        # Second init replaces it
        with patch.dict("sys.modules", {
            "opentelemetry": MagicMock(trace=mock_trace2),
            "opentelemetry.trace": mock_trace2,
            "opentelemetry.sdk.resources": MagicMock(Resource=MagicMock()),
            "opentelemetry.sdk.trace": MagicMock(TracerProvider=MagicMock()),
            "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=MagicMock()),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                OTLPSpanExporter=MagicMock()
            ),
        }):
            otel_module.init_otel("svc", "http://host2:4318")

        second_tracer = otel_module._tracer
        # The tracer should be whatever the second mock_trace2 returned
        assert second_tracer is mock_tracer2
