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
# Step types documentation (all 16 types)
# ---------------------------------------------------------------------------

_STEP_TYPES_DOC = """\
## Step Types

Each step has a `type` field (default: "standard"). Here are all 16 supported types:

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
Browser automation step - runs a headless browser in the sandbox for web scraping, form filling, or UI testing.
Supports two modes: "playwright" (agent writes Playwright scripts) and "computer_use" (Claude controls the browser via screenshots).
Fields: prompt (task description), browser_config: {mode, start_url, viewport_width, viewport_height, timeout_seconds, wait_after_action, screenshot_on_error, headless, credentials_env}
mode defaults to "playwright". Requires a prompt describing the browser task.

### sub_workflow (legacy)
Run another workflow as a sub-step with input/output mapping.
Fields: sub_workflow: {workflow, input_mapping, output_mapping, parallel_over, max_concurrent, timeout}

IMPORTANT: Types that do NOT need a prompt: http, code, condition, loop, race, sensor, transform, notify.
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
- Access social media platforms (Twitter/X, LinkedIn, Reddit, Instagram) - they require OAuth/API keys
- Access review platforms (G2, Trustpilot, Capterra, App Store) - they require JavaScript rendering
- Crawl multiple pages of a website - only simple single-page curl requests work
- Use Google/Bing search - search engines block automated curl requests

NOTE: For tasks that require a real browser (JavaScript rendering, form filling, multi-page
navigation, scraping dynamic sites), use the `browser` step type instead of `standard`.
Browser steps run a headless Playwright browser in the sandbox.

WHAT WORKS:
- Fetching simple HTML pages via curl (news sites, company homepages, documentation)
- Calling public REST APIs with JSON responses
- Processing data provided as user input (JSON, CSV, text pasted by user)
- Using the agent's built-in knowledge for analysis, writing, reasoning, and planning
- File operations (reading, writing, creating reports, generating code)
- Using tool connectors (Slack, Jira, GitHub, etc.) when configured on the step
- Browser automation via `type: browser` steps (Playwright or computer_use mode)

RULES FOR WEB-DEPENDENT WORKFLOWS:
1. If a workflow needs social media data, review data, or search results - require the data as INPUT (text or JSON), not as something the agent fetches
2. For data that requires external collection, add a note in the description: "Provide pre-collected data from [source]. Use external tools like Brandwatch, Mention, Google Alerts, or social listening APIs to collect data."
3. For simple URL fetching (single page), instruct the agent: "Use curl -s -L <url> to fetch the page content"
4. For tasks needing JavaScript rendering or multi-page navigation, use `type: browser` with appropriate browser_config
5. NEVER write prompts for standard steps that ask the agent to "search the web", "browse social media", "check review sites", or "crawl a website"
5. Prefer workflows where the user provides all data as input and the agent does analysis, transformation, and writing

## Rules
1. Every workflow MUST have input_schema with required and properties
2. Use kebab-case for workflow name and step IDs
3. First step should have no depends_on
4. Steps that run in parallel share the same depends_on
5. Use descriptive prompts that reference inputs and previous step outputs
6. Output ONLY valid YAML - no markdown fencing, no explanations
7. Choose appropriate models: sonnet for complex tasks, haiku for simple formatting
8. Never generate prompts that expect web browsing, social media access, or multi-page crawling
9. Use the correct step type for the task - prefer http over standard for API calls, code for data processing, condition/classify for routing
10. When a workflow needs external services (Slack, Jira, etc.), add the tool connector to the step's tools list

## Examples

{examples}

Generate a complete, valid workflow YAML based on the user's description.
Output ONLY the YAML content, nothing else."""


# ---------------------------------------------------------------------------
# Core generation
# ---------------------------------------------------------------------------

_API_URL = "https://api.anthropic.com/v1/messages"
_MODEL = "claude-sonnet-4-20250514"
_MAX_TOKENS = 4096
_TIMEOUT = 60


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
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from sandcastle.config import settings
        api_key = settings.anthropic_api_key
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required for workflow generation. "
            "Set it in your .env file or environment."
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
            _API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": _MAX_TOKENS,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_msg}],
            },
        )
        resp.raise_for_status()

    data = resp.json()
    raw_text = data["content"][0]["text"]

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
{{"mode": "yaml", "message": "Brief explanation of what was generated/changed", "yaml": "<complete valid YAML>"}}

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
- Access social media platforms (Twitter/X, LinkedIn, Reddit, Instagram) - they require OAuth/API keys
- Access review platforms (G2, Trustpilot, Capterra, App Store) - they require JavaScript rendering
- Crawl multiple pages of a website - only simple single-page curl requests work
- Use Google/Bing search - search engines block automated curl requests

NOTE: For tasks requiring a real browser, use the `browser` step type instead.

WHAT WORKS:
- Fetching simple HTML pages via curl (news sites, company homepages, documentation)
- Calling public REST APIs with JSON responses
- Processing data provided as user input (JSON, CSV, text pasted by user)
- Using the agent's built-in knowledge for analysis, writing, reasoning, and planning
- File operations (reading, writing, creating reports, generating code)
- Using tool connectors (Slack, Jira, GitHub, etc.) when configured on the step
- Browser automation via `type: browser` steps (Playwright or computer_use mode)

RULES FOR WEB-DEPENDENT WORKFLOWS:
1. If a workflow needs social media data, review data, or search results - require the data as INPUT
2. For tasks needing JavaScript rendering or multi-page navigation, use `type: browser`
3. NEVER write prompts for standard steps that ask the agent to "search the web", "browse social media", or "crawl a website"
4. Prefer workflows where the user provides all data as input and the agent does analysis

## Rules
1. Every workflow MUST have input_schema with required and properties
2. Use kebab-case for workflow name and step IDs
3. First step should have no depends_on
4. Steps that run in parallel share the same depends_on
5. Use descriptive prompts that reference inputs and previous step outputs
6. Choose appropriate models: sonnet for complex tasks, haiku for simple formatting
7. Use the correct step type for the task - prefer http over standard for API calls, code for data processing
8. When a workflow needs external services (Slack, Jira, etc.), add the tool connector to the step's tools list

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
    import os

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        from sandcastle.config import settings
        api_key = settings.anthropic_api_key
    if not api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY is required for workflow generation. "
            "Set it in your .env file or environment."
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
                content = (
                    f"[Current workflow YAML]\n{effective_yaml}\n\n"
                    f"[User request]\n{content}"
                )
            elif not latest_yaml and i == 0:
                content = (
                    f"[Existing workflow]\n{effective_yaml}\n\n"
                    f"[User request]\n{content}"
                )
        api_messages.append({"role": role, "content": content})

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _API_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _MODEL,
                "max_tokens": _MAX_TOKENS,
                "system": system_prompt,
                "messages": api_messages,
            },
        )
        resp.raise_for_status()

    data = resp.json()
    raw_text = data["content"][0]["text"]

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
