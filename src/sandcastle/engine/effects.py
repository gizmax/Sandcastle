"""Durable step effect ledger: a side effect fires once per replay lineage.

Replay and fork already skip steps whose output is in the checkpoint, and the
cassette already memoizes ``standard`` prompt steps. Neither covers the case
that actually costs money or changes the world: a hybrid step type - ``llm``,
``http``, ``notify``, ``tool`` - re-executing because it was never in the
checkpoint (cancelled at a pause), or because the replay legitimately starts
upstream of it. The ledger closes that gap.

Mechanism, in three moves:

1. **Fingerprint the effect, not the step.** ``step_effect_fingerprint``
   hashes the *resolved* description of what the step is about to do: method +
   URL + body for ``http``, model + prompt for ``llm``, and so on. Fork a step
   upstream with a new prompt and the downstream body changes, so the
   fingerprint changes, so the effect correctly fires again. The fingerprint is
   the dependency analysis, and it is exact rather than conservative.

2. **Claim before firing.** ``EffectLedger.claim`` INSERTs an ``in_flight`` row
   keyed on the fingerprint, committed in its own transaction *before* the
   network call. A duplicate INSERT loses the unique constraint, which is what
   makes the claim a lock that works identically on SQLite and PostgreSQL.

3. **Three states, no guessing.** ``committed`` -> return the recorded output at
   ``cost_usd=0.0``. ``failed`` -> the effect did not land, run it again.
   ``in_flight`` past its lease -> we genuinely do not know whether the POST
   arrived, so the step fails with :class:`EffectUncertain` rather than
   charging the card twice. ``on_uncertain: retry`` opts an idempotent endpoint
   back into re-execution.

Scope is the *lineage*, not the run: a replay inherits its parent's
``effect_scope_id`` (see ``Run.effect_scope_id``), which is what lets a
replayed ``http`` step see the effect its parent already committed. A fresh run
is its own scope, so two unrelated runs of the same workflow never share
effects. ``tenant_id`` is folded into the key for the same reason
``_compute_cache_key`` folds it in: cross-tenant reuse of a recorded output is
a data leak, not a cache hit.

The ledger is deliberately distinct from the cassette. A cassette is a portable
file that travels with a bundle; replaying somebody else's cassette must never
convince this installation that *its* POST already happened. The ledger is
installation-local and durable.

Kill switch: ``SANDCASTLE_EFFECT_LEDGER=0`` (``settings.effect_ledger_enabled``).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# HTTP methods that do not change server state, so re-executing them on a
# replay is cheap and usually *wanted* (a rate lookup should be fresh). A step
# opts back in with ``replay: memoize``.
SAFE_HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

VALID_REPLAY_MODES = frozenset({"", "memoize", "live"})
VALID_ON_UNCERTAIN = frozenset({"fail", "retry"})

EFFECT_IN_FLIGHT = "in_flight"
EFFECT_COMMITTED = "committed"
EFFECT_FAILED = "failed"

# Step types that always re-execute: pure transforms, control flow that mutates
# the run context (``condition``/``classify`` write branch_skip_steps, so a
# memoized return would silently un-skip the whole other branch), composites
# whose children carry their own guards, and the approval gate itself.
_LIVE_STEP_TYPES = frozenset(
    {
        "approval",
        "code",
        "condition",
        "classify",
        "delegate",
        "gate",
        "loop",
        "parse",
        "race",
        "report",
        "sensor",
        "sub_workflow",
        "trajectory-replay",
        "transform",
    }
)

# Step types the effect guard never intercepts at all - not for the ledger and
# not for the cassette. These either mutate the run context beyond their own
# output (condition/classify branch selection, gate blocking) or recurse into
# child steps that are guarded individually.
GUARD_EXEMPT_STEP_TYPES = frozenset(
    {
        # accept satisfies both halves of the rule above: like gate it blocks
        # the run on a verdict rather than merely producing output, and with
        # on_reject: retry_target it re-runs its target through
        # execute_step_with_retry, where that step is guarded on its own.
        # Guarding the accept step itself would memoize a verdict and, worse,
        # fingerprint a re-work round as a repeat of the round before it.
        # Because it is exempt it is never fingerprinted, so accept_config is
        # deliberately absent from _CONFIG_ATTRS - the same treatment
        # gate_config gets.
        "accept",
        "approval",
        "classify",
        "condition",
        "delegate",
        "gate",
        "loop",
        "race",
        "sub_workflow",
    }
)


def _ledger_is_required(settings) -> bool:
    """Whether an unreachable ledger should fail the step rather than run live.

    Explicit configuration wins.  Left unset, the answer follows the
    deployment: `sandcastle run --local` has no database to begin with, so
    failing every side-effecting step there would break a documented mode.  A
    server deployment does have one, so a ledger it cannot reach is a fault -
    and executing live is precisely the duplicate-POST this guards against.
    """
    configured = settings.effect_ledger_required
    if configured is not None:
        return bool(configured)
    return not settings.is_local_mode


class EffectUncertain(Exception):
    """A previous attempt claimed this effect and never reported an outcome.

    Raised when a claim row is ``in_flight`` past its lease. The honest answer
    is that we do not know whether the side effect landed, and guessing is how
    a card gets charged twice.
    """


def effect_mode_for(step: Any) -> str:
    """Return ``"memoize"`` or ``"live"`` for a step definition.

    An explicit ``replay:`` on the step always wins. Otherwise the default is
    per type: anything that spends money or changes the world memoizes; pure
    and composite types stay live. ``http`` splits on method, because a GET is
    a read and a POST is not.
    """
    declared = (getattr(step, "replay", "") or "").strip().lower()
    if declared in ("memoize", "live"):
        return declared

    step_type = getattr(step, "type", "")
    if step_type in _LIVE_STEP_TYPES:
        return "live"
    if step_type == "http":
        cfg = getattr(step, "http_config", None)
        method = (getattr(cfg, "method", "GET") or "GET").upper()
        return "live" if method in SAFE_HTTP_METHODS else "memoize"
    return "memoize"


def on_uncertain_for(step: Any) -> str:
    """Return the configured half-completion policy for a step."""
    declared = (getattr(step, "on_uncertain", "") or "").strip().lower()
    return declared if declared in VALID_ON_UNCERTAIN else "fail"


def _canonical(value: Any) -> str:
    """Stable JSON for hashing - sorted keys, no whitespace, str fallback."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


# Config fields whose *values* are credentials. The fingerprint keeps the field
# (a rotated token means a different effect) but only as a digest, so nothing
# secret-derived is ever written to a durable table.
_SECRET_FIELDS = frozenset({"auth", "api_key", "token", "password", "secret", "credentials"})

_CONFIG_ATTRS = (
    "llm_config",
    "http_config",
    "code_config",
    "tool_config",
    "sensor_config",
    "transform_config",
    "notify_config",
    "browser_config",
    "composio_config",
    "openclaw_config",
    "parse_config",
    "report_config",
    "managed_agent_config",
    "computer_use_config",
    # Without this an acp step's fingerprint would be (type, model, placeholder
    # prompt) - identical for two steps that spawn different harnesses in
    # different repos with different messages. The ledger would then memoize one
    # agent turn and hand its output to the other.
    "acp_config",
)


def _resolve_deep(value: Any, context: Any, *, secret: bool = False) -> Any:
    """Resolve templates through a nested config structure.

    Strings are resolved against the run context so the fingerprint describes
    the *actual* request, not the template. Secret-bearing values collapse to a
    digest so a token never reaches the ledger table.
    """
    from sandcastle.engine.executor import resolve_templates

    if isinstance(value, str):
        try:
            resolved = resolve_templates(value, context)
        except Exception:  # pragma: no cover - defensive, resolution is pure
            resolved = value
        return _digest(resolved) if secret else resolved
    if isinstance(value, dict):
        return {
            key: _resolve_deep(
                item, context, secret=secret or str(key).lower() in _SECRET_FIELDS
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_resolve_deep(item, context, secret=secret) for item in value]
    return value


def step_effect_fingerprint(step: Any, context: Any) -> str:
    """Hash the resolved description of the effect this step is about to cause.

    Inputs are the step type, the resolved prompt, the effective model, and the
    step's own type-specific config with every template resolved. Header and
    auth values are folded in as digests (see ``_SECRET_FIELDS``).

    Storage references (``{storage.x}``) are deliberately *not* expanded: that
    would need I/O on a hot path, and the reference itself is stable enough to
    identify the effect. A step whose payload varies only through a storage
    ref is a candidate for ``replay: live``.
    """
    from sandcastle.engine.executor import resolve_templates

    try:
        prompt = resolve_templates(step.prompt or "", context)
    except Exception:  # pragma: no cover - defensive
        prompt = step.prompt or ""

    payload: dict[str, Any] = {
        "type": step.type,
        "model": getattr(step, "model", "") or "",
        "prompt": prompt,
    }
    for attr in _CONFIG_ATTRS:
        cfg = getattr(step, attr, None)
        if cfg is None:
            continue
        raw = dataclasses.asdict(cfg) if dataclasses.is_dataclass(cfg) else cfg
        payload[attr] = _resolve_deep(raw, context)
    # HTTP headers can carry a bearer token that never appears in ``auth``.
    http_cfg = payload.get("http_config")
    if isinstance(http_cfg, dict) and isinstance(http_cfg.get("headers"), dict):
        http_cfg["headers"] = {
            name: _digest(value) for name, value in http_cfg["headers"].items()
        }
    return _digest(payload)


def compute_effect_key(
    scope_id: str,
    tenant_id: str | None,
    step_id: str,
    fingerprint: str,
    parallel_index: int | None = None,
    iteration_index: Any = None,
) -> str:
    """SHA-256 identity of one effect within one replay lineage.

    ``parallel_index`` and ``iteration_index`` are load-bearing, not
    decoration: without them a 500-item fan-out over a constant URL, or a loop
    body that posts once per iteration, would collapse into a single effect and
    the ledger would suppress N-1 legitimate sends.
    """
    return _digest(
        {
            "scope": scope_id or "",
            "tenant": tenant_id if tenant_id else "_none_",
            "step": step_id,
            "pidx": parallel_index,
            "iidx": iteration_index,
            "fp": fingerprint,
        }
    )


@dataclass
class ClaimResult:
    """Outcome of trying to claim an effect before executing it."""

    # "owned"      - we inserted the claim row; execute and report back.
    # "memoized"   - a committed row already exists; reuse its output.
    # "uncertain"  - an abandoned in_flight row; the caller applies on_uncertain.
    # "unavailable"- the ledger could not be reached; execute live (see the
    #                effect_ledger_required note in claim()).
    outcome: str
    effect_key: str = ""
    output: Any = None
    cost_usd: float = 0.0
    owner_run_id: str = ""
    detail: str = ""


def _numeric_setting(settings: Any, name: str, default: float) -> float:
    """Read a numeric setting, falling back when it is absent or not a number.

    Tests routinely replace the whole settings object with a MagicMock, whose
    attributes are mocks rather than numbers. A ledger that raised on that would
    fail every guarded step in those suites, which is noise, not a signal.
    """
    try:
        return type(default)(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


class EffectLedger:
    """Read/write access to the ``run_step_effects`` table.

    Every method is best-effort about *reaching* the database and strict about
    what it finds there: an unreachable ledger degrades to live execution (see
    ``settings.effect_ledger_required``), but an ``in_flight`` row past its
    lease always raises rather than re-firing.
    """

    def __init__(self, lease_seconds: int | None = None, ttl_days: int | None = None) -> None:
        from sandcastle.config import settings

        self.lease_seconds = (
            lease_seconds
            if lease_seconds is not None
            else int(_numeric_setting(settings, "effect_lease_seconds", 900))
        )
        self.ttl_days = (
            ttl_days
            if ttl_days is not None
            else int(_numeric_setting(settings, "effect_ledger_ttl_days", 30))
        )

    async def claim(
        self,
        *,
        effect_key: str,
        scope_id: str,
        run_id: str,
        tenant_id: str | None,
        step_id: str,
        step_type: str,
        parallel_index: int | None = None,
        iteration_index: int | None = None,
        wait_seconds: float | None = None,
    ) -> ClaimResult:
        """Try to become the owner of *effect_key*.

        Wins the INSERT -> ``owned``. Loses it -> inspect the existing row.
        A live claim (``in_flight`` inside its lease) means another worker is
        mid-flight, so poll briefly for its commit before declaring the effect
        uncertain.
        """
        from sandcastle.config import settings

        if wait_seconds is None:
            wait_seconds = _numeric_setting(settings, "effect_claim_wait_seconds", 5.0)

        try:
            inserted = await self._insert_claim(
                effect_key=effect_key,
                scope_id=scope_id,
                run_id=run_id,
                tenant_id=tenant_id,
                step_id=step_id,
                step_type=step_type,
                parallel_index=parallel_index,
                iteration_index=iteration_index,
            )
        except _LedgerUnavailable as exc:
            return self._unavailable(effect_key, exc)

        if inserted:
            return ClaimResult(outcome="owned", effect_key=effect_key)

        deadline = asyncio.get_running_loop().time() + max(wait_seconds, 0.0)
        while True:
            try:
                row = await self.lookup(effect_key)
            except _LedgerUnavailable as exc:
                return self._unavailable(effect_key, exc)
            if row is None:
                # The row vanished between the failed INSERT and this SELECT
                # (pruned, or rolled back). Nothing owns the effect: run it.
                return ClaimResult(outcome="owned", effect_key=effect_key)
            if row["status"] == EFFECT_COMMITTED:
                return ClaimResult(
                    outcome="memoized",
                    effect_key=effect_key,
                    output=row["output_data"],
                    cost_usd=row["cost_usd"],
                    owner_run_id=row["run_id"],
                )
            if row["status"] == EFFECT_FAILED:
                # A failed effect did not change the world. Take the row over.
                try:
                    if await self._take_over(effect_key, run_id):
                        return ClaimResult(outcome="owned", effect_key=effect_key)
                except _LedgerUnavailable as exc:
                    return self._unavailable(effect_key, exc)
                continue
            lease = row["lease_expires_at"]
            expired = lease is None or lease <= datetime.now(timezone.utc)
            if expired:
                return ClaimResult(
                    outcome="uncertain",
                    effect_key=effect_key,
                    owner_run_id=row["run_id"],
                    detail=(
                        f"effect {effect_key[:12]}... was claimed by run "
                        f"{row['run_id']} and never reported an outcome"
                    ),
                )
            if asyncio.get_running_loop().time() >= deadline:
                return ClaimResult(
                    outcome="uncertain",
                    effect_key=effect_key,
                    owner_run_id=row["run_id"],
                    detail=(
                        f"effect {effect_key[:12]}... is in flight in run "
                        f"{row['run_id']} and did not settle within "
                        f"{wait_seconds:g}s"
                    ),
                )
            await asyncio.sleep(0.05)

    async def commit(
        self, effect_key: str, output: Any, cost_usd: float, run_id: str = ""
    ) -> None:
        """Record the effect as landed, with the output to memoize."""
        expires = datetime.now(timezone.utc) + timedelta(days=self.ttl_days)
        await self._update(
            effect_key,
            {
                "status": EFFECT_COMMITTED,
                "output_data": {"value": output},
                "cost_usd": max(float(cost_usd or 0.0), 0.0),
                "committed_at": datetime.now(timezone.utc),
                "lease_expires_at": None,
                "expires_at": expires,
                "error": None,
            },
            run_id=run_id,
        )

    async def mark_failed(self, effect_key: str, error: str | None = None) -> None:
        """Release the claim: the effect reported a failure, so it may re-run."""
        expires = datetime.now(timezone.utc) + timedelta(days=self.ttl_days)
        await self._update(
            effect_key,
            {
                "status": EFFECT_FAILED,
                "lease_expires_at": None,
                "expires_at": expires,
                "error": (error or "")[:2000] or None,
            },
        )

    async def take_over(self, effect_key: str, run_id: str) -> bool:
        """Seize an abandoned claim so this run may re-execute the effect.

        Only reached via ``on_uncertain: retry``, i.e. the workflow author has
        asserted the endpoint is idempotent.
        """
        return await self._take_over(
            effect_key, run_id, from_status=(EFFECT_FAILED, EFFECT_IN_FLIGHT)
        )

    async def lookup(self, effect_key: str) -> dict[str, Any] | None:
        """Return the ledger row for *effect_key* as a plain dict, or None."""
        try:
            from sqlalchemy import select as sa_select

            from sandcastle.models.db import StepEffect, async_session

            async with async_session() as session:
                row = await session.scalar(
                    sa_select(StepEffect).where(StepEffect.effect_key == effect_key)
                )
                if row is None:
                    return None
                lease = row.lease_expires_at
                if lease is not None and lease.tzinfo is None:
                    lease = lease.replace(tzinfo=timezone.utc)
                payload = row.output_data or {}
                return {
                    "status": row.status,
                    "output_data": payload.get("value") if isinstance(payload, dict) else None,
                    "cost_usd": row.cost_usd or 0.0,
                    "run_id": row.run_id,
                    "lease_expires_at": lease,
                }
        except Exception as exc:
            raise _LedgerUnavailable(str(exc)) from exc

    # -- internals -----------------------------------------------------------

    def _unavailable(self, effect_key: str, exc: Exception) -> ClaimResult:
        from sandcastle.config import settings

        if _ledger_is_required(settings):
            raise EffectUncertain(
                f"effect ledger is unreachable and the ledger is required "
                f"in this deployment: {exc}"
            ) from exc
        logger.warning(
            "Effect ledger unavailable (%s) - step executes live. Set "
            "EFFECT_LEDGER_REQUIRED=1 to fail instead of re-executing.",
            exc,
        )
        return ClaimResult(outcome="unavailable", effect_key=effect_key, detail=str(exc))

    async def _insert_claim(
        self,
        *,
        effect_key: str,
        scope_id: str,
        run_id: str,
        tenant_id: str | None,
        step_id: str,
        step_type: str,
        parallel_index: int | None,
        iteration_index: int | None,
    ) -> bool:
        """INSERT the claim row in its own transaction. False = lost the race."""
        from sqlalchemy.exc import IntegrityError

        from sandcastle.models.db import StepEffect, async_session

        now = datetime.now(timezone.utc)
        try:
            async with async_session() as session:
                session.add(
                    StepEffect(
                        id=uuid.uuid4(),
                        effect_key=effect_key,
                        effect_scope_id=scope_id,
                        run_id=run_id,
                        tenant_id=tenant_id,
                        step_id=step_id[:255],
                        step_type=step_type[:50],
                        parallel_index=parallel_index,
                        iteration_index=iteration_index,
                        status=EFFECT_IN_FLIGHT,
                        created_at=now,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    return False
            return True
        except IntegrityError:
            return False
        except Exception as exc:
            raise _LedgerUnavailable(str(exc)) from exc

    async def _take_over(
        self,
        effect_key: str,
        run_id: str,
        from_status: tuple[str, ...] = (EFFECT_FAILED,),
    ) -> bool:
        """Re-arm a settled row for this run. False = somebody beat us to it."""
        from sqlalchemy import update as sa_update

        from sandcastle.models.db import StepEffect, async_session

        now = datetime.now(timezone.utc)
        try:
            async with async_session() as session:
                res = await session.execute(
                    sa_update(StepEffect)
                    .where(
                        StepEffect.effect_key == effect_key,
                        StepEffect.status.in_(from_status),
                    )
                    .values(
                        status=EFFECT_IN_FLIGHT,
                        run_id=run_id,
                        error=None,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                        attempt=StepEffect.attempt + 1,
                    )
                )
                await session.commit()
                return bool(res.rowcount)
        except Exception as exc:
            raise _LedgerUnavailable(str(exc)) from exc

    async def _update(
        self, effect_key: str, values: dict[str, Any], run_id: str = ""
    ) -> None:
        from sqlalchemy import update as sa_update

        from sandcastle.models.db import StepEffect, async_session

        if run_id:
            values = {**values, "run_id": run_id}
        try:
            async with async_session() as session:
                await session.execute(
                    sa_update(StepEffect)
                    .where(StepEffect.effect_key == effect_key)
                    .values(**values)
                )
                await session.commit()
        except Exception as exc:
            # A commit that cannot be recorded is worse than one that cannot be
            # claimed: the next replay will see in_flight and refuse to guess,
            # which is the safe direction. Log loudly, do not fail the step
            # whose effect already landed.
            logger.error(
                "Effect ledger write failed for %s...: %s", effect_key[:12], exc
            )


class _LedgerUnavailable(Exception):
    """The ledger table could not be read or written (infrastructure, not state)."""


async def prune_expired_effects(limit: int = 5000) -> int:
    """Delete ledger rows past their TTL. Returns the number removed.

    Called at worker startup. Without it the table only grows: nothing else in
    the codebase sweeps ``step_cache`` either, and the ledger has the same
    shape of problem.
    """
    try:
        from sqlalchemy import delete as sa_delete
        from sqlalchemy import select as sa_select

        from sandcastle.models.db import StepEffect, async_session

        now = datetime.now(timezone.utc)
        async with async_session() as session:
            stale = await session.scalars(
                sa_select(StepEffect.id)
                .where(StepEffect.expires_at.is_not(None), StepEffect.expires_at < now)
                .limit(limit)
            )
            ids = list(stale)
            if not ids:
                return 0
            await session.execute(sa_delete(StepEffect).where(StepEffect.id.in_(ids)))
            await session.commit()
            logger.info("Pruned %d expired step effect(s)", len(ids))
            return len(ids)
    except Exception as exc:
        logger.debug("Effect ledger prune skipped: %s", exc)
        return 0
