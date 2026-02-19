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
import { dirname } from "node:path";

const request = JSON.parse(process.env.SANDCASTLE_REQUEST);
const apiKey = process.env.MODEL_API_KEY || "";
const baseURL = process.env.MODEL_BASE_URL || "https://api.openai.com/v1";
const modelId = process.env.MODEL_ID || request.model || "gpt-4o";
const maxTurns = request.max_turns || 10;
const timeoutMs = (request.timeout || 300) * 1000;

const client = new OpenAI({ apiKey, baseURL });

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

// --- Tool execution ---

function executeTool(name, args) {
  try {
    switch (name) {
      case "bash": {
        const result = execSync(args.command, {
          encoding: "utf-8",
          timeout: 30000,
          maxBuffer: 1024 * 1024,
          cwd: "/home/user",
        });
        return result.slice(0, 50000);
      }
      case "read_file":
        return readFileSync(args.path, "utf-8").slice(0, 100000);
      case "write_file":
        mkdirSync(dirname(args.path), { recursive: true });
        writeFileSync(args.path, args.content, "utf-8");
        return `Written ${args.content.length} bytes to ${args.path}`;
      default:
        return `Unknown tool: ${name}`;
    }
  } catch (err) {
    return `Error: ${err.message || err}`;
  }
}

// --- Emit event (same protocol as runner.mjs) ---

function emit(event) {
  process.stdout.write(JSON.stringify(event) + "\n");
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

function totalCost() {
  return totalInputTokens * inputPrice + totalOutputTokens * outputPrice;
}

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

    let completion;
    try {
      completion = await client.chat.completions.create({
        model: modelId,
        messages,
        tools,
        tool_choice: "auto",
      });
    } catch (err) {
      emit({ type: "error", error: `API call failed: ${err.message}` });
      break;
    }

    trackUsage(completion.usage);

    const choice = completion.choices[0];
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
        result: msg.content || "",
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
        args = {};
      }

      const result = executeTool(tc.function.name, args);

      emit({
        type: "tool_use",
        tool: tc.function.name,
        args,
        result: result.slice(0, 2000),
      });

      messages.push({
        role: "tool",
        tool_call_id: tc.id,
        content: result,
      });
    }

    // Check finish reason
    if (choice.finish_reason === "stop") {
      emit({
        type: "result",
        result: msg.content || "",
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
    result: lastAssistant?.content || "",
    total_cost_usd: totalCost(),
    num_turns: turn,
  });
}

run().catch((err) => {
  emit({ type: "error", error: `Runner crashed: ${err.message}` });
  process.exit(1);
});
