# 2026 Stack Audit - Findings + Resolutions

Date: 2026-05-16
Audited by: WebFetch reference cards + diff against origin/main
Branch: chore/2026-stack-audit-fixes -> PR #217

## Scope

Comprehensive audit of:
- AI provider integrations + model IDs + beta headers
- Python backend dependencies
- Dashboard frontend dependencies
- 62 connectors (sampled)
- Open protocols (MCP, A2A, OpenAPI, Schema.org, OTel, CSP)
- SEO + AI bot policy
- EU AI Act Annex IV coverage

## Reference data (May 16, 2026 snapshot)

| Area | Reference |
|---|---|
| Anthropic models | https://docs.anthropic.com/en/docs/about-claude/models/overview |
| Anthropic API changes | https://docs.anthropic.com/en/release-notes/api |
| MCP spec | https://modelcontextprotocol.io/specification (rev 2025-11-25) |
| A2A spec | https://github.com/a2aproject (v1.0.0 since 2026-03-12) |
| Schema.org | https://schema.org/docs/releases.html (v30.0) |
| OpenAPI | https://www.openapis.org/ (v3.2.0 latest) |
| OTel Gen-AI semconv | https://opentelemetry.io/docs/specs/semconv/gen-ai/ |
| AI bot user agents | https://knownagents.com/agents |

## Findings + resolutions

### P0 - Resolved in this PR

| # | Finding | Resolution | Commit |
|---|---|---|---|
| 1 | /eu-ai-act/ missing from sitemap.xml (shipped in v0.31) | Added with priority 0.95, refreshed all lastmod dates | e432ad6 |
| 2 | robots.txt had no explicit AI bot policy (just `User-agent: *, Allow: /`) | Added explicit Allow for 17 AI crawlers (GPTBot, ClaudeBot, Google-Extended, etc) + Disallow for 4 aggressive scrapers (Bytespider, ImagesiftBot, omgili, PetalBot) | a1bf4aa |
| 3 | `anthropic>=0.50` floor (runtime needs 0.95+ for Managed Agents typing) | Bumped to `>=0.95` | a545847 |
| 4 | `mcp>=1.0` floor (runtime needs 1.23+ for DNS rebinding fix) | Bumped to `>=1.23` | a545847 |
| 5 | `weasyprint>=62` floor (6 majors stale, security CVEs) | Bumped to `>=68` | a545847 |
| 6 | `opentelemetry-*>=1.20` floor (>1y old) | Bumped to `>=1.40` | a545847 |
| 7 | `httpx>=0.27`, `pydantic>=2.0`, `cryptography>=42.0` floors stale | Bumped to current minimums (0.28, 2.9, 43.0) | a545847 |
| 8 | dashboard deps stale: lucide v0.575 (v1 latest), recharts 3.7 (3.8 latest), tailwind-merge 3.4 (3.6 latest), vitest 4.0 (4.1 latest) | Bumped all + adapted code for lucide v1 (Figma icon removed -> PenTool) + recharts 3.8 stricter Tooltip Formatter generics | f50695a |
| 9 | A2A Agent Card pre-v1.0 shape (uses `authentication`, missing `protocolVersion`/`provider`/`tags`/`examples`) | Rewrote per A2A v1.0.0 spec: added protocolVersion, provider, documentationUrl, securitySchemes (replaces authentication), security, skill tags+examples | 1589121 |
| 10 | claude-sonnet-4-20250514 / claude-opus-4-20250115 references in 5 production files (retire 15 June 2026) | Migrated to family aliases (`claude-sonnet-4-6`, `claude-haiku-4-5`, `claude-opus-4-7`) across executor, generator, browser_computer_use connector, routes, dashboard mock, test fixtures | fa3f759 |
| 11 | MCP server covered only 5 primitives (missing Elicitation, added in spec rev 2025-11-25) | Added `request_workflow_input` tool wrapping ctx.session.elicit() + manifest now advertises 6 primitives + spec_revision | 8527112 |
| 12 | No PR-level CI workflow (pages.yml only push-to-main, hub-validate.yml only hub/ paths, publish.yml only tags) | Added .github/workflows/pr-tests.yml: parallel python + dashboard jobs on PR open/push | 0150877 |
| 13 | `hypothesis` and `pytest-timeout` missing from [dev] extras (fresh install couldn't run suite) | Added both to pyproject [dev] | 36bf02f |

### P1 - Held for separate PRs

| # | Finding | Why deferred | Suggested next |
|---|---|---|---|
| 14 | TypeScript ~5.9 -> ~6.0 GA | Major bump drops legacy module formats (ES5/AMD/UMD/SystemJS); needs source audit | Separate PR after main lands |
| 15 | Vite ^7 -> ^8 (Rolldown default bundler) | Bundler swap; needs perf + manualChunks regression test | Separate PR |
| 16 | arq -> Taskiq / Dramatiq | arq is in maintenance-only mode upstream (issue #510); needs scheduler migration spike | Q3 2026 |
| 17 | fastembed<0.4 upper bound | Probably API breaking change; needs validation | Q3 2026 |
| 18 | EU AI Act Annex IV - verify all 9 required sections present | Generator exists at /workflows/{name}/annex-iv; section completeness audit needed | Q3 2026 |
| 19 | Schema.org v30 EU trust fields (companyRegistration, legalAddress, legalRepresentative) | Cosmetic JSON-LD addition for EU buyer trust signaling | Optional polish |
| 20 | Standard Webhooks scheme alignment (webhook-id, webhook-timestamp, webhook-signature) | Sandcastle's outbound webhooks currently use custom headers; aligning gets automatic interop with Svix/Brex/Kong | Q3 2026 |
| 21 | A2A push notifications (capability: false today) | A2A v1.0 supports them but Sandcastle hasn't implemented | Q4 2026 |

## Anthropic 2026 stack verification

| Area | Sandcastle pin | 2026 latest | Status |
|---|---|---|---|
| Claude beta header | `managed-agents-2026-04-01` | same | OK |
| Default models (post-migration) | sonnet-4-6, haiku-4-5, opus-4-7 | latest tier aliases | OK |
| Computer Use header | `computer-use-2025-11-24` | latest for Opus 4.7/Sonnet 4.6 | OK |
| MCP SDK floor (post-bump) | `mcp>=1.23` | 1.27.1 latest | OK |
| Anthropic SDK floor (post-bump) | `anthropic>=0.95` | 0.102.0 latest | OK |
| MCP primitives (post-add) | 6 (tools, resources, prompts, sampling, roots, elicitation) | 6 per spec rev 2025-11-25 | OK |
| A2A protocolVersion (post-rewrite) | 1.0.0 | 1.0.0 | OK |

## Sandcastle is AHEAD of mainstream

(Things competitors generally don't have)

- MCP-first publishing CLI (v0.31)
- EU AI Act dedicated landing page + 10 compliance template workflows
- Annex IV transparency report generator endpoint
- SHA-256 hash-chain audit trail
- 6-provider failover with SLO routing
- Self-hostable (BSL 1.1 -> Apache 2.0 after 2030)
- Computer Use + Memory Stores + Multiagent + Outcomes (v0.32 prep on feat/v0.32-agents-deep, unpushed)

## Test results

| Suite | Pre-PR | Post-PR | Source |
|---|---|---|---|
| Dashboard `npm run build` | clean | clean (lucide v1 + recharts 3.8 adapted) | local /tmp clone |
| Dashboard vitest | 784 passing | 784 passing | local /tmp clone |
| Python pytest | 15,014 passing | (pending CI) | CI: pr-tests.yml `python` job |
| TypeScript tsc | clean | clean | local /tmp clone |

CI pr-tests.yml workflow added by this PR is self-running on the PR (GitHub picks it up from the head commit). All future PRs benefit.

## Risk assessment

- **Low**: SEO fixes, dep floor bumps, dashboard minor bumps, MCP elicitation, hypothesis dev extra.
- **Medium**: A2A v1.0 shape change (consumers parsing the old `authentication` block see a different key). Mitigation: A2A v1.0 was published 12 March 2026; any v1.0-aware client expects the new shape. Pre-v1.0 clients are a rapidly shrinking population.
- **Medium**: Default model migration. Callers who relied on `model: "sonnet"` mapping to the dated ID will now get the family alias (Anthropic auto-resolves). Behavioral diff is minimal but non-zero. Mitigation: explicit dated IDs in user YAMLs continue to work.

## Follow-up actions

After this PR merges:
1. Cherry-pick equivalents onto feat/v0.32-agents-deep so v0.32 lands with the audit fixes baked in.
2. Open separate PRs for TS 6.0, Vite 8, arq migration spike.
3. Update CHANGELOG when bundled into the next release.
4. Trigger an FTP push of the new sitemap.xml + robots.txt to sandcastle-ai.eu (these are static, not auto-deployed from this repo).
