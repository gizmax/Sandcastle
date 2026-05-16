"""Anthropic Memory Stores API client.

A self-contained async client for the `/v1/memory_stores` beta endpoint that
provides workspace-scoped, versioned memory to managed-agent sessions. Memory
files are mounted at `/mnt/memory/` inside the agent runtime.

Constraints enforced client-side:
- Maximum 8 memory stores attached per session.
- Maximum 100 kB per memory file (UTF-8 encoded).
- Optional SHA-256 optimistic concurrency via `If-Match` on writes.
- 30-day retention is server-side; clients may force-redact a specific version
  for GDPR right-to-be-forgotten requests.

Designed for v0.32 of Sandcastle. No dependencies outside stdlib + httpx.
"""

from __future__ import annotations

from typing import Any

import httpx

# 100 kB ceiling enforced server-side; we mirror it here so callers fail fast.
MAX_MEMORY_FILE_BYTES = 100 * 1024
MAX_STORES_PER_SESSION = 8
DEFAULT_BETA_HEADER = "managed-agents-2026-04-01"


class MemoryStoresError(Exception):
    """Base error for the Memory Stores client."""


class MemoryStoresLimitError(MemoryStoresError):
    """Raised when attempting to attach more than 8 stores to a session."""


class MemoryFileTooLargeError(MemoryStoresError):
    """Raised when a memory file payload exceeds the 100 kB ceiling."""


class MemoryStoresNotFound(MemoryStoresError):
    """Raised on a 404 from the Memory Stores API."""


class MemoryStoresConflict(MemoryStoresError):
    """Raised on a 409 from the Memory Stores API (version mismatch, etc)."""


class MemoryStoresClient:
    """Async client for Anthropic Memory Stores (beta)."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        beta_header: str = DEFAULT_BETA_HEADER,
    ) -> None:
        if not api_key:
            raise ValueError("api_key must be a non-empty string")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.beta_header = beta_header

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-beta": self.beta_header,
            "content-type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        status = response.status_code
        if status < 400:
            return
        # Try to surface server error message but never let parsing crash.
        try:
            payload = response.json()
            message = payload.get("error", {}).get("message") or str(payload)
        except Exception:
            message = response.text or f"HTTP {status}"
        if status == 404:
            raise MemoryStoresNotFound(message)
        if status == 409 or status == 412:
            raise MemoryStoresConflict(message)
        raise MemoryStoresError(f"HTTP {status}: {message}")

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                self._url(path),
                headers=self._headers(extra_headers),
                json=json,
                params=params,
            )
        self._raise_for_status(response)
        return response

    # ------------------------------------------------------------------
    # Store lifecycle
    # ------------------------------------------------------------------
    async def create_store(
        self,
        name: str,
        description: str | None = None,
        read_only: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"name": name, "read_only": read_only}
        if description is not None:
            body["description"] = description
        response = await self._request("POST", "/v1/memory_stores", json=body)
        return response.json()

    async def list_stores(self, limit: int = 50) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", "/v1/memory_stores", params={"limit": limit}
        )
        payload = response.json()
        # API returns {"data": [...]}; tolerate a bare list too.
        if isinstance(payload, list):
            return payload
        return payload.get("data", [])

    async def get_store(self, store_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/v1/memory_stores/{store_id}")
        return response.json()

    async def delete_store(self, store_id: str) -> None:
        await self._request("DELETE", f"/v1/memory_stores/{store_id}")

    # ------------------------------------------------------------------
    # Memory file operations
    # ------------------------------------------------------------------
    async def list_memories(self, store_id: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", f"/v1/memory_stores/{store_id}/memories"
        )
        payload = response.json()
        if isinstance(payload, list):
            return payload
        return payload.get("data", [])

    async def write_memory(
        self,
        store_id: str,
        path: str,
        content: str,
        expected_version: str | None = None,
    ) -> dict[str, Any]:
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_MEMORY_FILE_BYTES:
            raise MemoryFileTooLargeError(
                f"memory file '{path}' is {len(encoded)} bytes; "
                f"max is {MAX_MEMORY_FILE_BYTES} bytes"
            )
        extra_headers: dict[str, str] | None = None
        if expected_version is not None:
            extra_headers = {"If-Match": expected_version}
        response = await self._request(
            "PUT",
            f"/v1/memory_stores/{store_id}/memories/{path}",
            json={"content": content},
            extra_headers=extra_headers,
        )
        return response.json()

    async def read_memory(
        self,
        store_id: str,
        path: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        params = {"version": version} if version is not None else None
        response = await self._request(
            "GET",
            f"/v1/memory_stores/{store_id}/memories/{path}",
            params=params,
        )
        return response.json()

    async def redact_version(
        self,
        store_id: str,
        version_id: str,
        reason: str,
    ) -> None:
        await self._request(
            "POST",
            f"/v1/memory_stores/{store_id}/versions/{version_id}/redact",
            json={"reason": reason},
        )

    # ------------------------------------------------------------------
    # Session attachment helper
    # ------------------------------------------------------------------
    @staticmethod
    def attach_to_session_payload(store_ids: list[str]) -> list[dict[str, str]]:
        """Return the `resources` chunk for a session create request.

        Raises MemoryStoresLimitError when more than 8 stores are supplied.
        """
        if len(store_ids) > MAX_STORES_PER_SESSION:
            raise MemoryStoresLimitError(
                f"cannot attach {len(store_ids)} stores; "
                f"max is {MAX_STORES_PER_SESSION} per session"
            )
        return [{"type": "memory_store", "id": sid} for sid in store_ids]


__all__ = [
    "MemoryStoresClient",
    "MemoryStoresError",
    "MemoryStoresLimitError",
    "MemoryFileTooLargeError",
    "MemoryStoresNotFound",
    "MemoryStoresConflict",
    "MAX_MEMORY_FILE_BYTES",
    "MAX_STORES_PER_SESSION",
    "DEFAULT_BETA_HEADER",
]
