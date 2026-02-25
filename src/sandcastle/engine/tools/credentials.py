"""Credential resolver for tool connectors.

Reads tool credentials from environment variables (TOOL_* prefix),
validates availability, and masks values for safe display.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def get_tool_credentials(tool_names: list[str]) -> dict[str, str]:
    """Resolve credentials for the given tools from environment variables.

    Supports named connections (e.g. "postgresql:analytics"). For named
    connections, credentials are looked up from the DB ToolConnection table.
    Plain tool names fall back to environment variables.

    Returns a dict of env_var_name -> value for all credentials that are set.
    Missing credentials are omitted (tools may work in limited mode).
    """
    from sandcastle.engine.tools.registry import get_tool, parse_tool_ref

    credentials: dict[str, str] = {}
    # Collect named connections that need DB lookup
    named_refs: list[tuple[str, str]] = []

    for ref in tool_names:
        base_name, conn_name = parse_tool_ref(ref)
        try:
            tool = get_tool(base_name)
        except KeyError:
            continue

        if conn_name:
            named_refs.append((base_name, conn_name))
        else:
            for env_var in tool.credential_env_vars:
                value = os.environ.get(env_var, "")
                if value:
                    credentials[env_var] = value

    # Resolve named connections from DB (sync helper for use in sandbox setup)
    if named_refs:
        db_creds = _resolve_named_connections(named_refs)
        credentials.update(db_creds)

    return credentials


def _resolve_named_connections(refs: list[tuple[str, str]]) -> dict[str, str]:
    """Look up named connection credentials from the database.

    Falls back to env vars if connection not found in DB.
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already in async context - use thread to run sync query
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_resolve_named_connections_sync, refs).result(timeout=5)
    else:
        return asyncio.run(_resolve_named_connections_async(refs))


async def _resolve_named_connections_async(
    refs: list[tuple[str, str]],
) -> dict[str, str]:
    """Async DB lookup for named connection credentials."""
    from sqlalchemy import select

    from sandcastle.engine.tools.registry import get_tool
    from sandcastle.models.db import ToolConnection, async_session

    credentials: dict[str, str] = {}
    async with async_session() as session:
        for tool_name, conn_name in refs:
            result = await session.execute(
                select(ToolConnection).where(
                    ToolConnection.tool_name == tool_name,
                    ToolConnection.connection_name == conn_name,
                )
            )
            conn = result.scalar_one_or_none()
            if conn and conn.credentials:
                from sandcastle.engine.crypto import decrypt_credentials

                credentials.update(decrypt_credentials(conn.credentials))
            else:
                # Fallback to env vars
                try:
                    tool = get_tool(tool_name)
                except KeyError:
                    continue
                for env_var in tool.credential_env_vars:
                    value = os.environ.get(env_var, "")
                    if value:
                        credentials[env_var] = value
    return credentials


def _resolve_named_connections_sync(
    refs: list[tuple[str, str]],
) -> dict[str, str]:
    """Sync wrapper for named connection resolution."""
    import asyncio

    return asyncio.run(_resolve_named_connections_async(refs))


def validate_tool_credentials(tool_names: list[str]) -> dict[str, dict]:
    """Check which tools have their credentials configured.

    Returns a dict of tool_name -> {
        "configured": bool,
        "missing": list[str],  # Missing env var names
        "present": list[str],  # Present env var names
    }
    """
    from sandcastle.engine.tools.registry import get_tool

    result: dict[str, dict] = {}
    for name in tool_names:
        try:
            tool = get_tool(name)
        except KeyError:
            result[name] = {
                "configured": False,
                "missing": [],
                "present": [],
                "error": "unknown tool",
            }
            continue

        missing = []
        present = []
        for env_var in tool.credential_env_vars:
            if os.environ.get(env_var):
                present.append(env_var)
            else:
                missing.append(env_var)

        result[name] = {
            "configured": len(missing) == 0 and len(tool.credential_env_vars) > 0,
            "missing": missing,
            "present": present,
        }
    return result


def mask_credential(value: str) -> str:
    """Mask a credential value for safe display.

    Shows first 4 and last 4 characters, with dots in between.
    Short values are fully masked.
    """
    if len(value) <= 8:
        return "*" * len(value)
    return value[:4] + "..." + value[-4:]


# Known credential token prefixes for auto-redaction
CREDENTIAL_PATTERNS: list[str] = [
    r"xoxb-[\w\-]+",  # Slack bot token
    r"xoxp-[\w\-]+",  # Slack user token
    r"ghp_[\w]+",  # GitHub personal access token
    r"gho_[\w]+",  # GitHub OAuth token
    r"ghs_[\w]+",  # GitHub server token
    r"sk-[\w]+",  # OpenAI / generic API key
    r"Bearer\s+[\w\-\.]+",  # Bearer token
    r"Basic\s+[\w=]+",  # Basic auth
    r"AKIA[\w]+",  # AWS access key
    r"(?:psql|postgres(?:ql)?):\/\/\S+",  # PostgreSQL connection string
    r"https?://hooks\.slack\.com/\S+",  # Slack webhook URL
]
