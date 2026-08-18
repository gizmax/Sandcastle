"""Context compaction - fit text into a token budget without throwing away the end.

The engine has always shrunk oversized context by cutting at a character offset
(``text[:max_tokens * 4]``). That is cheap and predictable, but it discards the
tail of the text, and for an LLM step output the conclusion usually lives there.

This module keeps that behaviour available as ``truncate`` and adds three
strategies that lose less:

``head_tail``  keep both ends, drop the middle. No model, no cost.
``prune``      shrink structured payloads (long arrays, repeated lines) while
               keeping their shape. No model, no cost.
``summarize``  ask a model to summarise. Costs a call, so it is opt-in, and it
               is designed to run on a local model where that call is free.

Every strategy reports how many tokens it saved so the saving can be recorded
on the step and shown back to the user - the point is not to compact silently
but to make the trade visible.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Same 4-chars-per-token approximation the executor has always used. It is
# rough, but changing it would silently move every existing budget, so the
# estimate stays put and the strategies get better instead.
CHARS_PER_TOKEN = 4

STRATEGIES = ("truncate", "head_tail", "prune", "summarize")

# Fraction of the budget given to the head when splitting head/tail. The head
# carries the task framing; the tail carries the conclusion. 60/40 keeps enough
# of the opening to stay readable while guaranteeing the ending survives.
HEAD_RATIO = 0.6

_TRUNCATE_MARKER = "\n\n[... truncated to fit context window ...]"


@dataclass
class CompactionResult:
    """What compaction did, so the caller can record it."""

    text: str
    tokens_before: int
    tokens_after: int
    strategy: str
    cost_usd: float = 0.0
    # Set when a strategy could not run and fell back to a cheaper one.
    fallback_from: str | None = None

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_before - self.tokens_after)

    @property
    def applied(self) -> bool:
        return self.tokens_after < self.tokens_before


def estimate_tokens(text: str) -> int:
    """Approximate token count for ``text``."""
    if not text:
        return 0
    return len(text) // CHARS_PER_TOKEN


def _truncate(text: str, max_chars: int, marker: str = _TRUNCATE_MARKER) -> str:
    return text[:max_chars] + marker


def _head_tail(text: str, max_chars: int) -> str:
    """Keep the opening and the ending, drop the middle."""
    dropped = len(text) - max_chars
    marker = f"\n\n[... {dropped} characters omitted from the middle ...]\n\n"
    budget = max_chars - len(marker)
    if budget <= 0:
        return _truncate(text, max_chars)
    head_chars = int(budget * HEAD_RATIO)
    tail_chars = budget - head_chars
    head = text[:head_chars]
    tail = text[-tail_chars:] if tail_chars > 0 else ""
    # Prefer cutting on a line boundary so neither half starts mid-token.
    nl = head.rfind("\n")
    if nl > head_chars * 0.5:
        head = head[:nl]
    nl = tail.find("\n")
    if -1 < nl < len(tail) * 0.5:
        tail = tail[nl + 1 :]
    return head + marker + tail


def _prune_json(data, max_items: int) -> tuple[object, bool]:
    """Shorten long arrays in a decoded JSON structure, keeping its shape."""
    changed = False
    if isinstance(data, list):
        out = []
        for item in data[:max_items]:
            pruned, ch = _prune_json(item, max_items)
            changed = changed or ch
            out.append(pruned)
        if len(data) > max_items:
            out.append(f"[... {len(data) - max_items} more items omitted ...]")
            changed = True
        return out, changed
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            pruned, ch = _prune_json(v, max_items)
            changed = changed or ch
            out[k] = pruned
        return out, changed
    return data, changed


def _prune_repeated_lines(text: str, max_repeats: int = 3) -> tuple[str, bool]:
    """Collapse runs of identical lines, which logs and traces produce a lot of."""
    lines = text.split("\n")
    out: list[str] = []
    changed = False
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        run = j - i + 1
        if run > max_repeats:
            out.extend([lines[i]] * max_repeats)
            out.append(f"[... previous line repeated {run - max_repeats} more times ...]")
            changed = True
        else:
            out.extend(lines[i : j + 1])
        i = j + 1
    return "\n".join(out), changed


def _prune(text: str, max_chars: int) -> str:
    """Model-free shrink: structured payloads first, then repeated lines."""
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
        except (ValueError, TypeError):
            pass
        else:
            # Step down the array cap until it fits, rather than guessing once.
            for max_items in (20, 10, 5, 3, 1):
                pruned, changed = _prune_json(data, max_items)
                candidate = json.dumps(pruned, indent=2, ensure_ascii=False, default=str)
                if len(candidate) <= max_chars:
                    return candidate
                if not changed:
                    break
    collapsed, changed = _prune_repeated_lines(text)
    if changed and len(collapsed) <= max_chars:
        return collapsed
    # Nothing structural left to remove - keep both ends rather than the head only.
    return _head_tail(collapsed if changed else text, max_chars)


SUMMARY_PROMPT = (
    "Condense the following text to at most {budget} words.\n"
    "Preserve, in this order of priority: any final answer, conclusion or "
    "decision; concrete values such as numbers, identifiers, file paths and "
    "error messages; and the overall structure. Drop restatement, filler and "
    "repetition. Do not add commentary, and do not introduce facts that are "
    "not present.\n\n"
    "--- TEXT ---\n{text}\n--- END ---"
)


def compact_sync(text: str, max_tokens: int, strategy: str = "truncate") -> CompactionResult:
    """Apply a model-free strategy. ``summarize`` falls back to ``head_tail``."""
    before = estimate_tokens(text)
    if max_tokens <= 0 or not text:
        return CompactionResult(text, before, before, strategy)
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return CompactionResult(text, before, before, strategy)

    fallback_from = None
    if strategy == "summarize":
        # Caller wanted a model but used the sync path; degrade loudly.
        fallback_from, strategy = "summarize", "head_tail"
    if strategy not in STRATEGIES:
        fallback_from, strategy = strategy, "truncate"

    if strategy == "head_tail":
        out = _head_tail(text, max_chars)
    elif strategy == "prune":
        out = _prune(text, max_chars)
    else:
        out = _truncate(text, max_chars)

    return CompactionResult(
        text=out,
        tokens_before=before,
        tokens_after=estimate_tokens(out),
        strategy=strategy,
        fallback_from=fallback_from,
    )


async def compact(
    text: str,
    max_tokens: int,
    strategy: str = "truncate",
    model: str = "",
) -> CompactionResult:
    """Apply ``strategy``, including ``summarize``.

    ``summarize`` never fails the step: if the model call errors or returns
    nothing usable, the text is compacted with ``head_tail`` instead and the
    result records what was attempted.
    """
    if strategy != "summarize":
        return compact_sync(text, max_tokens, strategy)

    before = estimate_tokens(text)
    if max_tokens <= 0 or not text:
        return CompactionResult(text, before, before, strategy)
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return CompactionResult(text, before, before, strategy)

    try:
        summary, cost = await _summarize(text, max_tokens, model)
    except Exception as e:  # noqa: BLE001 - compaction must never fail a step
        logger.warning("Compaction: summarize failed (%s); falling back to head_tail", e)
        result = compact_sync(text, max_tokens, "head_tail")
        result.fallback_from = "summarize"
        return result

    if not summary or not summary.strip():
        logger.warning("Compaction: summarize returned nothing; falling back to head_tail")
        result = compact_sync(text, max_tokens, "head_tail")
        result.fallback_from = "summarize"
        return result

    # A summary longer than the budget is worse than useless - trim it.
    if len(summary) > max_chars:
        summary = _head_tail(summary, max_chars)

    return CompactionResult(
        text=summary,
        tokens_before=before,
        tokens_after=estimate_tokens(summary),
        strategy="summarize",
        cost_usd=cost,
    )


async def _summarize(text: str, max_tokens: int, model: str) -> tuple[str, float]:
    """Summarise via the configured model. Returns (summary, cost_usd)."""
    from sandcastle.engine.sandshore import query

    # Words rather than tokens: models follow word budgets more reliably.
    budget_words = max(50, int(max_tokens * 0.6))
    prompt = SUMMARY_PROMPT.format(budget=budget_words, text=text)

    resolved = model or ""
    result = await query(prompt=prompt, model=resolved) if resolved else await query(prompt=prompt)

    out = getattr(result, "output", None) or getattr(result, "text", None) or ""
    cost = float(getattr(result, "total_cost_usd", 0.0) or 0.0)
    return str(out), cost


def normalize_strategy(value: str) -> str:
    """Map a configured value onto a known strategy, defaulting to truncate."""
    v = (value or "").strip().lower().replace("-", "_")
    return v if v in STRATEGIES else "truncate"
