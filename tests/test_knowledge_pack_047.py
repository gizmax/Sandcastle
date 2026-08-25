"""The v0.47 knowledge-work template pack.

Sandcastle's bundled catalogue is overwhelmingly devops/RPA, and the two newest
step types - ``accept`` (0.45) and ``acp`` (0.46) - shipped with no bundled
template at all. This pack adds six templates covering legal, recruiting, sales,
marketing and healthcare, and makes ``accept`` the spine of every one of them,
because knowledge work is exactly the domain where "the model produced output"
and "the output is safe to act on" come apart.

These tests pin three things:

* the structural contract every bundled template owes (parses, validates, plans,
  sandbox-clean code, discoverable in the catalogue) - mirroring
  ``test_model_neutral_templates_v033.py``;
* the pack's own thesis - every template has an accept gate, every accept gate
  has BOTH free deterministic checks and paid judges, every one carries an
  explicit local cost ceiling, and every notify is a dry run;
* that the flagship contract-review template actually executes its accept gate,
  through the real ``_execute_accept_step`` with only the judge HTTP mocked, on
  the approve path, the reject-retry-approve path, and the path where the panel
  never accepts and the run therefore stops.

See ``docs/design/047-knowledge-pack.md`` for the guard-vs-filter reasoning
behind each gate's ``fail_on_reject``.
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sandcastle.engine.dag import (
    VALID_STEP_TYPES,
    build_plan,
    parse_yaml_string,
    validate,
)
from sandcastle.engine.executor import (
    _CODE_STEP_BLOCKED_PATTERNS,
    RunContext,
    StepResult,
    _execute_accept_step,
)
from sandcastle.engine.hub_scanner import scan_template
from sandcastle.engine.storage import LocalStorage
from sandcastle.templates import list_templates

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT / "src" / "sandcastle" / "templates"

# stem -> the `# category:` header it must carry. The categories are all
# pre-existing ones with CATEGORY_LABELS entries, so no new category is
# introduced and the hub category bar is unaffected.
KNOWLEDGE_PACK = {
    "contract_review_clause_extractor": "hr_legal",
    "recruiting_screen_evidence_gate": "hr_legal",
    "crm_lead_enrichment_quality_gate": "sales_crm",
    "sales_call_brief": "sales_crm",
    "marketing_localization_qa": "marketing",
    "patient_intake_summarizer": "healthcare",
}
PACK = sorted(KNOWLEDGE_PACK)

FLAGSHIP = "contract_review_clause_extractor"


def _load(stem: str) -> str:
    path = TEMPLATES_DIR / f"{stem}.yaml"
    assert path.exists(), f"Template file not found: {path}"
    return path.read_text(encoding="utf-8")


def _wf(stem: str):
    return parse_yaml_string(_load(stem))


def _header_field(text: str, key: str) -> str | None:
    """Read one ``# key: value`` field from the YAML comment header."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            break
        stripped = stripped.lstrip("#").strip()
        if stripped.startswith(f"{key}:"):
            return stripped.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# The structural contract every bundled template owes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", PACK)
def test_parses_with_steps(stem: str) -> None:
    wf = _wf(stem)
    assert wf.name, f"{stem} has no name"
    assert len(wf.steps) > 0, f"{stem} has no steps"


@pytest.mark.parametrize("stem", PACK)
def test_validates_clean(stem: str) -> None:
    errors = validate(_wf(stem))
    assert errors == [], f"{stem} validate() errors: {errors}"


@pytest.mark.parametrize("stem", PACK)
def test_build_plan_covers_every_step_exactly_once(stem: str) -> None:
    wf = _wf(stem)
    planned = [sid for stage in build_plan(wf).stages for sid in stage]
    assert len(planned) == len(set(planned)), f"{stem} plans a step twice"
    assert set(planned) == {s.id for s in wf.steps}, f"{stem} plan misses steps"


@pytest.mark.parametrize("stem", PACK)
def test_step_types_and_references_resolve(stem: str) -> None:
    wf = _wf(stem)
    ids = {s.id for s in wf.steps}
    for step in wf.steps:
        assert step.type in VALID_STEP_TYPES, f"{stem}/{step.id} bad type {step.type!r}"
        for dep in step.depends_on:
            assert dep in ids, f"{stem}/{step.id} depends on unknown {dep!r}"
        if step.condition_config:
            branch = step.condition_config.then_steps + step.condition_config.else_steps
            assert branch, f"{stem}/{step.id} is a condition with no branches"
            for sid in branch:
                assert sid in ids, f"{stem}/{step.id} branches to unknown {sid!r}"
        if step.race_config:
            for branch_steps in step.race_config.branches:
                for sid in branch_steps:
                    assert sid in ids, f"{stem}/{step.id} races unknown {sid!r}"
        if step.accept_config:
            for target in step.accept_config.targets:
                assert target in ids, f"{stem}/{step.id} targets unknown {target!r}"


@pytest.mark.parametrize("stem", PACK)
def test_code_steps_are_sandbox_clean(stem: str) -> None:
    """Every ``code`` step must survive the executor blocklist and parse as Python."""
    for step in _wf(stem).steps:
        if step.type == "code" and step.code_config and step.code_config.code:
            code = step.code_config.code
            match = _CODE_STEP_BLOCKED_PATTERNS.search(code)
            assert match is None, f"{stem}/{step.id} sandbox-blocked {match.group(0)!r}"
            ast.parse(code)


@pytest.mark.parametrize("stem", PACK)
def test_passes_the_hub_security_scanner(stem: str) -> None:
    """``.github/workflows/hub-validate.yml`` scans every template in this directory."""
    result = scan_template(_load(stem))
    assert result.errors == [], f"{stem} scanner errors: {[e.message for e in result.errors]}"


@pytest.mark.parametrize("stem", PACK)
def test_discovered_in_catalog_with_expected_category(stem: str) -> None:
    info = {t.file_name: t for t in list_templates()}.get(f"{stem}.yaml")
    assert info is not None, f"{stem} not discovered by list_templates()"
    assert info.category == KNOWLEDGE_PACK[stem], f"{stem} category drifted"
    assert info.step_count > 0
    assert info.tags, f"{stem} has no tags in its header"


@pytest.mark.parametrize("stem", PACK)
def test_header_metadata_is_complete(stem: str) -> None:
    text = _load(stem)
    for key in ("name", "description", "tags", "category"):
        assert _header_field(text, key), f"{stem} header missing '{key}'"


# ---------------------------------------------------------------------------
# The pack's thesis
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stem", PACK)
def test_every_template_has_an_accept_gate(stem: str) -> None:
    """The reason this pack exists: knowledge work needs an outcome gate."""
    accepts = [s for s in _wf(stem).steps if s.type == "accept"]
    assert accepts, f"{stem} has no accept step - it does not belong in this pack"


@pytest.mark.parametrize("stem", PACK)
def test_accept_gates_pay_for_judges_only_after_free_checks(stem: str) -> None:
    """Checks are deterministic and free and run first; judges are the paid half.

    A gate with judges but no checks throws away the $0 rejection path.
    """
    for step in _wf(stem).steps:
        if step.type != "accept":
            continue
        cfg = step.accept_config
        assert cfg.checks, f"{stem}/{step.id} has judges but no free checks"
        assert cfg.judges, f"{stem}/{step.id} has no judges"
        assert len(cfg.judges) >= 2, f"{stem}/{step.id} is a panel of one"


@pytest.mark.parametrize("stem", PACK)
def test_accept_panels_are_unanimous_with_distinct_judges(stem: str) -> None:
    """quorum 0 resolves to unanimous, and each judge must have its own job."""
    for step in _wf(stem).steps:
        if step.type != "accept":
            continue
        cfg = step.accept_config
        assert cfg.quorum == 0, f"{stem}/{step.id} does not use the unanimous default"
        names = [j.name for j in cfg.judges]
        assert len(names) == len(set(names)), f"{stem}/{step.id} has duplicate judge names"
        rubrics = [j.rubric.strip() for j in cfg.judges]
        assert all(rubrics), f"{stem}/{step.id} has an empty rubric"
        assert len(set(rubrics)) == len(rubrics), (
            f"{stem}/{step.id} gives two judges the same rubric - that is a retry, not a panel"
        )


@pytest.mark.parametrize("stem", PACK)
def test_accept_gates_declare_a_local_cost_ceiling(stem: str) -> None:
    """A bundled template cannot assume the user set a run budget."""
    for step in _wf(stem).steps:
        if step.type != "accept":
            continue
        assert step.accept_config.max_cost_usd > 0, (
            f"{stem}/{step.id} has no max_cost_usd, so only the run budget bounds it"
        )


@pytest.mark.parametrize("stem", PACK)
def test_rework_loops_are_bounded(stem: str) -> None:
    for step in _wf(stem).steps:
        if step.type != "accept":
            continue
        cfg = step.accept_config
        if cfg.on_reject == "retry_target":
            assert 1 < cfg.max_rounds <= 3, (
                f"{stem}/{step.id} retries with max_rounds={cfg.max_rounds}"
            )


@pytest.mark.parametrize("stem", PACK)
def test_every_notify_is_a_dry_run(stem: str) -> None:
    """0.44 made notify LIVE by default. A bundled template must be safe on install day."""
    notifies = [s for s in _wf(stem).steps if s.type == "notify"]
    assert notifies, f"{stem} has no notify step"
    for step in notifies:
        assert step.notify_config.dry_run is True, (
            f"{stem}/{step.id} would send for real on install"
        )


def test_sensitive_domains_gate_outbound_action_on_a_human() -> None:
    """Recruiting and healthcare must not act without a named human who can time out."""
    for stem in ("recruiting_screen_evidence_gate", "patient_intake_summarizer"):
        approvals = [s for s in _wf(stem).steps if s.type == "approval"]
        assert approvals, f"{stem} has no human approval step"
        for step in approvals:
            assert step.approval_config.on_timeout == "abort", (
                f"{stem}/{step.id} lets silence become consent"
            )


def test_guard_and_filter_gates_are_both_represented_and_deliberate() -> None:
    """fail_on_reject is a per-gate decision, not a default. Both choices appear here.

    Guard: the artefact is a single decision a human will act on, so a rejection
    must stop the run. Filter: the artefact is a batch or an advisory input and
    the human can see the verdict.
    """
    expected = {
        "contract_review_clause_extractor": True,
        "recruiting_screen_evidence_gate": True,
        "marketing_localization_qa": True,
        "patient_intake_summarizer": True,
        "crm_lead_enrichment_quality_gate": False,
        "sales_call_brief": False,
    }
    actual = {}
    for stem in PACK:
        gates = [s for s in _wf(stem).steps if s.type == "accept"]
        actual[stem] = gates[0].accept_config.fail_on_reject
    assert actual == expected
    assert True in actual.values() and False in actual.values()


def test_filter_gates_publish_the_verdict_downstream() -> None:
    """fail_on_reject: false is only honest if something downstream reads the verdict.

    A filter that nobody consumes is just a silently ignored gate. The verdict
    may be consumed either as a template reference (``{steps.gate.output.decision}``)
    or inside a ``code`` step (``_steps.get('gate')``); both count.
    """
    for stem in ("crm_lead_enrichment_quality_gate", "sales_call_brief"):
        wf = _wf(stem)
        gate = next(s for s in wf.steps if s.type == "accept")
        text = _load(stem)

        via_template = f"steps.{gate.id}.output.decision" in text
        via_code = f"_steps.get('{gate.id}')" in text and "'decision'" in text
        assert via_template or via_code, (
            f"{stem} filters on a verdict nothing downstream reads"
        )

        # And the decision must reach a human-visible surface, not just a variable.
        consumers = [
            s
            for s in wf.steps
            if gate.id in s.depends_on and s.type in ("condition", "code", "notify")
        ]
        assert consumers, f"{stem} has no step that acts on the advisory verdict"


# ---------------------------------------------------------------------------
# ACP ships disabled, so it may never sit on a bundled template's happy path
# ---------------------------------------------------------------------------


def test_acp_is_reachable_only_behind_a_condition_that_defaults_off() -> None:
    """``acp_allowed_roots`` defaults to empty, so an unguarded acp step is broken
    for every default user. The one acp step in this pack sits in the ``then``
    branch of a condition whose input flag defaults to "false", and the ``else``
    branch is a plain llm step that runs on a stock install."""
    wf = _wf("marketing_localization_qa")
    acp_steps = [s for s in wf.steps if s.type == "acp"]
    assert len(acp_steps) == 1
    acp = acp_steps[0]

    # It is never a dependency of anything on the default path, and it is never
    # scheduled except through a condition branch.
    guards = [
        s
        for s in wf.steps
        if s.condition_config and acp.id in s.condition_config.then_steps
    ]
    assert len(guards) == 1, "the acp step is not guarded by exactly one condition"
    guard = guards[0]
    assert guard.condition_config.else_steps, "the guard has no working default branch"

    fallback_id = guard.condition_config.else_steps[0]
    fallback = next(s for s in wf.steps if s.id == fallback_id)
    assert fallback.type == "llm", "the default branch is not a plain model step"

    # The input flag that selects ACP must default to off.
    flag = wf.input_schema["properties"]["use_local_agent"]
    assert flag["default"] == "false"
    assert "use_local_agent" not in (wf.input_schema.get("required") or [])

    # And the step itself keeps the closed capabilities.
    assert acp.acp_config.filesystem == "read"
    assert acp.acp_config.terminal is False
    assert acp.acp_config.permission == "reject"
    assert acp.acp_config.elicitation == "decline"


# ---------------------------------------------------------------------------
# The flagship's accept gate, executed for real with only the judge HTTP mocked
# ---------------------------------------------------------------------------

# An extraction shaped to satisfy every deterministic check on the flagship's
# accept step, so the judges are actually reached.
GOOD_EXTRACTION = """## Key terms

| Term | Value | Clause |
| --- | --- | --- |
| Parties | Acme Ltd and Northwind Logistics GmbH | [Clause 1.1] |
| Term length | 24 months | [Clause 3.1] |
| Liability cap | 12 months' fees | [Clause 9.2] |
| Governing law | England and Wales | [Clause 18.1] |

## Obligations

1. Supplier shall deliver the monthly service report by the fifth business day [Clause 7.3].
2. Customer shall pay each invoice within 30 days of receipt [Clause 5.2].
3. Supplier shall maintain insurance of not less than EUR 5,000,000 [Clause 11.4].

## Deviations from playbook

Governing law is England and Wales as expected [Clause 18.1]. The liability cap
at [Clause 9.2] is expressed in months of fees rather than a fixed sum.

## Gaps

No force majeure clause is present. NOT PRESENT: data protection addendum.
"""

BAD_EXTRACTION = "## Key terms\n\nNothing found.\n"


def _storage():
    return LocalStorage(base_dir=tempfile.mkdtemp())


def _flagship_accept_step():
    wf = _wf(FLAGSHIP)
    return next(s for s in wf.steps if s.id == "accept_extraction"), wf


def _ctx(extraction: str = GOOD_EXTRACTION) -> RunContext:
    ctx = RunContext(
        run_id="00000000-0000-0000-0000-000000000047",
        workflow_name="contract-review-clause-extractor",
        input={
            "contract_file": "@upload:contract.pdf",
            "counterparty": "Northwind Logistics GmbH",
            "contract_type": "MSA",
            "governing_law_expected": "England and Wales",
            "citation_threshold": 0.8,
        },
    )
    index = {
        "clause_ids": ["1.1", "3.1", "5.2", "7.3", "9.2", "11.4", "18.1"],
        "headings_missing": ["force majeure"],
        "parse_looks_empty": False,
    }
    ctx.step_outputs["index_document"] = index
    ctx.step_results["index_document"] = StepResult(
        step_id="index_document", output=index, cost_usd=0.0
    )
    ctx.step_outputs["extract_terms"] = extraction
    ctx.step_results["extract_terms"] = StepResult(
        step_id="extract_terms", output=extraction, cost_usd=0.02
    )
    return ctx


def _judge_http(replies: list[str]):
    """A fake ``httpx.AsyncClient`` serving Anthropic-shaped judge replies.

    Patching at this level keeps ``_run_accept_judge`` real, so the test
    exercises the actual verdict parser and the actual token-based cost
    accounting rather than a hand-made verdict dict.
    """
    served: list[str] = []

    class _Resp:
        def __init__(self, text: str) -> None:
            self._text = text

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "content": [{"text": self._text}],
                "usage": {"input_tokens": 2400, "output_tokens": 120},
            }

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            text = replies[len(served)] if len(served) < len(replies) else replies[-1]
            served.append(text)
            return _Resp(text)

    return _Client, served


def _rerun_spy(outputs: list[str]):
    calls: list[dict] = []

    async def _fake(step, context, sandbox, storage, **kwargs):
        calls.append({"step_id": step.id, "overrides": kwargs.get("step_overrides")})
        return StepResult(step_id=step.id, output=outputs.pop(0), cost_usd=0.02)

    return _fake, calls


class TestContractReviewAcceptGate:
    """The flagship's gate, run through the real executor."""

    @pytest.mark.asyncio
    async def test_approve_path(self) -> None:
        step, wf = _flagship_accept_step()
        client, served = _judge_http(["APPROVE\nCoverage is complete and every obligation cites a real clause."])
        with patch("httpx.AsyncClient", client):
            result = await _execute_accept_step(step, _ctx(), None, _storage(), wf, 0)

        assert result.status == "completed"
        pack = result.output
        assert pack["decision"] == "approved"
        assert pack["rounds_used"] == 1
        assert pack["targets"] == ["extract_terms"]
        assert pack["rejected_by"] is None

        # Both judges ran, on their own models, and were billed.
        judges = pack["rounds"][-1]["judges"]
        assert [j["name"] for j in judges] == ["terms_coverage", "no_invented_terms"]
        assert {j["model"] for j in judges} == {"sonnet", "haiku"}
        assert all(j["verdict"] == "approved" for j in judges)
        assert pack["cost_usd"] > 0.0
        assert len(served) == 2

        # The free half really ran first, and really was free.
        assert pack["rounds"][-1]["checks_passed"] is True
        assert len(pack["rounds"][-1]["checks"]) == len(step.accept_config.checks)
        assert pack["rounds"][-1]["quorum"] == {"required": 2, "approved": 2, "total": 2}

    @pytest.mark.asyncio
    async def test_reject_then_retry_with_critique_then_approve(self) -> None:
        """Round 1 rejects, the extractor is re-run with the critique, round 2 approves."""
        step, wf = _flagship_accept_step()
        client, served = _judge_http(
            [
                "REJECT\nObligation 3 cites Clause 11.4 which is not in the clause list.",
                "REJECT\nThe insurance figure does not appear in the document.",
                "APPROVE\nEvery obligation now cites a clause that exists.",
                "APPROVE\nNothing is asserted that is not on the page.",
            ]
        )
        rerun, calls = _rerun_spy([GOOD_EXTRACTION])
        with patch("httpx.AsyncClient", client), patch(
            "sandcastle.engine.executor.execute_step_with_retry", rerun
        ):
            result = await _execute_accept_step(step, _ctx(), None, _storage(), wf, 0)

        assert result.status == "completed"
        assert result.output["decision"] == "approved"
        assert result.output["rounds_used"] == 2

        # The re-work targeted the extractor, once, between the two rounds.
        assert len(calls) == 1
        assert calls[0]["step_id"] == "extract_terms"
        revision = calls[0]["overrides"]["prompt"]
        assert "REVISION REQUEST" in revision
        assert "Clause 11.4" in revision, "the judges' critique was not fed back"

        assert result.output["rounds"][0]["decision"] == "rejected"
        assert result.output["rounds"][1]["decision"] == "approved"
        assert len(served) == 4

    @pytest.mark.asyncio
    async def test_panel_that_never_accepts_stops_the_run(self) -> None:
        """fail_on_reject is left at true on this gate: a rejection is a GUARD.

        Three rounds, two re-works, then the step fails with retryable=False so a
        step-level retry cannot re-roll the verdict.
        """
        step, wf = _flagship_accept_step()
        client, _ = _judge_http(["REJECT\nStill citing clauses that do not exist."])
        rerun, calls = _rerun_spy([GOOD_EXTRACTION] * 5)
        with patch("httpx.AsyncClient", client), patch(
            "sandcastle.engine.executor.execute_step_with_retry", rerun
        ):
            result = await _execute_accept_step(step, _ctx(), None, _storage(), wf, 0)

        assert result.status == "failed"
        assert result.retryable is False
        assert "Accept rejected" in (result.error or "")
        assert result.output["decision"] == "rejected"
        assert result.output["rounds_used"] == 3  # max_rounds on this gate
        assert len(calls) == 2  # re-work happens between rounds, never after the last

    @pytest.mark.asyncio
    async def test_broken_extraction_is_rejected_for_zero_dollars(self) -> None:
        """The deterministic checks reject before a judge is ever paid."""
        step, wf = _flagship_accept_step()
        judge = AsyncMock()
        with patch("sandcastle.engine.executor._run_accept_judge", judge):
            result = await _execute_accept_step(
                step, _ctx(BAD_EXTRACTION), None, _storage(), wf, 0
            )

        assert result.status == "failed"
        assert result.cost_usd == 0.0
        assert result.output["rejected_by"] == "checks"
        assert result.output["rounds"][-1]["judges"] == []
        judge.assert_not_awaited()

        failed = [c for c in result.output["rounds"][-1]["checks"] if not c["passed"]]
        assert failed, "no check actually failed on a deliberately broken extraction"
