"""OpenTelemetry instrumentation for Sandcastle workflow execution."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Lazy imports - only loaded when otel_enabled=True
_tracer = None


def init_otel(service_name: str, endpoint: str) -> None:
    """Initialize OpenTelemetry with OTLP HTTP exporter. No-op if deps missing."""
    global _tracer
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("sandcastle")
        logger.info("OpenTelemetry initialized: endpoint=%s", endpoint)
    except ImportError:
        logger.debug("OpenTelemetry not installed, tracing disabled")
    except Exception:
        logger.warning("Failed to initialize OpenTelemetry", exc_info=True)


@contextmanager
def workflow_span(run_id: str, workflow_name: str, **attrs: Any) -> Iterator[Any]:
    """Create a span for the entire workflow execution."""
    if not _tracer:
        yield None
        return
    with _tracer.start_as_current_span(
        f"workflow:{workflow_name}",
        attributes={
            "sandcastle.run_id": run_id,
            "sandcastle.workflow": workflow_name,
            **{f"sandcastle.{k}": str(v) for k, v in attrs.items()},
        },
    ) as span:
        yield span


@contextmanager
def step_span(
    run_id: str,
    step_id: str,
    step_type: str,
    model: str | None = None,
    **attrs: Any,
) -> Iterator[Any]:
    """Create a span for a single step execution."""
    if not _tracer:
        yield None
        return
    span_attrs: dict[str, str] = {
        "sandcastle.run_id": run_id,
        "sandcastle.step_id": step_id,
        "sandcastle.step_type": step_type,
    }
    if model:
        span_attrs["sandcastle.model"] = model
    span_attrs.update({f"sandcastle.{k}": str(v) for k, v in attrs.items()})
    with _tracer.start_as_current_span(
        f"step:{step_id}",
        attributes=span_attrs,
    ) as span:
        yield span


def record_step_result(
    span: Any,
    status: str,
    cost: float = 0.0,
    duration: float = 0.0,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> None:
    """Record step completion attributes on a span."""
    if not span:
        return
    span.set_attribute("sandcastle.status", status)
    span.set_attribute("sandcastle.cost_usd", cost)
    span.set_attribute("sandcastle.duration_s", duration)
    if tokens_in:
        span.set_attribute("sandcastle.tokens_in", tokens_in)
    if tokens_out:
        span.set_attribute("sandcastle.tokens_out", tokens_out)


def _reset() -> None:
    """Reset state for testing."""
    global _tracer
    _tracer = None
