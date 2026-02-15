# Sandcastle -Technical Specification v1.0
# Workflow Orchestrator Built on Sandstorm

## OVERVIEW

Sandcastle is a workflow orchestrator for autonomous agents. It uses Sandstorm
(https://github.com/tomascupr/sandstorm) as the execution engine -every
agent step is a Sandstorm API call. Sandcastle adds: DAG-based workflow
orchestration, persistent storage between runs, webhook callbacks,
scheduled runs, retry logic, and cost tracking.

Stack: Python 3.11+, FastAPI, Redis (job queue), PostgreSQL (state),
S3-compatible storage (agent data persistence).

---

## PROJECT STRUCTURE

```
sandcastle/
├── sandcastle.yaml.example      # Example workflow definition
├── docker-compose.yaml          # Redis + Postgres + MinIO for local dev
├── pyproject.toml
├── .env.example
├── README.md
├── LICENSE
│
├── src/
│   └── sandcastle/
│       ├── __init__.py
│       ├── main.py              # FastAPI app entrypoint
│       ├── config.py            # Settings from env / .env
│       │
│       ├── api/
│       │   ├── __init__.py
│       │   ├── routes.py        # API endpoints
│       │   └── schemas.py       # Pydantic request/response models
│       │
│       ├── engine/
│       │   ├── __init__.py
│       │   ├── dag.py           # DAG parser & dependency resolver
│       │   ├── executor.py      # Workflow executor (runs steps)
│       │   ├── sandbox.py       # Sandstorm client (calls /query)
│       │   └── storage.py       # S3 read/write for persistence
│       │
│       ├── queue/
│       │   ├── __init__.py
│       │   ├── worker.py        # Redis queue worker
│       │   └── scheduler.py     # Cron scheduler
│       │
│       ├── models/
│       │   ├── __init__.py
│       │   └── db.py            # SQLAlchemy models (runs, steps, costs)
│       │
│       └── webhooks/
│           ├── __init__.py
│           └── dispatcher.py    # Webhook callback sender
│
└── tests/
    ├── test_dag.py
    ├── test_executor.py
    └── test_api.py
```

---

## CORE COMPONENTS -DETAILED SPECS

### 1. WORKFLOW DEFINITION FORMAT (sandcastle.yaml)

Workflows are defined in YAML. Each workflow has steps. Each step
becomes one Sandstorm /query call.

```yaml
name: lead-enrichment
description: Enrich companies with firmographic data

# Global settings
sandstorm_url: ${SANDSTORM_URL}     # env var interpolation
default_model: sonnet
default_max_turns: 10
default_timeout: 300

steps:
  - id: scrape
    prompt: |
      Scrape the website {input.website} for company {input.name}.
      Extract: company description, approximate team size,
      tech stack, pricing model, headquarters location.
      Return ONLY valid JSON matching the output schema.
    model: sonnet              # override per step
    max_turns: 10
    timeout: 120
    parallel_over: input.companies   # fan-out: one agent per item
    output_schema:                    # becomes Sandstorm structured output
      type: object
      properties:
        description: { type: string }
        employee_estimate: { type: string }
        tech_stack: { type: array, items: { type: string } }
        pricing_model: { type: string }
        hq_location: { type: string }
    retry:
      max_attempts: 3
      backoff: exponential
      on_failure: skip           # skip | abort | fallback

  - id: enrich
    depends_on: [scrape]
    prompt: |
      Company: {input.name} ({input.website})
      Scraped data: {steps.scrape.output}
      
      Find additional information using web search:
      - Total funding and last funding round
      - Key contacts: CEO, CTO, VP Sales with LinkedIn URLs
      - Recent news from the last 6 months
      Return ONLY valid JSON.
    model: sonnet
    max_turns: 15
    output_schema:
      type: object
      properties:
        funding_total_usd: { type: number }
        last_round: { type: string }
        contacts:
          type: array
          items:
            type: object
            properties:
              name: { type: string }
              title: { type: string }
              linkedin_url: { type: string }
        recent_news:
          type: array
          items:
            type: object
            properties:
              headline: { type: string }
              date: { type: string }
              url: { type: string }

  - id: score
    depends_on: [scrape, enrich]
    prompt: |
      Score this lead 1-100 for a B2B SaaS sales team.
      Company: {input.name}
      Scrape data: {steps.scrape.output}
      Enrichment data: {steps.enrich.output}
      
      Consider: company size, funding stage, tech stack
      sophistication, growth signals from news.
      Return JSON with score and reasoning.
    model: haiku
    max_turns: 5
    output_schema:
      type: object
      properties:
        score: { type: integer }
        reasoning: { type: string }
        signals:
          type: array
          items: { type: string }

# What happens when workflow completes
on_complete:
  webhook: ${CALLBACK_URL}
  storage_path: enrichments/{run_id}/results.json

# What happens on failure
on_failure:
  dead_letter: true
  webhook: ${FAILURE_WEBHOOK_URL}

# Optional: recurring schedule (cron syntax)
schedule: null
```

**Template variable resolution order:**
- `{input.X}` → from the API request input
- `{steps.STEP_ID.output}` → JSON output from a completed step
- `{steps.STEP_ID.output.FIELD}` → specific field from step output
- `{storage.PATH}` → load content from persistent storage
- `{run_id}` → current run UUID
- `{date}` → current ISO date
- `${ENV_VAR}` → environment variable

---

### 2. DAG ENGINE (engine/dag.py)

Parses the workflow YAML and builds a dependency graph.

```python
"""
Key responsibilities:
- Parse YAML into a WorkflowDefinition dataclass
- Build adjacency list from depends_on
- Topological sort to determine execution order
- Detect cycles (raise error)
- Identify parallelizable groups (steps with same or no dependencies)

Key classes:

@dataclass
class StepDefinition:
    id: str
    prompt: str
    depends_on: list[str]
    model: str
    max_turns: int
    timeout: int
    parallel_over: str | None     # if set, fan-out over this input field
    output_schema: dict | None
    retry: RetryConfig | None
    fallback: FallbackConfig | None

@dataclass
class WorkflowDefinition:
    name: str
    description: str
    sandstorm_url: str
    default_model: str
    default_max_turns: int
    default_timeout: int
    steps: list[StepDefinition]
    on_complete: CompletionConfig | None
    on_failure: FailureConfig | None
    schedule: str | None

@dataclass
class ExecutionPlan:
    '''Result of topological sort -groups of steps that can run in parallel'''
    stages: list[list[str]]   # e.g. [["scrape"], ["enrich"], ["score"]]

Key methods:
- parse(yaml_path: str) -> WorkflowDefinition
- build_plan(workflow: WorkflowDefinition) -> ExecutionPlan
- validate(workflow: WorkflowDefinition) -> list[str]  # returns errors
"""
```

---

### 3. EXECUTOR (engine/executor.py)

The core loop. Executes an ExecutionPlan by calling Sandstorm for each step.

```python
"""
Key responsibilities:
- Execute stages sequentially (within a stage, steps run in parallel)
- For parallel_over steps: fan out into N parallel Sandstorm calls
- Resolve template variables in prompts before sending to Sandstorm
- Collect outputs from each step and store in run context
- Handle retries per step config
- On step failure: skip / abort / run fallback based on config
- Track cost per step (from Sandstorm result events)
- Stream progress to caller via callback or store in DB

The main execution flow:

async def execute_workflow(
    workflow: WorkflowDefinition,
    plan: ExecutionPlan,
    input_data: dict,
    run_id: str,
    storage: StorageBackend,
    db: Database,
) -> WorkflowResult:

    context = RunContext(
        run_id=run_id,
        input=input_data,
        step_outputs={},     # step_id -> output dict
        costs=[],
        status="running",
    )

    for stage in plan.stages:
        # All steps in a stage can run concurrently
        tasks = []
        for step_id in stage:
            step = workflow.get_step(step_id)
            
            if step.parallel_over:
                # Fan-out: create one task per item
                items = resolve_variable(step.parallel_over, context)
                for i, item in enumerate(items):
                    item_context = context.with_item(item, index=i)
                    tasks.append(execute_step(step, item_context, ...))
            else:
                tasks.append(execute_step(step, context, ...))
        
        # Run all tasks in this stage concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results, update context, handle failures
        for step_id, result in zip(stage, results):
            if isinstance(result, Exception):
                handle_failure(step_id, result, context)
            else:
                context.step_outputs[step_id] = result.output
                context.costs.append(result.cost)
        
        # Persist intermediate state
        await db.update_run(run_id, context)
    
    return WorkflowResult(
        run_id=run_id,
        outputs=context.step_outputs,
        total_cost=sum(context.costs),
        status="completed",
    )


async def execute_step(
    step: StepDefinition,
    context: RunContext,
    sandbox: SandstormClient,
    storage: StorageBackend,
) -> StepResult:
    '''Execute a single step with retry logic'''
    
    # 1. Resolve template variables in prompt
    prompt = resolve_templates(step.prompt, context)
    
    # 2. Load any storage references
    prompt = await resolve_storage_refs(prompt, storage)
    
    # 3. Build Sandstorm request
    request = {
        "prompt": prompt,
        "model": step.model,
        "max_turns": step.max_turns,
        "timeout": step.timeout,
    }
    
    # 4. If output_schema defined, pass as structured output config
    if step.output_schema:
        request["output_format"] = {
            "type": "json_schema",
            "schema": step.output_schema,
        }
    
    # 5. Call Sandstorm with retry
    for attempt in range(step.retry.max_attempts if step.retry else 1):
        try:
            result = await sandbox.query(request)
            return StepResult(
                step_id=step.id,
                output=result.structured_output or result.text,
                cost=result.total_cost_usd,
                duration=result.duration,
            )
        except Exception as e:
            if attempt < max_attempts - 1:
                await asyncio.sleep(backoff_delay(attempt))
                continue
            
            if step.fallback:
                return await execute_fallback(step.fallback, context, sandbox)
            raise
"""
```

---

### 4. SANDSTORM CLIENT (engine/sandbox.py)

Wraps communication with Sandstorm's /query endpoint.

```python
"""
Key responsibilities:
- POST to Sandstorm /query with SSE streaming
- Parse SSE events (system, assistant, user, result, error)
- Extract structured_output from result event
- Extract cost info from result event
- Handle connection errors and timeouts

class SandstormClient:
    def __init__(self, base_url: str, anthropic_api_key: str, e2b_api_key: str):
        ...
    
    async def query(self, request: dict) -> SandstormResult:
        '''
        Calls POST {base_url}/query with SSE streaming.
        Consumes the full stream and returns the final result.
        
        Returns SandstormResult with:
        - text: str (final assistant text)
        - structured_output: dict | None
        - total_cost_usd: float
        - num_turns: int
        - events: list[SSEEvent] (full event log for debugging)
        '''

    async def query_stream(self, request: dict) -> AsyncIterator[SSEEvent]:
        '''
        Same as query() but yields events as they arrive.
        Useful for real-time progress tracking.
        '''
    
    async def health(self) -> bool:
        '''GET {base_url}/health'''

Use httpx.AsyncClient with httpx-sse for SSE parsing.
"""
```

---

### 5. STORAGE (engine/storage.py)

Persistent storage for data that survives between runs.

```python
"""
Interface:

class StorageBackend(Protocol):
    async def read(self, path: str) -> str | None:
        '''Read file content from storage'''
    
    async def write(self, path: str, content: str) -> None:
        '''Write file content to storage'''
    
    async def list(self, prefix: str) -> list[str]:
        '''List files under prefix'''
    
    async def delete(self, path: str) -> None:
        '''Delete a file'''

Implementations:
- S3Storage -uses boto3/aioboto3, works with AWS S3, R2, MinIO
- LocalStorage -filesystem, for development

Config:
    STORAGE_BACKEND=s3           # s3 | local
    STORAGE_BUCKET=sandcastle-data
    STORAGE_ENDPOINT=http://localhost:9000   # for MinIO
    AWS_ACCESS_KEY_ID=...
    AWS_SECRET_ACCESS_KEY=...
"""
```

---

### 6. QUEUE & WORKER (queue/worker.py)

Redis-based job queue for async workflow execution.

```python
"""
When a workflow is submitted via API:
1. API creates a Run record in PostgreSQL (status=queued)
2. API pushes job to Redis queue
3. Returns run_id immediately to caller
4. Worker picks up job, runs executor, updates DB
5. On completion: fires webhook if configured

Use arq (https://arq-docs.helpmanual.io/) as the Redis queue library.
It's async-native, simple, and well-maintained.

Key functions:

async def process_workflow_job(ctx, run_id: str):
    '''
    Main worker function. Called by arq when a job is dequeued.
    1. Load Run from DB
    2. Parse workflow YAML
    3. Build execution plan
    4. Run executor
    5. Save results to DB + storage
    6. Fire webhook
    '''

Worker startup:
    arq worker sandcastle.queue.worker.WorkerSettings
"""
```

---

### 7. SCHEDULER (queue/scheduler.py)

Cron-based scheduling for recurring workflows.

```python
"""
Uses APScheduler with Redis job store.

On startup:
1. Scan all workflow files with schedule: field set
2. Register each as a cron job in APScheduler
3. When triggered, push a job to the Redis queue (same as API submission)

Also supports dynamic schedule creation via API:
POST /schedules { workflow, cron, input, notify }
DELETE /schedules/{id}
GET /schedules

Persist schedule definitions in PostgreSQL so they survive restarts.
"""
```

---

### 8. DATABASE MODELS (models/db.py)

```python
"""
Use SQLAlchemy 2.0 with async support (asyncpg driver).

Tables:

runs:
    id: UUID (primary key)
    workflow_name: str
    status: enum (queued, running, completed, failed, partial)
    input_data: JSONB
    output_data: JSONB (final merged outputs from all steps)
    total_cost_usd: float
    started_at: datetime
    completed_at: datetime
    error: text (null if success)
    callback_url: str (null if no webhook)
    tenant_id: str (null if single-tenant)
    created_at: datetime

run_steps:
    id: UUID (primary key)
    run_id: UUID (foreign key -> runs)
    step_id: str (matches YAML step id)
    parallel_index: int (null if not fanned out)
    status: enum (pending, running, completed, failed, skipped)
    input_prompt: text (resolved prompt sent to Sandstorm)
    output_data: JSONB
    cost_usd: float
    duration_seconds: float
    attempt: int (retry attempt number)
    error: text
    started_at: datetime
    completed_at: datetime

schedules:
    id: UUID (primary key)
    workflow_name: str
    cron_expression: str
    input_data: JSONB
    notify: JSONB (webhook, slack, email config)
    enabled: bool
    last_run_id: UUID (foreign key -> runs)
    created_at: datetime

Migrations: use Alembic.
"""
```

---

### 9. API ROUTES (api/routes.py)

```python
"""
FastAPI routes:

POST /workflows/run
    Body: { workflow: str, input: dict, callback_url?: str, callback_headers?: dict }
    → Creates a Run, queues it, returns { run_id, status: "queued" }

GET /runs/{run_id}
    → Returns run status, outputs, costs, step details

GET /runs/{run_id}/stream
    → SSE stream of live progress (proxied from executor)

GET /runs
    → List runs with filters (status, workflow, date range)
    → Pagination support

POST /workflows/run/sync
    → Synchronous execution -blocks until complete, returns full result
    → For simple use cases where caller wants to wait

POST /schedules
    Body: { workflow: str, cron: str, input: dict, notify?: dict }
    → Create a scheduled workflow

GET /schedules
    → List all schedules

DELETE /schedules/{id}
    → Remove a schedule

GET /health
    → { status: ok, sandstorm: ok/error, redis: ok/error, db: ok/error }

All routes return consistent JSON:
{ "data": ..., "error": null } on success
{ "data": null, "error": { "code": "...", "message": "..." } } on failure
"""
```

---

### 10. WEBHOOK DISPATCHER (webhooks/dispatcher.py)

```python
"""
When a workflow completes or fails, fire a webhook if configured.

POST to callback_url with:
{
    "event": "workflow.completed" | "workflow.failed",
    "run_id": "...",
    "workflow": "lead-enrichment",
    "status": "completed",
    "outputs": { ... merged step outputs ... },
    "costs": { "total_usd": 0.10, "breakdown": [...] },
    "duration_seconds": 45.2,
    "timestamp": "2025-02-15T..."
}

Headers: include any callback_headers from the original request.
Add X-Sandcastle-Signature header with HMAC-SHA256 of body
(using WEBHOOK_SECRET from env) for verification.

Retry webhook delivery 3 times with exponential backoff.
Log delivery attempts in DB.
"""
```

---

## CONFIGURATION (.env.example)

```env
# Sandstorm connection
SANDSTORM_URL=http://localhost:8000
ANTHROPIC_API_KEY=sk-ant-...
E2B_API_KEY=e2b_...

# Database
DATABASE_URL=postgresql+asyncpg://sandcastle:sandcastle@localhost:5432/sandcastle

# Redis
REDIS_URL=redis://localhost:6379/0

# Storage
STORAGE_BACKEND=s3
STORAGE_BUCKET=sandcastle-data
STORAGE_ENDPOINT=http://localhost:9000
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin

# Webhooks
WEBHOOK_SECRET=your-webhook-signing-secret

# Optional
LOG_LEVEL=info
```

---

## DOCKER-COMPOSE (for local development)

```yaml
version: "3.8"
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: sandcastle
      POSTGRES_USER: sandcastle
      POSTGRES_PASSWORD: sandcastle
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - miniodata:/data

volumes:
  pgdata:
  miniodata:
```

---

## DEPENDENCIES (pyproject.toml)

```toml
[project]
name = "sandcastle"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "httpx>=0.27",
    "httpx-sse>=0.4",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "arq>=0.26",
    "apscheduler>=3.10",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "aioboto3>=13",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.8",
]
```

---

## IMPLEMENTATION ORDER

Build in this sequence so you have something runnable at each step:

### Phase 1: Core (get a single workflow running end-to-end)
1. config.py -load .env settings
2. engine/sandbox.py -Sandstorm client
3. engine/dag.py -YAML parser + topological sort
4. engine/executor.py -basic sequential execution (no parallelism yet)
5. api/routes.py -POST /workflows/run/sync (synchronous, blocking)
6. main.py -wire it all up

TEST: curl with a simple 2-step workflow → get result back.

### Phase 2: Async + Persistence
7. models/db.py -SQLAlchemy models
8. Alembic migrations
9. queue/worker.py -arq worker
10. api/routes.py -add POST /workflows/run (async) + GET /runs/{id}
11. engine/storage.py -S3 storage backend
12. docker-compose.yaml

TEST: Submit workflow, poll /runs/{id}, see status progress.

### Phase 3: Production Features
13. Parallel execution (asyncio.gather in executor)
14. parallel_over fan-out logic
15. Retry logic with backoff
16. webhooks/dispatcher.py
17. queue/scheduler.py -cron scheduling
18. Cost tracking in DB

TEST: Run the lead enrichment workflow end-to-end with 5 companies.

### Phase 4: Polish
19. GET /runs with filters + pagination
20. GET /runs/{id}/stream -SSE progress proxy
21. Error handling edge cases
22. Tests
23. README + docs

---

## KEY DESIGN DECISIONS

1. **YAML over JSON for workflow definitions** -more readable for prompts
   which are multi-line strings. JSON schema for output_schema stays as-is.

2. **arq over Celery** -async-native, simpler, no kombu/amqp complexity.
   Perfect for our use case.

3. **Sandstorm as external service, not embedded** -Sandcastle calls
   Sandstorm over HTTP. This means you can run Sandstorm anywhere
   (Vercel, Docker, separate server) and Sandcastle orchestrates it.
   Clean separation of concerns.

4. **Fan-out via parallel_over** -instead of requiring users to write
   loops, they declare which input list to iterate over. The executor
   handles creating N parallel Sandstorm calls. This is the killer
   feature for batch processing (enriching 500 companies = 500
   parallel agents).

5. **Template variables use {curly braces}** -simple string interpolation,
   not Jinja2. Keep it dead simple. If we need conditionals later,
   we can upgrade.
"""
```
