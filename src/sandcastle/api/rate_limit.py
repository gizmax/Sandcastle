"""Rate limiter for API endpoints with pluggable backends.

Uses a sliding window counter per tenant/IP to prevent abuse
of expensive execution endpoints (each call creates an E2B sandbox).

Backends:
- **InMemoryBackend** (default) - single-process, no external deps
- **RedisBackend** - distributed, uses sorted sets for sliding window
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Backend Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RateLimitBackend(Protocol):
    """Interface for rate limit storage backends."""

    async def check_and_increment(
        self, key: str, max_requests: int, window_seconds: float
    ) -> int:
        """Check current count and increment. Returns current count after increment.

        Raises HTTPException(429) if limit is exceeded.
        """
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# In-Memory Backend
# ---------------------------------------------------------------------------


class InMemoryBackend:
    """Single-process in-memory sliding window backend."""

    def __init__(self) -> None:
        self._windows: dict[str, _Window] = defaultdict(_Window)

    async def check_and_increment(
        self, key: str, max_requests: int, window_seconds: float
    ) -> int:
        window = self._windows[key]
        current = window.count_in_window(window_seconds)
        if current < max_requests:
            window.add()
        # Return the attempted request number (1-indexed)
        return current + 1

    @property
    def active_keys(self) -> int:
        return len(self._windows)


# ---------------------------------------------------------------------------
# Redis Backend
# ---------------------------------------------------------------------------


class RedisBackend:
    """Distributed rate limiting using Redis sorted sets.

    Each key maps to a sorted set where members are unique request IDs
    scored by timestamp. Uses a pipeline for atomic check+increment.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis = None
        self._redis_lock = asyncio.Lock()

    async def _get_redis(self):
        if self._redis is not None:
            return self._redis
        async with self._redis_lock:
            if self._redis is not None:
                return self._redis
            try:
                from redis.asyncio import from_url

                self._redis = from_url(self._redis_url)
                return self._redis
            except ImportError:
                logger.warning(
                    "redis package not installed, falling back"
                    " to in-memory rate limiting"
                )
                return None

    # Lua script for atomic check-and-increment (avoids TOCTOU between
    # count check and zadd when two requests arrive concurrently).
    _LUA_CHECK_AND_INCREMENT = """
    local key = KEYS[1]
    local window_start = tonumber(ARGV[1])
    local max_requests = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local member = ARGV[4]
    local ttl = tonumber(ARGV[5])
    redis.call('ZREMRANGEBYSCORE', key, 0, window_start)
    local current = redis.call('ZCARD', key)
    if current < max_requests then
        redis.call('ZADD', key, now, member)
        redis.call('EXPIRE', key, ttl)
    end
    return current + 1
    """

    async def check_and_increment(
        self, key: str, max_requests: int, window_seconds: float
    ) -> int:
        redis = await self._get_redis()
        if redis is None:
            logger.error(
                "Redis rate limit backend unavailable - failing closed for key %s",
                key,
            )
            return max_requests + 1  # Fail closed if Redis unavailable

        import uuid

        now = time.time()
        window_start = now - window_seconds
        redis_key = f"rl:{key}"
        member = f"{now}:{uuid.uuid4().hex[:8]}"
        ttl = int(window_seconds) + 1

        try:
            result = await redis.eval(
                self._LUA_CHECK_AND_INCREMENT,
                1,
                redis_key,
                str(window_start),
                str(max_requests),
                str(now),
                member,
                str(ttl),
            )
            return int(result)
        except Exception as exc:
            logger.error(
                "Redis rate limit error (failing closed): %s", exc
            )
            return max_requests + 1  # Fail closed on Redis errors


# ---------------------------------------------------------------------------
# RateLimiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Sliding window rate limiter with pluggable backend.

    Keyed by tenant_id (authenticated) or client IP (anonymous).
    """

    def __init__(
        self,
        max_requests: int = 10,
        window_seconds: float = 60.0,
        backend: RateLimitBackend | None = None,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._backend = backend or InMemoryBackend()

    def _get_key(self, request: Request) -> str:
        """Extract rate limit key from request."""
        tenant = getattr(request.state, "tenant_id", None)
        if tenant:
            return f"tenant:{tenant}"
        client = request.client
        ip = client.host if client else "unknown"
        return f"ip:{ip}"

    async def check(self, request: Request) -> None:
        """Check rate limit. Raises HTTPException(429) if exceeded."""
        key = self._get_key(request)
        count = await self._backend.check_and_increment(
            key, self.max_requests, self.window_seconds
        )
        if count > self.max_requests:
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

    @property
    def info(self) -> dict:
        """Return current rate limiter state for debugging."""
        info = {
            "max_requests": self.max_requests,
            "window_seconds": self.window_seconds,
            "backend": type(self._backend).__name__,
        }
        if isinstance(self._backend, InMemoryBackend):
            info["active_keys"] = self._backend.active_keys
        return info


# ---------------------------------------------------------------------------
# Factory & Singleton
# ---------------------------------------------------------------------------


def _create_backend() -> RateLimitBackend:
    """Pick the appropriate backend based on configuration."""
    from sandcastle.config import settings

    if settings.redis_url:
        return RedisBackend(settings.redis_url)
    return InMemoryBackend()


# Singleton for execution endpoints (expensive - sandbox creation)
execution_limiter = RateLimiter(max_requests=10, window_seconds=60.0, backend=_create_backend())
