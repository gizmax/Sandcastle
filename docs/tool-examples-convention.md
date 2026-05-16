# Tool Examples Convention

This document is the contract for connector authors registering tools with the
Sandcastle agent runtime. It lives next to the `tool_search` module and the
linter that enforces it.

## Why examples matter

Two numbers drove this convention:

- **Tool selection accuracy: 49 percent -> 74 percent** once tool search was
  added so the agent only sees a relevant subset.
- **Parameter-shape accuracy: 72 percent -> 90 percent** once each tool
  carried 1 to 5 worked examples.

With 62 connectors and several tools each, Sandcastle is in the regime where
both effects compound. Skipping examples is the single fastest way to make
your connector look broken in evals.

## What every tool must declare

Every `ToolDefinition` registered with `default_registry` must satisfy
`validate_tool`:

1. **`name`** - stable, snake_case, unique inside the registry.
2. **`description`** - at least 20 characters. Lead with the verb. State what
   the tool does, not how. Mention the most useful inputs.
3. **`parameters`** - a JSON Schema (Draft 2020-12) for the input object.
4. **`examples`** - between 1 and 5 entries. Each entry is a dict shaped
   `{"input": {...}, "output": {...}}`. The `input` is validated against
   `parameters` at registration time; the `output` is illustrative and is not
   schema-checked but must be a dict so it serialises cleanly.
5. **`tags`** - optional but encouraged. The search ranker scores tag hits
   3x higher than description hits.
6. **`defer_loading`** - default `False`. Set to `True` only for rare,
   expensive, or domain-specialised tools that should not occupy the eager
   prompt budget. The agent reaches lazy tools via explicit search.

## When to defer loading

Reach for `defer_loading=True` when **any** of the following hold:

- The tool is used in fewer than 1 percent of runs across the connector.
- The tool wraps an expensive remote operation that requires careful framing.
- The tool is part of a specialised pack (forensics, legacy migration, niche
  protocol) that most agents should never see.

If in doubt, leave it eager. Hot tools are cheap; missed selections are not.

## Example YAML connector definition

```yaml
name: pdf
version: 1.0.0
tools:
  - name: pdf_extract_text
    description: Extract plain text from a PDF, preserving reading order.
    tags: [pdf, ocr, document]
    parameters:
      type: object
      properties:
        path:
          type: string
          description: Local filesystem path to the PDF.
        pages:
          type: string
          description: Optional page range like "1-5" or "3,7".
      required: [path]
    examples:
      - input: {path: "/tmp/contract.pdf"}
        output: {text: "Master Services Agreement ...", pages: 12}
      - input: {path: "/tmp/report.pdf", pages: "1-2"}
        output: {text: "Executive Summary ...", pages: 2}

  - name: pdf_redact_pii
    description: Redact PII spans from a PDF and return a clean copy path.
    tags: [pdf, privacy, redaction]
    defer_loading: true
    parameters:
      type: object
      properties:
        path: {type: string}
        modes:
          type: array
          items: {type: string, enum: [email, phone, ssn, name]}
      required: [path]
    examples:
      - input: {path: "/tmp/case.pdf", modes: ["email", "phone"]}
        output: {redacted_path: "/tmp/case.redacted.pdf", spans: 7}
```

## Linter command

Run the linter before publishing a connector. It calls `validate_tool` on
every entry and prints aggregated errors:

```bash
sandcastle tools validate
```

A passing run prints `OK` and exits 0. Any tool with errors fails the
command, blocking publish.

## Search and formatting at runtime

The agent runtime uses three calls on `default_registry`:

- `hot_tools()` for the eager system prompt.
- `search(query, limit=5)` when the agent asks for "more tools like X".
- `format_for_agent(tools)` to emit the Anthropic-compatible shape
  `{name, description, input_schema, examples?}`.

Authors do not need to call these directly; just register a well-formed
`ToolDefinition` and the runtime takes care of the rest.
