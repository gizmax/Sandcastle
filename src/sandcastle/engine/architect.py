"""The Architect: an autonomous generate -> run -> evaluate -> refine loop.

Give it a natural-language description and it returns a WORKING workflow with a
recorded cassette as proof. Each iteration: (1) generate (or refine) a workflow
via the existing NL generator, (2) execute it live with cassette RECORDING on,
budget- and time-capped, (3) evaluate the run - hard checks (completed, no
errors, non-empty output) plus an LLM judge scoring the output against the
original description, (4) below threshold, feed the failure back into the
generator's refine path and try again. On success the workflow and its freshly
recorded cassette are packed into a .sctpl bundle (the same proof format
``sandcastle template verify`` replays offline at $0) and installed where the
template hub surfaces it.

Costs: live run spend is tracked against ``architect_budget_usd`` and each run
is additionally capped at the remaining budget, so the loop can never overshoot
by more than one step. Advisor calls (generation/judge) are routed through the
same failover stack the generator uses.

Generated workflows are executed with ``admin_trusted=False`` - the loop never
runs ``code`` steps from model output - and the generator is instructed to emit
only replay-safe ``standard`` steps so the resulting bundle verifies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Appended to the user's description so the generator emits workflows whose
# bundle proof verifies offline (only `standard` steps are cassette-covered).
_REPLAY_SAFE_INSTRUCTION = (
    "\n\nConstraints: use ONLY standard prompt steps (no code, http, browser, "
    "tool, parse, or approval steps) so the workflow is fully reproducible "
    "from a recorded cassette."
)

_JUDGE_SYSTEM = (
    "You are a strict quality evaluator. Respond with ONLY a number between "
    "0.0 and 1.0."
)

_INPUT_GEN_SYSTEM = (
    "You generate realistic test inputs for workflows. Respond with ONLY a "
    "JSON object, no explanations and no markdown fencing."
)

_PLACEHOLDERS: dict[str, Any] = {
    "string": "example",
    "number": 1,
    "integer": 1,
    "boolean": True,
    "array": [],
    "object": {},
}


@dataclass
class IterationLog:
    """One pass of the generate -> run -> evaluate -> refine loop."""

    iteration: int
    generated: bool = False
    validation_errors: list[str] = field(default_factory=list)
    run_status: str = ""
    run_cost_usd: float = 0.0
    hard_check_failures: list[str] = field(default_factory=list)
    judge_score: float | None = None
    refinement_note: str = ""


@dataclass
class ArchitectResult:
    """Final outcome of an Architect session."""

    status: str  # "proven" | "verify_failed" | "max_iterations" | "budget_exceeded" | "error"
    proven: bool
    description: str
    yaml_content: str = ""
    workflow_name: str = ""
    test_input: dict[str, Any] = field(default_factory=dict)
    bundle_path: str | None = None
    installed_path: str | None = None
    template_name: str | None = None
    total_cost_usd: float = 0.0
    best_score: float = 0.0
    iterations: list[IterationLog] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view (for the API job store and CLI --json)."""
        data = asdict(self)
        # The full YAML of every iteration would bloat the job payload; the
        # final YAML is what callers act on.
        return data


def _sanitize_stem(name: str) -> str:
    """Sanitize a workflow name to a safe filename stem (no traversal)."""
    base = name.split("/")[-1]
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", base).lstrip(".")
    return safe or "workflow"


async def _judge_output(
    description: str, outputs: dict[str, Any]
) -> tuple[float, str]:
    """Score the run output 0.0-1.0 against the original description via LLM.

    Mirrors the AutoPilot judge: a single cheap call that must return a bare
    number. On any failure returns (0.5, note) so one flaky judge call neither
    blesses nor kills an iteration.
    """
    from sandcastle.engine.generator import _call_advisor_llm

    output_str = json.dumps(outputs, default=str)[:4000]
    user_msg = (
        "A workflow was built from this request:\n"
        f"{description}\n\n"
        "Its run produced these outputs:\n"
        f"{output_str}\n\n"
        "Rate from 0.0 (output does not satisfy the request at all) to 1.0 "
        "(output fully satisfies the request). Respond with ONLY the number."
    )
    try:
        raw = await _call_advisor_llm(
            system=_JUDGE_SYSTEM, user=user_msg, max_tokens=16, purpose="judge"
        )
        score = max(0.0, min(1.0, float(raw.strip())))
        return score, f"judge scored {score:.2f}"
    except Exception as exc:  # noqa: BLE001 - judge must never crash the loop
        logger.warning("Architect judge call failed: %s", exc)
        return 0.5, f"judge unavailable ({exc}); neutral 0.5 assumed"


async def _derive_test_input(
    description: str, input_schema: dict[str, Any] | None
) -> dict[str, Any]:
    """Build a plausible test input for the generated workflow.

    Order: schema defaults/examples -> one LLM call for the leftovers ->
    type-based placeholders. Always returns a dict covering every declared
    property, so the run never fails on missing input.
    """
    props = (input_schema or {}).get("properties", {}) or {}
    test_input: dict[str, Any] = {}
    missing: dict[str, Any] = {}
    for key, spec in props.items():
        spec = spec if isinstance(spec, dict) else {}
        if "default" in spec:
            test_input[key] = spec["default"]
        elif "example" in spec:
            test_input[key] = spec["example"]
        else:
            missing[key] = spec
    if not missing:
        return test_input

    from sandcastle.engine.generator import _call_advisor_llm

    try:
        user_msg = (
            f"Workflow purpose: {description}\n\n"
            f"Produce a realistic test value for each input property:\n"
            f"{json.dumps(missing, default=str)}\n\n"
            "Return a single JSON object mapping property name to value."
        )
        raw = await _call_advisor_llm(
            system=_INPUT_GEN_SYSTEM, user=user_msg, max_tokens=512,
            purpose="generation",
        )
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        generated = json.loads(raw)
        if isinstance(generated, dict):
            for key in missing:
                if key in generated:
                    test_input[key] = generated[key]
    except Exception as exc:  # noqa: BLE001 - fall through to placeholders
        logger.warning("Architect test-input generation failed: %s", exc)

    for key, spec in missing.items():
        if key not in test_input:
            test_input[key] = _PLACEHOLDERS.get(str(spec.get("type", "string")), "example")
    return test_input


def install_bundle(
    bundle_path: str | Path, target_dir: str | Path | None = None
) -> Path:
    """Install a bundle's workflow + proof cassettes like ``template install``.

    Writes into the community templates directory (where the dashboard hub and
    Lite wizard look) unless *target_dir* is given. Returns the installed
    workflow path.
    """
    from sandcastle.engine.bundle import read_bundle

    manifest, workflow_yaml, cassettes = read_bundle(bundle_path)
    if target_dir is None:
        from sandcastle.templates import _TEMPLATES_DIR

        target_dir = _TEMPLATES_DIR / "community"
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = _sanitize_stem(str(manifest["name"]))
    target_path = (target_dir / f"{stem}.yaml").resolve()
    if not str(target_path).startswith(str(target_dir.resolve())):
        raise ValueError("unsafe template name in manifest")
    target_path.write_text(workflow_yaml)
    for arcname, blob in cassettes.items():
        cassette_name = _sanitize_stem(Path(arcname).stem) + ".cassette.json"
        (target_dir / f"{stem}.{cassette_name}").write_bytes(blob)
    return target_path


async def design_workflow(
    description: str,
    *,
    test_input: dict[str, Any] | None = None,
    budget_usd: float | None = None,
    max_iterations: int | None = None,
    score_threshold: float | None = None,
    run_timeout_seconds: float = 600.0,
    output_dir: str | Path | None = None,
    install: bool = True,
    install_dir: str | Path | None = None,
    tenant_id: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> ArchitectResult:
    """Turn a natural-language description into a proven workflow template.

    Runs the generate -> run -> evaluate -> refine loop until a run completes
    with a judge score at or above the threshold, then packs the workflow and
    its recorded cassette into a verified .sctpl bundle and installs it.

    Args:
        description: What the workflow should do (natural language).
        test_input: Inputs for the proof run; derived from the generated
            input schema (defaults -> LLM -> placeholders) when omitted.
        budget_usd: Total live-run spend cap (default: settings.architect_budget_usd).
        max_iterations: Loop bound (default: settings.architect_max_iterations).
        score_threshold: Minimum judge score (default: settings.architect_score_threshold).
        run_timeout_seconds: Wall-clock cap per proof run.
        output_dir: Where the .sctpl bundle is written (default: temp dir).
        install: Install the proven bundle like ``template install`` does.
        install_dir: Override the install target (default: community templates dir).
        tenant_id: Calling tenant, forwarded to the generator.
        progress: Optional callback receiving human-readable progress lines.

    Returns:
        ArchitectResult - ``proven=True`` only when the packed bundle passes
        the same ``verify_bundle`` check that gates template installs.
    """
    from sandcastle.config import settings
    from sandcastle.engine import generator as _generator
    from sandcastle.engine.cassette import CassetteStore
    from sandcastle.engine.dag import build_plan, parse_yaml_string
    from sandcastle.engine.executor import execute_workflow

    budget = settings.architect_budget_usd if budget_usd is None else float(budget_usd)
    iterations = (
        settings.architect_max_iterations if max_iterations is None else int(max_iterations)
    )
    threshold = (
        settings.architect_score_threshold if score_threshold is None else float(score_threshold)
    )
    iterations = max(1, iterations)

    def _say(msg: str) -> None:
        logger.info("Architect: %s", msg)
        if progress is not None:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001 - progress is cosmetic
                pass

    result = ArchitectResult(status="error", proven=False, description=description)
    workdir = Path(output_dir) if output_dir else Path(
        tempfile.mkdtemp(prefix="sandcastle-architect-")
    )
    workdir.mkdir(parents=True, exist_ok=True)

    gen_description = description + _REPLAY_SAFE_INSTRUCTION
    yaml_current: str | None = None
    feedback = ""
    spent = 0.0
    best_score = -1.0
    best_yaml = ""

    for i in range(1, iterations + 1):
        log = IterationLog(iteration=i)
        result.iterations.append(log)

        # --- 1. generate / refine -----------------------------------------
        try:
            if yaml_current is None:
                _say(f"iteration {i}: generating workflow from description")
                gen = await _generator.generate_workflow(
                    gen_description, tenant_id=tenant_id
                )
            else:
                _say(f"iteration {i}: refining workflow ({feedback[:120]})")
                log.refinement_note = feedback
                gen = await _generator.generate_workflow(
                    gen_description,
                    refine_from=yaml_current,
                    refine_instruction=feedback,
                    tenant_id=tenant_id,
                )
        except Exception as exc:  # noqa: BLE001 - report, don't crash the job
            result.error = f"generation failed: {exc}"
            result.status = "error"
            break
        log.generated = True
        yaml_current = gen.yaml_content
        result.yaml_content = yaml_current
        log.validation_errors = list(gen.validation_errors)
        if gen.validation_errors:
            feedback = "Fix these validation errors: " + "; ".join(gen.validation_errors)
            log.run_status = "skipped (validation errors)"
            continue

        try:
            wf = parse_yaml_string(yaml_current)
            plan = build_plan(wf)
        except Exception as exc:  # noqa: BLE001 - feed parse failure back
            feedback = f"The YAML does not parse/plan: {exc}. Fix it."
            log.run_status = "skipped (parse error)"
            continue
        result.workflow_name = wf.name

        # --- 2. test input (once) ------------------------------------------
        if test_input is None:
            _say(f"iteration {i}: deriving test inputs from the input schema")
            test_input = await _derive_test_input(description, gen.input_schema)
        result.test_input = dict(test_input)

        # --- 3. run with cassette recording, budget- and time-capped --------
        remaining = budget - spent
        if remaining <= 0:
            result.status = "budget_exceeded"
            result.error = f"budget ${budget:.2f} exhausted before iteration {i} could run"
            break
        cassette_path = workdir / f"iter{i}.cassette.json"
        store = CassetteStore(cassette_path, "record")
        _say(f"iteration {i}: running '{wf.name}' (budget left ${remaining:.4f})")
        try:
            wf_result = await asyncio.wait_for(
                execute_workflow(
                    workflow=wf,
                    plan=plan,
                    input_data=dict(test_input),
                    max_cost_usd=remaining,
                    admin_trusted=False,  # never execute code steps from model output
                    tenant_id=tenant_id,
                    cassette=store,
                    cassette_mode="record",
                ),
                timeout=run_timeout_seconds,
            )
        except asyncio.TimeoutError:
            feedback = (
                f"The run timed out after {run_timeout_seconds:.0f}s. Make the "
                "workflow simpler/faster."
            )
            log.run_status = "timeout"
            continue
        except Exception as exc:  # noqa: BLE001 - feed run crash back
            feedback = f"The run crashed: {exc}. Fix the workflow."
            log.run_status = "crashed"
            continue

        run_cost = float(getattr(wf_result, "total_cost_usd", 0.0) or 0.0)
        spent += run_cost
        result.total_cost_usd = round(spent, 6)
        log.run_status = str(getattr(wf_result, "status", "unknown"))
        log.run_cost_usd = round(run_cost, 6)

        if log.run_status == "budget_exceeded":
            result.status = "budget_exceeded"
            result.error = f"run hit the architect budget cap (${budget:.2f} total)"
            break

        # --- 4. evaluate: hard checks + LLM judge ---------------------------
        outputs = getattr(wf_result, "outputs", {}) or {}
        hard_failures: list[str] = []
        if log.run_status != "completed":
            hard_failures.append(f"run finished with status '{log.run_status}'")
        run_error = getattr(wf_result, "error", None)
        if run_error:
            hard_failures.append(f"run error: {run_error}")
        if not outputs or all(v in (None, "", {}, []) for v in outputs.values()):
            hard_failures.append("run produced empty output")
        log.hard_check_failures = hard_failures

        if hard_failures:
            score = 0.0
            judge_note = "skipped (hard checks failed)"
        else:
            _say(f"iteration {i}: judging output against the description")
            score, judge_note = await _judge_output(description, outputs)
            log.judge_score = score
        if score > best_score:
            best_score, best_yaml = score, yaml_current
        result.best_score = max(0.0, best_score)

        # --- 5. success: pack the proof ------------------------------------
        if not hard_failures and score >= threshold:
            store.save()
            stem = _sanitize_stem(wf.name)
            wf_file = workdir / f"{stem}.yaml"
            wf_file.write_text(yaml_current)
            bundle_path = workdir / f"{stem}-1.0.0.sctpl"
            _say(f"iteration {i}: PASS (score {score:.2f}) - packing proof bundle")
            try:
                from sandcastle.engine.bundle import create_bundle, verify_bundle

                create_bundle(
                    wf_file,
                    [cassette_path],
                    bundle_path,
                    name=wf.name,
                    description=wf.description,
                    author="the-architect",
                    example_inputs=dict(test_input),
                )
                result.bundle_path = str(bundle_path)
                # verify_bundle drives its own event loop - keep it off ours.
                verify = await asyncio.to_thread(verify_bundle, bundle_path)
            except Exception as exc:  # noqa: BLE001 - packing must not lose the win
                result.status = "verify_failed"
                result.error = f"bundle packing/verification failed: {exc}"
                break
            if not verify.ok:
                result.status = "verify_failed"
                result.error = "bundle did not verify: " + "; ".join(
                    verify.errors
                    + [c.detail for c in verify.cassette_results if not c.passed]
                )
                break
            result.proven = True
            result.status = "proven"
            result.template_name = wf.name
            if install:
                try:
                    installed = install_bundle(bundle_path, target_dir=install_dir)
                    result.installed_path = str(installed)
                    _say(f"installed proven template -> {installed}")
                except Exception as exc:  # noqa: BLE001 - install is best-effort
                    logger.warning("Architect install failed: %s", exc)
            break

        # --- 6. refine feedback for the next pass ---------------------------
        if hard_failures:
            feedback = (
                "The workflow ran but failed hard checks: "
                + "; ".join(hard_failures)
                + ". Fix the workflow so the run completes with useful output."
            )
        else:
            feedback = (
                f"The run completed but the output only scored {score:.2f} "
                f"(threshold {threshold:.2f}) against the request. Improve the "
                "prompts so the output better satisfies: " + description
            )
        log.refinement_note = log.refinement_note or judge_note

        if spent >= budget:
            result.status = "budget_exceeded"
            result.error = (
                f"budget ${budget:.2f} exhausted after iteration {i} (spent ${spent:.4f})"
            )
            break
    else:
        result.status = "max_iterations"
        result.error = (
            f"no iteration reached the {threshold:.2f} threshold within "
            f"{iterations} iteration(s); returning the best attempt"
        )

    if result.status in ("max_iterations", "budget_exceeded") and best_yaml:
        result.yaml_content = best_yaml
    return result
