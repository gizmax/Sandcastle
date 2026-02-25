"""Agent memory - persistent context across workflow runs via Mem0."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Lazy singleton
_memory_client = None


def _get_client():
    """Get or create Mem0 Memory client (local mode)."""
    global _memory_client
    if _memory_client is None:
        from mem0 import Memory
        _memory_client = Memory()  # Local mode: SQLite + sentence-transformers
    return _memory_client


def resolve_scope_id(memory_config, workflow_name: str) -> str:
    """Map scope config to a Mem0 user_id."""
    scope = memory_config.scope if memory_config else "workflow"
    if scope == "agent" and memory_config and memory_config.agent:
        return f"agent:{memory_config.agent}"
    elif scope == "global":
        return "global"
    else:
        return f"workflow:{workflow_name}"


async def load_memories(
    scope_id: str,
    query: str = "",
    limit: int = 10,
) -> list[dict]:
    """Load relevant memories. If query provided, semantic search; otherwise get_all."""
    import asyncio
    try:
        client = _get_client()
        if query:
            results = await asyncio.to_thread(
                client.search, query, user_id=scope_id, limit=limit,
            )
        else:
            results = await asyncio.to_thread(
                client.get_all, user_id=scope_id,
            )
        # Normalize: mem0 returns list of dicts with "id", "memory", "metadata", etc.
        memories = []
        items = results.get("results", results) if isinstance(results, dict) else results
        for item in items[:limit]:
            memories.append({
                "id": item.get("id", ""),
                "memory": item.get("memory", ""),
                "metadata": item.get("metadata", {}),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
            })
        return memories
    except Exception as e:
        logger.warning(f"Failed to load memories for {scope_id}: {e}")
        return []


async def save_memory(
    scope_id: str,
    content: str,
    metadata: dict | None = None,
    run_id: str = "",
) -> list[dict]:
    """Add memory content. Mem0 auto-extracts facts and deduplicates."""
    import asyncio
    meta = metadata or {}
    if run_id:
        meta["run_id"] = run_id
    try:
        client = _get_client()
        result = await asyncio.to_thread(
            client.add, content, user_id=scope_id, metadata=meta,
        )
        return result if isinstance(result, list) else [result]
    except Exception as e:
        logger.warning(f"Failed to save memory for {scope_id}: {e}")
        return []


async def delete_memory(memory_id: str) -> bool:
    """Delete a specific memory by ID."""
    import asyncio
    try:
        client = _get_client()
        await asyncio.to_thread(client.delete, memory_id)
        return True
    except Exception as e:
        logger.warning(f"Failed to delete memory {memory_id}: {e}")
        return False


async def delete_all_memories(scope_id: str) -> bool:
    """Delete all memories for a scope."""
    import asyncio
    try:
        client = _get_client()
        await asyncio.to_thread(client.delete_all, user_id=scope_id)
        return True
    except Exception as e:
        logger.warning(f"Failed to delete all memories for {scope_id}: {e}")
        return False


def format_memories_for_prompt(memories: list[dict], max_chars: int = 2000) -> str:
    """Format memories as a prompt block for injection."""
    if not memories:
        return ""
    lines = ["[Agent Memory]"]
    total = 0
    for m in memories:
        text = m.get("memory", "")
        if not text:
            continue
        line = f"- {text}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    lines.append("[End of Agent Memory]")
    return "\n".join(lines)


def _reset_client():
    """Reset singleton for testing."""
    global _memory_client
    _memory_client = None
