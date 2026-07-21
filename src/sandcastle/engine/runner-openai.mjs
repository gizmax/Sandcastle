/**
 * Sandcastle OpenAI-compatible runner - executes inside E2B sandbox.
 * Reads request from SANDCASTLE_REQUEST env var, streams JSON events to stdout.
 *
 * Works with any OpenAI-compatible API: OpenAI, MiniMax, OpenRouter, etc.
 * Uses MODEL_API_KEY and MODEL_BASE_URL env vars for provider routing.
 */
import OpenAI from "openai";
import { execSync } from "node:child_process";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve, normalize } from "node:path";

// --- Emit event (same protocol as runner.mjs) ---

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

const apiKey = process.env.MODEL_API_KEY || "";
const baseURL = process.env.MODEL_BASE_URL || "https://api.openai.com/v1";
const modelId = process.env.MODEL_ID || request.model || "gpt-4o";
// Low default temperature for deterministic step output; some endpoints
// (e.g. NVIDIA-hosted models) default to 1.0 and produce garbled text.
const STEP_TEMPERATURE = process.env.STEP_TEMPERATURE
  ? parseFloat(process.env.STEP_TEMPERATURE)
  : 0.2;

// Validate max_turns to prevent unbounded execution
const rawMaxTurns = parseInt(request.max_turns, 10);
const maxTurns = (Number.isFinite(rawMaxTurns) && rawMaxTurns > 0)
  ? Math.min(rawMaxTurns, 200)
  : 10;

// Validate timeout - cap at 1 hour (3600s) to prevent indefinite blocking
const rawTimeout = parseInt(request.timeout, 10);
const timeoutSec = (Number.isFinite(rawTimeout) && rawTimeout > 0)
  ? Math.min(rawTimeout, 3600)
  : 300;
const timeoutMs = timeoutSec * 1000;

const client = new OpenAI({ apiKey, baseURL });

// --- Sandbox boundary ---

const SANDBOX_ROOT = "/home/user";

// --- Tool definitions ---

const tools = [
  {
    type: "function",
    function: {
      name: "bash",
      description: "Execute a bash command and return stdout/stderr.",
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "The bash command to execute." },
        },
        required: ["command"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "read_file",
      description: "Read the contents of a file.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Absolute file path to read." },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "Write content to a file, creating directories as needed.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Absolute file path to write." },
          content: { type: "string", description: "Content to write." },
        },
        required: ["path", "content"],
      },
    },
  },
];

// --- Max size for tool results stored in conversation history ---

const MAX_TOOL_RESULT_SIZE = 50_000;

// --- Path validation ---

function validatePath(filePath) {
  const absPath = resolve(filePath);
  const normalized = normalize(absPath);
  if (!normalized.startsWith(SANDBOX_ROOT + "/") && normalized !== SANDBOX_ROOT) {
    throw new Error(`Path '${filePath}' is outside sandbox root (${SANDBOX_ROOT})`);
  }
  return normalized;
}

// --- Tool execution ---

function executeTool(name, args) {
  try {
    switch (name) {
      case "bash": {
        if (!args.command || typeof args.command !== "string") {
          return "Error: 'command' argument is required and must be a string";
        }
        try {
          const result = execSync(args.command, {
            encoding: "utf-8",
            timeout: 30_000,
            maxBuffer: 1024 * 1024,
            cwd: SANDBOX_ROOT,
            stdio: ["pipe", "pipe", "pipe"],
          });
          return (result || "").slice(0, MAX_TOOL_RESULT_SIZE);
        } catch (execErr) {
          // execSync throws on non-zero exit - include both stdout and stderr
          const stdout = execErr.stdout || "";
          const stderr = execErr.stderr || "";
          const combined = stdout + (stderr ? `\nSTDERR: ${stderr}` : "");
          return (combined || `Error: ${execErr.message}`).slice(0, MAX_TOOL_RESULT_SIZE);
        }
      }
      case "read_file": {
        if (!args.path || typeof args.path !== "string") {
          return "Error: 'path' argument is required and must be a string";
        }
        const absPath = validatePath(args.path);
        return readFileSync(absPath, "utf-8").slice(0, 100_000);
      }
      case "write_file": {
        if (!args.path || typeof args.path !== "string") {
          return "Error: 'path' argument is required and must be a string";
        }
        if (typeof args.content !== "string") {
          return "Error: 'content' argument is required and must be a string";
        }
        const absPath = validatePath(args.path);
        mkdirSync(dirname(absPath), { recursive: true });
        writeFileSync(absPath, args.content, "utf-8");
        return `Written ${args.content.length} bytes to ${absPath}`;
      }
      default:
        return `Error: Unknown tool '${name}'. Available tools: bash, read_file, write_file`;
    }
  } catch (err) {
    return `Error: ${err?.message || String(err)}`;
  }
}

// --- Pricing helpers ---

const inputPrice = parseFloat(process.env.MODEL_INPUT_PRICE || "0") / 1_000_000;
const outputPrice = parseFloat(process.env.MODEL_OUTPUT_PRICE || "0") / 1_000_000;

let totalInputTokens = 0;
let totalOutputTokens = 0;

function trackUsage(usage) {
  if (!usage) return;
  totalInputTokens += usage.prompt_tokens || 0;
  totalOutputTokens += usage.completion_tokens || 0;
}


// Final answer: the model sometimes ends on an empty message (e.g. after tool
// flailing). Fall back to the last non-empty assistant text so the step never
// reports an empty result when the agent actually said something along the way.
function finalResult(messages, content) {
  if (content) return content;
  const prior = messages
    .filter((m) => m.role === "assistant" && typeof m.content === "string" && m.content.trim())
    .pop();
  return prior ? prior.content : "";
}

function totalCost() {
  return totalInputTokens * inputPrice + totalOutputTokens * outputPrice;
}

// --- Conversation history management ---

const MAX_HISTORY_MESSAGES = 80;

function trimHistory(messages) {
  if (messages.length <= MAX_HISTORY_MESSAGES) return;
  // Find a safe trim point that doesn't break tool_call/tool message pairing.
  // The OpenAI API requires that every assistant message with tool_calls
  // is immediately followed by the corresponding tool result messages.
  // Trim from the front (after the first user message) to preserve recent context.
  const keep = MAX_HISTORY_MESSAGES - 1;
  let cutIndex = messages.length - keep;

  // Ensure we don't cut in the middle of a tool call sequence.
  // Walk forward from the cut point to find a safe boundary.
  // Safe boundaries: right before a user message or an assistant message
  // that is NOT a tool response continuation.
  while (cutIndex < messages.length - 1) {
    const msg = messages[cutIndex];
    // If this is a tool result message, we might be splitting a tool call pair.
    // Move forward until we find a non-tool message.
    if (msg.role === "tool") {
      cutIndex++;
      continue;
    }
    // If this is an assistant message, check if the PREVIOUS message (before cut)
    // was an assistant with tool_calls that expects tool responses after it.
    break;
  }

  // Avoid trimming if we can't find a safe point
  if (cutIndex >= messages.length - 1) return;

  const trimmed = [messages[0], ...messages.slice(cutIndex)];
  messages.length = 0;
  messages.push(...trimmed);
}

// --- Retry helper for API calls ---

const MAX_API_RETRIES = 2;
const RETRY_BACKOFF_MS = 1000;

async function callApiWithRetry(client, params) {
  let lastError;
  for (let attempt = 0; attempt <= MAX_API_RETRIES; attempt++) {
    try {
      return await client.chat.completions.create(params);
    } catch (err) {
      lastError = err;
      const status = err.status || err.statusCode;
      // Retry on rate limit (429) and server errors (500+)
      if ((status === 429 || (status >= 500 && status < 600)) && attempt < MAX_API_RETRIES) {
        const retryAfter = err.headers?.["retry-after"];
        const waitMs = retryAfter
          ? Math.min(parseInt(retryAfter, 10) * 1000 || RETRY_BACKOFF_MS, 60_000)
          : RETRY_BACKOFF_MS * Math.pow(2, attempt);
        await new Promise((r) => setTimeout(r, waitMs));
        continue;
      }
      // Non-retryable error (401, 403, 400, etc.) - throw immediately
      throw err;
    }
  }
  throw lastError;
}

// --- Unhandled rejection safety net ---

process.on("unhandledRejection", (err) => {
  emit({ type: "error", error: `Unhandled rejection: ${err?.message || err}` });
  process.exit(1);
});

// --- Main agentic loop ---

async function run() {
  const messages = [{ role: "user", content: request.prompt }];
  let turn = 0;
  const deadline = Date.now() + timeoutMs;

  emit({ type: "system", message: `Starting OpenAI-compatible agent (model=${modelId})` });

  while (turn < maxTurns) {
    if (Date.now() > deadline) {
      emit({ type: "error", error: "Timeout exceeded" });
      break;
    }

    turn++;
    trimHistory(messages);

    let completion;
    try {
      completion = await callApiWithRetry(client, {
        model: modelId,
        messages,
        tools,
        tool_choice: "auto",
        temperature: STEP_TEMPERATURE,
      });
    } catch (err) {
      emit({ type: "error", error: `API call failed: ${err?.message || String(err)}` });
      break;
    }

    trackUsage(completion.usage);

    const choice = completion.choices?.[0];
    if (!choice) {
      emit({ type: "error", error: "API returned no choices" });
      break;
    }

    const msg = choice.message;

    // Add assistant message to history
    messages.push(msg);

    // Emit assistant text if present
    if (msg.content) {
      emit({ type: "assistant", content: [{ type: "text", text: msg.content }] });
    }

    // Check for tool calls
    if (!msg.tool_calls || msg.tool_calls.length === 0) {
      // No tool calls - agent is done
      emit({
        type: "result",
        result: finalResult(messages, msg.content),
        total_cost_usd: totalCost(),
        num_turns: turn,
      });
      return;
    }

    // Execute tool calls
    for (const tc of msg.tool_calls) {
      let args;
      try {
        args = JSON.parse(tc.function.arguments);
      } catch {
        // Malformed JSON from model - report the parse error back to the model
        // instead of executing with empty args which could cause unexpected behavior
        const parseError = `Error: Could not parse tool arguments as JSON: ${tc.function.arguments}`;
        emit({ type: "tool_use", tool: tc.function.name, args: {}, result: parseError.slice(0, 2000) });
        messages.push({ role: "tool", tool_call_id: tc.id, content: parseError });
        continue;
      }

      const result = executeTool(tc.function.name, args);

      // Ensure result is always a string before slicing
      const resultStr = typeof result === "string" ? result : String(result ?? "");

      emit({
        type: "tool_use",
        tool: tc.function.name,
        args,
        result: resultStr.slice(0, 2000),
      });

      // Truncate tool results in conversation history to prevent context overflow
      messages.push({
        role: "tool",
        tool_call_id: tc.id,
        content: resultStr.slice(0, MAX_TOOL_RESULT_SIZE),
      });
    }

    // Check finish reason
    if (choice.finish_reason === "stop") {
      emit({
        type: "result",
        result: finalResult(messages, msg.content),
        total_cost_usd: totalCost(),
        num_turns: turn,
      });
      return;
    }
  }

  // Max turns reached - emit result from last assistant message
  const lastAssistant = messages.filter((m) => m.role === "assistant").pop();
  emit({
    type: "result",
    result: finalResult(messages, lastAssistant?.content),
    total_cost_usd: totalCost(),
    num_turns: turn,
  });
}

run().catch((err) => {
  emit({ type: "error", error: `Runner crashed: ${err?.message || String(err)}` });
  process.exit(1);
});
