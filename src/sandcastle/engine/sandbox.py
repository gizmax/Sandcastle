"""Backward compatibility shim - re-exports from sandshore module.

This module is kept so that existing imports from ``sandcastle.engine.sandbox``
continue to work.  All real logic lives in ``sandcastle.engine.sandshore``.
"""

from sandcastle.engine.sandshore import (
    SandshoreError as SandstormError,
)
from sandcastle.engine.sandshore import (
    SandshoreResult as SandstormResult,
)
from sandcastle.engine.sandshore import (
    SandshoreRuntime as SandstormClient,
)
from sandcastle.engine.sandshore import (  # noqa: F401
    SSEEvent,
)
from sandcastle.engine.sandshore import (
    get_sandshore_runtime as get_sandstorm_client,
)

__all__ = [
    "SSEEvent",
    "SandstormClient",
    "SandstormResult",
    "SandstormError",
    "get_sandstorm_client",
]
