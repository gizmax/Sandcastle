# Security Audit — Round 9 (Codex)

**Date:** 2026-05-12 10:34 CEST
**Branch:** `fix/codex-audit-round-8` (no push, as requested)
**Scope:** `src/sandcastle/**/*.py`
**Focus:** files not heavily covered by rounds 1-8 — `engine/{memory,generator,report,pdf,doctor,policy,docparse,sandshore,evolution,autopilot,hub_scanner}.py`, `api/a2a.py`, `webhooks/dispatcher.py`, `mcp_server.py`, `sdk.py`, `models/db.py`
**Mode:** bounded, 15 iterations, `--fix` enabled for Confirmed Critical/High

## Summary

| Severity | Count | Status |
|----------|-------|--------|
| Critical | 0 | — |
| High | 5 | Fixed |
| Medium | 1 | Tracked (round 10) |
| Low | 0 | — |
| Info | 0 | — |

**STRIDE coverage:** S[partial] T[✓] R[n/a] I[✓] D[✓] E[✓] — 4/6 active
**OWASP coverage:** A01, A03, A04, A10 with findings; A02, A05, A07, A08
sanity-checked against prior rounds.

## Top Findings (all fixed unless noted)

1. **[HIGH] A2A endpoint bypassed `allowed_workflows`** — restricted API keys
   could call any workflow via `POST /a2a`. Fixed: dispatcher passes the
   whitelist to `_handle_tasks_send`.
2. **[HIGH] A2A bypassed per-key budget** — keys with `max_cost_usd` caps
   could run unbounded workflows via A2A. Fixed: budget resolution mirrors
   the REST path, executor receives the cap.
3. **[HIGH] Cross-tenant memory leakage** — Mem0 scope IDs were
   `workflow:<name>`, shared across tenants. Fixed: scope IDs now
   tenant-prefixed (`tenant:<id>/workflow:<name>`) when tenant context is
   available; all `execute_workflow` callers thread `tenant_id` through.
4. **[HIGH] Stored XSS in PDF/HTML reports** — title/subtitle/author/logo
   from `ReportConfig` interpolated unescaped. Fixed: `html.escape()` on
   every config field, `accent_color` validated against a strict hex regex.
5. **[HIGH] SSRF via WeasyPrint URL fetching** — `config.logo_url` and
   any `@import url(...)` resolved to arbitrary targets including cloud
   metadata IPs and `file://` paths. Fixed: custom `url_fetcher` blocks
   private IPs and non-http(s) schemes; logo URL pre-validated.
6. **[MEDIUM] Cross-tenant prompt-injection** — workflow generator inlines
   recent YAMLs from the global workflows_dir into the LLM system prompt.
   Tracked for round 10 (per-tenant scoping).

## Files

- [findings.md](./findings.md) — per-finding detail with code locations
- [security-audit-results.tsv](./security-audit-results.tsv) — iteration log

## Verification

- `pytest tests/test_a2a.py tests/test_memory.py tests/test_report_step_v28.py tests/test_security_pentest.py tests/test_a2a_agui_wave7.py tests/test_sdk_a2a_agui_wave9.py` — 117 + 406 passing
- Two known-flaky pre-existing failures (`test_workflow_api_a2a_v27.py::test_async_call_returns_queued` — rate limit, `test_executor_deep.py::test_dataclasses_replace_count` — drift from 38→47 fields, `test_coverage_90_batch15.py::test_workflow_detail_not_found_returns_404` — 405 vs 404) confirmed via `git stash` not introduced by this round.
