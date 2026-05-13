# Security Audit Findings — Round 9

**Date:** 2026-05-12
**Branch:** fix/codex-audit-round-8
**Scope:** `src/sandcastle/**/*.py` (excluding files heavily audited in rounds 1-8)
**Focus:** engine/, mcp_server, a2a, webhooks, memory, generator, report

---

## [HIGH] Finding 1: A2A endpoint bypasses `allowed_workflows` API key restriction

- **OWASP:** A01 Broken Access Control
- **STRIDE:** Elevation of Privilege
- **Location:** `src/sandcastle/api/a2a.py:263-417`
- **Confidence:** Confirmed
- **Status:** Fixed in this round

**Description.** API keys can be scoped to a whitelist of workflow names via
`ApiKey.allowed_workflows`. The REST handlers in `api/routes.py` enforce this
check (lines 4428-4439, 4695-4707, 9645-9656). The A2A JSON-RPC `tasks/send`
handler (`_handle_tasks_send`) executed `execute_workflow` without ever
inspecting `request.state.allowed_workflows`. A tenant-scoped key restricted
to e.g. `"weather-bot"` could call any other workflow on the instance via
`POST /a2a`.

**Mitigation.** `a2a_endpoint` now reads `request.state.allowed_workflows`
and passes it to `_handle_tasks_send`, which short-circuits with a 403-style
A2A failure when the requested workflow is not in the whitelist.

---

## [HIGH] Finding 2: A2A endpoint bypasses per-key budget enforcement

- **OWASP:** A04 Insecure Design (DoS / spend abuse)
- **STRIDE:** Denial of Service
- **Location:** `src/sandcastle/api/a2a.py:384-407`
- **Confidence:** Confirmed
- **Status:** Fixed in this round

**Description.** The REST execution paths resolve the caller's
`ApiKey.max_cost_usd` via `_resolve_budget()` and pass it to
`execute_workflow(max_cost_usd=...)`. The A2A handler did neither, so a key
with a $5 cap could still invoke workflows of unbounded cost via `/a2a`.

**Mitigation.** New `_resolve_tenant_budget()` looks up the caller's active
API key budget and the dispatcher forwards it to `_handle_tasks_send`. The
Run record is created with `max_cost_usd` set and the executor receives the
same value, restoring the same enforcement guarantee as the REST endpoints.

---

## [HIGH] Finding 3: Cross-tenant memory leakage via shared scope IDs

- **OWASP:** A01 Broken Access Control
- **STRIDE:** Information Disclosure
- **Location:** `src/sandcastle/engine/memory.py:312-323`
- **Confidence:** Confirmed
- **Status:** Fixed in this round

**Description.** `resolve_scope_id()` built memory scope IDs from
`workflow_name` only:

```python
return f"workflow:{workflow_name}"   # or  "agent:..."  or  "global"
```

Because workflow names are unique per *instance*, not per *tenant*, the
embeddings store in Qdrant treats `workflow:research-bot` as a single
namespace shared across every tenant. Anything saved by tenant A — names,
emails, addresses, API outputs — became retrievable for tenant B on the
next semantic search of the same workflow.

**Mitigation.** `resolve_scope_id()` now takes an optional `tenant_id`
argument; when provided, scope IDs are prefixed with `tenant:<id>/`.
`execute_workflow()` propagates `tenant_id` from the Run record (or the
authenticated caller). `_VALID_SCOPE_RE` was extended to accept the
prefix while continuing to reject path-traversal characters. All three
production callers (REST sync runs, REST batch/`/v1` runs, queue worker,
A2A endpoint) pass the tenant when available.

---

## [HIGH] Finding 4: Stored XSS / HTML injection in PDF & HTML reports

- **OWASP:** A03 Injection
- **STRIDE:** Tampering
- **Location:** `src/sandcastle/engine/report.py:1773-1840` (pre-fix line numbers)
- **Confidence:** Confirmed
- **Status:** Fixed in this round

**Description.** `render_report()` and `render_report_html()` interpolated
`config.title`, `config.subtitle`, `config.author`, and `config.logo_url`
into the report HTML template without escaping. If any of those fields
flow from user input (and they can — workflows expose `{input.X}`
templating for report metadata), an attacker can inject arbitrary HTML
into the generated PDF or HTML output (and into the `string-set: doc-title`
header used by WeasyPrint, which propagates into every page header).

**Mitigation.** All four fields are now wrapped through `html.escape()`
before string formatting. The logo URL is additionally validated (see
finding 5).

---

## [HIGH] Finding 5: SSRF via WeasyPrint resource fetching

- **OWASP:** A10 Server-Side Request Forgery
- **STRIDE:** Information Disclosure
- **Location:** `src/sandcastle/engine/report.py:1767-1788` (pre-fix line numbers)
- **Confidence:** Confirmed
- **Status:** Fixed in this round

**Description.** WeasyPrint's default URL fetcher is invoked at render time
for any `<img>` `src`, `<link>` CSS, or `@import` URL embedded in the HTML
document. `config.logo_url` was passed through unchecked, and the bundled
CSS contains `@import url('https://fonts.googleapis.com/...')`. An attacker
who controls `logo_url` (or a future config field that lands in `<style>`)
can:

- Read cloud metadata (`http://169.254.169.254/latest/meta-data/...`)
- Probe internal services on `127.0.0.1`, `10.x`, `172.16.x`, `192.168.x`
- Use `file:///etc/passwd` to read host files (WeasyPrint allows `file://`
  schemes by default)

**Mitigation.** A new `_safe_url_fetcher()` is installed via
`WeasyprintHTML(string=..., url_fetcher=_safe_url_fetcher)`. It allows
`data:` URIs (needed for in-document base64 chart images) and `http(s)://`
URLs that pass DNS resolution against the same `_BLOCKED_NETWORKS` list
used by `webhooks/dispatcher.py`. Everything else (file://, gopher://,
links to private IPs) raises `ValueError`, which WeasyPrint surfaces as a
benign missing-resource. `config.logo_url` is also pre-validated before
being embedded in the `<img>` tag so unsafe URLs never reach the renderer.

Additionally, `accent_color` is now validated through `_safe_accent()`
against `#[0-9A-Fa-f]{3,8}` so it cannot break out of the CSS string
template via injected `} body { background: url(...)` payloads.

---

## [MEDIUM] Finding 6: Cross-tenant prompt-injection via workflow style references

- **OWASP:** A04 Insecure Design
- **STRIDE:** Tampering
- **Location:** `src/sandcastle/engine/generator.py:117-159`
- **Confidence:** Likely
- **Status:** Tracked, not auto-fixed (Medium severity)

**Description.** `_load_recent_user_workflows()` reads the three most
recently modified YAML files from the *global* `settings.workflows_dir`
and inlines up to 200 lines of each into the LLM system prompt as "style
reference". In a multi-tenant deployment this is a shared directory, so
tenant A can plant a workflow whose name/description contains adversarial
instructions ("ignore previous instructions and …") that steer the
workflow generator on subsequent runs by other tenants.

**Recommended fix (for round 10):** scope `_load_recent_user_workflows()`
by tenant — either store user workflow files in a per-tenant subdirectory
or filter the listing by `tenant_id`. Until then, deployments that rely
on this feature should disable it via configuration or run a single-tenant
instance.

---

## Coverage Matrix (round 9)

| OWASP | Tested | New Findings | Notes |
|-------|--------|--------------|-------|
| A01 Broken Access Control | yes | 2 | A2A, memory |
| A02 Cryptographic Failures | partial | 0 | rounds 1-8 covered Fernet rotation |
| A03 Injection | yes | 1 | report HTML/XSS |
| A04 Insecure Design | yes | 2 | budget bypass, prompt injection |
| A05 Security Misconfiguration | partial | 0 | CSP touched in round 8 |
| A06 Vulnerable Components | n/a | 0 | dependency audit out of scope |
| A07 Auth & Identification | partial | 0 | rounds 1-2, 5, 8 |
| A08 Software & Data Integrity | yes | 0 | webhook HMAC verified intact |
| A09 Logging & Monitoring | not tested | 0 | |
| A10 SSRF | yes | 1 | WeasyPrint logo/font fetch |

| STRIDE | Tested | New Findings |
|--------|--------|--------------|
| Spoofing | partial | 0 |
| Tampering | yes | 2 |
| Repudiation | n/a | 0 |
| Information Disclosure | yes | 2 |
| Denial of Service | yes | 1 |
| Elevation of Privilege | yes | 1 |

---

## Files Modified

- `src/sandcastle/api/a2a.py` — allowed_workflows + budget enforcement + tenant_id propagation
- `src/sandcastle/api/routes.py` — pass `tenant_id` to `execute_workflow` (sync + v1 paths)
- `src/sandcastle/queue/worker.py` — pass `Run.tenant_id` to `execute_workflow`
- `src/sandcastle/engine/executor.py` — accept and forward `tenant_id` to `resolve_scope_id`
- `src/sandcastle/engine/memory.py` — tenant-prefixed scope IDs + updated scope regex
- `src/sandcastle/engine/report.py` — HTML escaping, safe URL fetcher, accent validation
