# `type: acp` — Sandcastle as an Agent Client Protocol client

**Status:** experimental, shipped in 0.45.
**Protocol version:** `1` (the integer on the wire).
**Direction:** Sandcastle is an ACP **client** only. ACP server mode is not implemented
and is not planned for 0.45 — see [Why not a server](#why-not-a-server).

An `acp` step spawns an external agent harness as a local subprocess and drives it over
newline-delimited JSON-RPC 2.0 on its stdio. Roughly 38 harnesses speak the protocol
today, including Claude Code, Codex, Gemini CLI and goose. The point is not that
Sandcastle gets a better agent; it is that the fifth, sixth and seventh bespoke agent
integrations never get written, and that this is the only one of Sandcastle's five agent
integrations that can **cancel a running turn gracefully** and **see the agent's tool
calls**.

---

## The shape of it

```yaml
- id: implement
  type: acp
  depends_on: [plan]
  acp_config:
    agent: claude                 # or command: + args:
    cwd: "{input.repo_path}"
    env_passthrough: ["ANTHROPIC_API_KEY"]
    message: |
      Implement the brief below.
      {steps.plan.output}
    filesystem: readwrite
    permission: ask
    permission_rules:
      - kind: read
        decision: allow_once
      - kind: edit
        decision: allow_once
      - kind: execute
        decision: reject_once     # no shell, even in ask mode
    timeout: 1100
    idle_timeout: 240
    output_format: full
```

A complete, validating example is committed at
[`workflows/acp/acp-refactor-and-review.yaml`](../workflows/acp/acp-refactor-and-review.yaml).

### Prerequisites

| Requirement | Why |
|---|---|
| `settings.acp_allowed_roots` non-empty (`SANDCASTLE_ACP_ALLOWED_ROOTS`) | `cwd` must resolve inside one of these. Empty means the step type is **off**. |
| An admin-trusted run | An `acp` step spawns an arbitrary local executable, the same blast radius as a `code` step, so it uses the same gate. |
| `settings.data_residency` empty | See [Data residency](#data-residency) — this fails closed. |
| The harness on `PATH` | The `agent:` shorthand expands to an `npx`/binary invocation. Sandcastle never downloads it. |

---

## Configuration reference

| Key | Type | Default | Notes |
|---|---|---|---|
| `command` | str | `""` | argv[0]. Never shell-interpreted. Required unless `agent` is set. **Not** template-resolved. |
| `args` | list[str] | `[]` | **Not** template-resolved. |
| `agent` | str | `""` | `claude` \| `codex` \| `gemini` \| `goose`. Expands to a built-in `(command, args)`; unknown values are a validation error. |
| `env` | dict | `{}` | Merged onto the minimal env. Values are template-resolved. |
| `env_passthrough` | list[str] | `[]` | Names of parent env vars to forward. Nothing is inherited otherwise. |
| `cwd` | str | `""` | **Required.** Absolute, existing, inside `acp_allowed_roots`. Template-resolved. |
| `additional_directories` | list[str] | `[]` | Same path checks. Only sent when the agent advertises the capability, else a runtime error naming it. |
| `mcp_servers` | list[dict] | `[]` | Passed through verbatim as ACP `McpServer[]`. Each entry needs `name` and either `command` (stdio) or `url`. |
| `mode` | str | `""` | Opaque `modeId` → `session/set_mode`. Mode ids are agent-defined; **no safety is derived from them**. |
| `config_options` | dict | `{}` | `{configId: valueId}` → `session/set_config_option`. |
| `message` | str | `""` | The brief. Falls back to `step.prompt`. Template-resolved, storage refs expanded. |
| `permission` | str | `"reject"` | `reject` \| `allow_once` \| `allow_always` \| `ask`. |
| `permission_rules` | list[dict] | `[]` | `{kind?, title_matches?, tool?, decision}` — ordered, first match wins. |
| `filesystem` | str | `"none"` | `none` \| `read` \| `readwrite`. Controls what we advertise **and** what we answer. |
| `terminal` | bool | `false` | 0.45: `true` is a validation error. |
| `elicitation` | str | `"decline"` | 0.45: `ask` is a validation error. |
| `timeout` | int | `900` | Whole turn, spawn included. |
| `idle_timeout` | int | `180` | Seconds with no inbound traffic before we abort. `0` disables. |
| `max_output_chars` | int | `200000` | Cap on the reassembled transcript. |
| `cost_per_call` | float | `0.0` | Billed when the harness reports no cost. See [Cost](#cost-what-sandcastle-can-and-cannot-measure). |
| `protocol_version` | int | `1` | Anything else is a validation error. |
| `strict_version` | bool | `true` | Fail fast when the agent negotiates a different version. |
| `output_format` | str | `"text"` | `text` \| `json` \| `full`. |
| `include_thoughts` | bool | `false` | Fold `agent_thought_chunk` into `output.thoughts` (`full` only). |
| `include_tool_calls` | bool | `true` | Record tool calls in `output.tool_calls` (`full` only). |

### Output

`text` — the reassembled `agent_message_chunk` transcript.
`json` — `json.loads()` of that transcript; on failure `{"raw_text": ..., "_parse_error": true}`.
`full` — `{text, stop_reason, session_id, agent, protocol_version, modes, permissions,
usage, plan, truncated}` plus `thoughts` / `tool_calls` when enabled.

### `stopReason` → step status

| `stopReason` | status | retryable |
|---|---|---|
| `end_turn` | `completed` | yes |
| `max_tokens` | `completed`, `output.truncated = true` | yes |
| `max_turn_requests` | `completed`, `output.truncated = true` | yes |
| `refusal` | `failed` | **no** — a refusal is deterministic |
| `cancelled` | `failed` | **no** |

> **`end_turn` is not success.** It means the turn ended. Always follow an `acp` step
> with deterministic verification (`type: code` running `git diff`) and, ideally, an
> independent review. The committed example does both; that pattern is mandatory, not
> decorative.

---

## Cost: what Sandcastle can and cannot measure

> When Sandcastle drives an external agent over ACP, the tokens are spent by that
> harness against that harness's own credentials. Sandcastle does not see the model
> requests, does not see per-message token counts, and cannot price them. It reports
> exactly what the harness volunteers, and reports zero when the harness volunteers
> nothing.

ACP v1 gives a client exactly one accounting signal, and it is optional:

```jsonc
{"sessionUpdate":"usage_update","used":53000,"size":200000,
 "cost":{"amount":0.045,"currency":"USD"}}
```

- `used` is **tokens currently in the context window**, not tokens consumed. It goes
  *down* after a compaction. Summing it across updates produces a number that means
  nothing, and Sandcastle never does.
- `size` is the window capacity.
- `cost` is optional and **cumulative**. Sandcastle takes the last value, never a sum.

There is no `inputTokens` and no `outputTokens` anywhere in ACP v1.

**The rules the code follows:**

1. Reported `cost` with `currency == "USD"` → `cost_usd = amount`, `usage.cost_source = "agent_reported"`.
2. Reported `cost` in another currency → **no conversion**. `cost_usd = cost_per_call`,
   the foreign amount stays visible in `output.usage`, and a warning names the currency.
   Sandcastle has no FX layer and inventing one silently corrupts every budget check.
3. No `cost` reported → `cost_usd = cost_per_call` (default `0.0`), `cost_source = "declared"`.
4. **Never estimated from `used`.** Pricing a context-occupancy number would be
   fabricating a bill.

**Consequences, stated rather than hidden:**

- **`max_cost_usd` is ADVISORY for `acp` steps.** A harness that reports no cost is
  invisible to the budget guard. `timeout` and `idle_timeout` are the real limits. Set
  `cost_per_call` if you want an `acp` step to consume budget at all.
- The pre-run cost estimator lists `acp` under its non-LLM set and returns `$0` rather
  than inventing a token estimate.

**UNVERIFIED:** whether `@agentclientprotocol/claude-agent-acp` actually emits
`usage_update` with a `cost` field has not been confirmed against a running adapter.
The schema makes it optional. If it does not, an `acp` step driving Claude Code reports
`cost_per_call` (default `$0`) and the "agent-reported cost" path never fires. Both
branches are implemented and tested against the fake agent; only the real adapter's
behaviour is unconfirmed.

---

## Replay, the effect ledger, and the Black Box

An external agent that edits files and calls tools has side effects that must not
silently re-fire on a replay. `acp` is therefore **guarded by the 0.45 step effect
ledger** (`engine/effects.py`) and defaults to `replay: memoize`:

- The first execution in a replay lineage claims the effect, runs the harness, and
  commits the transcript.
- A replay in the same lineage returns that transcript at `cost_usd = 0.0` **without
  spawning anything**.
- A claim left `in_flight` past its lease means a previous attempt started an agent turn
  and never reported an outcome — the repo may be half-edited. The step then fails with
  `EffectUncertain` rather than running the agent again. `on_uncertain: retry` opts out,
  and should only be used when re-running the harness is genuinely harmless.
- `replay: live` forces re-execution.

The effect fingerprint includes the resolved `acp_config`, so changing the message, the
workspace or the harness produces a different effect and the agent legitimately runs
again.

**Black Box status:** the same 0.45 guard added cassette record/replay to the hybrid
dispatch path, so an `acp` step **is** recorded to and replayed from the signed cassette
when one is attached to the run. Before 0.45 no hybrid step type was — that gap is
closed, not inherited.

---

## Security

An `acp` step spawns **an arbitrary local executable with a working directory and a
network connection**. That is a bigger blast radius than `delegate`, `openclaw` or
`managed-agent`, and it is treated as such.

| # | Threat | Mitigation |
|---|---|---|
| T1 | Arbitrary command execution via `command`/`args` from a hub-downloaded workflow | `create_subprocess_exec`, never `shell=True`; no metacharacter interpretation. `command`/`args` are **not** template-resolved, so no upstream step's output can become the executable. `agent:` resolves to a built-in table, never a registry download. Requires an admin-trusted run. |
| T2 | Credential exfiltration via inherited env | `build_acp_env` starts from `build_minimal_subprocess_env()` — PATH/HOME/LANG/TMPDIR/`LC_*` only — then adds `env` and only the names in `env_passthrough`. Nothing is inherited implicitly. The passthrough **names** are recorded in the audit event; values never are. |
| T3 | Path escape via `cwd` / `additional_directories` | `..` rejected pre-resolution, must be absolute, must exist, must be a directory, must resolve inside `acp_allowed_roots`. Symlinks out of the root are caught because the check is on the resolved path. |
| T4 | The agent asks *us* to read/write files (`fs/*`) — reaching files through the client it could not reach itself | Default `filesystem: none`: we advertise `fs:{readTextFile:false,writeTextFile:false}` and answer `-32601`. When enabled, every path is re-checked against the session workspace, and writes need `readwrite`. This is a capability we grant, so it is one we can revoke — unlike a managed container whose filesystem is opaque to us. |
| T5 | Shell via `terminal/*` | `terminal: true` is a **validation error** in 0.45. We never advertise the capability and answer `-32601` if asked anyway. |
| T6 | Permission fatigue / accidental blanket allow | `permission: reject` by default. Rules match on `ToolCall.kind`/`title`, **never** on the agent-defined `optionId` string. `ask` with no matching rule still rejects — there is no human on the end of an unattended run. Every decision lands in `output.permissions` and in the SHA-256 audit chain. `allow_always` is honoured for the session only, never persisted. |
| T7 | Prompt injection from repo contents → destructive action | Not solvable at the protocol layer. Deny-by-default permissions, `filesystem: none` unless needed, and the mandatory verification step. **ACP does not make an untrusted repo safe to point an agent at.** |
| T8 | Untrusted MCP servers via `mcp_servers` | Passed through verbatim, so an stdio entry is another arbitrary local exec. Same admin gate; validation rejects entries without `name` and a transport. |
| T9 | stdout/stderr flooding | 50 MB stdio read limit, `max_output_chars` on the transcript, an 8 KB stderr ring buffer, and `_truncate_output` afterwards regardless. |
| T10 | Zombie subprocesses on cancellation | Always reaped in a `finally`: terminate → wait → kill. A leaked harness keeps spending the user's money after the run is gone. |
| T11 | Data residency | Fails closed — see below. |

### What this does **not** give you

There is **no seccomp or container isolation applied to an `acp` subprocess.** The
`SandshoreRuntime` sandbox wraps LLM provider queries, not local processes. The harness
runs with the Sandcastle worker's own OS privileges, in the workspace you named. That is
the honest statement; do not read more into `acp_allowed_roots` than "the client will
not point it somewhere else".

### Data residency

`_enforce_data_residency` works off a model-registry entry, and Sandcastle does not know
which model an external harness calls. So `data_residency` **cannot** be enforced for
`type: acp`, and an `acp` step under a non-empty `data_residency` setting **fails
closed** with an explicit error. A compliance mode that silently does not apply to the
newest step type is worse than no compliance mode.

---

## What ACP does and does not subsume

| Step type | Subsumed by `acp`? |
|---|---|
| **`openclaw`** | **In design, yes — fully.** It is a thin POST to an OpenAI-compatible endpoint with `skills` as fake function-tools and a hardcoded cost. ACP does all of it better: real streaming, real tool-call visibility, real cancellation, real usage reporting. Blocked on the vendor: OpenClaw does not ship an ACP adapter today. **Deprecate when it does, not before.** |
| **`agent`, `runtime: local`** | **Yes, and it is an upgrade.** `LocalRuntime` is one non-streaming `POST /api/chat` to Ollama with no tools, no filesystem and no cancellation — a chat call wearing the word "agent". |
| **`agent`, `runtime: auto`/`anthropic`** | **Partly.** The template/`describe` machinery is prompt authoring and is orthogonal to transport; the router mostly forwards into `managed-agent`. A `runtime: "acp"` adapter needs a widened `AgentRuntime.execute` contract (no `cwd`, no session id, and a return type presuming token counts we will not have), so it is 0.46 work. |
| **`managed-agent`** | **No.** `environment_id`, `packages`, `network_access`, `memory_stores`, `multiagent`, `outcomes`, `shared_files` — ACP has no vocabulary for provisioning a remote container or mounting memory stores. It is a session protocol, not a compute product. |
| **`delegate`** | **No, and it should not.** `delegate` recurses into another *Sandcastle* workflow. Routing it through ACP would mean acting as an ACP server to ourselves: more machinery, less capability. |
| **`sub_workflow`** | No, same reasoning. |

Net: ACP subsumes 1 of 4 outright (vendor-blocked), 1 partially, and 2 not at all. The
real argument for it is not retirement of the four — it is that new harnesses cost zero
integrations, and that this is the only one that can cancel gracefully and see tool calls.

---

## Why not a server

Three reasons, none of which is "the spec is young" — the protocol is at stable version
`1` with a published schema, a governance doc and an RFD process:

1. **Being a server means other people's editors depend on us.** Consuming a
   capability-negotiated protocol costs a pin. Publishing one means Zed and JetBrains
   users file bugs when we lag an RFD.
2. **A Sandcastle workflow is not a chat session.** ACP's model is one `cwd`, one
   conversational turn stream, `session/prompt` in and `stopReason` out. A DAG with
   approval pauses, fan-out and sub-workflows does not map onto that without inventing
   semantics in someone else's protocol.
3. **The v2 draft restructures exactly the areas a server would touch** — prompt
   lifecycle, permission requests, tool-call updates. Building a v1 server now means
   rewriting it.

## Not mesh-routable

`acp` is deliberately absent from `mesh.ROUTABLE_STEP_TYPES`. `cwd` is a local path, so
routing the step to a node with a different filesystem is a silent correctness bug; the
wire payload would have to carry `env_passthrough` credential names; and `session/cancel`
plus the `session/update` stream are both bound to a live stdio pipe on the executing
node. The right long-term answer is a mesh *capability* ("this node has the harness and
a checkout of repo X"), which is a bigger design than 0.45.

---

## Testing and manual verification

`tests/test_acp_client.py` and `tests/test_acp_step.py` run against
`tests/fixtures/fake_acp_agent.py` — a hand-rolled ACP agent speaking the real wire
format on real pipes. No network, no `npx`, no vendor credentials.

For a real harness, `scripts/acp_smoke.py` runs one turn against `claude`, `codex`,
`gemini` or `goose` and prints what came back:

```
SANDCASTLE_ACP_ALLOWED_ROOTS='["/path/to/parent"]' \
  python scripts/acp_smoke.py --agent claude --cwd /path/to/repo \
  --message "List the files in this directory and stop."
```

### Manual smoke run

> **Not yet recorded.** This section is where the output of a real
> `claude-agent-acp` / `codex-acp` / `goose` run is pasted, and it is a definition-of-done
> item for the 0.45 release. The 0.45 branch has no recorded run against a real harness
> yet — nothing in this document should be read as claiming one was performed.

Two things to check when you do it, both flagged as unverified above:

1. Does the harness emit `usage_update` with a `cost` field? (The release's cost story
   depends on it.)
2. Does it print a non-ACP banner to stdout before the handshake? Sandcastle tolerates
   up to 64 such lines and then fails with a protocol error, but the count is a guess.

---

## Known limits

- `terminal/*` and `elicitation` are refused outright in 0.45.
- `session/load`, `session/resume` and `fork_session` are not used; a paused run cannot
  reattach to an agent session across a restart. Strong 0.46 candidate.
- ACP `plan` updates are captured into `output.plan` but not surfaced in the dashboard.
- `session/set_config_option`'s exact parameter names are taken from the documented
  method shape and have **not** been verified against a running harness.
- The `agent:` shorthand expands to an **unpinned** invocation. What actually ran is
  recorded from the harness's own `agentInfo` in the audit event. Use explicit
  `command`/`args` with your own pin when you need byte-exact reproducibility.
