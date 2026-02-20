"""Backward compatibility shim - re-exports from sandshore module.

This module is kept so that existing imports from ``sandcastle.engine.sandbox``
continue to work.  All real logic lives in ``sandcastle.engine.sandshore``.
"""

from sandcastle.engine.sandshore import (  # noqa: F401
    SandshoreError,
    SandshoreResult,
    SandshoreRuntime,
    SSEEvent,
    get_sandshore_runtime,
)

__all__ = [
    "SSEEvent",
    "SandshoreRuntime",
    "SandshoreResult",
    "SandshoreError",
    "get_sandshore_runtime",
]
