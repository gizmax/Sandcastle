"""Tests for OpenTelemetry instrumentation module."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_otel():
    """Reset the otel module's global tracer state."""
    import sandcastle.engine.otel as otel_mod
    otel_mod._reset()


@pytest.fixture(autouse=True)
def _reset_tracer():
    """Ensure tracer is reset before and after each test."""
    _reset_otel()
    yield
    _reset_otel()


# ---------------------------------------------------------------------------
# init_otel
# ---------------------------------------------------------------------------


class TestInitOtel:
    def test_no_op_when_import_error(self):
        """init_otel is a no-op when opentelemetry packages are not installed."""
        import sandcastle.engine.otel as otel_mod

        with patch("builtins.__import__", side_effect=ImportError("no otel")):
            # Should not raise
            otel_mod.init_otel("sandcastle", "http://localhost:4318")

        # Tracer must remain None
        assert otel_mod._tracer is None

    def test_no_op_on_generic_exception(self):
        """init_otel swallows any unexpected exception during setup."""
        import sandcastle.engine.otel as otel_mod

        broken_trace = MagicMock()
        broken_trace.set_tracer_provider = MagicMock(side_effect=RuntimeError("boom"))

        fake_modules = {
            "opentelemetry": MagicMock(),
            "opentelemetry.trace": broken_trace,
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.trace": MagicMock(),
            "opentelemetry.sdk.trace.export": MagicMock(),
            "opentelemetry.exporter": MagicMock(),
            "opentelemetry.exporter.otlp": MagicMock(),
            "opentelemetry.exporter.otlp.proto": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(),
            "opentelemetry.sdk.resources": MagicMock(),
        }
        with patch.dict(sys.modules, fake_modules):
            # Patch the import inside init_otel to use broken_trace
            with patch("opentelemetry.trace", broken_trace):
                # Should not raise
                otel_mod.init_otel("sandcastle", "http://localhost:4318")

        assert otel_mod._tracer is None

    def test_initializes_tracer_with_valid_deps(self):
        """init_otel sets _tracer when all deps are available."""
        import sandcastle.engine.otel as otel_mod

        mock_tracer = MagicMock()
        mock_trace = MagicMock()
        mock_trace.get_tracer.return_value = mock_tracer

        mock_provider = MagicMock()
        mock_provider_cls = MagicMock(return_value=mock_provider)
        mock_exporter = MagicMock()
        mock_exporter_cls = MagicMock(return_value=mock_exporter)
        mock_processor_cls = MagicMock()
        mock_resource = MagicMock()
        mock_resource_cls = MagicMock()
        mock_resource_cls.create = MagicMock(return_value=mock_resource)

        fake_modules = {
            "opentelemetry": MagicMock(trace=mock_trace),
            "opentelemetry.trace": mock_trace,
            "opentelemetry.sdk": MagicMock(),
            "opentelemetry.sdk.trace": MagicMock(TracerProvider=mock_provider_cls),
            "opentelemetry.sdk.trace.export": MagicMock(BatchSpanProcessor=mock_processor_cls),
            "opentelemetry.exporter": MagicMock(),
            "opentelemetry.exporter.otlp": MagicMock(),
            "opentelemetry.exporter.otlp.proto": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http": MagicMock(),
            "opentelemetry.exporter.otlp.proto.http.trace_exporter": MagicMock(
                OTLPSpanExporter=mock_exporter_cls
            ),
            "opentelemetry.sdk.resources": MagicMock(Resource=mock_resource_cls),
        }

        with patch.dict(sys.modules, fake_modules):
            otel_mod.init_otel("sandcastle", "http://localhost:4318")

        assert otel_mod._tracer is mock_tracer


# ---------------------------------------------------------------------------
# workflow_span
# ---------------------------------------------------------------------------


class TestWorkflowSpan:
    def test_yields_none_when_tracer_not_initialized(self):
        """workflow_span yields None when no tracer is active."""
        from sandcastle.engine.otel import workflow_span

        with workflow_span("run-1", "test-workflow") as span:
            assert span is None

    def test_yields_none_with_extra_attrs(self):
        """workflow_span accepts keyword attrs without crashing when disabled."""
        from sandcastle.engine.otel import workflow_span

        with workflow_span("run-1", "test-workflow", depth=0, backend="local") as span:
            assert span is None

    def test_yields_real_span_when_tracer_set(self):
        """workflow_span delegates to the tracer when initialized."""
        import sandcastle.engine.otel as otel_mod

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_span)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_ctx

        otel_mod._tracer = mock_tracer
        try:
            with otel_mod.workflow_span("run-2", "my-workflow") as span:
                assert span is mock_span
        finally:
            otel_mod._tracer = None


# ---------------------------------------------------------------------------
# step_span
# ---------------------------------------------------------------------------


class TestStepSpan:
    def test_yields_none_when_tracer_not_initialized(self):
        """step_span yields None when no tracer is active."""
        from sandcastle.engine.otel import step_span

        with step_span("run-1", "step-a", "prompt") as span:
            assert span is None

    def test_yields_none_without_model(self):
        """step_span works without the optional model argument."""
        from sandcastle.engine.otel import step_span

        with step_span("run-1", "step-b", "http") as span:
            assert span is None

    def test_yields_none_with_model_and_extra_attrs(self):
        """step_span accepts model and extra kwargs without crashing when disabled."""
        from sandcastle.engine.otel import step_span

        with step_span("run-1", "step-c", "prompt", model="claude-3", attempt=1) as span:
            assert span is None

    def test_yields_real_span_when_tracer_set(self):
        """step_span delegates to the tracer when initialized."""
        import sandcastle.engine.otel as otel_mod

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_span)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_ctx

        otel_mod._tracer = mock_tracer
        try:
            with otel_mod.step_span("run-2", "step-x", "prompt", model="claude-3") as span:
                assert span is mock_span
        finally:
            otel_mod._tracer = None

    def test_span_name_includes_step_id(self):
        """step_span names the span with the step_id."""
        import sandcastle.engine.otel as otel_mod

        mock_span = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_span)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_ctx

        otel_mod._tracer = mock_tracer
        try:
            with otel_mod.step_span("run-3", "my-step", "code"):
                pass
            call_name = mock_tracer.start_as_current_span.call_args[0][0]
            assert call_name == "step:my-step"
        finally:
            otel_mod._tracer = None


# ---------------------------------------------------------------------------
# record_step_result
# ---------------------------------------------------------------------------


class TestRecordStepResult:
    def test_no_op_with_none_span(self):
        """record_step_result does nothing when span is None."""
        from sandcastle.engine.otel import record_step_result

        # Should not raise
        record_step_result(None, "completed", cost=0.01, duration=1.5)

    def test_no_op_with_none_span_all_args(self):
        """record_step_result accepts all optional arguments with None span."""
        from sandcastle.engine.otel import record_step_result

        record_step_result(
            None,
            status="failed",
            cost=0.05,
            duration=3.2,
            tokens_in=100,
            tokens_out=200,
        )

    def test_sets_attributes_on_real_span(self):
        """record_step_result writes all fields to a real span."""
        from sandcastle.engine.otel import record_step_result

        mock_span = MagicMock()
        record_step_result(mock_span, "completed", cost=0.02, duration=2.0, tokens_in=50, tokens_out=80)

        calls = {c.args[0]: c.args[1] for c in mock_span.set_attribute.call_args_list}
        assert calls["sandcastle.status"] == "completed"
        assert calls["sandcastle.cost_usd"] == 0.02
        assert calls["sandcastle.duration_s"] == 2.0
        assert calls["sandcastle.tokens_in"] == 50
        assert calls["sandcastle.tokens_out"] == 80

    def test_skips_zero_tokens(self):
        """record_step_result omits tokens_in/tokens_out when both are zero."""
        from sandcastle.engine.otel import record_step_result

        mock_span = MagicMock()
        record_step_result(mock_span, "completed")

        attr_names = [c.args[0] for c in mock_span.set_attribute.call_args_list]
        assert "sandcastle.tokens_in" not in attr_names
        assert "sandcastle.tokens_out" not in attr_names


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestOtelConfig:
    def test_otel_enabled_default_false(self):
        """settings.otel_enabled defaults to False."""
        from sandcastle.config import settings

        assert settings.otel_enabled is False

    def test_otel_endpoint_default_empty(self):
        """settings.otel_endpoint defaults to empty string."""
        from sandcastle.config import settings

        assert settings.otel_endpoint == ""

    def test_otel_service_name_default(self):
        """settings.otel_service_name defaults to 'sandcastle'."""
        from sandcastle.config import settings

        assert settings.otel_service_name == "sandcastle"

    def test_otel_fields_are_env_configurable(self):
        """OTEL settings can be overridden via environment variables."""
        from sandcastle.config import Settings

        s = Settings(
            otel_enabled=True,
            otel_endpoint="http://otel-collector:4318",
            otel_service_name="my-service",
        )
        assert s.otel_enabled is True
        assert s.otel_endpoint == "http://otel-collector:4318"
        assert s.otel_service_name == "my-service"
