# Memory MCP Server

`sandcastle.engine.memory_mcp_server` exposes Sandcastle's existing
mem0 + Qdrant memory layer over the Model Context Protocol.

## Why this exists

Anthropic's 2026-05-19 Managed Agents update shipped self-hosted sandboxes
but explicitly **disclaimed `memory_stores` support inside them**. Agents
running in your own infrastructure therefore lose Anthropic's native
memory tool the moment they leave the hosted runtime.

Sandcastle already has the memory plumbing wired up in
`src/sandcastle/engine/memory.py`:

- mem0 with an Anthropic-LLM adapter that strips `top_p` for Claude 4.5+
- fastembed embeddings (`BAAI/bge-small-en-v1.5`)
- Qdrant persistent vector store
- admission control, decay, enrichment, conflict detection

This module is a thin MCP wrapper around that stack so any self-hosted
sandbox agent can reach the same store through an MCP tunnel.

## Tools

| Tool             | Purpose                                  | Required input          |
| ---------------- | ---------------------------------------- | ----------------------- |
| `add`            | Store a memory                           | `text`, `user_id`       |
| `search`         | Semantic search by user                  | `query`, `user_id`      |
| `forget`         | GDPR right-to-be-forgotten (one row)     | `memory_id`             |
| `list_memories`  | Inventory all memories for a user        | `user_id` (limit <= 200)|

Each tool ships a JSON Schema input definition and 1-5 worked examples,
per the v0.32 tool-search convention in `docs/tool-examples-convention.md`.
`search` returns the exact shape Anthropic's Memory tool expects:

```json
{"results": [{"id": "mem_abc", "text": "...", "score": 0.91, "metadata": {}}]}
```

## Resources

- `sandcastle://memory/users` - list user_ids with at least one memory row
- `sandcastle://memory/health` - mem0 reachability + Qdrant import status

## Prompts

- `memory_qa(user_id, question)` - answer using only memories for that user

## Deployment via MCP tunnel

1. Start the server in the same network as the self-hosted sandbox:
   ```bash
   sandcastle memory-mcp serve --transport streamable-http --port 8765
   ```
   (or use `--transport stdio` when the sandbox spawns it directly).
2. Expose the port through your MCP tunnel of choice. Sandcastle ships
   `engine/mcp_tunnel.py`; any reverse proxy that speaks the MCP
   streamable HTTP profile works.
3. In the agent config, register the tunnel URL as an MCP server. The
   four tools will appear under the `sandcastle-memory` namespace.

The server is built to **start even when mem0 / Qdrant is missing**.
Each tool call lazily imports the memory module and raises a typed
`MemoryMCPError(code='memory_unavailable')` with a remediation hint
instead of failing at boot.

## GDPR endpoints

- `forget(memory_id)` - delete a single row. Idempotent: returns
  `{"deleted": false}` when the id is unknown.
- `list_memories(user_id)` - data-export contract. Pair it with `forget`
  in a loop to honour an erasure request for a specific user.

The shared admission-control + enrichment metadata travels with every
record so audit logs can prove provenance for each retained memory.

## Example workflow YAML

```yaml
name: support-agent-with-memory
description: Customer support reply that recalls prior interactions.
steps:
  - id: recall
    type: standard
    prompt: |
      Use the configured `sandcastle-memory` MCP server's `search` tool to
      find up to five memories for user:{input.customer_id} about:
      {input.message}
  - id: reply
    type: standard
    depends_on: [recall]
    prompt: |
      You are a polite support agent. Use the retrieved memories below to
      personalise the reply. Cite memory ids when relevant.
      Memories: {steps.recall.output}
      Question: {input.message}
  - id: remember
    type: standard
    depends_on: [reply]
    prompt: |
      Use the configured `sandcastle-memory` MCP server's `add` tool to save
      this support interaction for user:{input.customer_id}:
      {input.message} -> {steps.reply.output}
```

## CLI

```text
sandcastle memory-mcp serve [--transport stdio|streamable-http] [--port N]
```

The `__main__` agent is the owner of the top-level parser. This module
exposes `build_arg_parser()`, `cli_main(argv)` and `serve(...)` so the
orchestrator only needs a one-line dispatch entry to wire it in.
