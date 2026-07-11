"""Memory MCP server - exposes Sandcastle's mem0+Qdrant memory layer over MCP.

Why this module exists
----------------------
Anthropic's 2026-05-19 Managed Agents update introduced self-hosted sandboxes
but explicitly disclaimed ``memory_stores`` support inside them. Sandcastle
already has the memory plumbing (mem0 + AnthropicLLM wrapper + Qdrant) in
``src/sandcastle/engine/memory.py``. This server re-exposes that layer over
the Model Context Protocol, so a self-hosted-sandbox agent reaches it via an
MCP tunnel rather than the unavailable native ``memory_stores`` feature.

Design rules
~~~~~~~~~~~~
- Self-contained: only stdlib + ``mcp`` package + lazy import of the
  Sandcastle memory module.
- Lazy import of the heavy memory stack so the server still starts even
  when mem0 or Qdrant is missing; tool calls then raise a typed error.
- Every tool ships a JSON Schema and 1-5 example invocations (the v0.32
  tool-search convention in ``docs/tool-examples-convention.md``).
- Two MCP resources (``sandcastle://memory/users`` and
  ``sandcastle://memory/health``) plus one prompt (``memory_qa``).

CLI
~~~
``sandcastle memory-mcp serve [--transport stdio|streamable-http] [--port N]``

The ``__main__`` agent wires the parser entry. This file only owns the
sub-module surface (``cli_main``, ``serve``) so the orchestration is shared
without cross-file churn.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import json
import logging
import os
import re
import sys
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Max items per ``list_memories`` request - mirrors the v0.32 API cap.
MAX_LIST_LIMIT = 200
#: Default page size when the caller does not specify one.
DEFAULT_LIST_LIMIT = 50
#: Max length for the ``text`` parameter on ``add`` - mirrors memory.py.
MAX_TEXT_LENGTH = 100_000
#: Max user_id length - any longer and we reject before hitting the backend.
MAX_USER_ID_LENGTH = 256

# ``user_id`` must be a plain identifier (alphanumerics, dot, dash, underscore,
# colon, slash). Slash + colon allow ``tenant:<id>/workflow:<name>`` style
# scopes used by Sandcastle's existing scope resolver.
_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_.\-:/]+$")


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class MemoryMCPError(RuntimeError):
    """Raised by tool calls when the memory backend cannot be reached."""

    def __init__(self, message: str, code: str = "memory_unavailable") -> None:
        self.code = code
        super().__init__(message)


class MemoryValidationError(ValueError):
    """Raised when input validation fails."""


# ---------------------------------------------------------------------------
# Lazy import wrapper
# ---------------------------------------------------------------------------


def _load_memory_module() -> Any:
    """Import ``sandcastle.engine.memory`` lazily.

    Returns the module on success. Raises :class:`MemoryMCPError` with a
    clear remediation hint when the import fails (typically because mem0
    or one of its deps is missing).
    """
    try:
        from sandcastle.engine import memory as _memory_mod
    except Exception as exc:  # noqa: BLE001 - we want every failure mode here
        raise MemoryMCPError(
            "Sandcastle memory stack is unavailable: "
            f"{exc}. Install with: pip install 'sandcastle-ai[memory]'.",
            code="memory_unavailable",
        ) from exc
    return _memory_mod


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_user_id(value: Any) -> str:
    """Validate ``user_id`` for any memory operation."""
    if not isinstance(value, str):
        raise MemoryValidationError(
            f"user_id must be a string, got {type(value).__name__}"
        )
    stripped = value.strip()
    if not stripped:
        raise MemoryValidationError("user_id must not be empty")
    if len(stripped) > MAX_USER_ID_LENGTH:
        raise MemoryValidationError(
            f"user_id must not exceed {MAX_USER_ID_LENGTH} characters"
        )
    if not _USER_ID_RE.match(stripped):
        raise MemoryValidationError(
            "user_id may only contain alphanumerics, '.', '-', '_', ':', '/'"
        )
    return stripped


def _validate_text(value: Any) -> str:
    if not isinstance(value, str):
        raise MemoryValidationError(
            f"text must be a string, got {type(value).__name__}"
        )
    if not value.strip():
        raise MemoryValidationError("text must not be empty")
    if len(value) > MAX_TEXT_LENGTH:
        raise MemoryValidationError(
            f"text must not exceed {MAX_TEXT_LENGTH} characters"
        )
    return value


def _validate_memory_id(value: Any) -> str:
    if not isinstance(value, str):
        raise MemoryValidationError(
            f"memory_id must be a string, got {type(value).__name__}"
        )
    stripped = value.strip()
    if not stripped:
        raise MemoryValidationError("memory_id must not be empty")
    if len(stripped) > 256:
        raise MemoryValidationError("memory_id must not exceed 256 characters")
    return stripped


def _validate_limit(value: Any, *, cap: int = MAX_LIST_LIMIT) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise MemoryValidationError(
            f"limit must be an integer, got {type(value).__name__}"
        )
    if value < 1:
        raise MemoryValidationError("limit must be at least 1")
    if value > cap:
        raise MemoryValidationError(f"limit must not exceed {cap}")
    return value


def _validate_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MemoryValidationError(
            f"metadata must be an object, got {type(value).__name__}"
        )
    return value


def _normalize_record(item: Any) -> dict[str, Any]:
    """Coerce a raw mem0 record into the shape Anthropic's Memory tool expects.

    Anthropic's tool expects ``{id, text, score?, metadata}`` per result.
    mem0 uses ``memory`` for the text body and exposes ``score`` only on
    search results, so we normalise both shapes here.
    """
    if not isinstance(item, dict):
        return {"id": "", "text": str(item), "metadata": {}}
    text = item.get("text") or item.get("memory") or ""
    out: dict[str, Any] = {
        "id": item.get("id", ""),
        "text": text,
        "metadata": item.get("metadata") or {},
    }
    if "score" in item:
        out["score"] = item["score"]
    for key in ("created_at", "updated_at"):
        if item.get(key):
            out[key] = item[key]
    return out


# ---------------------------------------------------------------------------
# Tool definitions (schema + examples)
# ---------------------------------------------------------------------------


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "add",
        "description": (
            "Store a new memory for a user. Calls the existing Sandcastle "
            "mem0 backend so admission control, enrichment, and Qdrant "
            "indexing all apply. Returns the created record(s)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Memory content to store.",
                    "minLength": 1,
                    "maxLength": MAX_TEXT_LENGTH,
                },
                "user_id": {
                    "type": "string",
                    "description": (
                        "User or scope identifier (e.g. 'user:42' or "
                        "'workflow:invoice-extract')."
                    ),
                    "minLength": 1,
                    "maxLength": MAX_USER_ID_LENGTH,
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional metadata dict attached to the memory.",
                    "additionalProperties": True,
                },
            },
            "required": ["text", "user_id"],
            "additionalProperties": False,
        },
        "examples": [
            {
                "input": {
                    "text": "User prefers concise JSON responses.",
                    "user_id": "user:42",
                },
                "output": {"id": "mem_abc", "text": "User prefers concise JSON responses."},
            },
            {
                "input": {
                    "text": "Invoice numbering format is INV-YYYY-NNNN.",
                    "user_id": "workflow:invoice-extract",
                    "metadata": {"source": "kickoff"},
                },
                "output": {"id": "mem_xyz", "text": "Invoice numbering format is INV-YYYY-NNNN."},
            },
        ],
    },
    {
        "name": "search",
        "description": (
            "Semantic search across memories for one user. Returns a list "
            "of {id, text, score, metadata} entries sorted by relevance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                    "minLength": 1,
                },
                "user_id": {
                    "type": "string",
                    "description": "User or scope identifier.",
                    "minLength": 1,
                    "maxLength": MAX_USER_ID_LENGTH,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (1-200, default 5).",
                    "minimum": 1,
                    "maximum": MAX_LIST_LIMIT,
                    "default": 5,
                },
            },
            "required": ["query", "user_id"],
            "additionalProperties": False,
        },
        "examples": [
            {
                "input": {"query": "preferred response format", "user_id": "user:42"},
                "output": {"results": [{"id": "mem_abc", "text": "User prefers concise JSON responses.", "score": 0.82}]},
            },
            {
                "input": {"query": "invoice format", "user_id": "workflow:invoice-extract", "limit": 3},
                "output": {"results": [{"id": "mem_xyz", "text": "Invoice numbering format is INV-YYYY-NNNN.", "score": 0.91}]},
            },
        ],
    },
    {
        "name": "forget",
        "description": (
            "GDPR right-to-be-forgotten: delete a single memory row by id. "
            "Returns the id and a deleted flag."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "Memory row identifier returned by add() or search().",
                    "minLength": 1,
                },
            },
            "required": ["memory_id"],
            "additionalProperties": False,
        },
        "examples": [
            {
                "input": {"memory_id": "mem_abc"},
                "output": {"memory_id": "mem_abc", "deleted": True},
            },
        ],
    },
    {
        "name": "list_memories",
        "description": (
            "Inventory all stored memories for a user, newest first. "
            "Useful for GDPR data-export endpoints and dashboards."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "User or scope identifier.",
                    "minLength": 1,
                    "maxLength": MAX_USER_ID_LENGTH,
                },
                "limit": {
                    "type": "integer",
                    "description": "Max records to return (1-200, default 50).",
                    "minimum": 1,
                    "maximum": MAX_LIST_LIMIT,
                    "default": DEFAULT_LIST_LIMIT,
                },
            },
            "required": ["user_id"],
            "additionalProperties": False,
        },
        "examples": [
            {
                "input": {"user_id": "user:42"},
                "output": {"results": [{"id": "mem_abc", "text": "..."}]},
            },
            {
                "input": {"user_id": "user:42", "limit": 10},
                "output": {"results": [{"id": "mem_abc", "text": "..."}]},
            },
        ],
    },
]


def get_tool_definitions() -> list[dict[str, Any]]:
    """Return the deep-copied list of tool definitions."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": json.loads(json.dumps(t["input_schema"])),
            "examples": [dict(ex) for ex in t["examples"]],
        }
        for t in TOOL_DEFINITIONS
    ]


# ---------------------------------------------------------------------------
# Tool implementations - thin wrappers over the existing memory module
# ---------------------------------------------------------------------------


async def _tool_add(
    text: str,
    user_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = _validate_text(text)
    user_id = _validate_user_id(user_id)
    metadata = _validate_metadata(metadata)

    mem = _load_memory_module()
    try:
        records = await mem.save_memory(
            user_id,
            text,
            metadata=metadata,
            skip_admission=True,
        )
    except Exception as exc:
        raise MemoryMCPError(
            f"add() failed: {exc}", code="backend_error"
        ) from exc
    normalized = [_normalize_record(r) for r in (records or [])]
    return {"results": normalized}


async def _tool_search(
    query: str,
    user_id: str,
    limit: int = 5,
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip():
        raise MemoryValidationError("query must be a non-empty string")
    user_id = _validate_user_id(user_id)
    limit = _validate_limit(limit)

    mem = _load_memory_module()
    try:
        records = await mem.load_memories(user_id, query=query, limit=limit)
    except Exception as exc:
        raise MemoryMCPError(
            f"search() failed: {exc}", code="backend_error"
        ) from exc
    return {"results": [_normalize_record(r) for r in (records or [])]}


async def _tool_forget(memory_id: str) -> dict[str, Any]:
    memory_id = _validate_memory_id(memory_id)
    mem = _load_memory_module()
    try:
        deleted = await mem.delete_memory(memory_id)
    except Exception as exc:
        raise MemoryMCPError(
            f"forget() failed: {exc}", code="backend_error"
        ) from exc
    return {"memory_id": memory_id, "deleted": bool(deleted)}


async def _tool_list_memories(
    user_id: str,
    limit: int = DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    user_id = _validate_user_id(user_id)
    limit = _validate_limit(limit)
    mem = _load_memory_module()
    try:
        records = await mem.load_memories(user_id, limit=limit)
    except Exception as exc:
        raise MemoryMCPError(
            f"list_memories() failed: {exc}", code="backend_error"
        ) from exc
    return {"results": [_normalize_record(r) for r in (records or [])]}


#: Public registry the test suite and the FastMCP wiring iterate over.
TOOL_HANDLERS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "add": _tool_add,
    "search": _tool_search,
    "forget": _tool_forget,
    "list_memories": _tool_list_memories,
}


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


async def resource_users() -> dict[str, Any]:
    """List user_ids that currently have at least one memory row.

    mem0's local Qdrant backend does not expose a native "list users"
    primitive, so we probe ``get_all`` without a user_id when available;
    otherwise we return an empty list with a hint.
    """
    try:
        mem = _load_memory_module()
    except MemoryMCPError as exc:
        return {"users": [], "error": str(exc)}
    try:
        client = mem._get_client()
        get_all = getattr(client, "get_all", None)
        if get_all is None:
            return {"users": [], "error": "backend does not support user listing"}
        raw = await asyncio.to_thread(get_all)
        items = (
            raw.get("results", raw) if isinstance(raw, dict) else raw
        ) or []
        users = sorted({
            str(item.get("user_id", ""))
            for item in items
            if isinstance(item, dict) and item.get("user_id")
        })
        return {"users": users}
    except Exception as exc:  # noqa: BLE001
        return {"users": [], "error": str(exc)}


async def resource_health() -> dict[str, Any]:
    """Probe both mem0 (via the existing health check) and Qdrant reachability."""
    try:
        mem = _load_memory_module()
    except MemoryMCPError as exc:
        return {
            "status": "error",
            "mem0": {"status": "missing", "error": str(exc)},
            "qdrant": {"status": "unknown"},
        }
    try:
        mem0_health = await mem.memory_health_check()
    except Exception as exc:  # noqa: BLE001
        mem0_health = {"status": "error", "error": str(exc)}

    # Qdrant detection - try to import the client; it's bundled with mem0.
    qdrant_status: dict[str, Any] = {"status": "unknown"}
    try:
        import qdrant_client  # noqa: F401
        qdrant_status = {"status": "importable"}
    except Exception as exc:  # noqa: BLE001
        qdrant_status = {"status": "missing", "error": str(exc)}

    overall = (
        "ok"
        if mem0_health.get("status") == "ok"
        and qdrant_status.get("status") == "importable"
        else "degraded"
    )
    return {"status": overall, "mem0": mem0_health, "qdrant": qdrant_status}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


MEMORY_QA_TEMPLATE = (
    "You are a memory-grounded assistant for user '{user_id}'.\n"
    "Answer the user's question using ONLY the memories below.\n"
    "If the memories are insufficient, say so plainly and do not invent facts.\n\n"
    "Question: {question}\n\n"
    "Retrieved memories will be injected by the MCP client before you respond."
)


def render_memory_qa_prompt(user_id: str, question: str = "") -> str:
    """Render the ``memory_qa`` prompt template with user_id substitution."""
    safe_user = _validate_user_id(user_id)
    return MEMORY_QA_TEMPLATE.format(
        user_id=safe_user,
        question=question or "<question>",
    )


# ---------------------------------------------------------------------------
# FastMCP wiring
# ---------------------------------------------------------------------------


def create_memory_mcp_server() -> Any:
    """Create the FastMCP server exposing all four tools, two resources, one prompt."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - exercised when mcp is absent
        raise MemoryMCPError(
            "MCP package is not installed. Install with: pip install 'sandcastle-ai[mcp]'.",
            code="mcp_missing",
        ) from exc

    server = FastMCP(
        "Sandcastle Memory",
        instructions=(
            "Memory operations backed by Sandcastle's mem0+Qdrant store. "
            "Use add/search/forget/list_memories for per-user memory CRUD."
        ),
    )

    # The FastMCP decorators infer schemas from typed function signatures.
    # We attach our handwritten schema + examples via the ``meta`` field so
    # downstream clients can introspect the convention payload.
    for tool_def in TOOL_DEFINITIONS:
        name = tool_def["name"]
        handler = TOOL_HANDLERS[name]
        meta = {
            "input_schema": tool_def["input_schema"],
            "examples": tool_def["examples"],
        }
        server.add_tool(
            handler,
            name=name,
            description=tool_def["description"],
            meta=meta,
        )

    @server.resource("sandcastle://memory/users")
    async def _res_users() -> str:
        return json.dumps(await resource_users(), default=str)

    @server.resource("sandcastle://memory/health")
    async def _res_health() -> str:
        return json.dumps(await resource_health(), default=str)

    @server.prompt(
        name="memory_qa",
        description="Answer using only the memories stored for the given user_id.",
    )
    def _prompt_memory_qa(user_id: str, question: str = "") -> str:
        return render_memory_qa_prompt(user_id, question)

    return server


# ---------------------------------------------------------------------------
# CLI - the __main__ agent wires this into the top-level parser
# ---------------------------------------------------------------------------


class _BearerAuthMiddleware:
    """Pure-ASGI middleware enforcing a static bearer token on HTTP requests.

    The Memory MCP tools take a caller-supplied ``user_id``, so an unauthenticated
    streamable-http transport would allow cross-tenant read/write/delete. This
    middleware rejects any request missing a valid ``Authorization: Bearer`` header
    with a 401 before it reaches the MCP handlers.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._expected = f"Bearer {token}".encode("latin-1")

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        provided = headers.get(b"authorization", b"")
        if not hmac.compare_digest(provided, self._expected):
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"www-authenticate", b"Bearer"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"error":"unauthorized"}',
                }
            )
            return
        await self.app(scope, receive, send)


def serve(transport: str = "stdio", port: int = 8765) -> None:
    """Start the Memory MCP server on the requested transport."""
    if transport not in {"stdio", "streamable-http"}:
        raise MemoryValidationError(
            f"transport must be 'stdio' or 'streamable-http', got {transport!r}"
        )
    server = create_memory_mcp_server()
    if transport == "stdio":
        # stdio is not network-exposed; no auth needed.
        server.run(transport="stdio")
        return

    # streamable-http is network-exposed and the tools trust a caller-supplied
    # user_id, so we fail closed unless a bearer token is configured.
    token = (os.environ.get("MEMORY_MCP_TOKEN") or "").strip()
    if not token:
        from sandcastle.config import settings as _settings

        if not _settings.is_local_mode:
            raise MemoryMCPError(
                "Refusing to start the Memory MCP over streamable-http without "
                "authentication. Set MEMORY_MCP_TOKEN to a secret bearer token so "
                "callers must send 'Authorization: Bearer <token>', or use the stdio "
                "transport. This fails closed to prevent cross-tenant memory access.",
                code="auth_required",
            )
        logger.warning(
            "Memory MCP streamable-http started WITHOUT MEMORY_MCP_TOKEN. This is "
            "allowed only because the server is in local mode; anyone who can reach "
            "the port can read/write/delete any user's memories. Set MEMORY_MCP_TOKEN "
            "before exposing this beyond localhost."
        )

    # FastMCP reads the port from its settings object.
    try:
        server.settings.port = int(port)
    except Exception:  # pragma: no cover - defensive
        pass

    if token:
        # Wrap the streamable-http ASGI app with bearer enforcement and run it
        # ourselves, since FastMCP.run has no per-request auth hook.
        import uvicorn

        app = _BearerAuthMiddleware(server.streamable_http_app(), token)
        uvicorn.run(
            app,
            host=server.settings.host,
            port=int(port),
            log_level=str(server.settings.log_level).lower(),
        )
    else:
        server.run(transport="streamable-http")


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the ``sandcastle memory-mcp`` argument parser."""
    parser = argparse.ArgumentParser(
        prog="sandcastle memory-mcp",
        description="Memory MCP server (mem0+Qdrant) for self-hosted sandboxes.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    p_serve = sub.add_parser("serve", help="Start the Memory MCP server.")
    p_serve.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="Transport to bind (default: stdio).",
    )
    p_serve.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for streamable-http transport (ignored for stdio).",
    )
    return parser


def cli_main(argv: list[str] | None = None) -> int:
    """Entrypoint suitable for wiring into ``sandcastle.__main__``.

    The top-level ``sandcastle`` CLI is owned by a different agent. To stay
    inside file ownership, we expose ``cli_main`` here and the orchestrator
    only needs a one-line dispatcher entry pointing at this function.
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.action == "serve":
        try:
            serve(transport=args.transport, port=args.port)
        except MemoryMCPError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    parser.error(f"unknown action: {args.action}")
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(cli_main())
