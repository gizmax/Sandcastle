"""AI Workflow Generator - creates valid YAML workflows from natural language."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from sandcastle.engine.dag import parse_yaml_string, validate
from sandcastle.engine.providers import KNOWN_MODELS
from sandcastle.engine.tools.registry import TOOL_REGISTRY

logger = logging.getLogger(__name__)


@dataclass
class GenerateResult:
    """Result from the AI workflow generator."""

    yaml_content: str
    name: str = ""
    description: str = ""
    steps_count: int = 0
    validation_errors: list[str] = field(default_factory=list)
    input_schema: dict | None = None
    fix_attempts: int = 0
    similar_template: str | None = None


# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------

_EXAMPLE_TEMPLATES = [
    "research_agent",
    "data_extractor",
    "email_campaign",
    "review_and_approve",
    # v0.2666: diverse examples covering advanced step types
    "deployment_monitor",              # condition step (branching)
    "self_healing_pipeline",           # complex multi-step with parallel deps
    "competitive_intel",               # deep research with many depends_on
    "contract_analyzer_with_parse",    # parse step (document extraction)
]


def _load_example_templates() -> str:
    """Load curated templates as few-shot examples for the system prompt."""
    templates_dir = Path(__file__).parent.parent / "templates"
    parts: list[str] = []
    for name in _EXAMPLE_TEMPLATES:
        path = templates_dir / f"{name}.yaml"
        if path.exists():
            parts.append(f"--- Example: {name} ---\n{path.read_text()}")
    return "\n\n".join(parts)


def _find_similar_template(description: str) -> str | None:
    """Find a template with >50% keyword overlap with the user's description.

    Loads all template YAML files, extracts name + description from each,
    and does simple keyword matching. Returns the best match name or None.
    """
    templates_dir = Path(__file__).parent.parent / "templates"
    if not templates_dir.is_dir():
        return None

    # Split description into lowercase words (alpha only, skip short ones)
    desc_words = set(
        w for w in re.findall(r"[a-z]+", description.lower()) if len(w) > 2
    )
    if not desc_words:
        return None

    best_name: str | None = None
    best_score: float = 0.0

    for path in templates_dir.glob("*.yaml"):
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue

        # Extract name and description from the first ~20 lines
        header_lines = content.split("\n", 20)[:20]
        header_text = ""
        for line in header_lines:
            if line.startswith("name:") or line.startswith("description:"):
                header_text += " " + line.split(":", 1)[1]

        # Also include the filename stem as searchable text
        header_text += " " + path.stem.replace("_", " ").replace("-", " ")

        template_words = set(
            w for w in re.findall(r"[a-z]+", header_text.lower()) if len(w) > 2
        )
        if not template_words:
            continue

        overlap = len(desc_words & template_words)
        score = overlap / len(desc_words)

        if score > best_score:
            best_score = score
            best_name = path.stem

    if best_score > 0.5 and best_name:
        return best_name
    return None


def _load_recent_user_workflows(max_files: int = 3, max_lines: int = 200) -> str:
    """Load the most recent workflow YAML files from the user's workflows dir.

    Returns formatted string for injection into the system prompt, or empty
    string if no user workflows exist.
    """
    try:
        from sandcastle.config import settings
        workflows_dir = Path(settings.workflows_dir).resolve()
    except Exception:
        return ""

    if not workflows_dir.is_dir():
        return ""

    # Find YAML files sorted by modification time (newest first)
    yaml_files: list[Path] = []
    try:
        yaml_files = sorted(
            [*workflows_dir.glob("*.yaml"), *workflows_dir.glob("*.yml")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return ""

    if not yaml_files:
        return ""

    parts: list[str] = []
    for path in yaml_files[:max_files]:
        try:
            lines = path.read_text(errors="replace").split("\n")
            # Cap at max_lines to prevent token bloat
            content = "\n".join(lines[:max_lines])
            parts.append(content)
        except OSError:
            continue

    if not parts:
        return ""

    return "## User's Recent Workflows (for style reference)\n" + "\n---\n".join(parts)


# ---------------------------------------------------------------------------
# Step types documentation (all 19 types)
# ---------------------------------------------------------------------------

_STEP_TYPES_DOC = """\
## Step Types

Each step has a `type` field (default: "standard"). Here are all 20 supported types:

### standard (default)
Default LLM agent step - runs an agent in a sandbox with tools.
Fields: prompt, model, max_turns, timeout, tools

### llm
Direct LLM call without agent loop or sandbox. Lightweight and fast.
Fields: prompt, model, llm_config: {system_prompt}

### http
HTTP request step - calls an external API endpoint.
Fields: http_config: {url, method, headers, body, auth}
No prompt required.

### code
Inline code execution in sandbox.
Fields: code_config: {code, language}
language defaults to "python". No prompt required.

### condition
If/else branching based on an expression.
Fields: condition_config: {expression, then: [step_ids], else: [step_ids]}
expression is evaluated against previous step outputs. No prompt required.

### classify
LLM-based multi-class routing. Classifies input into categories and routes to different branches.
Fields: classify_config: {categories: [list], input, model, branches: {category: [step_ids]}}

### loop
Iterate over a list or repeat until a condition is met.
Fields: loop_config: {over, step_ids: [list], max_iterations, until}
over is a variable path (e.g. "steps.fetch.output.items"). No prompt required.

### race
Run parallel branches - first valid result wins, others are cancelled.
Fields: race_config: {branches: [[step_ids], [step_ids]], validator}
validator is an optional expression to validate results. No prompt required.

### sensor
Poll an external URL at intervals until a condition is met (e.g. waiting for deployment).
Fields: sensor_config: {url, check_interval, timeout, condition, method, headers}
check_interval in seconds (default 30), timeout in seconds (default 1800). No prompt required.

### gate
Multi-strategy approval gate. Supports LLM evaluation, human approval, and timeout strategies.
Fields: gate_config: {strategies: [{type: "llm_eval"|"human"|"timeout", config: {...}}]}
No prompt required.

### transform
Jinja2 template-based data transformation. Maps and reshapes data between steps.
Fields: transform_config: {template}
template is a Jinja2 string with access to steps.X.output variables. No prompt required.

### notify
Send a notification to an external service (Slack, Teams, email, etc).
Fields: notify_config: {service, channel, message}
service is a tool connector name, message supports {steps.X.output} vars. No prompt required.

### delegate
Invoke another workflow as a sub-step. Useful for composing complex pipelines.
Fields: delegate_config: {workflow, task_description, timeout}
workflow is the name of the workflow to invoke.

### approval (legacy)
Human-in-the-loop approval gate.
Fields: approval_config: {message, show_data, timeout_hours, on_timeout, allow_edit}

### browser
Browser automation step - runs a headless browser in the sandbox
for web scraping, form filling, or UI testing.
Supports three modes: "playwright" (agent writes Playwright scripts),
"computer_use" (Claude controls the browser via screenshots),
and "dom" (lightweight DOM-only extraction).
Fields: prompt (task description), browser_config:
{mode, start_url, viewport_width, viewport_height,
timeout_seconds, wait_after_action, screenshot_on_error,
headless, credentials_env, max_actions, capture_screenshots,
output_schema, captcha_strategy}
mode defaults to "playwright". Requires a prompt describing
the browser task.

### composio
Execute any of 500+ business app actions via Composio API.
Supports Gmail, Slack, GitHub, Salesforce, HubSpot, Jira, and hundreds more.
Fields: composio_config: {action, params, connected_account_id, app}
action is a Composio action ID (e.g. "gmail_send_email", "github_create_issue").
params is a dict of action-specific parameters.
connected_account_id links to a pre-authenticated Composio connection.
app is an optional filter (e.g. "github").
No prompt required. Requires TOOL_COMPOSIO_API_KEY env var.

### parse
Parse documents (PDF, DOCX, XLSX, CSV). Extracts text or markdown from files.
Fields: parse_config: {input_path, output_format, pages, ocr_engine, languages}
input_path is a file path or variable (e.g. "{input.file_path}").
output_format: "markdown" (default), "text", or "json".
pages: optional page range for PDFs (e.g. "1-5").
ocr_engine: "auto" (default) | "pymupdf" | "chandra" - chandra for scanned docs, handwriting, tables from images.
languages: optional list of language codes for Chandra OCR (e.g. ["cs", "en"]).
No prompt required (prompt is optional context).

### report
Generate beautiful PDF/HTML reports using WeasyPrint + Jinja2.
The prompt is used to generate report content via LLM, then rendered into a
professional PDF with cover page, table of contents, and styled typography.
Fields: prompt (content generation instruction), report_config:
{template, format, theme, title, subtitle, author, logo_url,
accent_color, include_toc, include_page_numbers, paper_size}
format: "pdf" (default) | "html"
theme: "professional" (default) | "minimal" | "academic"
paper_size: "A4" (default) | "letter"
accent_color: hex color (default "#f59e0b")
title/subtitle/author support {input.X} template variables.
Requires a prompt. Requires sandcastle-ai[report] extra.

### openclaw
Call OpenClaw AI agents. Routes requests to an OpenClaw gateway.
Fields: openclaw_config: {agent, task, gateway_url}
agent: name of the OpenClaw agent to invoke.
task: description of what the agent should do.
gateway_url: optional, defaults to env OPENCLAW_GATEWAY_URL.
No prompt required.

### sub_workflow (legacy)
Run another workflow as a sub-step with input/output mapping.
Fields: sub_workflow: {workflow, input_mapping, output_mapping,
parallel_over, max_concurrent, timeout}

IMPORTANT: Types that do NOT need a prompt: http, code, condition,
loop, race, sensor, gate, transform, notify, composio, openclaw, parse.
All other types require a prompt field.

## Dynamic Context Retrieval (optional)

Steps can fetch external context before execution. The resolved context is
available as {{context}} in the prompt template:

- context_query: "search query to fetch relevant context before execution"
- context_source: "memory"  # memory|web|files|custom
- context_max_tokens: 2000  # max context length

Sources:
- memory: semantic search over agent memory (requires memory config)
- web: search the web via Tavily (requires TOOL_TAVILY_API_KEY)
- files: keyword search over local workflow YAML files
- custom: run a shell command, capture stdout as context

Example:
```yaml
steps:
  - id: research
    context_query: "latest trends in {{input.topic}}"
    context_source: web
    context_max_tokens: 3000
    prompt: |
      Based on this research:
      {{context}}

      Write a comprehensive analysis of {{input.topic}}.
```

## Optional Step Configuration

Steps can include these optional fields for resilience and memory:

- retry: {max_attempts: 3, backoff: exponential} - automatic retry on failure
- memory: {read: true, write: true} - agent memory (persists across runs)

## Optional Self-Describing Metadata (recommended for production workflows)

Each step can include metadata that explains its purpose and ownership:
- responsibility: "What this step does in one sentence"
- source_hint: "Why this step exists - business reason or ticket reference"
- owner: "Who is responsible for this step"
- added_date: "2026-03-30"

These fields power `sandcastle describe`, `sandcastle lint`, and `sandcastle owners` commands.
Include them in production workflows for better documentation and auditability.

## Workflow-Level Classification

Workflows can declare an EU AI Act risk classification at the top level:
- risk_level: minimal|limited|high - determines compliance requirements
"""


def _load_tool_names() -> str:
    """Load available tool connector names with configuration status.

    For each tool, checks whether its credential env vars are set.
    Configured tools are marked as ready to use so the LLM prefers them.
    """
    lines: list[str] = []
    # Group by category for readability
    categories: dict[str, list[tuple[str, str, str]]] = {}
    for name, tool in sorted(TOOL_REGISTRY.items()):
        cat = tool.category
        if cat not in categories:
            categories[cat] = []

        # Check if all credential env vars are set (non-empty)
        cred_vars = tool.credential_env_vars
        if cred_vars and all(os.environ.get(v) for v in cred_vars):
            status = "(CONFIGURED - ready to use)"
        elif cred_vars:
            # Show the first missing env var as a hint
            missing = next((v for v in cred_vars if not os.environ.get(v)), cred_vars[0])
            status = f"(requires {missing})"
        else:
            status = ""

        categories[cat].append((name, tool.description, status))

    for cat in sorted(categories):
        lines.append(f"**{cat.replace('_', ' ').title()}:**")
        for name, desc, status in categories[cat]:
            suffix = f" {status}" if status else ""
            lines.append(f"- {name}: {desc}{suffix}")
        lines.append("")

    lines.append(
        "Prefer tools marked CONFIGURED as the user has already set them up."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _build_system_prompt(*, include_user_workflows: bool = False) -> str:
    """Build the system prompt with schema docs, model list, and examples.

    When include_user_workflows is True, appends recent user workflows
    from the workflows directory as style reference (few-shot).
    """
    models = ", ".join(sorted(KNOWN_MODELS))
    examples = _load_example_templates()
    tool_connectors = _load_tool_names()
    user_workflows_section = ""
    if include_user_workflows:
        user_workflows_section = _load_recent_user_workflows()

    return f"""\
You are a workflow generator for Sandcastle, an AI agent orchestrator.
Your job is to produce valid YAML workflow definitions based on the user's description.

## YAML Schema

A workflow YAML has these top-level fields:
- name: kebab-case identifier (required)
- description: short description (required)
- default_model: model name (optional, default: sonnet)
- default_max_turns: integer (optional, default: 10)
- default_timeout: seconds (optional, default: 300)
- default_tools: list of tool connector names for all steps (optional)
- input_schema: JSON Schema for user inputs (required)
  - required: list of required field names
  - properties: object with field definitions (type, description)
- steps: list of step objects (required)

Each step has:
- id: unique kebab-case identifier (required)
- prompt: the instruction for the agent (required for most types, see Step Types)
- depends_on: list of step IDs this step waits for (optional)
- model: model name (optional, overrides default_model)
- max_turns: integer (optional)
- type: step type string (optional, default: "standard")
- tools: list of tool connector names for this step (optional, e.g. ["slack", "jira"])

{_STEP_TYPES_DOC}

## Available Tool Connectors

Steps can use external tool connectors via the `tools` field. Use `tools: [name]` on a step
or `default_tools: [name]` at workflow level. Named connections use colon syntax: "tool:connection".

{tool_connectors}

## Available Models
{models}
Always use these short names - NEVER use full API model IDs.

## Variable Syntax
- {{input.X}} - reference user input field X
- {{steps.STEP_ID.output}} - reference output of a previous step

## Sandbox Execution Environment

Workflows run inside sandboxed environments (E2B cloud sandbox, Docker, or local subprocess).
The agent has access to ONLY these tools: Bash (with curl), Read, Write, Edit, Glob, Grep.
Steps can also use external tool connectors (see Available Tool Connectors above).

CRITICAL LIMITATIONS for standard steps - the agent CANNOT:
- Browse the web or render JavaScript - no browser is available in standard steps
- Use WebSearch or WebFetch - these tools do NOT exist in the sandbox
- Access social media platforms (Twitter/X, LinkedIn, Reddit,
  Instagram) - they require OAuth/API keys
- Access review platforms (G2, Trustpilot, Capterra, App Store)
  - they require JavaScript rendering
- Crawl multiple pages of a website - only simple single-page
  curl requests work
- Use Google/Bing search - search engines block automated
  curl requests

NOTE: For tasks that require a real browser (JavaScript
rendering, form filling, multi-page navigation, scraping
dynamic sites), use the `browser` step type instead of
`standard`. Browser steps run a headless Playwright browser
in the sandbox.

WHAT WORKS:
- Fetching simple HTML pages via curl (news sites, company
  homepages, documentation)
- Calling public REST APIs with JSON responses
- Processing data provided as user input (JSON, CSV, text
  pasted by user)
- Using the agent's built-in knowledge for analysis, writing,
  reasoning, and planning
- File operations (reading, writing, creating reports,
  generating code)
- Using tool connectors (Slack, Jira, GitHub, etc.) when
  configured on the step
- Browser automation via `type: browser` steps (Playwright
  or computer_use mode)

RULES FOR WEB-DEPENDENT WORKFLOWS:
1. If a workflow needs social media data, review data, or
   search results - require the data as INPUT (text or JSON),
   not as something the agent fetches
2. For data that requires external collection, add a note in
   the description: "Provide pre-collected data from [source].
   Use external tools like Brandwatch, Mention, Google Alerts,
   or social listening APIs to collect data."
3. For simple URL fetching (single page), instruct the agent:
   "Use curl -s -L <url> to fetch the page content"
4. For tasks needing JavaScript rendering or multi-page
   navigation, use `type: browser` with appropriate
   browser_config
5. NEVER write prompts for standard steps that ask the agent
   to "search the web", "browse social media", "check review
   sites", or "crawl a website"
5. Prefer workflows where the user provides all data as input
   and the agent does analysis, transformation, and writing

## Rules
1. Every workflow MUST have input_schema with required and
   properties
2. Use kebab-case for workflow name and step IDs
3. First step should have no depends_on
4. Steps that run in parallel share the same depends_on
5. Use descriptive prompts that reference inputs and previous
   step outputs
6. Output ONLY valid YAML - no markdown fencing, no
   explanations
7. Choose appropriate models: sonnet for complex tasks, haiku
   for simple formatting
8. Never generate prompts that expect web browsing, social
   media access, or multi-page crawling
9. Use the correct step type for the task - prefer http over
   standard for API calls, code for data processing,
   condition/classify for routing
10. When a workflow needs external services (Slack, Jira,
    etc.), add the tool connector to the step's tools list

## Examples

{examples}

{user_workflows_section}

Generate a complete, valid workflow YAML based on the user's description.
Output ONLY the YAML content, nothing else."""


# ---------------------------------------------------------------------------
# SLO-aware quality tier routing
# ---------------------------------------------------------------------------

# Quality requirements per advisor purpose.
# Higher tier = more capable (expensive) model preferred.
ADVISOR_QUALITY_TIERS: dict[str, str] = {
    "generation": "high",    # Workflow generation needs best quality
    "chat": "high",          # Interactive chat needs good responses
    "explain": "medium",     # Error explanation - good enough is fine
    "evolution": "medium",   # Prompt mutation - creative but not critical
    "judge": "low",          # Quality scoring - just needs a number 0-1
}

# Model capability ranking per provider (tier -> model ID).
# "high" = best available, "medium" = balanced, "low" = cheapest.
ADVISOR_MODEL_TIERS: dict[str, dict[str, str]] = {
    "anthropic": {
        "high": "claude-sonnet-4-20250514",
        "medium": "claude-haiku-4-20250514",
        "low": "claude-haiku-4-20250514",
    },
    "mistral": {
        "high": "mistral-large-latest",
        "medium": "mistral-small-latest",
        "low": "mistral-small-latest",
    },
    "openai": {
        "high": "gpt-4o",
        "medium": "gpt-4o-mini",
        "low": "gpt-4o-mini",
    },
    "ollama": {
        "high": "llama3.2",
        "medium": "llama3.2",
        "low": "llama3.2",
    },
    "omlx": {
        "high": "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        "medium": "mlx-community/Mistral-Small-24B-Instruct-2501-4bit",
        "low": "mlx-community/gemma-3-27b-it-qat-4bit",
    },
    "google": {
        "high": "google/gemini-2.5-pro",
        "medium": "google/gemini-2.5-pro",
        "low": "google/gemini-2.5-pro",
    },
    "minimax": {
        "high": "MiniMax-M2.5",
        "medium": "MiniMax-M2.5",
        "low": "MiniMax-M2.5",
    },
}


def _resolve_model_for_tier(provider_name: str, quality_tier: str) -> str | None:
    """Return the model ID for *provider_name* at *quality_tier*, or None if unknown.

    When the provider has no tier map defined, returns None so the caller can
    fall back to the provider's default model from ``_PROVIDER_CONFIGS``.
    """
    tier_map = ADVISOR_MODEL_TIERS.get(provider_name)
    if tier_map is None:
        return None
    return tier_map.get(quality_tier)


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

# Provider configuration - configurable via env vars
_PROVIDER_CONFIGS = {
    "anthropic": {
        "api_url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
        "region": "us",
        "headers_fn": lambda key: {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    },
    "openai": {
        "api_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
        "region": "us",
        "headers_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    },
    "mistral": {
        "api_url": "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-large-latest",
        "api_key_env": "MISTRAL_API_KEY",
        "region": "eu",
        "headers_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    },
    "ollama": {
        "api_url": "http://localhost:11434/v1/chat/completions",
        "model": "llama3.2",
        "api_key_env": "",  # Ollama doesn't need a key
        "region": "local",
        "headers_fn": lambda _: {"Content-Type": "application/json"},
    },
    "google": {
        "api_url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "google/gemini-2.5-pro",
        "api_key_env": "OPENROUTER_API_KEY",
        "region": "us",
        "headers_fn": lambda key: {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    },
    "minimax": {
        "api_url": "https://api.minimaxi.chat/v1/chat/completions",
        "model": "MiniMax-M2.5",
        "api_key_env": "MINIMAX_API_KEY",
        "region": "us",
        "headers_fn": lambda key: {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    },
    "omlx": {
        "api_url": "{omlx_base_url}/v1/chat/completions",
        "model": "mlx-community/Llama-4-Scout-17B-16E-Instruct-4bit",
        "api_key_env": "",  # no key needed for local inference
        "region": "local",
        "headers_fn": lambda _: {"Content-Type": "application/json"},
    },
}


def _get_advisor_config() -> dict:
    """Resolve advisor provider from environment variables.

    SANDCASTLE_ADVISOR_PROVIDER: anthropic (default) | openai | mistral | ollama | omlx
    SANDCASTLE_ADVISOR_MODEL: override model name

    When ``data_residency`` is set in settings, the chosen provider must
    operate in the required region. If it doesn't, a ``ValueError`` is raised
    with a clear message listing the compliant providers.
    """
    import os

    from sandcastle.config import settings

    provider = os.environ.get("SANDCASTLE_ADVISOR_PROVIDER", "anthropic").lower()
    if provider not in _PROVIDER_CONFIGS:
        provider = "anthropic"

    config = dict(_PROVIDER_CONFIGS[provider])

    # Resolve dynamic base URL for oMLX provider
    if "{omlx_base_url}" in config.get("api_url", ""):
        config["api_url"] = config["api_url"].format(
            omlx_base_url=settings.omlx_base_url.rstrip("/")
        )

    model_override = os.environ.get("SANDCASTLE_ADVISOR_MODEL", "")
    if model_override:
        config["model"] = model_override

    # Enforce data residency constraint
    residency = settings.data_residency
    if residency:
        provider_region = config.get("region", "us")
        if provider_region != residency:
            # Build list of compliant providers for the error message
            compliant = [
                name for name, cfg in _PROVIDER_CONFIGS.items()
                if cfg.get("region", "us") == residency
            ]
            raise ValueError(
                f"Data residency mode '{residency}' is active but advisor provider "
                f"'{provider}' runs in region '{provider_region}'. "
                f"Use a provider in the '{residency}' region. "
                f"Compliant providers: {', '.join(compliant) or 'none configured'}. "
                f"Set SANDCASTLE_ADVISOR_PROVIDER to one of these."
            )

    return config


def _resolve_api_key() -> str:
    """Resolve API key from env or settings, respecting provider config."""
    import os

    cfg = _get_advisor_config()
    key_env = cfg.get("api_key_env", "ANTHROPIC_API_KEY")

    # Ollama doesn't need a key
    if not key_env:
        return "ollama-no-key"

    api_key = os.environ.get(key_env, "")
    if not api_key:
        # Use the provider-generic attr_map lookup so each provider only gets
        # its own key - never fall through to anthropic_api_key for mistral etc.
        api_key = _resolve_api_key_for_provider(_resolve_provider_name())
        # _resolve_api_key_for_provider returns "no-key-required" for keyless
        # providers and "" when no key is found - normalise "no-key-required"
        # to a truthy sentinel so callers can check `if not api_key`.
        if api_key == "no-key-required":
            api_key = "ollama-no-key"
    return api_key


def _is_anthropic_provider() -> bool:
    """Check if the current provider uses Anthropic-format API."""
    cfg = _get_advisor_config()
    return cfg.get("api_key_env") == "ANTHROPIC_API_KEY"


def _build_request_body(
    model: str,
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    *,
    is_anthropic: bool | None = None,
) -> dict:
    """Build provider-specific request body.

    When *is_anthropic* is None the current global provider is inspected.
    Pass an explicit bool when building a body for a specific provider in the
    failover loop to avoid touching global state.
    """
    if is_anthropic is None:
        is_anthropic = _is_anthropic_provider()
    if is_anthropic:
        return {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
    # OpenAI/Ollama-compatible format
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}] + messages,
    }


def _parse_response_text(data: dict, *, is_anthropic: bool | None = None) -> str:
    """Extract text from provider-specific response format.

    When *is_anthropic* is None the current global provider is inspected.
    Pass an explicit bool when parsing a response for a specific provider in
    the failover loop to avoid touching global state.
    """
    if is_anthropic is None:
        is_anthropic = _is_anthropic_provider()
    if is_anthropic:
        return data["content"][0]["text"]
    # OpenAI/Ollama format
    return data["choices"][0]["message"]["content"]


def _get_api_url() -> str:
    """Get API URL from advisor config."""
    return _get_advisor_config().get("api_url", _API_URL)


def _get_model() -> str:
    """Get model from advisor config."""
    return _get_advisor_config().get("model", _MODEL)


def _get_headers(api_key: str) -> dict:
    """Get request headers from advisor config."""
    cfg = _get_advisor_config()
    headers_fn = cfg.get("headers_fn")
    if headers_fn:
        return headers_fn(api_key)
    return {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


# Defaults (used when no env override)
_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 4096
_TIMEOUT = 60


def _resolve_provider_name() -> str:
    """Return the current advisor provider name (e.g. 'anthropic', 'mistral')."""
    import os
    provider = os.environ.get("SANDCASTLE_ADVISOR_PROVIDER", "anthropic").lower()
    if provider not in _PROVIDER_CONFIGS:
        return "anthropic"
    return provider


def _resolve_api_key_for_provider(provider_name: str) -> str:
    """Resolve the API key for a specific provider name.

    Returns an empty string when no key is available.
    """
    import os

    cfg = _PROVIDER_CONFIGS.get(provider_name, {})
    key_env = cfg.get("api_key_env", "")

    # Providers that do not need a key (e.g. Ollama)
    if not key_env:
        return "no-key-required"

    api_key = os.environ.get(key_env, "")
    if not api_key:
        from sandcastle.config import settings

        attr_map = {
            "ANTHROPIC_API_KEY": "anthropic_api_key",
            "OPENAI_API_KEY": "openai_api_key",
            "MISTRAL_API_KEY": "mistral_api_key",
            "OPENROUTER_API_KEY": "openrouter_api_key",
            "MINIMAX_API_KEY": "minimax_api_key",
        }
        attr = attr_map.get(key_env)
        if attr:
            api_key = getattr(settings, attr, "") or ""
    return api_key


def _build_providers_to_try(primary: str, residency: str) -> list[str]:
    """Return an ordered list of providers to attempt, starting with *primary*.

    Fallback providers are added when they have a configured API key and
    satisfy the *residency* constraint (empty = no constraint).

    Local providers (Ollama, oMLX) are always included as fallbacks even
    without a key (they need no key) but only when residency allows it
    ("local" or "").
    """
    providers_to_try: list[str] = [primary]
    for name, cfg in _PROVIDER_CONFIGS.items():
        if name == primary:
            continue
        provider_region = cfg.get("region", "us")
        # Respect data residency
        if residency and provider_region != residency:
            continue
        # Check key availability
        key = _resolve_api_key_for_provider(name)
        if not key:
            continue
        providers_to_try.append(name)
    return providers_to_try


async def _call_advisor_llm(
    system: str,
    user: str,
    max_tokens: int = 2048,
    *,
    purpose: str = "generation",
    run_id: str | None = None,
) -> str:
    """Call the configured advisor LLM with automatic failover across providers.

    On rate-limit (429), server error (5xx), or network failure the call is
    transparently retried with the next available provider. The user never
    sees the error; the audit log records which provider was ultimately used
    and whether a failover occurred.

    Only 4xx client errors other than 429 are not retried (they indicate a
    problem with the request itself, not the provider).

    Args:
        system: System prompt.
        user: User message.
        max_tokens: Maximum tokens in response.
        purpose: Audit purpose tag - "generation", "evolution", "judge", "explain".
        run_id: Optional run ID to associate with the audit event.

    Returns:
        The response text string from the first provider that succeeds.

    Raises:
        RuntimeError: When all available providers have been exhausted.
        httpx.HTTPStatusError: When a non-retryable 4xx error is returned by
            any provider.
    """
    import os as _os

    from sandcastle.config import settings

    primary = _resolve_provider_name()
    residency = settings.data_residency
    providers_to_try = _build_providers_to_try(primary, residency)

    # Determine quality tier from purpose
    quality_mode = getattr(settings, "advisor_quality_mode", "auto")
    if quality_mode == "always_best":
        quality_tier = "high"
    elif quality_mode == "always_cheapest":
        quality_tier = "low"
    else:  # "auto" (default)
        quality_tier = ADVISOR_QUALITY_TIERS.get(purpose, "high")

    # Explicit model override wins over SLO tier selection
    model_override = _os.environ.get("SANDCASTLE_ADVISOR_MODEL", "")

    last_error: BaseException | None = None
    failover_provider: str | None = None  # the provider that ultimately succeeded
    failover_reason: str | None = None    # error that caused the failover

    for i, provider_name in enumerate(providers_to_try):
        cfg = dict(_PROVIDER_CONFIGS[provider_name])
        api_url = cfg["api_url"]
        # Resolve dynamic base URL for oMLX provider
        if "{omlx_base_url}" in api_url:
            from sandcastle.config import settings as _settings
            api_url = api_url.format(
                omlx_base_url=_settings.omlx_base_url.rstrip("/")
            )
        provider_region = cfg.get("region", "us")
        is_anthropic = cfg.get("api_key_env") == "ANTHROPIC_API_KEY"

        # Select model: explicit override > SLO tier > provider default
        if model_override:
            model = model_override
            model_selected_by = "user_override"
        else:
            tier_model = _resolve_model_for_tier(provider_name, quality_tier)
            if tier_model:
                model = tier_model
                model_selected_by = "slo_routing"
            else:
                model = cfg["model"]
                model_selected_by = "default"

        api_key = _resolve_api_key_for_provider(provider_name)
        headers = cfg["headers_fn"](api_key)
        body = _build_request_body(
            model, system, [{"role": "user", "content": user}], max_tokens,
            is_anthropic=is_anthropic,
        )

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(api_url, json=body, headers=headers)
                resp.raise_for_status()
                result_text = _parse_response_text(resp.json(), is_anthropic=is_anthropic)

            # Success - record failover info if we switched providers
            if i > 0:
                failover_provider = provider_name
                logger.info(
                    "Advisor failover succeeded: %s -> %s (attempt %d, purpose=%s)",
                    primary,
                    provider_name,
                    i + 1,
                    purpose,
                )

            break  # Exit loop on success

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            last_error = exc
            if status == 429:
                logger.warning(
                    "Advisor '%s' rate-limited (429), trying next provider...",
                    provider_name,
                )
                # Record the first error that caused us to leave the primary provider
                if failover_reason is None and len(providers_to_try) > i + 1:
                    failover_reason = f"429 rate-limit from {provider_name}"
                continue
            elif status >= 500:
                logger.warning(
                    "Advisor '%s' server error (%d), trying next provider...",
                    provider_name,
                    status,
                )
                if failover_reason is None and len(providers_to_try) > i + 1:
                    failover_reason = f"{status} server error from {provider_name}"
                continue
            else:
                # 4xx client errors (except 429) are not retryable
                raise

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            last_error = exc
            logger.warning(
                "Advisor '%s' unreachable (%s), trying next provider...",
                provider_name,
                type(exc).__name__,
            )
            if failover_reason is None and len(providers_to_try) > i + 1:
                failover_reason = f"{type(exc).__name__} from {provider_name}"
            continue

    else:
        # Loop exhausted without a break - all providers failed
        raise RuntimeError(
            f"All advisor providers failed. Tried: {', '.join(providers_to_try)}. "
            f"Last error: {last_error}"
        )

    # Emit provider audit event (best-effort - never block generation)
    try:
        from sandcastle.models.db import async_session
        from sandcastle.engine.audit import append_audit_event
        from sandcastle.engine.providers import PROVIDER_REGISTRY

        # Rough cost estimate based on token counts (approximation)
        input_tokens_approx = len(user) // 4 + len(system) // 4
        output_tokens_approx = len(result_text) // 4
        cost_estimate = 0.0
        for info in PROVIDER_REGISTRY.values():
            if info.provider == provider_name or provider_name in info.provider:
                cost_estimate = (
                    info.input_price_per_m * input_tokens_approx / 1_000_000
                    + info.output_price_per_m * output_tokens_approx / 1_000_000
                )
                break

        audit_payload = {
            "provider": provider_name,
            "model": model,
            "region": provider_region,
            "purpose": purpose,
            "quality_tier": quality_tier,
            "model_selected_by": model_selected_by,
            "cost_estimate_usd": round(cost_estimate, 6),
            "attempt": i + 1,
            "failover_from": primary if failover_provider is not None else None,
            "failover_reason": failover_reason if failover_provider is not None else None,
        }
        async with async_session() as session:
            await append_audit_event(
                session,
                event_type="advisor.llm_call",
                run_id=run_id,
                actor_id=f"advisor:{provider_name}",
                payload=audit_payload,
            )
            await session.commit()
    except Exception:
        pass  # Never break generation for audit

    return result_text


async def generate_workflow(
    description: str,
    *,
    refine_from: str | None = None,
    refine_instruction: str | None = None,
) -> GenerateResult:
    """Generate a workflow YAML from a natural language description.

    Args:
        description: What the workflow should do.
        refine_from: Existing YAML to refine.
        refine_instruction: What to change in the existing YAML.

    Returns:
        GenerateResult with the generated YAML and metadata.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set.
        httpx.HTTPStatusError: If the Anthropic API returns an error.
    """
    api_key = _resolve_api_key()
    if not api_key:
        raise ValueError(
            "API key is required for workflow generation. "
            "Set ANTHROPIC_API_KEY (or provider key) in your .env file or environment."
        )

    # Template matching: find a similar template before generation
    similar_template: str | None = None
    try:
        similar_template = _find_similar_template(description)
    except Exception:
        pass  # Never block generation for template matching

    # Build system prompt with user's recent workflows for style reference
    system_prompt = _build_system_prompt(include_user_workflows=True)

    # Build user message
    if refine_from and refine_instruction:
        user_msg = (
            f"Here is an existing workflow YAML:\n\n{refine_from}\n\n"
            f"Please modify it as follows: {refine_instruction}\n\n"
            f"Original description: {description}"
        )
    else:
        user_msg = description

    raw_text = await _call_advisor_llm(
        system=system_prompt,
        user=user_msg,
        max_tokens=_MAX_TOKENS,
        purpose="generation",
    )

    # Strip markdown fencing if present
    yaml_content = _strip_fencing(raw_text)

    # Validate and auto-fix via LLM if validation errors are found
    (
        yaml_content, name, description, steps_count,
        validation_errors, input_schema, fix_attempts,
    ) = await _attempt_fix_loop(yaml_content, system_prompt)

    result = GenerateResult(
        yaml_content=yaml_content,
        name=name,
        description=description,
        steps_count=steps_count,
        validation_errors=validation_errors,
        input_schema=input_schema,
        fix_attempts=fix_attempts,
        similar_template=similar_template,
    )

    return result


def _strip_fencing(text: str) -> str:
    """Remove markdown code fencing from generated YAML."""
    text = text.strip()
    # Remove ```yaml ... ``` or ``` ... ```
    m = re.match(r"^```(?:ya?ml)?\s*\n(.*?)```\s*$", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text


# ---------------------------------------------------------------------------
# Generate-validate-fix loop
# ---------------------------------------------------------------------------

_MAX_FIX_ATTEMPTS = 2

_FIX_PROMPT_TEMPLATE = """\
The workflow YAML you generated has these validation errors:
{error_list}

Here is the YAML:
{generated_yaml}

Fix ALL validation errors and return ONLY the corrected YAML. Do not add explanations."""


async def _attempt_fix_loop(
    yaml_content: str,
    system_prompt: str,
    max_attempts: int = _MAX_FIX_ATTEMPTS,
) -> tuple[str, str, str, int, list[str], dict | None, int]:
    """Validate YAML and auto-fix via LLM if validation errors are found.

    Returns a tuple of (yaml_content, name, description, steps_count,
    validation_errors, input_schema, fix_attempts).

    Only retries for validation errors - YAML parse errors are not retried
    because the output is too broken for a targeted fix prompt.
    """
    fix_attempts = 0

    for attempt in range(max_attempts + 1):  # 0 = initial, 1..N = fix attempts
        try:
            wf = parse_yaml_string(yaml_content)
            errors = validate(wf)
            if not errors:
                return (
                    yaml_content, wf.name, wf.description,
                    len(wf.steps), [], wf.input_schema, fix_attempts,
                )
            # Validation errors found - try to fix if we have attempts left
            if attempt < max_attempts:
                fix_attempts += 1
                logger.info(
                    "Auto-fix attempt %d for %d errors",
                    fix_attempts, len(errors),
                )
                error_list = "\n".join(f"- {e}" for e in errors)
                fix_prompt = _FIX_PROMPT_TEMPLATE.format(
                    error_list=error_list,
                    generated_yaml=yaml_content,
                )
                raw_fix = await _call_advisor_llm(
                    system=system_prompt,
                    user=fix_prompt,
                    max_tokens=_MAX_TOKENS,
                    purpose="generation",
                )
                yaml_content = _strip_fencing(raw_fix)
            else:
                # Out of attempts - return best result with remaining errors
                return (
                    yaml_content, wf.name, wf.description,
                    len(wf.steps), errors, wf.input_schema, fix_attempts,
                )
        except Exception as exc:
            # YAML parse error - do not retry, return immediately
            return (yaml_content, "", "", 0, [f"YAML parse error: {exc}"], None, fix_attempts)

    # Should not reach here, but safety fallback
    return (yaml_content, "", "", 0, ["Unknown error during fix loop"], None, fix_attempts)


# ---------------------------------------------------------------------------
# Chat-based generation (multi-turn)
# ---------------------------------------------------------------------------


def _build_chat_system_prompt() -> str:
    """Build the system prompt for multi-turn chat-based generation."""
    models = ", ".join(sorted(KNOWN_MODELS))
    examples = _load_example_templates()
    tool_connectors = _load_tool_names()

    return f"""\
You are a workflow design assistant for Sandcastle, an AI agent orchestrator.
You help users create and modify workflow YAML definitions through conversation.

You respond in JSON format with one of two modes:

MODE 1 - QUESTIONS (when you need more info):
{{"mode": "questions", "message": "Your 2-4 clarifying questions as natural text"}}

MODE 2 - YAML (when you have enough info to generate/update):
{{"mode": "yaml", "message": "Brief explanation of what was
generated/changed", "yaml": "<complete valid YAML>"}}

Decision rules:
- First user message, no existing workflow: ask 2-4 relevant questions (MODE 1)
- User says "just generate" / "go ahead" / "skip questions": produce YAML immediately (MODE 2)
- User answered your questions: produce YAML (MODE 2)
- Existing workflow provided + clear instruction: update YAML (MODE 2)
- Existing workflow provided + vague request: ask what to change (MODE 1)
- After YAML already generated: new message = refinement -> updated YAML (MODE 2)

## YAML Schema

A workflow YAML has these top-level fields:
- name: kebab-case identifier (required)
- description: short description (required)
- default_model: model name (optional, default: sonnet)
- default_max_turns: integer (optional, default: 10)
- default_timeout: seconds (optional, default: 300)
- default_tools: list of tool connector names for all steps (optional)
- input_schema: JSON Schema for user inputs (required)
  - required: list of required field names
  - properties: object with field definitions (type, description)
- steps: list of step objects (required)

Each step has:
- id: unique kebab-case identifier (required)
- prompt: the instruction for the agent (required for most types, see Step Types)
- depends_on: list of step IDs this step waits for (optional)
- model: model name (optional, overrides default_model)
- max_turns: integer (optional)
- type: step type string (optional, default: "standard")
- tools: list of tool connector names for this step (optional, e.g. ["slack", "jira"])

{_STEP_TYPES_DOC}

## Available Tool Connectors

Steps can use external tool connectors via the `tools` field. Use `tools: [name]` on a step
or `default_tools: [name]` at workflow level. Named connections use colon syntax: "tool:connection".

{tool_connectors}

## Available Models
{models}
Always use these short names - NEVER use full API model IDs.

## Variable Syntax
- {{input.X}} - reference user input field X
- {{steps.STEP_ID.output}} - reference output of a previous step

## Sandbox Execution Environment

Workflows run inside sandboxed environments (E2B cloud sandbox, Docker, or local subprocess).
The agent has access to ONLY these tools: Bash (with curl), Read, Write, Edit, Glob, Grep.
Steps can also use external tool connectors (see Available Tool Connectors above).

CRITICAL LIMITATIONS for standard steps - the agent CANNOT:
- Browse the web or render JavaScript - no browser is available in standard steps
- Use WebSearch or WebFetch - these tools do NOT exist in the sandbox
- Access social media platforms (Twitter/X, LinkedIn, Reddit,
  Instagram) - they require OAuth/API keys
- Access review platforms (G2, Trustpilot, Capterra, App
  Store) - they require JavaScript rendering
- Crawl multiple pages of a website - only simple
  single-page curl requests work
- Use Google/Bing search - search engines block automated
  curl requests

NOTE: For tasks requiring a real browser, use the `browser`
step type instead.

WHAT WORKS:
- Fetching simple HTML pages via curl (news sites, company
  homepages, documentation)
- Calling public REST APIs with JSON responses
- Processing data provided as user input (JSON, CSV, text
  pasted by user)
- Using the agent's built-in knowledge for analysis, writing,
  reasoning, and planning
- File operations (reading, writing, creating reports,
  generating code)
- Using tool connectors (Slack, Jira, GitHub, etc.) when
  configured on the step
- Browser automation via `type: browser` steps (Playwright
  or computer_use mode)

RULES FOR WEB-DEPENDENT WORKFLOWS:
1. If a workflow needs social media data, review data, or
   search results - require the data as INPUT
2. For tasks needing JavaScript rendering or multi-page
   navigation, use `type: browser`
3. NEVER write prompts for standard steps that ask the agent
   to "search the web", "browse social media", or "crawl a
   website"
4. Prefer workflows where the user provides all data as input
   and the agent does analysis

## Rules
1. Every workflow MUST have input_schema with required and
   properties
2. Use kebab-case for workflow name and step IDs
3. First step should have no depends_on
4. Steps that run in parallel share the same depends_on
5. Use descriptive prompts that reference inputs and previous
   step outputs
6. Choose appropriate models: sonnet for complex tasks, haiku
   for simple formatting
7. Use the correct step type for the task - prefer http over
   standard for API calls, code for data processing
8. When a workflow needs external services (Slack, Jira,
   etc.), add the tool connector to the step's tools list

## Examples

{examples}

IMPORTANT: Output ONLY a valid JSON object. No markdown fencing, no extra text."""


def _extract_latest_yaml(messages: list[dict]) -> str | None:
    """Scan conversation history for the last assistant message containing YAML.

    Assistant messages in chat mode are JSON with {"mode": "yaml", "yaml": "..."}.
    We parse each assistant message to find the most recent YAML output, so that
    refinement requests always operate on the latest generated version.
    """
    import json as _json

    latest = None
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        try:
            parsed = _json.loads(content)
            if parsed.get("mode") == "yaml" and parsed.get("yaml"):
                latest = _strip_fencing(parsed["yaml"])
        except (ValueError, TypeError, AttributeError):
            # Not JSON or unexpected structure - skip
            continue
    return latest


async def generate_chat(
    messages: list[dict],
    *,
    existing_yaml: str | None = None,
) -> dict:
    """Multi-turn chat-based workflow generation.

    Args:
        messages: Conversation history [{role, content}, ...].
        existing_yaml: Existing workflow YAML for edit mode.

    Returns:
        Dict with: mode, message, yaml_content?, name?, steps_count?,
        validation_errors?, input_schema?
    """
    import json

    api_key = _resolve_api_key()
    if not api_key:
        raise ValueError(
            "API key is required for workflow generation. "
            "Set ANTHROPIC_API_KEY (or provider key) in your .env file or environment."
        )

    system_prompt = _build_chat_system_prompt()

    # Find the latest YAML from a previous assistant message in the conversation.
    # This fixes context accumulation: when the user does multiple refinements,
    # we use the most recently generated YAML as context (not the original).
    latest_yaml = _extract_latest_yaml(messages)
    effective_yaml = latest_yaml or existing_yaml

    # Prepare messages - inject YAML context before the last user message
    api_messages = []
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role", "user") == "user":
            last_user_idx = i
            break

    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        # Inject YAML context: if we have a latest_yaml from conversation history,
        # inject it before the last user message (refinement). Otherwise fall back
        # to injecting existing_yaml into the first user message.
        if effective_yaml and role == "user":
            if latest_yaml and i == last_user_idx:
                content = f"[Current workflow YAML]\n{effective_yaml}\n\n[User request]\n{content}"
            elif not latest_yaml and i == 0:
                content = f"[Existing workflow]\n{effective_yaml}\n\n[User request]\n{content}"
        api_messages.append({"role": role, "content": content})

    # Build the full user message from the api_messages list (last user message with context)
    # _call_advisor_llm takes a single user string, so we pass the last message content.
    # For multi-turn, the last message already has injected YAML context from the loop above.
    last_user_content = ""
    for msg in reversed(api_messages):
        if msg.get("role") == "user":
            last_user_content = msg["content"]
            break

    raw_text = await _call_advisor_llm(
        system=system_prompt,
        user=last_user_content,
        max_tokens=_MAX_TOKENS,
        purpose="chat",
    )

    # Parse JSON response
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Try to extract JSON from the response
        json_match = re.search(r"\{[\s\S]*\}", raw_text)
        if json_match:
            parsed = json.loads(json_match.group())
        else:
            return {"mode": "questions", "message": raw_text}

    mode = parsed.get("mode", "questions")
    message = parsed.get("message", "")

    if mode == "yaml":
        yaml_content = _strip_fencing(parsed.get("yaml", ""))

        # Validate and auto-fix via LLM if validation errors are found
        chat_system = _build_chat_system_prompt()
        (
            yaml_content, name, description, steps_count,
            validation_errors, input_schema, fix_attempts,
        ) = await _attempt_fix_loop(yaml_content, chat_system)

        result: dict = {
            "mode": "yaml",
            "message": message,
            "yaml_content": yaml_content,
            "name": name,
            "description": description,
            "steps_count": steps_count,
            "validation_errors": validation_errors,
            "input_schema": input_schema,
            "fix_attempts": fix_attempts,
        }

        return result

    # mode == "questions" or unknown
    return {"mode": "questions", "message": message}


# ---------------------------------------------------------------------------
# Sync wrapper for CLI
# ---------------------------------------------------------------------------


def generate_workflow_sync(
    description: str,
    *,
    refine_from: str | None = None,
    refine_instruction: str | None = None,
) -> GenerateResult:
    """Synchronous wrapper around generate_workflow for CLI usage."""
    return asyncio.run(
        generate_workflow(
            description,
            refine_from=refine_from,
            refine_instruction=refine_instruction,
        )
    )


# ---------------------------------------------------------------------------
# Error Explainer - AI-powered step failure explanation
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = re.compile(
    r"(?i)"
    r"(?:bearer|basic)\s+[a-zA-Z0-9_\-\.=+/]{10,}"  # Bearer/Basic auth tokens
    r"|[\"']?(?:api[_-]?key|token|secret[_a-z]*|password|authorization|account_?key)[\"']?\s*[:=]\s*[\"']?(?!\[REDACTED\])\S{8,}"  # key=value, quoted or not
    r"|(?:sk|pk|ghp|gho|glpat|xox[bpsa]|eyJ)[a-zA-Z0-9_\-]{10,}"  # Known prefixes
    r"|(?:AKIA|ASIA)[A-Z0-9]{16}"  # AWS access keys
    r"|[a-zA-Z0-9+_.-]+://[^\s:]*:[^\s@]+@[^\s]+"  # Credential URLs (postgres://user:pass@host, redis://:pass@host)
    r"|[a-fA-F0-9]{32,64}(?=['\"\s,}:;\].]|$)"  # Long hex strings (case-insensitive, more delimiters + EOL)
)

_PEM_PATTERN = re.compile(
    r"-----BEGIN[A-Z \n]*PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END[A-Z \n]*PRIVATE KEY-----",
)


def _scrub_secrets(text: str) -> str:
    """Redact potential secrets/tokens from text before sending to external LLM."""
    text = _PEM_PATTERN.sub("[REDACTED-PEM-KEY]", text)
    return _SECRET_PATTERNS.sub("[REDACTED]", text)


_EXPLAIN_SYSTEM = """You are a workflow debugging assistant for Sandcastle,
an AI workflow orchestration platform. A user's workflow step has failed.
Your job is to explain the error in plain language and suggest a fix.

Respond ONLY with a valid JSON object (no markdown fencing):
{
  "summary": "One sentence plain-English explanation of what went wrong",
  "cause": "Technical root cause in 1-2 sentences",
  "fix": "Actionable fix suggestion in 1-3 sentences",
  "severity": "low|medium|high|critical"
}

Guidelines:
- Be concise and actionable
- If the error is a rate limit (429), suggest retry config or model routing
- If the error is auth-related, suggest checking credentials
- If the error is a timeout, suggest increasing timeout or simplifying the prompt
- If the error is a budget exceeded, suggest cheaper model or shorter prompt
- Reference Sandcastle-specific features (retry config, fallback, SLO, model_pool)
"""


async def explain_error(
    step_id: str,
    step_type: str,
    error: str,
    *,
    prompt: str = "",
    model: str = "",
    workflow_name: str = "",
) -> dict:
    """Explain a step failure using AI and suggest a fix.

    Args:
        step_id: Failed step identifier.
        step_type: Step type (llm, http, code, etc.).
        error: Raw error message from the step.
        prompt: Step prompt (truncated for context).
        model: Model used by the step.
        workflow_name: Parent workflow name.

    Returns:
        Dict with: summary, cause, fix, severity.
    """
    import json

    api_key = _resolve_api_key()
    if not api_key:
        return {
            "summary": error[:200],
            "cause": "Unable to generate AI explanation (API key not set)",
            "fix": "Set ANTHROPIC_API_KEY (or provider key) to enable AI error explanations",
            "severity": "medium",
        }

    # Scrub secrets from error and prompt before sending to external LLM
    scrubbed_error = _scrub_secrets(error[:2000])
    scrubbed_prompt = _scrub_secrets(prompt[:500]) if prompt else "N/A"

    user_msg = f"""Workflow: {workflow_name or 'unknown'}
Step: {step_id} (type: {step_type})
Model: {model or 'N/A'}
Prompt (first 500 chars): {scrubbed_prompt}

ERROR:
{scrubbed_error}"""

    try:
        # SLO routing: purpose="explain" -> medium tier model
        text = await _call_advisor_llm(
            system=_EXPLAIN_SYSTEM,
            user=user_msg,
            max_tokens=512,
            purpose="explain",
        )
        return json.loads(text)
    except Exception:
        # Fallback to basic explanation
        return {
            "summary": error[:200],
            "cause": "AI explanation unavailable",
            "fix": "Check the raw error message above for details",
            "severity": "medium",
        }
