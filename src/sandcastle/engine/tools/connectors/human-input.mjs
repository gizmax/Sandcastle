/**
 * Human Input connector - structured human-in-the-loop data collection.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SANDCASTLE_API_URL (default: http://localhost:8080)
 */

const API_URL = (process.env.TOOL_SANDCASTLE_API_URL || "http://localhost:8080").replace(/\/$/, "");
const BASE = `${API_URL}/api/human-input`;

async function api(path, method = "POST", body = null) {
  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Human Input API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function request_input(prompt, schema = "{}", options = "{}") {
  const parsedSchema = typeof schema === "string" ? JSON.parse(schema) : schema;
  const parsedOptions = typeof options === "string" ? JSON.parse(options) : options;

  if (!prompt) throw new Error("prompt is required");

  const timeout = parsedOptions.timeout || 3600;
  const channel = parsedOptions.channel || "dashboard";
  const validChannels = ["dashboard", "slack", "email"];
  if (!validChannels.includes(channel)) {
    throw new Error(`Invalid channel: ${channel}. Must be one of: ${validChannels.join(", ")}`);
  }

  const data = await api("/requests", "POST", {
    type: "input",
    prompt,
    schema: parsedSchema,
    timeout,
    channel,
    metadata: parsedOptions.metadata || {},
  });
  return {
    requestId: data.requestId || data.id,
    status: "pending",
    channel,
    expiresIn: timeout,
  };
}

export async function check_input(requestId) {
  if (!requestId) throw new Error("requestId is required");
  const data = await api(`/requests/${requestId}`, "GET");
  return {
    requestId,
    status: data.status || "pending",
    data: data.response || null,
    completedAt: data.completedAt || null,
  };
}

export async function request_approval(title, details = "", options = "{}") {
  const parsedOptions = typeof options === "string" ? JSON.parse(options) : options;

  if (!title) throw new Error("title is required");

  const timeout = parsedOptions.timeout || 3600;
  const channel = parsedOptions.channel || "dashboard";
  const urgency = parsedOptions.urgency || "normal";

  const data = await api("/requests", "POST", {
    type: "approval",
    prompt: title,
    schema: {
      type: "object",
      properties: {
        approved: { type: "boolean", description: title },
        reason: { type: "string", description: "Optional reason" },
      },
      required: ["approved"],
    },
    details,
    timeout,
    channel,
    urgency,
    metadata: parsedOptions.metadata || {},
  });
  return {
    requestId: data.requestId || data.id,
    status: "pending",
    type: "approval",
    channel,
  };
}

export async function request_review(content, instructions = "") {
  if (!content) throw new Error("content is required");

  const data = await api("/requests", "POST", {
    type: "review",
    prompt: instructions || "Please review and edit the following content.",
    schema: {
      type: "object",
      properties: {
        editedContent: { type: "string", description: "Reviewed/edited content" },
        approved: { type: "boolean", description: "Content approved as-is" },
        comments: { type: "string", description: "Review comments" },
      },
      required: ["editedContent"],
    },
    content,
    timeout: 7200,
    channel: "dashboard",
    metadata: { originalLength: content.length },
  });
  return {
    requestId: data.requestId || data.id,
    status: "pending",
    type: "review",
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("human-input.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { request_input, check_input, request_approval, request_review };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node human-input.mjs <request_input|check_input|request_approval|request_review> [args...]`
    );
    process.exit(1);
  }
  try {
    const result = await dispatch[fn](...args);
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}
