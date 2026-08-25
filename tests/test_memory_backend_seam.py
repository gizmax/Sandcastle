"""Memory backend seam: path safety, the MemoryBackend Protocol, dispatch.

Three things are under test here, in order of importance:

1. Path traversal. `_VALID_SCOPE_RE` used to accept `workflow:..`,
   `agent:..` and `tenant:../workflow:x`, and the tenant sanitizer in
   `resolve_scope_id` left ".." untouched. That is inert for Mem0 (a scope
   is a Qdrant user_id) but is a live traversal primitive for any backend
   that maps a scope onto a directory. These tests are the regression net.
2. The `MemoryBackend` Protocol and the `_Mem0Backend` adapter, which must
   conform to it and preserve the pre-refactor error semantics exactly.
3. `settings.memory_backend`, which had zero readers and now selects the
   backend for every call site that does not pass `backend=` explicitly.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from sandcastle.engine.memory import (
    MemoryBackend,
    MemoryBackendError,
    _get_backend,
    _Mem0Backend,
    _reset_client,
    _resolve_backend_name,
    _sanitize_tenant_dots,
    _validate_scope,
    delete_all_memories,
    delete_memory,
    load_memories,
    memory_health_check,
    resolve_scope_id,
    save_memory,
)


class MockMem0Client:
    """Minimal Mem0 stand-in - same shape as tests/test_memory_wave7.py."""

    def __init__(self):
        self._store: dict[str, list[dict]] = {}
        self._counter = 0

    def add(self, content: str, user_id: str = "", metadata: dict | None = None):
        self._counter += 1
        entry = {
            "id": f"mem-{self._counter}",
            "memory": content,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._store.setdefault(user_id, []).append(entry)
        return [entry]

    def get_all(self, user_id: str = ""):
        return self._store.get(user_id, [])

    def search(self, query: str, user_id: str = "", limit: int = 10):
        return {"results": self._store.get(user_id, [])[:limit]}

    def delete(self, memory_id: str):
        for uid, items in self._store.items():
            self._store[uid] = [m for m in items if m["id"] != memory_id]

    def delete_all(self, user_id: str = ""):
        self._store.pop(user_id, None)


@pytest.fixture(autouse=True)
def _reset_memory_singleton():
    _reset_client()
    yield
    _reset_client()


@pytest.fixture
def mock_client():
    client = MockMem0Client()
    with patch("sandcastle.engine.memory._get_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# 1. Path traversal
# ---------------------------------------------------------------------------


# Every one of these PASSED _validate_scope before the fix.
_TRAVERSAL_SCOPES = [
    "workflow:..",
    "agent:..",
    "tenant:../workflow:x",
    "tenant:../global",
    "tenant:./global",
    "workflow:.",
    "agent:.",
    "workflow:...",
    "workflow:. .",
    "workflow:a..b",
    "agent:..b",
    "agent:b..",
    "tenant:a..b/global",
    "tenant:./workflow:x",
]

# These were already rejected; they stay rejected.
_ENCODED_AND_ABSOLUTE_SCOPES = [
    "workflow:../../etc/passwd",
    "workflow:/etc/passwd",
    "agent:/../../etc/shadow",
    "global/..",
    "workflow:..%2f..%2fetc",
    "tenant:..%2f/global",
    "workflow:%2e%2e%2fetc",
    "workflow:..\\..\\windows",
    "workflow:a/../b",
    "tenant:/global",
    "workflow:x\x00/../y",
    "workflow:x\n../y",
    "workflow:x\n",
]

_STILL_VALID_SCOPES = [
    "global",
    "__health_check__",
    "workflow:my-workflow",
    "workflow:my.workflow.v2",
    "agent:my bot name",
    "agent:standup-bot",
    "tenant:acme/workflow:invoice-extract",
    "tenant:acme.com/global",
    "tenant:t-1/agent:bot_2",
    "workflow:" + "a" * 200,
]


class TestScopeTraversalRejected:
    """The scope validator must refuse anything path-shaped."""

    @pytest.mark.parametrize("scope_id", _TRAVERSAL_SCOPES)
    def test_dot_and_dotdot_scopes_rejected(self, scope_id):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope(scope_id)

    @pytest.mark.parametrize("scope_id", _ENCODED_AND_ABSOLUTE_SCOPES)
    def test_absolute_and_encoded_scopes_rejected(self, scope_id):
        with pytest.raises(ValueError, match="Invalid scope_id"):
            _validate_scope(scope_id)

    @pytest.mark.parametrize("scope_id", _STILL_VALID_SCOPES)
    def test_legitimate_scopes_still_accepted(self, scope_id):
        _validate_scope(scope_id)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("scope_id", _TRAVERSAL_SCOPES[:4])
    async def test_public_api_rejects_traversal_scopes(
        self, mock_client, scope_id,
    ):
        """load/save/delete_all all validate, so none of them can traverse."""
        with pytest.raises(ValueError, match="Invalid scope_id"):
            await load_memories(scope_id)
        with pytest.raises(ValueError, match="Invalid scope_id"):
            await save_memory(scope_id, "content here", skip_admission=True)
        with pytest.raises(ValueError, match="Invalid scope_id"):
            await delete_all_memories(scope_id)


class TestTenantSanitizer:
    """resolve_scope_id must never emit a traversable tenant segment."""

    @pytest.mark.parametrize(
        "tenant_id",
        ["..", ".", "...", "../..", "..%2f", "../../etc", "./", " .. ", "/.."],
    )
    def test_hostile_tenant_ids_produce_valid_scopes(self, tenant_id):
        scope = resolve_scope_id(None, "wf", tenant_id=tenant_id)
        assert ".." not in scope
        # The whole point: the result must survive its own validator.
        _validate_scope(scope)

    def test_ordinary_tenant_ids_keep_their_dots(self):
        scope = resolve_scope_id(None, "wf", tenant_id="acme.com")
        assert scope == "tenant:acme.com/workflow:wf"

    def test_sanitizer_downgrades_only_when_needed(self):
        assert _sanitize_tenant_dots("acme.com") == "acme.com"
        assert _sanitize_tenant_dots("..") == "__"
        assert _sanitize_tenant_dots(".") == "_"
        assert _sanitize_tenant_dots("a..b") == "a__b"
        assert _sanitize_tenant_dots("") == "_"

    def test_tenant_truncation_cannot_reintroduce_traversal(self):
        scope = resolve_scope_id(None, "wf", tenant_id="a" * 62 + "..")
        assert ".." not in scope
        _validate_scope(scope)


class TestApiScopeRegex:
    """The API-layer regex had the same hole and gets the same fix."""

    @pytest.mark.parametrize(
        "scope_id",
        ["workflow:..", "agent:..", "workflow:.", "workflow:a..b",
         "workflow:../../etc/passwd", "workflow:x\n"],
    )
    def test_api_regex_rejects_traversal(self, scope_id):
        from sandcastle.api.routes import _SCOPE_ID_RE

        assert not _SCOPE_ID_RE.match(scope_id)

    @pytest.mark.parametrize(
        "scope_id",
        ["global", "workflow:my-wf", "agent:bot", "workflow:my.wf.v2"],
    )
    def test_api_regex_accepts_normal_scopes(self, scope_id):
        from sandcastle.api.routes import _SCOPE_ID_RE

        assert _SCOPE_ID_RE.match(scope_id)


# ---------------------------------------------------------------------------
# 2. Backend interface
# ---------------------------------------------------------------------------


class TestBackendProtocol:
    """_Mem0Backend is the first conformer to the MemoryBackend Protocol."""

    def test_mem0_backend_conforms_to_protocol(self):
        backend: MemoryBackend = _Mem0Backend("local")
        for method in ("load", "save", "delete", "delete_all", "health"):
            assert inspect.iscoroutinefunction(getattr(backend, method))

    def test_get_backend_returns_a_conformer(self):
        assert isinstance(_get_backend("local"), _Mem0Backend)

    def test_get_backend_caches_per_name(self):
        first = _get_backend("local")
        assert _get_backend("local") is first
        assert _get_backend("cloud") is not first

    def test_reset_client_clears_backend_cache(self):
        first = _get_backend("local")
        _reset_client()
        assert _get_backend("local") is not first

    @pytest.mark.asyncio
    async def test_adapter_resolves_client_per_call(self, mock_client):
        """The adapter must not cache the client, so patches keep working."""
        backend = _Mem0Backend("local")
        mock_client.add("Remembered fact here", user_id="global")
        assert len(await backend.load("global", "", 10)) == 1


class TestBackendDispatch:
    """Error semantics must be identical to the pre-refactor behaviour."""

    @pytest.mark.asyncio
    async def test_unknown_backend_load_raises(self):
        with pytest.raises(MemoryBackendError, match="Unknown memory backend"):
            await load_memories("global", backend="nonexistent")

    @pytest.mark.asyncio
    async def test_unknown_backend_save_raises(self):
        with pytest.raises(MemoryBackendError):
            await save_memory(
                "global", "content here", skip_admission=True,
                backend="nonexistent",
            )

    @pytest.mark.asyncio
    async def test_unknown_backend_delete_raises(self):
        with pytest.raises(MemoryBackendError, match="Unknown memory backend"):
            await delete_memory("mem-1", backend="nonexistent")

    @pytest.mark.asyncio
    async def test_cloud_backend_health_reports_error(self):
        result = await memory_health_check(backend="cloud")
        assert result["status"] == "error"
        assert result["backend"] == "cloud"

    @pytest.mark.asyncio
    async def test_delete_accepts_scope_id_and_mem0_ignores_it(
        self, mock_client,
    ):
        mock_client.add("To delete now", user_id="global")
        mem_id = mock_client.get_all(user_id="global")[0]["id"]
        assert await delete_memory(mem_id, scope_id="global") is True
        assert mock_client.get_all(user_id="global") == []

    @pytest.mark.asyncio
    async def test_save_roundtrips_through_the_adapter(self, mock_client):
        records = await save_memory(
            "workflow:seam", "The invoice threshold is 5000 euros",
            skip_admission=True,
        )
        assert len(records) == 1
        stored = mock_client.get_all(user_id="workflow:seam")
        assert stored[0]["memory"] == "The invoice threshold is 5000 euros"


# ---------------------------------------------------------------------------
# 3. settings.memory_backend wiring
# ---------------------------------------------------------------------------


class TestMemoryBackendSetting:
    """The setting had zero readers; it must now select the backend."""

    def test_default_resolves_to_local(self):
        assert _resolve_backend_name("") == "local"

    def test_explicit_name_wins_over_setting(self):
        with patch("sandcastle.config.settings.memory_backend", "cloud"):
            assert _resolve_backend_name("local") == "local"

    def test_setting_selects_backend(self):
        with patch("sandcastle.config.settings.memory_backend", "cloud"):
            assert _resolve_backend_name("") == "cloud"
            assert _get_backend("").name == "cloud"

    @pytest.mark.asyncio
    async def test_health_check_reports_the_resolved_backend(self):
        with patch("sandcastle.config.settings.memory_backend", "cloud"):
            result = await memory_health_check()
        assert result["backend"] == "cloud"

    @pytest.mark.asyncio
    async def test_default_health_check_still_reports_local(self):
        with patch(
            "sandcastle.engine.memory._get_client",
            return_value=MockMem0Client(),
        ):
            result = await memory_health_check()
        assert result["status"] == "ok"
        assert result["backend"] == "local"

    def test_executor_passes_backend_to_all_three_call_sites(self):
        """Guard against the wiring silently regressing to the default."""
        from pathlib import Path

        import sandcastle.engine.executor as executor

        source = Path(executor.__file__).read_text()
        assert "backend=_mem_read_settings.memory_backend" in source
        assert "backend=_mem_write_settings.memory_backend" in source
        assert "backend=_mem_settings.memory_backend" in source

    @pytest.mark.asyncio
    async def test_unset_backend_uses_setting_not_literal_local(self):
        """A call site that omits backend= follows MEMORY_BACKEND."""
        seen: list[str] = []

        def _fake_get_client(name="local"):
            seen.append(name)
            return MockMem0Client()

        with (
            patch("sandcastle.config.settings.memory_backend", "cloud"),
            patch(
                "sandcastle.engine.memory._get_client",
                side_effect=_fake_get_client,
            ),
        ):
            await load_memories("global")

        assert seen == ["cloud"]


class TestConfigValidation:
    """_VALID_MEMORY_BACKENDS still gates what the setting may hold."""

    def test_unknown_backend_falls_back_to_local(self):
        from sandcastle.config import Settings

        assert Settings(memory_backend="does-not-exist").memory_backend == "local"

    def test_known_backends_pass_through(self):
        from sandcastle.config import Settings

        assert Settings(memory_backend="cloud").memory_backend == "cloud"
        assert Settings(memory_backend="LOCAL").memory_backend == "local"


class TestMem0ApiGenerations:
    """Reads must work against both mem0 API generations.

    mem0 2.x rejects `user_id=` at top level (ValueError, "use filters=")
    while 0.x/1.x raises TypeError on `filters=`/`top_k=`. Passing legacy
    kwargs to 2.x and swallowing the ValueError meant every memory read on
    the installed backend silently returned [] - found by the 0.47 memory
    eval workstream, verified against mem0 2.0.18 live.
    """

    def _rows(self):
        return {"results": [{"id": "m1", "memory": "fact", "metadata": {}}]}

    def test_search_speaks_mem0_2x(self):
        from sandcastle.engine.memory import _mem0_search

        class Mem0V2:
            def search(self, query, **kwargs):
                if "user_id" in kwargs or "limit" in kwargs:
                    raise ValueError(
                        "Top-level entity parameters {'user_id'} are not "
                        "supported in search(). Use filters={'user_id': ...}"
                    )
                assert kwargs["filters"] == {"user_id": "workflow:x"}
                assert kwargs["top_k"] == 5
                return {"results": [{"id": "m1", "memory": "fact", "metadata": {}}]}

        out = _mem0_search(Mem0V2(), "q", "workflow:x", 5)
        assert out["results"][0]["memory"] == "fact"

    def test_search_falls_back_to_legacy_mem0(self):
        from sandcastle.engine.memory import _mem0_search

        class Mem0V1:
            def search(self, query, user_id=None, limit=100):
                assert user_id == "workflow:x" and limit == 5
                return {"results": [{"id": "m1", "memory": "fact", "metadata": {}}]}

        out = _mem0_search(Mem0V1(), "q", "workflow:x", 5)
        assert out["results"][0]["memory"] == "fact"

    def test_get_all_speaks_mem0_2x(self):
        from sandcastle.engine.memory import _mem0_get_all

        class Mem0V2:
            def get_all(self, **kwargs):
                if "user_id" in kwargs:
                    raise ValueError("use filters=")
                assert kwargs["filters"] == {"user_id": "workflow:x"}
                return {"results": []}

        assert _mem0_get_all(Mem0V2(), "workflow:x") == {"results": []}

    def test_get_all_falls_back_to_legacy_mem0(self):
        from sandcastle.engine.memory import _mem0_get_all

        class Mem0V1:
            def get_all(self, user_id=None):
                assert user_id == "workflow:x"
                return {"results": []}

        assert _mem0_get_all(Mem0V1(), "workflow:x") == {"results": []}

    @pytest.mark.asyncio
    async def test_load_memories_returns_rows_on_mem0_2x(self):
        """The end-to-end regression: reads must not silently return []."""
        from unittest.mock import patch

        from sandcastle.engine import memory as mem

        class Mem0V2:
            def search(self, query, **kwargs):
                if "user_id" in kwargs:
                    raise ValueError("use filters=")
                return {"results": [{"id": "m1", "memory": "the fact", "metadata": {}}]}

        with patch.object(mem, "_get_client", return_value=Mem0V2()):
            rows = await mem.load_memories(
                "workflow:x", query="anything", limit=3, backend="local",
            )
        assert [r["memory"] for r in rows] == ["the fact"]
