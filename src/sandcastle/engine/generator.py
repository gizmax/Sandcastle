"""AI Workflow Generator - creates valid YAML workflows from natural language."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from sandcastle.engine.dag import parse_yaml_string, validate
from sandcastle.engine.providers import KNOWN_MODELS
from sandcastle.engine.tools.registry import TOOL_REGISTRY


@dataclass
class GenerateResult:
    """Result from the AI workflow generator."""

    yaml_content: str
    name: str = ""
    description: str = ""
    steps_count: int = 0
    validation_errors: list[str] = field(default_factory=list)
    input_schema: dict | None = None


# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------

_EXAMPLE_TEMPLATES = [
    "research_agent",
    "data_extractor",
    "email_campaign",
    "review_and_approve",
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


# ---------------------------------------------------------------------------
# Step types documentation (all 17 types)
# ---------------------------------------------------------------------------

_STEP_TYPES_DOC = """\
## Step Types

Each step has a `type` field (default: "standard"). Here are all 17 supported types:

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

### sub_workflow (legacy)
Run another workflow as a sub-step with input/output mapping.
Fields: sub_workflow: {workflow, input_mapping, output_mapping,
parallel_over, max_concurrent, timeout}

IMPORTANT: Types that do NOT need a prompt: http, code, condition,
loop, race, sensor, gate, transform, notify, composio.
All other types require a prompt field.
"""


def _load_tool_names() -> str:
    """Load available tool connector names and descriptions from the registry."""
    lines: list[str] = []
    # Group by category for readability
    categories: dict[str, list[tuple[str, str]]] = {}
    for name, tool in sorted(TOOL_REGISTRY.items()):
        cat = tool.category
        if cat not in categories:
            categories[cat] = []
        categories[cat].append((name, tool.description))

    for cat in sorted(categories):
        lines.append(f"**{cat.replace('_', ' ').title()}:**")
        for name, desc in categories[cat]:
            lines.append(f"- {name}: {desc}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    """Build the system prompt with schema docs, model list, and examples."""
    models = ", ".join(sorted(KNOWN_MODELS))
    examples = _load_example_templates()
    tool_connectors = _load_tool_names()

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

Generate a complete, valid workflow YAML based on the user's description.
Output ONLY the YAML content, nothing else."""


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

# Provider configuration - configurable via env vars
_PROVIDER_CONFIGS = {
    "anthropic": {
        "api_url": "https://api.anthropic.com/v1/messages",
        "model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
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
        "headers_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    },
    "mistral": {
        "api_url": "https://api.mistral.ai/v1/chat/completions",
        "model": "mistral-large-latest",
        "api_key_env": "MISTRAL_API_KEY",
        "headers_fn": lambda key: {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    },
    "ollama": {
        "api_url": "http://localhost:11434/v1/chat/completions",
        "model": "llama3.2",
        "api_key_env": "",  # Ollama doesn't need a key
        "headers_fn": lambda _: {"Content-Type": "application/json"},
    },
}


def _get_advisor_config() -> dict:
    """Resolve advisor provider from environment variables.

    SANDCASTLE_ADVISOR_PROVIDER: anthropic (default) | openai | ollama
    SANDCASTLE_ADVISOR_MODEL: override model name
    """
    import os

    provider = os.environ.get("SANDCASTLE_ADVISOR_PROVIDER", "anthropic").lower()
    if provider not in _PROVIDER_CONFIGS:
        provider = "anthropic"

    config = dict(_PROVIDER_CONFIGS[provider])
    model_override = os.environ.get("SANDCASTLE_ADVISOR_MODEL", "")
    if model_override:
        config["model"] = model_override

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
        from sandcastle.config import settings

        # Try provider-specific settings first
        if key_env == "OPENAI_API_KEY":
            api_key = getattr(settings, "openai_api_key", "") or ""
        if not api_key:
            api_key = settings.anthropic_api_key
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
) -> dict:
    """Build provider-specific request body."""
    if _is_anthropic_provider():
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


def _parse_response_text(data: dict) -> str:
    """Extract text from provider-specific response format."""
    if _is_anthropic_provider():
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


async def _call_advisor_llm(system: str, user: str, max_tokens: int = 2048) -> str:
    """Call the configured advisor LLM (any provider) with a system + user message.

    Uses the SANDCASTLE_ADVISOR_PROVIDER/MODEL env vars. Works with
    Anthropic, OpenAI, Mistral, and Ollama (local).

    Returns the response text string.
    """
    import httpx

    api_key = _resolve_api_key()
    api_url = _get_api_url()
    model = _get_model()
    headers = _get_headers(api_key)
    body = _build_request_body(model, system, [{"role": "user", "content": user}], max_tokens)

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(api_url, json=body, headers=headers)
        resp.raise_for_status()
        return _parse_response_text(resp.json())


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

    system_prompt = _build_system_prompt()

    # Build user message
    if refine_from and refine_instruction:
        user_msg = (
            f"Here is an existing workflow YAML:\n\n{refine_from}\n\n"
            f"Please modify it as follows: {refine_instruction}\n\n"
            f"Original description: {description}"
        )
    else:
        user_msg = description

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _get_api_url(),
            headers=_get_headers(api_key),
            json=_build_request_body(
                model=_get_model(),
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=_MAX_TOKENS,
            ),
        )
        resp.raise_for_status()

    data = resp.json()
    raw_text = _parse_response_text(data)

    # Strip markdown fencing if present
    yaml_content = _strip_fencing(raw_text)

    # Validate the generated YAML
    result = GenerateResult(yaml_content=yaml_content)
    try:
        wf = parse_yaml_string(yaml_content)
        result.name = wf.name
        result.description = wf.description
        result.steps_count = len(wf.steps)
        result.input_schema = wf.input_schema
        errors = validate(wf)
        result.validation_errors = errors
    except Exception as exc:
        result.validation_errors = [f"YAML parse error: {exc}"]

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

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _get_api_url(),
            headers=_get_headers(api_key),
            json=_build_request_body(
                model=_get_model(),
                system=system_prompt,
                messages=api_messages,
                max_tokens=_MAX_TOKENS,
            ),
        )
        resp.raise_for_status()

    data = resp.json()
    raw_text = _parse_response_text(data)

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
        result: dict = {
            "mode": "yaml",
            "message": message,
            "yaml_content": yaml_content,
        }

        # Validate the generated YAML
        try:
            wf = parse_yaml_string(yaml_content)
            result["name"] = wf.name
            result["description"] = wf.description
            result["steps_count"] = len(wf.steps)
            result["input_schema"] = wf.input_schema
            errors = validate(wf)
            result["validation_errors"] = errors
        except Exception as exc:
            result["name"] = ""
            result["steps_count"] = 0
            result["validation_errors"] = [f"YAML parse error: {exc}"]

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

    # Use a cheaper/faster model for explanations
    cfg = _get_advisor_config()
    explain_model = cfg.get("model", _MODEL)
    # Prefer haiku for Anthropic (cheaper), otherwise use configured model
    if cfg.get("api_key_env") == "ANTHROPIC_API_KEY":
        explain_model = "claude-haiku-4-5-20251001"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _get_api_url(),
                headers=_get_headers(api_key),
                json=_build_request_body(
                    model=explain_model,
                    system=_EXPLAIN_SYSTEM,
                    messages=[{"role": "user", "content": user_msg}],
                    max_tokens=512,
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            text = _parse_response_text(data)
            return json.loads(text)
    except Exception:
        # Fallback to basic explanation
        return {
            "summary": error[:200],
            "cause": "AI explanation unavailable",
            "fix": "Check the raw error message above for details",
            "severity": "medium",
        }
