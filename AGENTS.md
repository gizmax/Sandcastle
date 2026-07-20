# Sandcastle — repo guide for coding agents

"You write YAML. Sandcastle ships AI to production." Python 3.12+ FastAPI workflow
orchestrator. License BSL-1.1.

## Layout

- `src/sandcastle/` — the package
  - `engine/` — workflow parsing (`dag.py`), execution (`executor.py`, very large), runtime (`sandshore.py`), backends
  - `api/` — FastAPI routes (`routes.py` is a 13k-line module — extend it in place, do not split), `auth.py` (API-key auth + middleware), `schemas.py`
  - `models/db.py` — SQLAlchemy async models (single source of truth for schema)
  - `queue/` — arq worker (`worker.py`), apscheduler (`scheduler.py`)
  - `main.py` — app factory + lifespan; `config.py` — pydantic-settings (env prefix `SANDCASTLE_` implied by field names in UPPER_SNAKE)
- `alembic/versions/` — migrations (production Postgres path; SQLite dev uses `create_all`)
- `tests/` — pytest, `asyncio_mode = "auto"`, file-backed SQLite fixtures in `conftest.py`
- `dashboard/` — React 19 + TS + Vite SPA (served by the API; hatchling force-includes `dashboard/dist`)
- `workflows/*.yaml` — built-in workflow templates; must pass `dag` parse + `validate()`

## Commands

- Install: `pip install -e ".[dev,mcp,memory,otel,parse,report,docker,security]"` (a ready venv lives in `.venv/`, use `.venv/bin/python`)
- Tests: `.venv/bin/python -m pytest tests/ -q --tb=short --timeout=60` (full suite ~14k tests; prefer running only the test files relevant to your change)
- Lint: `.venv/bin/python -m ruff check src tests` and `.venv/bin/python -m ruff format --check src tests` (line-length 100, E501 ignored)
- Dashboard: `cd dashboard && npm ci && npm run build` (runs `tsc` + vite build); tests via `npm test` (vitest) if present
- Migrations check: `alembic upgrade head` against Postgres in CI (`postgres-migrations` job)

## Conventions

- Minimal, scoped diffs. No drive-by refactors, no renames, no reformatting outside your change.
- Match the surrounding style; ruff config above is the gate.
- All DB access is async SQLAlchemy 2.0 style, sessions via `async with async_session()`.
- New settings go in `config.py` as pydantic-settings fields with safe, fail-closed defaults.
- Every behavior change needs a test in `tests/` following existing naming (`test_<area>.py`); run the tests you add plus the nearest existing test file.
- Never commit; never touch `.git`. Never weaken existing security guards (SSRF pinning, sandbox confinement, auth middleware, constant-time comparisons).
- Security invariants that must hold after any change:
  - Every route is either under `/api` (auth middleware applies) or explicitly listed as public in `api/auth.py`.
  - Tenant scoping: no cross-tenant reads/writes; `tenant_id` must propagate through engine contexts.
  - File access confined to `data_dir` via `resolve()` + `is_relative_to()`.
