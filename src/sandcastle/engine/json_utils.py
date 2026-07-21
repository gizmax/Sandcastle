"""JSON coercion helpers for DB persistence."""

from __future__ import annotations

import json
from typing import Any


def json_safe(value: Any) -> Any:
    """Return a JSON-serializable version of ``value``.

    Workflow outputs may contain non-JSON-native objects (datetime, UUID,
    Decimal, arbitrary SDK types). Persisting them to a JSON column must never
    fail — unknown types degrade to their string form instead of breaking the
    whole result write.
    """
    return json.loads(json.dumps(value, default=str))
