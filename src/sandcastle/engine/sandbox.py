"""Backward compatibility shim - re-exports from sandshore module.

This module is kept so that existing imports from ``sandcastle.engine.sandbox``
continue to work.  All real logic lives in ``sandcastle.engine.sandshore``.
"""

from sandcastle.engine.sandshore import (  # noqa: F401
    CircuitBreaker,
    RuntimeMetrics,
    SandshoreError,
    SandshoreResult,
    SandshoreRuntime,
    SSEEvent,
    _extract_text,
    cleanup_pool,
    get_sandshore_runtime,
    pool_stats,
)

__all__ = [
    "CircuitBreaker",
    "RuntimeMetrics",
    "SSEEvent",
    "SandshoreRuntime",
    "SandshoreResult",
    "SandshoreError",
    "_extract_text",
    "cleanup_pool",
    "get_sandshore_runtime",
    "pool_stats",
]
