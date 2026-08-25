# 0.47 — The knowledge-work template pack

Status: implemented in this branch. Workstream 3 of 0.47.

## Why this pack exists

Sandcastle ships 262 bundled workflow templates. Sorted by what they actually
automate, they are overwhelmingly devops, SRE, data-pipeline and RPA work:
alert-to-fix loops, warehouse quality sentinels, reconciliation closers,
release trains. That is a faithful picture of where workflow engines came from
and a poor picture of where agent adoption is going.

The a16z/gdb figures on fastest-growing agent usage put the growth outside
code entirely — legal 108x, sales 41x, recruiting 41x, marketing 26x. None of
those are pipeline-shaped problems. They are *judgement-shaped* problems: a
human currently reads a document, forms an opinion, and is accountable for it.
Automating that is not "call a model and post the answer"; it is "call a model
and then prove the answer was good enough to act on, and stop if it was not."

That is exactly what 0.45's `type: accept` was built for, and until now no
bundled template used it. Same for 0.46's `type: acp`. The two newest step
types in the engine had zero representation in the catalogue that users
actually browse.

So this pack has two jobs, and they are the same job:

1. Put real knowledge work in the bundled catalogue — legal, recruiting,
   sales, marketing, healthcare.
2. Make `accept` the spine of those templates, because knowledge work is
   precisely the domain where "the model produced output" and "the output is
   safe to act on" come apart.

## The templates

Six new templates. All live in `src/sandcastle/templates/`.

| File | Domain | Job it does | Step types exercised |
|---|---|---|---|
| `contract_review_clause_extractor.yaml` | Legal | Extract key terms and obligations from a contract, prove the extraction against a rubric, escalate the uncertain ones | `parse` `llm` `code` **`accept`** (checks + 2 judges + quorum + `retry_target`) `condition` `approval` `report` `notify` |
| `recruiting_screen_evidence_gate.yaml` | Recruiting | Screen a CV against a role spec with every claim quoted from the CV, bias-guarded, human approval before any rejection goes out | `parse` `code` `llm` **`accept`** (evidence-traceability + bias judges) `condition` `approval` `notify` (dry-run) |
| `crm_lead_enrichment_quality_gate.yaml` | Sales ops | Enrich a lead list from the web, validate deterministically, gate on data quality, write back to the CRM behind a dry run | `code` `http` `race` `llm` **`accept`** (schema + quality judges, `fail_on_reject: false`) `condition` `http` `notify` |
| `sales_call_brief.yaml` | Sales | Build a pre-call brief from CRM + news + prior-call memory, accept-gated on "no invented facts about the account" | `http` `race` `llm` **`accept`** `code` `notify` + workflow **`memory`** (graded, `admit_threshold`) |
| `marketing_localization_qa.yaml` | Marketing | QA a localized campaign against the source copy: terminology, claims, legal disclaimers, locale formatting | `code` `llm` **`accept`** `condition` + **`acp`** (guarded branch, off by default) `approval` `notify` |
| `patient_intake_summarizer.yaml` | Healthcare | Turn an intake questionnaire into a clinician-facing summary that never invents a symptom, with PHI minimisation and a mandatory clinician sign-off | `code` `llm` **`accept`** (hallucination guard) `gate` `approval` `notify` (dry-run) |

### Why these six, and what got cut

The brief allowed a pick of 2–4 beyond the three mandated ones. I took three:

- **Sales-call brief** — chosen because it is the only natural home for
  workflow-level `memory` with a graded `admit_threshold`. A pre-call brief is
  the canonical "what do we already know about this account" problem, and
  0.47's whole thesis is that memory needs grading. It also gives the pack a
  template where `accept` guards *groundedness* rather than *format*.
- **Marketing localization QA** — chosen as the ACP host. Localization QA is
  the one knowledge-work job in this list that genuinely wants a local agent
  with filesystem access (a locale file tree in a repo), so the ACP branch is
  motivated rather than decorative.
- **Healthcare intake summarizer** — chosen because it forces the pack to
  demonstrate the *most* conservative defaults: PHI minimisation in code
  before any model sees the record, a hallucination-guard accept step, and two
  separate human stops.

**Invoice dispute triage was cut.** The catalogue already ships
`reg_e_dispute_provisional_credit_engine`, `ar_collections_dunning_prioritizer`
and `three_way_match_pay_run`. A seventh template that reconciles a disputed
invoice would have added a fourth finance-ops variant and exercised nothing
the other six do not. Six templates that each demonstrate something distinct
beat seven where one is filler.

## Design decisions

Everything below is a decision I made in the absence of a prior design doc.

### 1. `accept` is the spine, `gate` stays for permission

The repo already has `gate`, and the two are easy to confuse. The split I held
to across all six templates:

- `gate` answers **"may this run proceed"** — a policy question, often with a
  human strategy attached. Used in `patient_intake_summarizer` for the PHI
  handling attestation.
- `accept` answers **"did this work succeed"** — an outcome question, answered
  with an evidence pack.
- `approval` answers **"will a named human take responsibility"** — used
  wherever something leaves the building.

Where a template needed both, `accept` runs first. There is no point asking a
human to approve output that has not yet been shown to be correct.

### 2. Checks before judges, always

Every `accept` step in this pack puts deterministic `checks:` before `judges:`.
This is not decoration — it is the cost argument. A contract extraction that
came back empty, or that lost its `## Obligations` heading, is rejected for
$0 before either judge is billed. The check types are constrained by the
engine to `contains`, `not_contains`, `not_empty`, `equals`, `regex_match`,
`schema_match`, `max_cost`, `max_duration`; `llm_judge` is banned in `checks:`
by `BANNED_ACCEPT_CHECK_TYPES` precisely so paid judging cannot hide there.

I used `schema_match` in the CRM template because enrichment output is
genuinely a schema problem, and `regex_match` in the recruiting template to
assert that the screen output contains quoted CV evidence (`"..."` spans)
rather than paraphrase.

### 3. `fail_on_reject` — guard vs filter, decided per gate

0.45 made rejection stop the run unless `fail_on_reject: false`. I treated
that as a real decision each time rather than a default, and commented the
choice in every template:

| Template | Gate | Setting | Why |
|---|---|---|---|
| Contract review | `accept_extraction` | **guard** (default `true`) | A contract extraction that failed its rubric must not reach a lawyer's report presented as reviewed. Stopping is the correct outcome; the retry loop already had three chances. |
| Recruiting screen | `accept_screen` | **guard** (default `true`) | An unevidenced screen must never reach the approval step. If the model cannot quote the CV, there is nothing for a recruiter to approve. |
| CRM enrich | `accept_data_quality` | **filter** (`fail_on_reject: false`) | This is a batch of leads, not one decision. A low-quality batch should be *routed to manual review*, not abort the run and lose the good rows. The `condition` step immediately downstream branches on `decision`. |
| Sales-call brief | `accept_brief` | **filter** (`fail_on_reject: false`) | A rep with a flagged, caveated brief 20 minutes before a call is better off than a rep with no brief and a failed run. The notify carries the verdict. |
| Localization QA | `accept_localization` | **guard** (default `true`) | Shipping a mistranslated legal disclaimer is the failure this template exists to prevent. |
| Patient intake | `accept_summary` | **guard** (default `true`) | A summary that may contain an invented symptom must not reach a clinician at all. |

The pattern: **guard when the artefact is a single decision a human will act
on; filter when the artefact is a batch or an advisory input and the human
can see the verdict.** Four guards, two filters.

### 4. Two-judge quorum means unanimous, and that is deliberate

`quorum: 0` resolves to unanimous against `len(judges)` at runtime, so adding
a third judge *tightens* the panel instead of quietly loosening it. Every
template in this pack leaves `quorum: 0` with two judges — both must approve.
The two judges are always given **different jobs and different models**, not
two shots at the same question:

- Contract review: `terms_coverage` (sonnet — did we find the key terms?) and
  `no_invented_terms` (haiku — is every obligation traceable to a clause?).
- Recruiting: `evidence_traceability` (sonnet) and `bias_guard` (sonnet — is
  any judgement resting on a protected characteristic or a proxy for one?).
- Healthcare: `clinical_faithfulness` (sonnet) and `no_added_findings`
  (haiku).

Giving one judge the "did we get it right" job and the other the "did we make
something up" job is the whole point. Two judges asked the same question are a
retry, not a panel.

### 5. Retry with critique: `on_reject: retry_target`, `max_rounds: 3`

The contract-review flagship uses the engine's own bounded re-work loop rather
than hand-rolling a condition/loop pair. `on_reject: retry_target` sends the
extraction back to the extractor with the judges' critique appended, at most
three rounds, and re-judges. It is bounded four ways — `max_rounds`, the
step's own `max_cost_usd`, the run budget, and the depth guard — and the
critique is escaped before re-injection so a judge cannot smuggle a template
variable into the next prompt.

`max_cost_usd` is set explicitly on every accept step in the pack (0.30–0.75
depending on judge count and document size). An accept step with no local cap
inherits only the run budget, which in a bundled template a user may not have
set at all.

### 6. Human escalation is a separate step, not `on_reject: escalate_to_human`

`escalate_to_human` exists, but it raises through the ApprovalRequest surface
*inside* the accept step, which conflates "the panel could not decide" with
"a named human owns this outcome". For the contract review's low-confidence
path I used an explicit `condition` → `approval` pair instead, so the
low-confidence clauses are shown to the reviewer as data (`show_data`) rather
than buried in an escalation message. The reviewer sees exactly which clauses
scored low and why.

### 7. ACP is a guarded branch, never the happy path

`type: acp` ships disabled: `settings.acp_allowed_roots` defaults to an empty
list, so every `cwd` fails the allowed-roots check at runtime. A template
whose happy path required ACP would fail for 100% of default users.

`marketing_localization_qa.yaml` therefore puts the ACP step behind a
`condition` on an input flag that **defaults to `"false"`**:

```yaml
- id: choose_qa_engine
  type: condition
  condition_config:
    expression: "'{input.use_local_agent}' == 'true'"
    then: [qa_with_local_agent]   # type: acp — needs acp_allowed_roots
    else: [qa_with_model]         # the default, works out of the box
```

Both branch heads feed the accept step, so the workflow validates, builds a
complete plan, and runs end to end on a stock install without ACP ever being
spawned. The ACP step itself is configured with the closed defaults the engine
prefers: `filesystem: read`, `permission: reject`, `terminal: false`, no
inherited environment. The template comments say plainly that the branch
requires `acp_allowed_roots` to be set and what it grants.

I considered a fully commented-out ACP block (also sanctioned) and rejected it:
a commented block is not validated, not planned, and not tested, so it rots.
A guarded branch is real YAML the engine checks on every run.

### 8. `dry_run: true` on every notify. No exceptions.

0.44 made `notify` live by default and shipped templates that then sent real
messages. Every `notify` step in these six templates carries
`dry_run: true`. In the recruiting and healthcare templates this is
load-bearing rather than hygienic: the notify steps are candidate-facing and
patient-facing, and the template's job is to model the safe default. The
comment above each says so, and says which line to flip.

### 9. Sensitive domains model their own safe defaults

Recruiting and healthcare get treatment the other four do not:

- **No outbound communication without a human.** In
  `recruiting_screen_evidence_gate.yaml` *both* dispositions are gated behind an
  `approval` step with `on_timeout: abort`. A screen that times out does
  nothing at all. The rejection path is gated for the obvious reason; the
  advance path is gated too, because a false advance costs an interviewer a day
  and the candidate a wasted process. I considered `on_timeout: skip` on the
  advance path so an unattended run would auto-advance, and rejected it: `skip`
  skips the approval and lets the downstream steps run, which turns silence
  into consent. Both paths abort, and the template says so.
- **No candidate-facing step exists.** The workflow's only `notify` targets the
  internal recruiting channel and is a dry run. Communicating with the
  candidate is deliberately left outside the workflow, downstream of the human
  who read the approval.
- **Bias guard is in the rubric, not in a comment.** The `bias_guard` judge
  rejects the screen if any stated reason rests on name, age, gender,
  nationality, university prestige, employment-gap length, or a photograph —
  and the deterministic pre-step strips those fields from the CV text before
  the screener model ever sees them, so the judge is a backstop, not the only
  defence.
- **PHI minimisation happens in `code`, before any model call.** The
  healthcare template's first step redacts direct identifiers deterministically
  and passes only a de-identified record forward. The identifiers are
  reattached, in code, only in the final clinician-facing artefact.

### 10. Model choices

`default_model: sonnet` throughout, matching the house convention. Judges are
mixed on purpose: the "did we get it right" judge runs sonnet, the "did we
invent something" judge usually runs haiku, because detecting an unsupported
claim against a source text is a cheaper task than assessing substantive
coverage. Classifiers and routers run haiku. This keeps a two-judge panel on a
one-page document under about $0.02 and makes the `max_cost_usd` ceilings
meaningful rather than theatrical.

## Registry and counts — what I found and what I did

This is the part the brief flagged, and the finding is not the expected one.

**`scripts/build_registry.py` has no discovery path for new built-in
templates.** `_build_registry()` does not walk `src/sandcastle/templates/`. It
takes the existing entries in `hub/registry.json`, drops any whose
`download_url` no longer resolves to a file, refreshes the content-derived
fields (`sha256`, `step_count`, `description`, `models_used`, timestamps) of
the survivors, and appends community entries built from
`hub/community/**/*.yaml` plus `hub/community/seed.json`. Community workflows
are discovered from the filesystem. **Built-ins are not.**

The consequence is measurable today, before this pack:

- `src/sandcastle/templates/` contained **262** YAML files (268 after this pack).
- `hub/registry.json` contains **248** built-in entries.
- Those 248 entries point at only **185 distinct files** — 63 files carry *two*
  entries each, a persona-authored one and a `gizmax/*` one (185 + 63 = 248).
- So **77** shipped built-in templates had no registry entry at all, including
  `three_way_match_pay_run`, `closed_loop_autoremediator`,
  `quote_to_cash_orchestrator` and `ugc_studio` — templates the site's own
  `llms.txt` names as examples. With this pack it is 83.

The last commit to grow the built-in half of the registry predates 0.41. Every
built-in added since — several full waves — went uncatalogued.

Two related pre-existing breakages, both verified on a clean checkout of
`121e922` **before** this pack was written:

- `python scripts/build_registry.py --check` **fails** (exit 1, "hub/registry.json
  is stale"). It is not reproducible across machines: `created_at`, `updated_at`,
  `generated_at` and `stats.last_updated` are derived from file **mtime**, which
  is checkout time in any fresh clone or worktree. This is why CI runs only
  `update_hub_registry.py --check` and never `build_registry.py --check`.
- The hub-validate checksum gate reports **42 errors** — 21 templates whose
  `sha256` is stale, counted once in each of the two registries. None of them
  are touched by this pack.

### What I did about it

**Nothing to the numbers, deliberately.** Specifically:

- Added the six templates to `src/sandcastle/templates/`.
- Ran `scripts/build_registry.py` and `scripts/update_hub_registry.py`, then
  both with `--check`. Both are green. Both are no-ops for these templates,
  exactly as the code above predicts.
- **Did not** hand-edit `hub/registry.json` or `site/hub/registry.json`.
- **Did not** change `283` or `248` anywhere on the site, in `llms.txt`,
  `llms-full.txt`, or `README.md`.
- Made exactly **one** documentation edit, and it is unrelated to this pack:
  `README.md:1699` claimed the Community Hub "lists 181 templates, including 8
  community-submitted templates". The registry has held 283 and 35 since well
  before this branch, so that sentence was simply false. Corrected to 283/35.
  No count that describes the new templates changed.

The reasoning: those seven public claims are anchored to the hub registry
(`283 = 248 built-in + 35 community`, and the hub page renders exactly those
283 entries). The registry did not change, so the claims are still true of the
thing they describe. Bumping them to 289/254 would have made the site advertise
templates the hub cannot serve — the precise failure the numbers exist to
prevent — and `test_web_hub_registry_categories_are_derived_not_drifted`
would not have caught it, because that test derives its expectations from the
registry itself.

Adding real entries would have required inventing `author`, `rating`,
`review_count` and `downloads` values, which `hub/schema/template.schema.json`
marks required and which existing built-in entries carry as plausible-looking
fabrications (`"rating": 3.8, "review_count": 10` on a template with no users).
I am not adding six more.

### The follow-up this needs

Give `build_registry.py` a curated intake for built-ins, mirroring the
community half it already has:

1. Add `src/sandcastle/templates/seed.json` (or `hub/builtin-seed.json`)
   keyed by template filename, carrying only the metadata YAML cannot express:
   `slug`, `category`, `tags`, `license`, and honest zeros for
   `rating`/`review_count`/`downloads`.
2. Have `_build_registry()` discover `src/sandcastle/templates/*.yaml` and
   require a manifest entry for each, the same way
   `_validate_community_source_inputs` requires one for each community
   workflow. A new template with no manifest entry then fails CI loudly
   instead of being silently absent.
3. Backfill the 76 orphans, drop the fabricated ratings on the existing 248,
   and update the seven site claims **once**, from the regenerated number.

That is a coherent piece of work with a blast radius across the whole
catalogue and the entire public site. It is not a rider on a template pack,
and doing half of it — six new entries alongside 76 orphans — would leave the
catalogue in a worse state than either doing all of it or none of it.

### Category impact

The six templates carry these `# category:` headers:

| Template | Category |
|---|---|
| `contract_review_clause_extractor` | `hr_legal` |
| `recruiting_screen_evidence_gate` | `hr_legal` |
| `crm_lead_enrichment_quality_gate` | `sales_crm` |
| `sales_call_brief` | `sales_crm` |
| `marketing_localization_qa` | `marketing` |
| `patient_intake_summarizer` | `healthcare` |

All six are existing categories with existing `CATEGORY_LABELS` entries, so no
new category is introduced. Because the registry does not ingest them, the
public category bar is unchanged and
`test_template_hub_categories_v034.py::test_web_hub_registry_categories_are_derived_not_drifted`
stays green. They *are* discovered by `sandcastle.templates.list_templates()`,
so they appear in the product's own template browser immediately.

## Tests

`tests/test_knowledge_pack_047.py`:

- Structural suite over all six, mirroring `test_model_neutral_templates_v033.py`:
  parses, `validate()` returns `[]`, `build_plan()` covers every step exactly
  once, `depends_on` and branch targets resolve, step types are valid, `code`
  steps pass `_CODE_STEP_BLOCKED_PATTERNS` and `ast.parse`.
- Pack-thesis assertions: every template has at least one `accept` step; every
  accept step has both `checks` and `judges`; every accept step sets an
  explicit `max_cost_usd`; every `notify` step sets `dry_run: true`; the
  category header matches the table above.
- End-to-end accept execution for the contract-review flagship, through the
  real `_execute_accept_step` with `httpx.AsyncClient` patched so
  `_run_accept_judge` does real verdict parsing and real cost accounting:
  - **approve path** — both judges return `APPROVE`, decision is `approved`,
    `rounds_used == 1`, cost is non-zero and recorded.
  - **reject → retry → approve path** — round 1 rejects, the target is
    re-executed with the critique, round 2 approves, `rounds_used == 2`.
  - **reject exhausts rounds** — the step fails, so the run stops, proving the
    guard choice in decision 3 is real.
- `hub_scanner.scan_template()` returns no errors for all six, which is what
  `.github/workflows/hub-validate.yml` enforces in CI over the whole templates
  directory.

Result: **92 tests, all passing.** The hub and category suites
(`test_hub.py`, `test_community_hub_seed.py`,
`test_template_hub_categories_v034.py`, `test_template_hub_nextwave.py`,
`test_template_hub_automation_rpa.py`, `test_model_neutral_templates_v033.py`)
were re-run alongside them: **323 passing**, unchanged.

### One category to avoid

`tests/test_template_hub_automation_rpa.py::test_web_hub_registry_surfaces_automation_rpa`
asserts `registry_bar["automation_rpa"] >= len(files on disk with that category)`,
and it currently sits at **exact parity, 37 = 37**. Adding a single
`# category: automation_rpa` template to `src/sandcastle/templates/` without
also adding a registry entry breaks it immediately. None of the six templates
here use that category, and that is not a coincidence — it is the one hard
coupling between the directory and the registry in the whole test suite. Any
future template pack should either avoid `automation_rpa` or fix the registry
intake first.

## Incidental findings

Two things found while building this, neither fixed here because both sit
outside a template pack's blast radius.

**1. `workflows/acp/acp-refactor-and-review.yaml` has an inert safety guard.**
Its condition step is written as:

```yaml
condition_config:
  expression: "{steps.preflight.output.clean} == True"
  then_steps: [plan, implement, diffstat, review, notify]
  else_steps: []
```

but the parser reads `data.get("then", [])` and `data.get("else", [])`
(`dag.py:1313-1314`). `then_steps:` / `else_steps:` are the *dataclass* field
names, not the YAML keys. So both branches parse as empty, nothing is added to
`branch_skip_steps`, and every downstream step runs regardless of whether the
working tree was clean — which is the exact case the preflight guard exists to
stop. The fix is a two-line rename to `then:` / `else:`. All six templates in
this pack use `then:` / `else:`, matching `three_way_match_pay_run.yaml` and
every other bundled template.

**2. The code-step blocklist is a substring scan, and it matches inside
comments and regex literals.** `_CODE_STEP_BLOCKED_PATTERNS` includes
`ord\s*\(` with no word boundary and `re.IGNORECASE`, so an entirely innocent
regex alternation like `medical record(?: number)?` is rejected as a
character-code builtin — the letters `ord` followed by `(` are enough. So is a
*comment* explaining the problem. `patient_intake_summarizer.yaml` works around
it by spelling the alternation out, and says why inline. Worth either adding a
word boundary to that pattern or scanning the AST rather than the raw source;
either way a template author currently gets a confusing failure with no hint
about which of the many blocked names actually matched.
