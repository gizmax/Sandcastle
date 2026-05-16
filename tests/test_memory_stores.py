"""Tests for the Anthropic Memory Stores client.

Uses unittest.mock to patch httpx.AsyncClient so no network is involved.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sandcastle.engine.memory_stores import (
    DEFAULT_BETA_HEADER,
    MAX_MEMORY_FILE_BYTES,
    MemoryFileTooLargeError,
    MemoryStoresClient,
    MemoryStoresConflict,
    MemoryStoresError,
    MemoryStoresLimitError,
    MemoryStoresNotFound,
)


def _make_response(
    status: int = 200,
    json_body: Any = None,
    text: str = "",
) -> MagicMock:
    """Build a mock httpx.Response with the fields the client touches."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.text = text or ""
    if json_body is None:
        json_body = {}
    resp.json = MagicMock(return_value=json_body)
    return resp


class _CapturingClient:
    """Async context manager double for httpx.AsyncClient.

    Records the latest .request(...) kwargs on the surrounding test via the
    `calls` list it is initialised with.
    """

    def __init__(self, calls: list[dict[str, Any]], response: MagicMock) -> None:
        self._calls = calls
        self._response = response

    async def __aenter__(self) -> "_CapturingClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def request(self, method: str, url: str, **kwargs: Any) -> MagicMock:
        self._calls.append({"method": method, "url": url, **kwargs})
        return self._response


def _patch_httpx(response: MagicMock) -> tuple[Any, list[dict[str, Any]]]:
    """Return a patch context manager and the list that will collect calls."""
    calls: list[dict[str, Any]] = []

    def _factory(*args: Any, **kwargs: Any) -> _CapturingClient:
        return _CapturingClient(calls, response)

    return patch("sandcastle.engine.memory_stores.httpx.AsyncClient", _factory), calls


@pytest.fixture
def client() -> MemoryStoresClient:
    return MemoryStoresClient(api_key="sk-test-123")


# ---------------------------------------------------------------------------
# create_store
# ---------------------------------------------------------------------------
async def test_create_store_posts_correct_body(client: MemoryStoresClient) -> None:
    resp = _make_response(200, {"id": "ms_abc", "name": "scratch"})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        result = await client.create_store("scratch", description="for tests")
    assert result == {"id": "ms_abc", "name": "scratch"}
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/v1/memory_stores")
    assert call["json"] == {
        "name": "scratch",
        "read_only": False,
        "description": "for tests",
    }


async def test_create_store_sets_beta_and_auth_headers(
    client: MemoryStoresClient,
) -> None:
    resp = _make_response(200, {"id": "ms_abc"})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        await client.create_store("scratch", read_only=True)
    headers = calls[0]["headers"]
    assert headers["x-api-key"] == "sk-test-123"
    assert headers["anthropic-beta"] == DEFAULT_BETA_HEADER
    assert calls[0]["json"]["read_only"] is True
    assert "description" not in calls[0]["json"]


# ---------------------------------------------------------------------------
# list / get / delete
# ---------------------------------------------------------------------------
async def test_list_stores_unwraps_data_envelope(client: MemoryStoresClient) -> None:
    resp = _make_response(200, {"data": [{"id": "ms_1"}, {"id": "ms_2"}]})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        stores = await client.list_stores(limit=25)
    assert stores == [{"id": "ms_1"}, {"id": "ms_2"}]
    assert calls[0]["method"] == "GET"
    assert calls[0]["params"] == {"limit": 25}


async def test_get_store_returns_payload(client: MemoryStoresClient) -> None:
    resp = _make_response(200, {"id": "ms_xyz", "name": "demo"})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        store = await client.get_store("ms_xyz")
    assert store["id"] == "ms_xyz"
    assert calls[0]["url"].endswith("/v1/memory_stores/ms_xyz")


async def test_delete_store_uses_delete_verb(client: MemoryStoresClient) -> None:
    resp = _make_response(204)
    ctx, calls = _patch_httpx(resp)
    with ctx:
        result = await client.delete_store("ms_xyz")
    assert result is None
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["url"].endswith("/v1/memory_stores/ms_xyz")


# ---------------------------------------------------------------------------
# write_memory
# ---------------------------------------------------------------------------
async def test_write_memory_includes_if_match_when_version_given(
    client: MemoryStoresClient,
) -> None:
    resp = _make_response(200, {"path": "notes.md", "version": "v2"})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        await client.write_memory(
            "ms_1", "notes.md", "hello", expected_version="v1-sha256"
        )
    headers = calls[0]["headers"]
    assert headers.get("If-Match") == "v1-sha256"
    assert calls[0]["method"] == "PUT"
    assert calls[0]["json"] == {"content": "hello"}


async def test_write_memory_omits_if_match_when_no_version(
    client: MemoryStoresClient,
) -> None:
    resp = _make_response(200, {"path": "notes.md", "version": "v1"})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        await client.write_memory("ms_1", "notes.md", "hello")
    assert "If-Match" not in calls[0]["headers"]


async def test_write_memory_rejects_oversize_content(
    client: MemoryStoresClient,
) -> None:
    payload = "x" * (MAX_MEMORY_FILE_BYTES + 1)
    with pytest.raises(MemoryFileTooLargeError):
        await client.write_memory("ms_1", "big.txt", payload)


# ---------------------------------------------------------------------------
# read_memory + redact_version
# ---------------------------------------------------------------------------
async def test_read_memory_passes_version_param(client: MemoryStoresClient) -> None:
    resp = _make_response(200, {"path": "notes.md", "content": "hi", "version": "v7"})
    ctx, calls = _patch_httpx(resp)
    with ctx:
        result = await client.read_memory("ms_1", "notes.md", version="v7")
    assert result["version"] == "v7"
    assert calls[0]["params"] == {"version": "v7"}


async def test_redact_version_calls_redact_path(client: MemoryStoresClient) -> None:
    resp = _make_response(204)
    ctx, calls = _patch_httpx(resp)
    with ctx:
        await client.redact_version("ms_1", "ver_42", reason="gdpr request 991")
    assert calls[0]["method"] == "POST"
    assert calls[0]["url"].endswith("/v1/memory_stores/ms_1/versions/ver_42/redact")
    assert calls[0]["json"] == {"reason": "gdpr request 991"}


# ---------------------------------------------------------------------------
# attach_to_session_payload
# ---------------------------------------------------------------------------
def test_attach_to_session_payload_rejects_more_than_eight() -> None:
    too_many = [f"ms_{i}" for i in range(9)]
    with pytest.raises(MemoryStoresLimitError):
        MemoryStoresClient.attach_to_session_payload(too_many)


def test_attach_to_session_payload_shape() -> None:
    payload = MemoryStoresClient.attach_to_session_payload(["ms_a", "ms_b"])
    assert payload == [
        {"type": "memory_store", "id": "ms_a"},
        {"type": "memory_store", "id": "ms_b"},
    ]


# ---------------------------------------------------------------------------
# Error mapping (404 / 409 / 5xx)
# ---------------------------------------------------------------------------
async def test_404_maps_to_not_found(client: MemoryStoresClient) -> None:
    resp = _make_response(
        404, {"error": {"message": "store not found"}}, text="not found"
    )
    ctx, _ = _patch_httpx(resp)
    with ctx, pytest.raises(MemoryStoresNotFound):
        await client.get_store("ms_missing")


async def test_409_maps_to_conflict(client: MemoryStoresClient) -> None:
    resp = _make_response(
        409, {"error": {"message": "version mismatch"}}, text="conflict"
    )
    ctx, _ = _patch_httpx(resp)
    with ctx, pytest.raises(MemoryStoresConflict):
        await client.write_memory("ms_1", "notes.md", "hi", expected_version="stale")


async def test_500_maps_to_base_error(client: MemoryStoresClient) -> None:
    resp = _make_response(500, {"error": {"message": "boom"}}, text="boom")
    ctx, _ = _patch_httpx(resp)
    with ctx, pytest.raises(MemoryStoresError) as info:
        await client.list_stores()
    # Make sure we did NOT misclassify the 500 as a more specific subclass.
    assert not isinstance(info.value, (MemoryStoresNotFound, MemoryStoresConflict))
