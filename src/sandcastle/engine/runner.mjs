/**
 * Sandcastle runner - executes Claude Agent SDK inside E2B sandbox.
 * Reads request from SANDCASTLE_REQUEST env var, streams JSON events to stdout.
 *
 * Tracks token usage from assistant messages and calculates cost using
 * MODEL_INPUT_PRICE / MODEL_OUTPUT_PRICE env vars (USD per 1M tokens).
 */
import { query } from "@anthropic-ai/claude-agent-sdk";

// --- Emit helper ---

function emit(event) {
    process.stdout.write(JSON.stringify(event) + "\n");
}

// --- Parse and validate request ---

if (!process.env.SANDCASTLE_REQUEST) {
    emit({ type: "error", error: "SANDCASTLE_REQUEST env var is not set" });
    process.exit(1);
}

let request;
try {
    request = JSON.parse(process.env.SANDCASTLE_REQUEST);
} catch (err) {
    emit({ type: "error", error: `Failed to parse SANDCASTLE_REQUEST: ${err.message}` });
    process.exit(1);
}

if (!request.prompt || typeof request.prompt !== "string") {
    emit({ type: "error", error: "SANDCASTLE_REQUEST must contain a non-empty 'prompt' string" });
    process.exit(1);
}

// --- Setup ---

const options = {
    allowedTools: ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
    permissionMode: "bypassPermissions",
    model: request.model || "sonnet",
    maxTurns: request.max_turns || 10,
};
if (request.output_format) options.outputFormat = request.output_format;
if (request.max_budget_usd) options.maxBudgetUsd = request.max_budget_usd;
if (request.timeout) options.timeoutMs = request.timeout * 1000;

// --- Pricing helpers ---

const inputPrice = parseFloat(process.env.MODEL_INPUT_PRICE || "0") / 1_000_000;
const outputPrice = parseFloat(process.env.MODEL_OUTPUT_PRICE || "0") / 1_000_000;

let totalInputTokens = 0;
let totalOutputTokens = 0;

function trackUsage(message) {
    // SDKAssistantMessage has message.message.usage with input_tokens/output_tokens
    const usage = message.message?.usage;
    if (!usage) return;
    totalInputTokens += usage.input_tokens || 0;
    totalOutputTokens += usage.output_tokens || 0;
}

function totalCost() {
    return totalInputTokens * inputPrice + totalOutputTokens * outputPrice;
}

// --- Unhandled rejection safety net ---

process.on("unhandledRejection", (err) => {
    emit({ type: "error", error: `Unhandled rejection: ${err?.message || err}` });
    process.exit(1);
});

// --- Main loop ---

try {
    for await (const message of query({
        prompt: request.prompt,
        options,
    })) {
        if (message.type === "assistant") {
            trackUsage(message);
        }

        if (message.type === "result") {
            // Override cost with our own calculation based on registry pricing
            const cost = totalCost();
            const patched = {
                ...message,
                total_cost_usd: cost > 0 ? cost : (message.total_cost_usd || 0),
                input_tokens: totalInputTokens,
                output_tokens: totalOutputTokens,
            };
            emit(patched);
        } else {
            emit(message);
        }
    }
} catch (err) {
    emit({ type: "error", error: `Runner error: ${err.message}` });
    process.exit(1);
}
