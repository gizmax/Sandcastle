"""Agent memory v2 - persistent context across workflow runs via Mem0.

Features beyond v1:
- Write admission control (importance scoring, novelty check)
- Memory decay / TTL with relevance scoring
- Conflict detection via word overlap heuristics
- Graph memory support (Neo4j, optional)
- Structured memory enrichment (keywords, auto-tags)
- Configurable backends (local, cloud)
- Custom exception hierarchy with error kinds
- Health check endpoint
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class MemoryErrorKind(Enum):
    """Categorizes memory subsystem errors."""
    BACKEND_UNAVAILABLE = "backend_unavailable"
    ADMISSION_REJECTED = "admission_rejected"
    CONFLICT_DETECTED = "conflict_detected"
    DECAY_EXPIRED = "decay_expired"
    UNKNOWN = "unknown"


class MemoryError(Exception):
    """Base exception for memory subsystem."""

    def __init__(
        self,
        message: str,
        kind: MemoryErrorKind = MemoryErrorKind.UNKNOWN,
    ) -> None:
        self.kind = kind
        super().__init__(message)


class MemoryAdmissionError(MemoryError):
    """Raised when a memory write is rejected by admission control."""

    def __init__(self, message: str, score: float = 0.0) -> None:
        self.score = score
        super().__init__(message, MemoryErrorKind.ADMISSION_REJECTED)


class MemoryBackendError(MemoryError):
    """Raised when the memory backend is unreachable or fails."""

    def __init__(self, message: str) -> None:
        super().__init__(message, MemoryErrorKind.BACKEND_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Anthropic LLM adapter (removes top_p for Claude 4.5 compatibility)
# ---------------------------------------------------------------------------


class _SandcastleAnthropicLLM:
    """Anthropic LLM adapter that removes top_p.

    Claude 4.5 models reject requests that include both temperature and
    top_p. mem0's base LLM config sends both by default, so we override
    _get_common_params to strip top_p before the Anthropic API call.

    This replaces a fragile monkey-patch on the client instance with a
    proper subclass that survives upstream mem0 refactors.
    """

    _base_cls = None  # resolved lazily to avoid import at module level

    @classmethod
    def _resolve_base(cls):
        """Import and cache the real AnthropicLLM class from mem0."""
        if cls._base_cls is None:
            from mem0.llms.anthropic import AnthropicLLM
            cls._base_cls = AnthropicLLM
        return cls._base_cls

    def __new__(cls, config):
        """Dynamically create a subclass of AnthropicLLM with the override.

        We use __new__ + dynamic subclass because AnthropicLLM is only
        available when mem0 is installed, and we don't want a top-level
        import dependency.
        """
        base = cls._resolve_base()

        # Build the subclass once and cache it
        if not hasattr(cls, "_built_subclass"):
            class _Patched(base):
                """AnthropicLLM that strips top_p from common params."""

                def _get_common_params(self, **kwargs):
                    params = super()._get_common_params(**kwargs)
                    params.pop("top_p", None)
                    return params

            _Patched.__name__ = "_SandcastleAnthropicLLM"
            _Patched.__qualname__ = "_SandcastleAnthropicLLM"
            cls._built_subclass = _Patched

        return cls._built_subclass(config)


# ---------------------------------------------------------------------------
# Client management - lazy singletons per backend
# ---------------------------------------------------------------------------

_clients: dict[str, Any] = {}
_clients_lock = threading.Lock()
_graph_client: Any | None = None
_graph_client_lock = threading.Lock()
_graph_client_initialized = False  # True once we have attempted initialization

# English stopwords for keyword extraction
_STOPWORDS: set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "and", "but", "or", "nor", "not", "so", "yet", "for", "at",
    "by", "in", "of", "on", "to", "up", "it", "its", "this",
    "that", "with", "from", "into", "about", "than", "then",
    "also", "just", "more", "some", "any", "each", "all", "both",
    "few", "most", "other", "such", "only", "own", "same", "very",
    "too", "out", "over", "when", "where", "how", "what", "which",
    "who", "whom", "there", "here", "after", "before", "between",
}

# Scope ID format: must be "workflow:<name>", "agent:<name>", or "global".
# Also accept the internal "__health_check__" scope for health probes.
_VALID_SCOPE_RE = re.compile(
    r"^(workflow:[a-zA-Z0-9_. -]{1,200}"
    r"|agent:[a-zA-Z0-9_. -]{1,200}"
    r"|global"
    r"|__health_check__)$"
)


def _validate_scope(scope_id: str) -> None:
    """Raise ValueError if scope_id has an unexpected format."""
    if not _VALID_SCOPE_RE.match(scope_id):
        raise ValueError(
            f"Invalid scope_id format: {scope_id!r}. "
            "Expected 'workflow:<name>', 'agent:<name>', or 'global'."
        )


def _get_client(backend: str = "local") -> Any:
    """Get or create a Mem0 client for the given backend.

    Supported backends:
      - "local" (default): Anthropic LLM + fastembed local embeddings via Memory()
      - "cloud": Mem0 cloud platform (not yet implemented)

    Thread-safe: a lock prevents duplicate initialization under concurrent calls.
    """
    # Fast path (no lock needed once initialized)
    if backend in _clients:
        return _clients[backend]

    if backend == "cloud":
        raise NotImplementedError(
            "Cloud memory backend is not yet implemented. "
            "Set SANDBOX_MEMORY_BACKEND=local or install mem0ai "
            "and use local mode. Cloud support requires a Mem0 "
            "Platform API key - coming in a future release."
        )

    if backend != "local":
        raise MemoryBackendError(
            f"Unknown memory backend: {backend!r}. "
            "Supported: 'local', 'cloud'."
        )

    with _clients_lock:
        # Re-check inside the lock (double-checked locking pattern)
        if backend in _clients:
            return _clients[backend]
        try:
            from mem0 import Memory
            from pathlib import Path

            # Use a persistent path for Qdrant so the collection survives
            # server restarts. Without this, Qdrant defaults to /tmp/qdrant
            # which is cleared on reboot, causing PointStruct validation
            # errors when mem0 resets the index and encounters memories
            # with None vectors.
            qdrant_path = os.getenv(
                "SANDCASTLE_QDRANT_PATH",
                str(Path.home() / ".sandcastle" / "data" / "qdrant"),
            )
            Path(qdrant_path).mkdir(parents=True, exist_ok=True)

            # Use Anthropic LLM + fastembed local embeddings (no OpenAI needed)
            config = {
                "llm": {
                    "provider": "anthropic",
                    "config": {
                        "model": "claude-haiku-4-5-20251001",
                        "temperature": 0.1,
                        "max_tokens": 2000,
                    },
                },
                "embedder": {
                    "provider": "fastembed",
                    "config": {
                        "model": "BAAI/bge-small-en-v1.5",
                    },
                },
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "collection_name": "mem0_local",
                        "embedding_model_dims": 384,
                        "path": qdrant_path,
                    },
                },
            }
            client = Memory.from_config(config)

            # Claude 4.5 models reject temperature+top_p together.
            # Replace the default AnthropicLLM with our patched subclass
            # that strips top_p from API params.
            try:
                client.llm = _SandcastleAnthropicLLM(client.llm.config)
            except Exception as llm_exc:
                logger.warning(
                    "Could not replace LLM with _SandcastleAnthropicLLM, "
                    "falling back to monkey-patch: %s", llm_exc,
                )
                # Fallback: monkey-patch if subclass creation fails
                _orig = client.llm._get_common_params

                def _patched(**kwargs):
                    params = _orig(**kwargs)
                    params.pop("top_p", None)
                    return params

                client.llm._get_common_params = _patched

            _clients[backend] = client
            return client
        except Exception as exc:
            raise MemoryBackendError(
                f"Failed to initialize local Mem0 backend: {exc}"
            ) from exc


def _get_graph_client() -> Any | None:
    """Return a graph-enabled Mem0 client if Neo4j deps are available.

    Returns None if graph dependencies are missing or init fails.
    Thread-safe: uses a lock and a sentinel flag to avoid duplicate attempts.
    """
    global _graph_client, _graph_client_initialized

    # Fast path: already initialized (success or failure)
    if _graph_client_initialized:
        return _graph_client

    with _graph_client_lock:
        # Double-checked locking
        if _graph_client_initialized:
            return _graph_client

        try:
            from mem0 import Memory
            config = {
                "graph_store": {
                    "provider": "neo4j",
                    "config": {
                        "url": os.getenv("NEO4J_URL", "bolt://localhost:7687"),
                        "username": os.getenv("NEO4J_USERNAME", "neo4j"),
                        "password": os.getenv("NEO4J_PASSWORD", ""),
                    },
                },
            }
            _graph_client = Memory.from_config(config)
            logger.info("Graph memory client initialized (Neo4j)")
        except Exception as exc:
            logger.debug(
                "Graph memory unavailable (Neo4j deps missing or "
                "connection failed): %s", exc,
            )
            _graph_client = None
        finally:
            _graph_client_initialized = True

        return _graph_client


# ---------------------------------------------------------------------------
# Scope resolution (unchanged public API)
# ---------------------------------------------------------------------------


def resolve_scope_id(
    memory_config: Any,
    workflow_name: str,
) -> str:
    """Map scope config to a Mem0 user_id."""
    scope = memory_config.scope if memory_config else "workflow"
    if scope == "agent" and memory_config and memory_config.agent:
        return f"agent:{memory_config.agent}"
    elif scope == "global":
        return "global"
    else:
        return f"workflow:{workflow_name}"


# ---------------------------------------------------------------------------
# Write admission control
# ---------------------------------------------------------------------------

_STRUCTURED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\{.*:.*\}"),           # JSON-like
    re.compile(r"\[.*,.*\]"),           # Array-like
    re.compile(r"\d{4}-\d{2}-\d{2}"),   # ISO date
    re.compile(r"\d+\.\d+"),            # Decimal number
]


def _word_set(text: str) -> set[str]:
    """Extract lowercase word tokens from text."""
    return {
        w.lower()
        for w in re.findall(r"[a-zA-Z0-9_]+", text)
        if len(w) > 2
    }


def score_importance(
    content: str,
    existing_memories: list[dict],
) -> float:
    """Score content importance from 0.0 to 1.0.

    Factors:
    - Length penalty for very short or very long content
    - Novelty penalty if content overlaps heavily with existing memories
    - Bonus for structured data (JSON, numbers, dates)
    """
    score = 0.5  # baseline

    # Length penalty
    length = len(content.strip())
    if length < 20:
        score -= 0.25
    elif length > 2000:
        score -= 0.15
    elif 50 <= length <= 500:
        score += 0.1  # sweet spot

    # Structured data bonus
    for pattern in _STRUCTURED_PATTERNS:
        if pattern.search(content):
            score += 0.1
            break

    # Novelty check - compare against existing memories
    if existing_memories:
        content_words = _word_set(content)
        if content_words:
            max_overlap = 0.0
            for mem in existing_memories:
                mem_text = mem.get("memory", "")
                if not mem_text:
                    continue
                mem_words = _word_set(mem_text)
                if not mem_words:
                    continue
                overlap = len(
                    content_words & mem_words
                ) / max(len(content_words), 1)
                max_overlap = max(max_overlap, overlap)

            # High overlap = low novelty
            if max_overlap > 0.8:
                score -= 0.3
            elif max_overlap > 0.5:
                score -= 0.15

    return max(0.0, min(1.0, score))


def should_admit(
    content: str,
    existing_memories: list[dict],
    threshold: float = 0.3,
) -> tuple[bool, float, str]:
    """Decide whether a memory write should be admitted.

    Returns:
        (admitted, score, reason) tuple.
    """
    if not content or not content.strip():
        return (False, 0.0, "Empty content")

    score = score_importance(content, existing_memories)

    if score < threshold:
        reason = (
            f"Score {score:.2f} below threshold {threshold:.2f} "
            f"- content may be too short, redundant, or low-value"
        )
        return (False, score, reason)

    return (True, score, "Admitted")


# ---------------------------------------------------------------------------
# Memory decay / TTL
# ---------------------------------------------------------------------------


def apply_decay(
    memories: list[dict],
    max_age_days: int = 90,
) -> list[dict]:
    """Filter and score memories by recency.

    - Removes memories older than max_age_days
    - Adds a `relevance_score` field (1.0 = today, 0.1 at max_age)
    - Returns sorted by relevance_score descending
    """
    if max_age_days <= 0:
        # 0 = no expiry, keep all with default relevance
        return [{**m, "relevance_score": 1.0} for m in memories]

    now = datetime.now(timezone.utc)
    result: list[dict] = []

    for mem in memories:
        created = mem.get("created_at", "")
        if not created:
            # No timestamp - keep with mid relevance
            enriched = {**mem, "relevance_score": 0.5}
            result.append(enriched)
            continue

        try:
            if isinstance(created, str):
                # Handle ISO format with or without timezone
                ts = created.replace("Z", "+00:00")
                dt = datetime.fromisoformat(ts)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            elif isinstance(created, (int, float)):
                dt = datetime.fromtimestamp(created, tz=timezone.utc)
            else:
                dt = datetime.now(timezone.utc)
        except (ValueError, TypeError):
            dt = datetime.now(timezone.utc)

        age_days = (now - dt).total_seconds() / 86400.0

        if age_days > max_age_days:
            continue  # expired

        # Linear decay from 1.0 (today) to 0.1 (at max_age)
        relevance = max(
            0.1,
            1.0 - (age_days / max_age_days) * 0.9,
        )

        enriched = {**mem, "relevance_score": round(relevance, 3)}
        result.append(enriched)

    result.sort(key=lambda m: m.get("relevance_score", 0), reverse=True)
    return result


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


def detect_conflicts(
    new_content: str,
    existing_memories: list[dict],
) -> list[dict]:
    """Find existing memories that may conflict with new content.

    Uses word overlap heuristic: memories sharing >40% of words
    with the new content are flagged as potential conflicts.
    """
    new_words = _word_set(new_content)
    if not new_words:
        return []

    conflicts: list[dict] = []
    for mem in existing_memories:
        mem_text = mem.get("memory", "")
        if not mem_text:
            continue
        mem_words = _word_set(mem_text)
        if not mem_words:
            continue

        # Overlap coefficient (Szymkiewicz-Simpson): intersection / min(len_a, len_b)
        intersection = new_words & mem_words
        smaller = min(len(new_words), len(mem_words))
        if smaller == 0:
            continue

        overlap_ratio = len(intersection) / smaller
        if overlap_ratio > 0.4:
            conflicts.append({
                **mem,
                "_overlap_ratio": round(overlap_ratio, 3),
                "_shared_words": sorted(intersection)[:10],
            })

    return conflicts


# ---------------------------------------------------------------------------
# Structured memory enrichment
# ---------------------------------------------------------------------------

_TAG_PATTERNS: dict[str, list[str]] = {
    "issue": ["error", "fail", "bug", "crash", "broken", "exception"],
    "preference": ["prefer", "like", "want", "favorite", "choose"],
    "insight": ["learn", "found", "discover", "realize", "notice"],
    "data": [],  # detected via regex below
}


def enrich_memory(
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Enrich memory content with keywords, tags, and timestamps.

    Returns dict with: content, keywords, tags, metadata, enriched_at.
    """
    # Extract keywords: split, filter stopwords, take top 5 unique
    words = re.findall(r"[a-zA-Z0-9_]+", content.lower())
    candidates = [
        w for w in words
        if len(w) > 3 and w not in _STOPWORDS
    ]
    # Deduplicate preserving first-seen order
    seen: set[str] = set()
    unique: list[str] = []
    for w in candidates:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    keywords = unique[:5]

    # Auto-tag based on content patterns
    content_lower = content.lower()
    tags: list[str] = []
    for tag, trigger_words in _TAG_PATTERNS.items():
        if tag == "data":
            # Check for numbers/dates via regex
            if (
                re.search(r"\d{4}-\d{2}-\d{2}", content)
                or re.search(r"\d+\.\d+", content)
                or re.search(r"\b\d{3,}\b", content)
            ):
                tags.append("data")
        else:
            for trigger in trigger_words:
                if trigger in content_lower:
                    tags.append(tag)
                    break

    return {
        "content": content,
        "keywords": keywords,
        "tags": sorted(set(tags)),
        "metadata": metadata or {},
        "enriched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Core CRUD operations (backward-compatible public API)
# ---------------------------------------------------------------------------


async def load_memories(
    scope_id: str,
    query: str = "",
    limit: int = 10,
    max_age_days: int | None = None,
    backend: str = "local",
) -> list[dict]:
    """Load relevant memories with optional decay filtering.

    If query is provided, performs semantic search; otherwise get_all.
    When max_age_days is set, expired memories are filtered out and
    remaining ones receive a relevance_score.
    """
    import asyncio

    _validate_scope(scope_id)

    try:
        client = _get_client(backend)
        if query:
            results = await asyncio.to_thread(
                client.search, query,
                user_id=scope_id, limit=limit,
            )
        else:
            results = await asyncio.to_thread(
                client.get_all, user_id=scope_id,
            )

        # Normalize mem0 response format
        memories: list[dict] = []
        items = (
            results.get("results", results)
            if isinstance(results, dict)
            else results
        )
        for item in items[:limit]:
            memories.append({
                "id": item.get("id", ""),
                "memory": item.get("memory", ""),
                "metadata": item.get("metadata", {}),
                "created_at": item.get("created_at", ""),
                "updated_at": item.get("updated_at", ""),
            })

        # Apply decay if requested
        if max_age_days is not None:
            memories = apply_decay(memories, max_age_days=max_age_days)

        return memories

    except MemoryBackendError:
        raise
    except Exception as exc:
        logger.warning("Failed to load memories for %s: %s", scope_id, exc)
        return []


async def save_memory(
    scope_id: str,
    content: str,
    metadata: dict[str, Any] | None = None,
    run_id: str = "",
    admit_threshold: float = 0.3,
    skip_admission: bool = False,
    backend: str = "local",
) -> list[dict]:
    """Add memory content with admission control.

    Mem0 auto-extracts facts and deduplicates internally.
    Admission control rejects low-value writes before hitting
    the backend.

    Args:
        scope_id: Target memory scope.
        content: Text content to memorize.
        metadata: Optional metadata dict.
        run_id: Associated workflow run ID.
        admit_threshold: Minimum importance score (0.0-1.0).
        skip_admission: Bypass admission control if True.
        backend: Memory backend to use.

    Returns:
        List of created/updated memory records.

    Raises:
        MemoryAdmissionError: If content is rejected by admission.
        MemoryBackendError: If the backend is unreachable.
    """
    import asyncio

    _validate_scope(scope_id)

    # Reject oversized content to prevent storage abuse.
    # API schema limits to 100KB; enforce the same limit for internal calls.
    _MAX_CONTENT_SIZE = 100_000
    if len(content) > _MAX_CONTENT_SIZE:
        original_len = len(content)
        content = content[:_MAX_CONTENT_SIZE]
        logger.warning(
            "Memory content for %s truncated from %d to %d chars",
            scope_id, original_len, _MAX_CONTENT_SIZE,
        )

    meta = metadata or {}
    if run_id:
        meta["run_id"] = run_id

    # Admission control
    if not skip_admission:
        try:
            existing = await load_memories(
                scope_id, limit=20, backend=backend,
            )
        except Exception:
            existing = []

        admitted, score, reason = should_admit(
            content, existing, threshold=admit_threshold,
        )
        if not admitted:
            logger.info(
                "Memory write rejected for %s (score=%.2f): %s",
                scope_id, score, reason,
            )
            raise MemoryAdmissionError(reason, score=score)

    # Enrich content
    enriched = enrich_memory(content, meta)
    if enriched["tags"]:
        meta["tags"] = ",".join(enriched["tags"])
    if enriched["keywords"]:
        meta["keywords"] = ",".join(enriched["keywords"])

    try:
        client = _get_client(backend)
        result = await asyncio.to_thread(
            client.add, content,
            user_id=scope_id, metadata=meta,
        )
        records = result if isinstance(result, list) else [result]

        # Validate and log what the backend actually confirmed
        confirmed = len(records)
        if confirmed == 0:
            logger.warning(
                "Memory save for %s: backend returned 0 records "
                "(content length=%d chars) - possible silent failure",
                scope_id, len(content),
            )
        else:
            logger.info(
                "Memory save for %s: %d record(s) confirmed by backend",
                scope_id, confirmed,
            )

        return records

    except MemoryBackendError:
        raise
    except Exception as exc:
        logger.warning("Failed to save memory for %s: %s", scope_id, exc)
        raise MemoryBackendError(
            f"Backend write failed: {exc}"
        ) from exc


async def delete_memory(
    memory_id: str,
    backend: str = "local",
) -> bool:
    """Delete a specific memory by ID."""
    import asyncio
    try:
        client = _get_client(backend)
        await asyncio.to_thread(client.delete, memory_id)
        return True
    except MemoryBackendError:
        raise
    except Exception as exc:
        logger.warning("Failed to delete memory %s: %s", memory_id, exc)
        return False


async def delete_all_memories(
    scope_id: str,
    backend: str = "local",
) -> bool:
    """Delete all memories for a scope."""
    import asyncio
    _validate_scope(scope_id)
    try:
        client = _get_client(backend)
        await asyncio.to_thread(client.delete_all, user_id=scope_id)
        return True
    except MemoryBackendError:
        raise
    except Exception as exc:
        logger.warning(
            "Failed to delete all memories for %s: %s", scope_id, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def format_memories_for_prompt(
    memories: list[dict],
    max_chars: int = 2000,
) -> str:
    """Format memories as a prompt block for injection.

    When enrichment metadata (tags, keywords) is present on memories,
    it is included inline for richer context.
    """
    if not memories:
        return ""

    header = "[Agent Memory]"
    footer = "[End of Agent Memory]"
    # Reserve space for header, footer, and newlines between them
    reserved = len(header) + len(footer) + 2  # 2 newlines
    budget = max(0, max_chars - reserved)

    lines: list[str] = [header]
    total = 0

    for m in memories:
        text = m.get("memory", "")
        if not text:
            continue

        # Build suffix with tags/keywords if available
        meta = m.get("metadata") or {}
        suffix_parts: list[str] = []
        tags_str = meta.get("tags", "")
        if tags_str:
            suffix_parts.append(f"tags:{tags_str}")
        kw_str = meta.get("keywords", "")
        if kw_str:
            suffix_parts.append(f"kw:{kw_str}")
        relevance = m.get("relevance_score")
        if relevance is not None:
            suffix_parts.append(f"rel:{relevance}")

        suffix = f" [{', '.join(suffix_parts)}]" if suffix_parts else ""
        line = f"- {text}{suffix}"

        if total + len(line) > budget:
            break
        lines.append(line)
        total += len(line)

    lines.append(footer)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


async def memory_health_check(
    backend: str = "local",
) -> dict[str, Any]:
    """Check if the memory backend is reachable and operational.

    Returns a dict with status, backend, latency_ms, and details.
    """
    import asyncio

    start = time.monotonic()
    try:
        client = _get_client(backend)
        # Probe with a harmless get_all on a non-existent scope
        await asyncio.to_thread(
            client.get_all, user_id="__health_check__",
        )
        latency = (time.monotonic() - start) * 1000
        return {
            "status": "ok",
            "backend": backend,
            "latency_ms": round(latency, 1),
            "graph_available": _get_graph_client() is not None,
        }
    except NotImplementedError as exc:
        return {
            "status": "error",
            "backend": backend,
            "error": str(exc),
            "graph_available": False,
        }
    except Exception as exc:
        latency = (time.monotonic() - start) * 1000
        return {
            "status": "error",
            "backend": backend,
            "latency_ms": round(latency, 1),
            "error": str(exc),
            "graph_available": False,
        }


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _reset_client() -> None:
    """Reset all client singletons (for testing)."""
    global _clients, _graph_client, _graph_client_initialized
    _clients = {}
    _graph_client = None
    _graph_client_initialized = False
