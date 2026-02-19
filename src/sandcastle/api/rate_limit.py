"""In-memory rate limiter for API endpoints.

Uses a sliding window counter per tenant/IP to prevent abuse
of expensive execution endpoints (each call creates an E2B sandbox).
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import HTTPException, Request


@dataclass
class _Window:
    """Sliding window counter."""

    timestamps: list[float] = field(default_factory=list)

    def count_in_window(self, window_seconds: float) -> int:
        """Count requests within the sliding window."""
        cutoff = time.monotonic() - window_seconds
        self.timestamps = [t for t in self.timestamps if t > cutoff]
        return len(self.timestamps)

    def add(self) -> None:
        """Record a new request."""
        self.timestamps.append(time.monotonic())


class RateLimiter:
    """In-memory sliding window rate limiter.

    Keyed by tenant_id (authenticated) or client IP (anonymous).
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: float = 60.0,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._windows: dict[str, _Window] = defaultdict(_Window)

    def _get_key(self, request: Request) -> str:
        """Extract rate limit key from request."""
        # Use tenant_id if authenticated, otherwise client IP
        tenant = getattr(request.state, "tenant_id", None)
        if tenant:
            return f"tenant:{tenant}"
        client = request.client
        ip = client.host if client else "unknown"
        return f"ip:{ip}"

    def check(self, request: Request) -> None:
        """Check rate limit. Raises HTTPException(429) if exceeded."""
        key = self._get_key(request)
        window = self._windows[key]

        current = window.count_in_window(self.window_seconds)
        if current >= self.max_requests:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded: {self.max_requests} requests "
                    f"per {int(self.window_seconds)}s. Try again later."
                ),
                headers={
                    "Retry-After": str(int(self.window_seconds)),
                    "X-RateLimit-Limit": str(self.max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )
        window.add()

    @property
    def info(self) -> dict:
        """Return current rate limiter state for debugging."""
        return {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "active_keys": len(self._windows),
        }


# Singleton for execution endpoints (expensive - sandbox creation)
execution_limiter = RateLimiter(max_requests=10, window_seconds=60.0)
