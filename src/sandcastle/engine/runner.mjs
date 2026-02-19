/**
 * Sandcastle runner - executes Claude Agent SDK inside E2B sandbox.
 * Reads request from SANDCASTLE_REQUEST env var, streams JSON events to stdout.
 */
import { query } from "@anthropic-ai/claude-agent-sdk";

const request = JSON.parse(process.env.SANDCASTLE_REQUEST);

const options = {
    allowedTools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebSearch", "WebFetch"],
    permissionMode: "bypassPermissions",
    model: request.model || "sonnet",
    maxTurns: request.max_turns || 10,
};
if (request.output_format) options.outputFormat = request.output_format;
if (request.max_budget_usd) options.maxBudgetUsd = request.max_budget_usd;
if (request.timeout) options.timeoutMs = request.timeout * 1000;

for await (const message of query({
    prompt: request.prompt,
    options,
})) {
    process.stdout.write(JSON.stringify(message) + "\n");
}
