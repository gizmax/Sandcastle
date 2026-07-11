"""Tests for the Memory MCP server (mem0+Qdrant wrapper)."""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sandcastle.engine import memory_mcp_server as mms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class _FakeMemoryModule(types.ModuleType):
    """In-memory stand-in for sandcastle.engine.memory."""

    def __init__(self) -> None:
        super().__init__("sandcastle.engine.memory")
        self.save_memory = AsyncMock(return_value=[{"id": "mem_1", "memory": "hi"}])
        self.load_memories = AsyncMock(return_value=[{"id": "mem_1", "memory": "hi", "score": 0.5}])
        self.delete_memory = AsyncMock(return_value=True)
        self.memory_health_check = AsyncMock(return_value={"status": "ok"})

        client = MagicMock()
        client.get_all = MagicMock(
            return_value={"results": [{"user_id": "user:1"}, {"user_id": "user:2"}]}
        )
        self._get_client = MagicMock(return_value=client)


@pytest.fixture()
def fake_memory(monkeypatch: pytest.MonkeyPatch) -> _FakeMemoryModule:
    """Inject a fake memory module so tool calls do not touch mem0."""
    import sandcastle.engine as _engine_pkg

    fake = _FakeMemoryModule()
    monkeypatch.setitem(sys.modules, "sandcastle.engine.memory", fake)
    # `from sandcastle.engine import memory` resolves the parent package's
    # attribute first, so once the real submodule has been imported by an
    # earlier test the sys.modules swap alone is bypassed. Patch the attribute
    # too so the fake is injected regardless of prior import state.
    monkeypatch.setattr(_engine_pkg, "memory", fake, raising=False)
    return fake


# ---------------------------------------------------------------------------
# Tool definitions / schemas
# ---------------------------------------------------------------------------


class TestToolDefinitions:
    """Tool definition shape + tool-examples convention."""

    def test_four_tools_declared(self) -> None:
        defs = mms.get_tool_definitions()
        names = [d["name"] for d in defs]
        assert names == ["add", "search", "forget", "list_memories"]

    def test_every_tool_has_json_schema_with_required_and_properties(self) -> None:
        for d in mms.get_tool_definitions():
            schema = d["input_schema"]
            assert schema["type"] == "object"
            assert isinstance(schema.get("properties"), dict)
            assert isinstance(schema.get("required"), list)
            assert schema["required"], f"{d['name']} has empty required list"
            assert schema.get("additionalProperties") is False

    def test_each_tool_has_one_to_five_examples(self) -> None:
        for d in mms.get_tool_definitions():
            n = len(d["examples"])
            assert 1 <= n <= 5, f"{d['name']} has {n} examples (need 1-5)"
            for ex in d["examples"]:
                assert isinstance(ex.get("input"), dict)
                assert isinstance(ex.get("output"), dict)


# ---------------------------------------------------------------------------
# Lazy import behaviour
# ---------------------------------------------------------------------------


class TestLazyImport:
    """The server must start even when mem0 is absent; tool calls then fail typed."""

    def test_load_memory_module_raises_typed_error_when_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the memory import path is broken, _load_memory_module raises MemoryMCPError."""
        # Replace the cached module with one whose attribute lookup is impossible,
        # AND patch the actual import path inside _load_memory_module so it explodes.
        import sandcastle.engine as _engine_pkg

        # Drop the real module from sys.modules and shadow it with a broken loader.
        monkeypatch.delitem(sys.modules, "sandcastle.engine.memory", raising=False)
        monkeypatch.delattr(_engine_pkg, "memory", raising=False)

        # Patch the importer used inside _load_memory_module via importlib.
        def _broken_import_module(*args: Any, **kwargs: Any) -> Any:
            raise ImportError("mem0 not installed")

        # The function uses 'from sandcastle.engine import memory'. To force that
        # to fail, we patch sandcastle.engine's __getattr__ via a sentinel.
        monkeypatch.setitem(sys.modules, "sandcastle.engine.memory", None)

        with pytest.raises(mms.MemoryMCPError) as exc_info:
            mms._load_memory_module()
        assert exc_info.value.code == "memory_unavailable"
        assert "mem0" in str(exc_info.value) or "memory stack" in str(exc_info.value)

    def test_server_still_constructs_without_memory_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even if memory imports would fail, create_memory_mcp_server() must succeed
        # because the import is deferred to tool call time.
        monkeypatch.delitem(sys.modules, "sandcastle.engine.memory", raising=False)
        server = mms.create_memory_mcp_server()
        assert server.name == "Sandcastle Memory"


# ---------------------------------------------------------------------------
# add() routing
# ---------------------------------------------------------------------------


class TestAddTool:
    def test_add_routes_to_save_memory_with_user_id(self, fake_memory: _FakeMemoryModule) -> None:
        result = _run(mms._tool_add("hello world", "user:42"))
        fake_memory.save_memory.assert_awaited_once()
        call = fake_memory.save_memory.await_args
        assert call.args[0] == "user:42"
        assert call.args[1] == "hello world"
        assert call.kwargs.get("skip_admission") is True
        assert result["results"][0]["text"] == "hi"
        assert result["results"][0]["id"] == "mem_1"

    def test_add_rejects_empty_text(self, fake_memory: _FakeMemoryModule) -> None:
        with pytest.raises(mms.MemoryValidationError):
            _run(mms._tool_add("   ", "user:42"))

    def test_add_rejects_bad_user_id(self, fake_memory: _FakeMemoryModule) -> None:
        with pytest.raises(mms.MemoryValidationError):
            _run(mms._tool_add("ok", "bad user id!!"))


# ---------------------------------------------------------------------------
# search() shape
# ---------------------------------------------------------------------------


class TestSearchTool:
    def test_search_returns_anthropic_memory_tool_shape(
        self, fake_memory: _FakeMemoryModule
    ) -> None:
        fake_memory.load_memories.return_value = [
            {
                "id": "m1",
                "memory": "First memory",
                "score": 0.91,
                "metadata": {"src": "kickoff"},
            }
        ]
        result = _run(mms._tool_search("prefer", "user:1", limit=3))
        assert "results" in result
        item = result["results"][0]
        assert set(item.keys()) >= {"id", "text", "score", "metadata"}
        assert item["text"] == "First memory"
        assert item["score"] == 0.91

    def test_search_passes_user_id_scoping(self, fake_memory: _FakeMemoryModule) -> None:
        _run(mms._tool_search("q", "workflow:foo"))
        call = fake_memory.load_memories.await_args
        assert call.args[0] == "workflow:foo"
        assert call.kwargs["query"] == "q"


# ---------------------------------------------------------------------------
# forget()
# ---------------------------------------------------------------------------


class TestForgetTool:
    def test_forget_rejects_empty_memory_id(self, fake_memory: _FakeMemoryModule) -> None:
        with pytest.raises(mms.MemoryValidationError):
            _run(mms._tool_forget(""))

    def test_forget_calls_delete_memory(self, fake_memory: _FakeMemoryModule) -> None:
        result = _run(mms._tool_forget("mem_1"))
        fake_memory.delete_memory.assert_awaited_once_with("mem_1")
        assert result == {"memory_id": "mem_1", "deleted": True}


# ---------------------------------------------------------------------------
# list_memories limit cap
# ---------------------------------------------------------------------------


class TestListMemories:
    def test_list_memories_enforces_limit_cap(self, fake_memory: _FakeMemoryModule) -> None:
        with pytest.raises(mms.MemoryValidationError):
            _run(mms._tool_list_memories("user:1", limit=mms.MAX_LIST_LIMIT + 1))

    def test_list_memories_default_limit_used(self, fake_memory: _FakeMemoryModule) -> None:
        _run(mms._tool_list_memories("user:1"))
        call = fake_memory.load_memories.await_args
        assert call.kwargs.get("limit") == mms.DEFAULT_LIST_LIMIT


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


class TestResources:
    def test_health_resource_probes_mem0_and_qdrant(
        self, fake_memory: _FakeMemoryModule
    ) -> None:
        health = _run(mms.resource_health())
        assert "mem0" in health
        assert "qdrant" in health
        # mem0_health_check was called via the fake
        fake_memory.memory_health_check.assert_awaited()
        # status field summarises both probes
        assert health["status"] in {"ok", "degraded"}

    def test_users_resource_returns_unique_user_ids(
        self, fake_memory: _FakeMemoryModule
    ) -> None:
        out = _run(mms.resource_users())
        assert "users" in out
        assert out["users"] == ["user:1", "user:2"]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_memory_qa_renders_with_user_id_substitution(self) -> None:
        text = mms.render_memory_qa_prompt("user:42", question="What do I prefer?")
        assert "user:42" in text
        assert "What do I prefer?" in text
        assert "ONLY the memories" in text

    def test_memory_qa_rejects_bad_user_id(self) -> None:
        with pytest.raises(mms.MemoryValidationError):
            mms.render_memory_qa_prompt("bad id!!")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCLI:
    def test_arg_parser_has_serve_subcommand(self) -> None:
        parser = mms.build_arg_parser()
        ns = parser.parse_args(["serve", "--transport", "stdio"])
        assert ns.action == "serve"
        assert ns.transport == "stdio"
        assert ns.port == 8765

    def test_serve_rejects_unknown_transport(self) -> None:
        with pytest.raises(mms.MemoryValidationError):
            mms.serve(transport="grpc")

    def test_cli_main_serve_invokes_serve(self) -> None:
        with patch.object(mms, "serve") as mocked:
            rc = mms.cli_main(["serve", "--transport", "stdio"])
        assert rc == 0
        mocked.assert_called_once()


class TestStreamableHttpAuth:
    """streamable-http must fail closed without MEMORY_MCP_TOKEN (wave 2 fix C)."""

    def test_stdio_unaffected_by_missing_token(self, monkeypatch) -> None:
        """stdio transport starts regardless of token (not network-exposed)."""
        monkeypatch.delenv("MEMORY_MCP_TOKEN", raising=False)
        fake_server = MagicMock()
        with patch.object(mms, "create_memory_mcp_server", return_value=fake_server):
            mms.serve(transport="stdio")
        fake_server.run.assert_called_once_with(transport="stdio")

    def test_streamable_http_refuses_without_token_in_non_local_mode(
        self, monkeypatch
    ) -> None:
        """No token + non-local mode => refuse to start (fail closed)."""
        monkeypatch.delenv("MEMORY_MCP_TOKEN", raising=False)
        fake_server = MagicMock()
        from sandcastle.config import settings as _settings

        monkeypatch.setattr(
            type(_settings), "is_local_mode",
            property(lambda self: False), raising=False,
        )
        with patch.object(mms, "create_memory_mcp_server", return_value=fake_server):
            with pytest.raises(mms.MemoryMCPError) as exc_info:
                mms.serve(transport="streamable-http")
        assert "MEMORY_MCP_TOKEN" in str(exc_info.value)
        fake_server.run.assert_not_called()

    def test_streamable_http_allowed_without_token_in_local_mode(
        self, monkeypatch
    ) -> None:
        """No token + local mode => allowed with a warning (dev convenience)."""
        monkeypatch.delenv("MEMORY_MCP_TOKEN", raising=False)
        fake_server = MagicMock()
        from sandcastle.config import settings as _settings

        monkeypatch.setattr(
            type(_settings), "is_local_mode",
            property(lambda self: True), raising=False,
        )
        with patch.object(mms, "create_memory_mcp_server", return_value=fake_server):
            mms.serve(transport="streamable-http")
        fake_server.run.assert_called_once_with(transport="streamable-http")

    def test_streamable_http_with_token_wraps_with_auth(self, monkeypatch) -> None:
        """A token => run our own uvicorn with the bearer-auth ASGI wrapper."""
        monkeypatch.setenv("MEMORY_MCP_TOKEN", "s3cret")
        fake_server = MagicMock()
        fake_app = MagicMock()
        fake_server.streamable_http_app.return_value = fake_app
        fake_server.settings.host = "127.0.0.1"
        fake_server.settings.log_level = "INFO"
        fake_uvicorn = MagicMock()
        with (
            patch.object(mms, "create_memory_mcp_server", return_value=fake_server),
            patch.dict("sys.modules", {"uvicorn": fake_uvicorn}),
        ):
            mms.serve(transport="streamable-http", port=9911)
        # FastMCP.run must NOT be used (no per-request auth there).
        fake_server.run.assert_not_called()
        fake_uvicorn.run.assert_called_once()
        wrapped_app = fake_uvicorn.run.call_args.args[0]
        assert isinstance(wrapped_app, mms._BearerAuthMiddleware)

    def test_bearer_middleware_rejects_missing_header(self) -> None:
        """The ASGI wrapper 401s a request with no Authorization header."""
        mw = mms._BearerAuthMiddleware(AsyncMock(), "tok")
        sent = []

        async def _send(msg):
            sent.append(msg)

        async def _receive():
            return {"type": "http.request"}

        scope = {"type": "http", "headers": []}
        asyncio.run(mw(scope, _receive, _send))
        assert sent[0]["status"] == 401

    def test_bearer_middleware_allows_valid_token(self) -> None:
        """The ASGI wrapper forwards a request with a matching bearer token."""
        inner = AsyncMock()
        mw = mms._BearerAuthMiddleware(inner, "tok")

        async def _send(msg):
            pass

        async def _receive():
            return {"type": "http.request"}

        scope = {"type": "http", "headers": [(b"authorization", b"Bearer tok")]}
        asyncio.run(mw(scope, _receive, _send))
        inner.assert_awaited_once()


# ---------------------------------------------------------------------------
# FastMCP wiring smoke test
# ---------------------------------------------------------------------------


class TestFastMCPWiring:
    def test_server_registers_four_tools(self) -> None:
        server = mms.create_memory_mcp_server()
        tools = asyncio.run(server.list_tools())
        names = sorted(t.name for t in tools)
        assert names == sorted(["add", "search", "forget", "list_memories"])

    def test_each_registered_tool_carries_examples_in_meta(self) -> None:
        server = mms.create_memory_mcp_server()
        tools = asyncio.run(server.list_tools())
        for tool in tools:
            assert tool.meta is not None
            assert "examples" in tool.meta
            assert 1 <= len(tool.meta["examples"]) <= 5
            assert "input_schema" in tool.meta
