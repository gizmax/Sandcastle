"""Model Time Machine - counterfactual replay of your real recorded workload.

"What would last month have looked like on a different model?" The Time Machine
answers that with data instead of benchmarks. It selects recorded run cassettes
(completed runs whose model steps persisted their resolved prompt, output, cost
and latency - the same substrate the deterministic cassette/replay system uses),
then either:

- **dry-run (default, free, instant)**: prices the recorded token volume against
  the target model's pricing table and projects the cost delta - no API calls; or
- **live replay (explicit opt-in, budget-capped)**: re-executes every recorded
  LLM step against the target model, measures real cost and latency, and scores
  old vs new outputs with an LLM judge (configurable, defaults to a cheap model).

A live replay REQUIRES an explicit ``budget_usd``: the projected replay cost
(target-model calls + judge calls) is estimated up front from recorded token
volumes and the job refuses to start if the estimate exceeds the budget. During
execution the measured spend is re-checked per step and the replay truncates
rather than overshooting.

Reports are persisted as JSON artifacts under ``{data_dir}/timemachine/`` -
consistent with the cassette/adapter-registry file substrate, no migration
needed - and exposed via an in-process async job registry (POST /api/timemachine
-> job_id, GET /api/timemachine/{job_id} -> status + report).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPORT_VERSION = 1

# Chars-per-token heuristic used to derive token volumes from recorded text.
# Token counts are not persisted per step, so ~4 chars/token (the standard
# rule of thumb for English/code) prices the recorded volume.
_CHARS_PER_TOKEN = 4

# Map bare Claude aliases to real Anthropic model IDs for direct API calls
# (same mapping the executor's explicit-llm step uses).
_CLAUDE_MODEL_ALIASES = {
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "opus": "claude-opus-4-7",
}

# Truncation caps for judge inputs - keeps judge calls cheap and bounded.
_JUDGE_TEXT_CAP = 4000
_JUDGE_MAX_TOKENS = 64
_REPLAY_MAX_TOKENS = 4096
# Cap of per-step detail rows embedded in a report artifact.
_REPORT_STEP_CAP = 200

_JUDGE_SYSTEM = (
    "You are an impartial evaluator comparing two AI responses to the same task. "
    "Score each response 0-10 for correctness, completeness, and instruction-following. "
    'Respond with ONLY a JSON object: {"a": <score>, "b": <score>}'
)


class TimeMachineError(Exception):
    """Base error for Time Machine operations."""


class BudgetRequiredError(TimeMachineError):
    """A live replay was requested without an explicit budget_usd cap."""


class BudgetExceededError(TimeMachineError):
    """The pre-flight cost estimate exceeds the explicit budget cap."""


# ---------------------------------------------------------------------------
# Cassette selection (recorded workload from the runs/run_steps substrate)
# ---------------------------------------------------------------------------


@dataclass
class RecordedStep:
    """One recorded LLM interaction: resolved prompt, output, cost, latency."""

    step_id: str
    model: str
    prompt: str
    output_text: str
    cost_usd: float
    duration_seconds: float


@dataclass
class RunCassette:
    """The recorded LLM steps of one completed run."""

    run_id: str
    workflow_name: str
    created_at: datetime | None
    steps: list[RecordedStep] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.steps)


def parse_since(spec: str) -> datetime:
    """Parse a relative window like ``30d`` / ``12h`` or an ISO date into a UTC datetime."""
    spec = (spec or "").strip()
    m = re.fullmatch(r"(\d+)\s*([dhw])", spec, re.IGNORECASE)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        delta = {"h": timedelta(hours=n), "d": timedelta(days=n), "w": timedelta(weeks=n)}[unit]
        return datetime.now(timezone.utc) - delta
    dt = datetime.fromisoformat(spec)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def estimate_tokens(text: str) -> int:
    """Estimate the token count of *text* (~4 chars/token, minimum 1 for non-empty)."""
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _output_as_text(output: Any) -> str:
    """Render a recorded step output (str or JSON structure) as comparable text."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except Exception:
        return str(output)


async def select_cassettes(
    workflow: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_cassettes: int = 20,
    tenant_id: str | None = None,
) -> list[RunCassette]:
    """Select recorded run cassettes from the database, newest first.

    A cassette is a completed run with at least one model-bearing step that
    persisted its resolved prompt (``run_steps.input_prompt``) - exactly the
    interactions the deterministic cassette recorder captures.
    """
    from sqlalchemy import select

    from sandcastle.models.db import Run, RunStatus, RunStep, StepStatus, async_session

    max_cassettes = max(1, min(int(max_cassettes), 500))
    stmt = select(Run).where(Run.status == RunStatus.COMPLETED)
    if workflow:
        stmt = stmt.where(Run.workflow_name == workflow)
    if since is not None:
        stmt = stmt.where(Run.created_at >= since)
    if until is not None:
        stmt = stmt.where(Run.created_at <= until)
    if tenant_id is not None:
        stmt = stmt.where(Run.tenant_id == tenant_id)
    stmt = stmt.order_by(Run.created_at.desc()).limit(max_cassettes * 3)

    cassettes: list[RunCassette] = []
    async with async_session() as session:
        runs = (await session.execute(stmt)).scalars().all()
        for run in runs:
            if len(cassettes) >= max_cassettes:
                break
            step_stmt = (
                select(RunStep)
                .where(RunStep.run_id == run.id, RunStep.status == StepStatus.COMPLETED)
                .order_by(RunStep.started_at)
            )
            rows = (await session.execute(step_stmt)).scalars().all()
            steps = [
                RecordedStep(
                    step_id=s.step_id,
                    model=s.model or "",
                    prompt=s.input_prompt or "",
                    output_text=_output_as_text(s.output_data),
                    cost_usd=float(s.cost_usd or 0.0),
                    duration_seconds=float(s.duration_seconds or 0.0),
                )
                for s in rows
                if s.model and s.input_prompt
            ]
            if steps:
                cassettes.append(
                    RunCassette(
                        run_id=str(run.id),
                        workflow_name=run.workflow_name,
                        created_at=run.created_at,
                        steps=steps,
                    )
                )
    return cassettes


# ---------------------------------------------------------------------------
# Cost estimation (dry-run pricing - zero API calls)
# ---------------------------------------------------------------------------


def estimate_replay_cost(
    cassettes: list[RunCassette],
    target_model: str,
    judge_model: str | None = None,
) -> dict[str, Any]:
    """Price the recorded token volume against the target (and judge) model pricing.

    Returns projected target-model cost for the selection, the projected judge
    cost for a live replay, and the recorded original spend - all derived from
    recorded prompt/output text via the chars-per-token heuristic.
    """
    from sandcastle.engine.providers import resolve_model

    target = resolve_model(target_model)
    judge = resolve_model(judge_model) if judge_model else None

    input_tokens = 0
    output_tokens = 0
    judge_tokens_in = 0
    original_cost = 0.0
    step_count = 0
    for cas in cassettes:
        for st in cas.steps:
            in_tok = estimate_tokens(st.prompt)
            out_tok = estimate_tokens(st.output_text)
            input_tokens += in_tok
            output_tokens += out_tok
            original_cost += st.cost_usd
            step_count += 1
            # Judge sees prompt + both outputs (old + assumed-similar new), capped.
            judge_tokens_in += min(in_tok, _JUDGE_TEXT_CAP // _CHARS_PER_TOKEN)
            judge_tokens_in += 2 * min(out_tok, _JUDGE_TEXT_CAP // _CHARS_PER_TOKEN)

    target_cost = (
        input_tokens * target.input_price_per_m + output_tokens * target.output_price_per_m
    ) / 1_000_000
    judge_cost = 0.0
    if judge is not None:
        judge_out = step_count * _JUDGE_MAX_TOKENS
        judge_cost = (
            judge_tokens_in * judge.input_price_per_m + judge_out * judge.output_price_per_m
        ) / 1_000_000

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "step_count": step_count,
        "original_cost_usd": round(original_cost, 6),
        "projected_cost_usd": round(target_cost, 6),
        "projected_judge_cost_usd": round(judge_cost, 6),
        "projected_total_live_cost_usd": round(target_cost + judge_cost, 6),
    }


# ---------------------------------------------------------------------------
# Live model + judge calls
# ---------------------------------------------------------------------------


async def call_model(
    model_str: str,
    prompt: str,
    max_tokens: int = _REPLAY_MAX_TOKENS,
    system: str | None = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Call *model_str* once with *prompt*; return text, token usage, cost and latency.

    Mirrors the executor's explicit-llm step: Anthropic Messages API for Claude
    models, OpenAI-compatible chat/completions for everything else (including
    local NIM/Ollama/oMLX, which price at $0).
    """
    import httpx

    from sandcastle.engine.providers import get_api_key, resolve_base_url, resolve_model

    info = resolve_model(model_str)
    api_key = get_api_key(info)
    started = time.monotonic()

    if info.provider == "claude":
        api_model = _CLAUDE_MODEL_ALIASES.get(info.api_model_id, info.api_model_id)
        body: dict[str, Any] = {
            "model": api_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
    else:
        base_url = resolve_base_url(info)
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "content-type": "application/json",
                },
                json={"model": info.api_model_id, "max_tokens": max_tokens, "messages": messages},
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)

    latency = time.monotonic() - started
    cost = (in_tok * info.input_price_per_m + out_tok * info.output_price_per_m) / 1_000_000
    return {
        "text": text,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cost_usd": cost,
        "latency_seconds": latency,
    }


def _parse_judge_scores(raw: str) -> tuple[float | None, float | None]:
    """Extract {"a": x, "b": y} scores from a judge response, tolerating fences/prose."""
    text = (raw or "").strip()
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not m:
        return None, None
    try:
        data = json.loads(m.group(0))
        a, b = float(data.get("a")), float(data.get("b"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    clamp = lambda v: max(0.0, min(10.0, v))  # noqa: E731 - tiny local helper
    return clamp(a), clamp(b)


async def judge_outputs(
    judge_model: str,
    prompt: str,
    old_output: str,
    new_output: str,
) -> dict[str, Any]:
    """Score the original (a) vs replayed (b) output with the judge model, 0-10 each."""
    user = (
        f"TASK:\n{prompt[:_JUDGE_TEXT_CAP]}\n\n"
        f"RESPONSE A:\n{old_output[:_JUDGE_TEXT_CAP]}\n\n"
        f"RESPONSE B:\n{new_output[:_JUDGE_TEXT_CAP]}\n\n"
        'JSON scores only: {"a": <0-10>, "b": <0-10>}'
    )
    result = await call_model(
        judge_model, user, max_tokens=_JUDGE_MAX_TOKENS, system=_JUDGE_SYSTEM
    )
    score_old, score_new = _parse_judge_scores(result["text"])
    return {"score_old": score_old, "score_new": score_new, "cost_usd": result["cost_usd"]}


# ---------------------------------------------------------------------------
# The Time Machine itself
# ---------------------------------------------------------------------------


def _pct_delta(old: float, new: float) -> float | None:
    """Signed percent change from old to new, None when old is 0."""
    if not old:
        return None
    return round((new - old) / old * 100.0, 2)


def _avg(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _build_verdict(
    target_model: str,
    mode: str,
    monthly_savings: float,
    quality_delta_pct: float | None,
) -> str:
    """One-line headline: 'Switching to X saves $Y/mo at -Z% quality'."""
    if monthly_savings >= 0:
        money = f"saves ${monthly_savings:,.2f}/mo"
    else:
        money = f"costs ${abs(monthly_savings):,.2f}/mo more"
    if mode == "dry_run":
        return (
            f"Switching to {target_model} {money} (projected from recorded token volume). "
            "Run a live replay to measure quality."
        )
    if quality_delta_pct is None:
        return f"Switching to {target_model} {money} (quality not scored)."
    sign = "+" if quality_delta_pct >= 0 else ""
    return f"Switching to {target_model} {money} at {sign}{quality_delta_pct:.1f}% quality."


async def run_time_machine(
    target_model: str,
    workflow: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_cassettes: int = 20,
    live: bool = False,
    budget_usd: float | None = None,
    judge_model: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Replay the selected recorded workload against *target_model* and report deltas.

    Dry-run (default): pure pricing-table math over recorded token volumes, free.
    Live: re-executes each recorded LLM step on the target model and judges old vs
    new output; requires ``budget_usd`` and refuses to start when the pre-flight
    estimate exceeds it.
    """
    from sandcastle.config import settings
    from sandcastle.engine.providers import resolve_model

    resolve_model(target_model)  # raises KeyError early for unknown models
    judge = judge_model or getattr(settings, "timemachine_judge_model", "haiku")
    mode = "live" if live else "dry_run"

    cassettes = await select_cassettes(
        workflow=workflow,
        since=since,
        until=until,
        max_cassettes=max_cassettes,
        tenant_id=tenant_id,
    )
    estimate = estimate_replay_cost(cassettes, target_model, judge_model=judge if live else None)

    if live:
        if budget_usd is None:
            raise BudgetRequiredError(
                "Live replay makes real API calls - an explicit budget_usd cap is required."
            )
        projected = estimate["projected_total_live_cost_usd"]
        if projected > budget_usd:
            raise BudgetExceededError(
                f"Estimated live replay cost ${projected:.4f} exceeds budget "
                f"${budget_usd:.4f}. Raise the budget or narrow the selection."
            )

    # -- selection summary ---------------------------------------------------
    created = [c.created_at for c in cassettes if c.created_at is not None]
    window_days = 30.0
    if len(created) >= 2:
        span = max(created) - min(created)
        window_days = max(span.total_seconds() / 86400.0, 1.0)
    elif since is not None:
        span = (until or datetime.now(timezone.utc)) - since
        window_days = max(span.total_seconds() / 86400.0, 1.0)

    original_cost = estimate["original_cost_usd"]
    selection = {
        "runs": len(cassettes),
        "steps": estimate["step_count"],
        "workflows": sorted({c.workflow_name for c in cassettes}),
        "original_cost_usd": original_cost,
        "window_days": round(window_days, 2),
        "first_run_at": min(created).isoformat() if created else None,
        "last_run_at": max(created).isoformat() if created else None,
    }

    # -- per-step replay/judging (live) or projection (dry-run) ---------------
    per_wf: dict[str, dict[str, Any]] = {}
    step_details: list[dict[str, Any]] = []
    measured_cost = 0.0
    steps_replayed = 0
    steps_failed = 0
    truncated = False

    for cas in cassettes:
        agg = per_wf.setdefault(
            cas.workflow_name,
            {
                "workflow": cas.workflow_name,
                "runs": 0,
                "steps": 0,
                "original_cost_usd": 0.0,
                "new_cost_usd": 0.0,
                "quality_old": [],
                "quality_new": [],
                "latency_old": [],
                "latency_new": [],
            },
        )
        agg["runs"] += 1
        for st in cas.steps:
            agg["steps"] += 1
            agg["original_cost_usd"] += st.cost_usd
            agg["latency_old"].append(st.duration_seconds)

            if not live:
                in_tok = estimate_tokens(st.prompt)
                out_tok = estimate_tokens(st.output_text)
                target = resolve_model(target_model)
                projected_step = (
                    in_tok * target.input_price_per_m + out_tok * target.output_price_per_m
                ) / 1_000_000
                agg["new_cost_usd"] += projected_step
                continue

            if budget_usd is not None and measured_cost >= budget_usd:
                truncated = True
                break
            detail: dict[str, Any] = {
                "run_id": cas.run_id,
                "workflow": cas.workflow_name,
                "step_id": st.step_id,
                "old_model": st.model,
                "old_cost_usd": round(st.cost_usd, 6),
                "old_latency_seconds": round(st.duration_seconds, 3),
            }
            try:
                replayed = await call_model(target_model, st.prompt)
                measured_cost += replayed["cost_usd"]
                steps_replayed += 1
                agg["new_cost_usd"] += replayed["cost_usd"]
                agg["latency_new"].append(replayed["latency_seconds"])
                detail.update(
                    new_cost_usd=round(replayed["cost_usd"], 6),
                    new_latency_seconds=round(replayed["latency_seconds"], 3),
                    new_output_preview=replayed["text"][:500],
                )
                try:
                    scores = await judge_outputs(
                        judge, st.prompt, st.output_text, replayed["text"]
                    )
                    measured_cost += scores["cost_usd"]
                    if scores["score_old"] is not None:
                        agg["quality_old"].append(scores["score_old"])
                        agg["quality_new"].append(scores["score_new"])
                    detail.update(
                        score_old=scores["score_old"], score_new=scores["score_new"]
                    )
                except Exception as exc:  # noqa: BLE001 - judge failure is non-fatal
                    logger.warning("Time Machine judge failed for %s: %s", st.step_id, exc)
                    detail["judge_error"] = str(exc)[:200]
            except Exception as exc:  # noqa: BLE001 - a step failure must not kill the job
                logger.warning("Time Machine replay failed for %s: %s", st.step_id, exc)
                steps_failed += 1
                detail["error"] = str(exc)[:300]
            if len(step_details) < _REPORT_STEP_CAP:
                step_details.append(detail)
        if truncated:
            break

    # -- aggregation -----------------------------------------------------------
    per_workflow: list[dict[str, Any]] = []
    for wf_name in sorted(per_wf):
        a = per_wf[wf_name]
        q_old, q_new = _avg(a["quality_old"]), _avg(a["quality_new"])
        l_old, l_new = _avg(a["latency_old"]), _avg(a["latency_new"])
        per_workflow.append(
            {
                "workflow": wf_name,
                "runs": a["runs"],
                "steps": a["steps"],
                "original_cost_usd": round(a["original_cost_usd"], 6),
                "new_cost_usd": round(a["new_cost_usd"], 6),
                "cost_delta_usd": round(a["new_cost_usd"] - a["original_cost_usd"], 6),
                "cost_delta_pct": _pct_delta(a["original_cost_usd"], a["new_cost_usd"]),
                "quality_old": round(q_old, 2) if q_old is not None else None,
                "quality_new": round(q_new, 2) if q_new is not None else None,
                "quality_delta_pct": (
                    _pct_delta(q_old, q_new) if q_old is not None and q_new is not None else None
                ),
                "latency_old_seconds": round(l_old, 3) if l_old is not None else None,
                "latency_new_seconds": round(l_new, 3) if l_new is not None else None,
                "latency_delta_pct": (
                    _pct_delta(l_old, l_new) if l_old is not None and l_new is not None else None
                ),
            }
        )

    new_cost_total = sum(w["new_cost_usd"] for w in per_workflow)
    all_q_old = [s for a in per_wf.values() for s in a["quality_old"]]
    all_q_new = [s for a in per_wf.values() for s in a["quality_new"]]
    all_l_old = [s for a in per_wf.values() for s in a["latency_old"]]
    all_l_new = [s for a in per_wf.values() for s in a["latency_new"]]
    q_old_avg, q_new_avg = _avg(all_q_old), _avg(all_q_new)
    l_old_avg, l_new_avg = _avg(all_l_old), _avg(all_l_new)
    quality_delta_pct = (
        _pct_delta(q_old_avg, q_new_avg)
        if q_old_avg is not None and q_new_avg is not None
        else None
    )

    # Extrapolate the selection's window to a monthly figure. The cost ratio is
    # measured (live) or projected (dry-run) on the selection, then applied to
    # the selection's original spend normalized to 30 days.
    monthly_original = original_cost * (30.0 / window_days)
    ratio = (new_cost_total / original_cost) if original_cost else 0.0
    monthly_projected = monthly_original * ratio
    monthly_savings = monthly_original - monthly_projected

    report: dict[str, Any] = {
        "version": REPORT_VERSION,
        "mode": mode,
        "target_model": target_model,
        "judge_model": judge if live else None,
        "params": {
            "workflow": workflow,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "max_cassettes": max_cassettes,
            "budget_usd": budget_usd,
        },
        "selection": selection,
        "estimate": estimate,
        "live": (
            {
                "measured_cost_usd": round(measured_cost, 6),
                "budget_usd": budget_usd,
                "steps_replayed": steps_replayed,
                "steps_failed": steps_failed,
                "truncated": truncated,
            }
            if live
            else None
        ),
        "cost": {
            "original_usd": round(original_cost, 6),
            "new_usd": round(new_cost_total, 6),
            "delta_usd": round(new_cost_total - original_cost, 6),
            "delta_pct": _pct_delta(original_cost, new_cost_total),
        },
        "quality": (
            {
                "old_avg": round(q_old_avg, 2) if q_old_avg is not None else None,
                "new_avg": round(q_new_avg, 2) if q_new_avg is not None else None,
                "delta_pct": quality_delta_pct,
            }
            if live
            else None
        ),
        "latency": (
            {
                "old_avg_seconds": round(l_old_avg, 3) if l_old_avg is not None else None,
                "new_avg_seconds": round(l_new_avg, 3) if l_new_avg is not None else None,
                "delta_pct": (
                    _pct_delta(l_old_avg, l_new_avg)
                    if l_old_avg is not None and l_new_avg is not None
                    else None
                ),
            }
            if live
            else None
        ),
        "extrapolation": {
            "window_days": round(window_days, 2),
            "monthly_original_usd": round(monthly_original, 4),
            "monthly_projected_usd": round(monthly_projected, 4),
            "monthly_savings_usd": round(monthly_savings, 4),
        },
        "per_workflow": per_workflow,
        "steps": step_details,
        "verdict": _build_verdict(target_model, mode, monthly_savings, quality_delta_pct),
    }
    return report


# ---------------------------------------------------------------------------
# Async job registry + JSON artifact persistence
# ---------------------------------------------------------------------------


@dataclass
class TimeMachineJob:
    """In-process record of one Time Machine job."""

    job_id: str
    status: str  # "running" | "completed" | "failed"
    params: dict[str, Any]
    created_at: str
    completed_at: str | None = None
    report: dict[str, Any] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "params": self.params,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "report": self.report,
            "error": self.error,
        }


_JOBS: dict[str, TimeMachineJob] = {}
_MAX_JOBS_IN_MEMORY = 50


def _artifact_dir() -> Path:
    from sandcastle.config import settings

    return Path(settings.data_dir) / "timemachine"


def _persist_job(job: TimeMachineJob) -> None:
    try:
        d = _artifact_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{job.job_id}.json").write_text(json.dumps(job.to_dict(), indent=2, default=str))
    except Exception as exc:  # noqa: BLE001 - persistence is best-effort
        logger.warning("Failed to persist Time Machine report %s: %s", job.job_id, exc)


def _prune_jobs() -> None:
    if len(_JOBS) <= _MAX_JOBS_IN_MEMORY:
        return
    for job_id in sorted(_JOBS, key=lambda j: _JOBS[j].created_at)[: -_MAX_JOBS_IN_MEMORY]:
        _JOBS.pop(job_id, None)


async def _execute_job(job: TimeMachineJob, kwargs: dict[str, Any]) -> None:
    try:
        report = await run_time_machine(**kwargs)
        job.report = report
        job.status = "completed"
    except Exception as exc:  # noqa: BLE001 - job errors surface via status
        logger.exception("Time Machine job %s failed", job.job_id)
        job.status = "failed"
        job.error = str(exc)[:500]
    job.completed_at = datetime.now(timezone.utc).isoformat()
    _persist_job(job)


def start_job(**kwargs: Any) -> TimeMachineJob:
    """Create a Time Machine job and run it as a background asyncio task."""
    job = TimeMachineJob(
        job_id=str(uuid.uuid4()),
        status="running",
        params={
            k: (v.isoformat() if isinstance(v, datetime) else v)
            for k, v in kwargs.items()
            if k != "tenant_id"
        },
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _JOBS[job.job_id] = job
    _prune_jobs()
    task = asyncio.create_task(_execute_job(job, kwargs))
    # Keep a reference so the task is not garbage-collected mid-flight.
    task.add_done_callback(lambda _t: None)
    return job


def get_job(job_id: str) -> dict[str, Any] | None:
    """Look up a job in memory first, then fall back to its persisted artifact."""
    job = _JOBS.get(job_id)
    if job is not None:
        return job.to_dict()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", job_id or ""):
        return None
    path = _artifact_dir() / f"{job_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:  # noqa: BLE001 - corrupt artifact reads as missing
            return None
    return None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """Summaries of recent jobs (memory + disk artifacts), newest first."""
    seen: dict[str, dict[str, Any]] = {}
    for job in _JOBS.values():
        seen[job.job_id] = job.to_dict()
    try:
        for path in _artifact_dir().glob("*.json"):
            if path.stem in seen:
                continue
            try:
                seen[path.stem] = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        pass
    jobs = sorted(seen.values(), key=lambda j: j.get("created_at") or "", reverse=True)[:limit]
    summaries = []
    for j in jobs:
        report = j.get("report") or {}
        summaries.append(
            {
                "job_id": j.get("job_id"),
                "status": j.get("status"),
                "created_at": j.get("created_at"),
                "completed_at": j.get("completed_at"),
                "mode": report.get("mode") or ("live" if (j.get("params") or {}).get("live") else "dry_run"),
                "target_model": report.get("target_model")
                or (j.get("params") or {}).get("target_model"),
                "verdict": report.get("verdict"),
                "error": j.get("error"),
            }
        )
    return summaries
