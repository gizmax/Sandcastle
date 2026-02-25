/**
 * MCP Bridge connector - call any Model Context Protocol server from workflows.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_MCP_SERVER_URL (HTTP endpoint of MCP server)
 */

const SERVER_URL = process.env.TOOL_MCP_SERVER_URL || "";
let _requestId = 0;

function nextId() {
  return ++_requestId;
}

async function rpc(method, params = {}) {
  if (!SERVER_URL) {
    throw new Error("TOOL_MCP_SERVER_URL is not set");
  }
  const body = {
    jsonrpc: "2.0",
    id: nextId(),
    method,
    params,
  };
  const resp = await fetch(SERVER_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`MCP server ${resp.status}: ${text.slice(0, 500)}`);
  }
  const data = await resp.json();
  if (data.error) {
    throw new Error(`MCP error ${data.error.code}: ${data.error.message}`);
  }
  return data.result;
}

async function ensureInitialized() {
  const result = await rpc("initialize", {
    protocolVersion: "2024-11-05",
    capabilities: {},
    clientInfo: { name: "sandcastle-mcp-bridge", version: "1.0.0" },
  });
  return result;
}

export async function list_tools() {
  await ensureInitialized();
  const result = await rpc("tools/list");
  return (result.tools || []).map((t) => ({
    name: t.name,
    description: t.description || "",
    inputSchema: t.inputSchema || {},
  }));
}

export async function call_tool(toolName, args = "{}") {
  const parsedArgs = typeof args === "string" ? JSON.parse(args) : args;
  await ensureInitialized();
  const result = await rpc("tools/call", {
    name: toolName,
    arguments: parsedArgs,
  });
  const content = result.content || [];
  const texts = content
    .filter((c) => c.type === "text")
    .map((c) => c.text);
  const images = content
    .filter((c) => c.type === "image")
    .map((c) => ({ mimeType: c.mimeType, data: c.data?.slice(0, 200) + "..." }));
  return {
    tool: toolName,
    isError: result.isError || false,
    text: texts.join("\n"),
    images: images.length > 0 ? images : undefined,
    rawContentCount: content.length,
  };
}

export async function list_resources() {
  await ensureInitialized();
  const result = await rpc("resources/list");
  return (result.resources || []).map((r) => ({
    uri: r.uri,
    name: r.name || "",
    description: r.description || "",
    mimeType: r.mimeType || "text/plain",
  }));
}

export async function read_resource(uri) {
  if (!uri) throw new Error("uri is required");
  await ensureInitialized();
  const result = await rpc("resources/read", { uri });
  const contents = result.contents || [];
  return contents.map((c) => ({
    uri: c.uri,
    mimeType: c.mimeType || "text/plain",
    text: c.text ? c.text.slice(0, 50000) : undefined,
    blob: c.blob ? c.blob.slice(0, 200) + "..." : undefined,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("mcp-bridge.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_tools, call_tool, list_resources, read_resource };
  if (!dispatch[fn]) {
    console.error(`Usage: node mcp-bridge.mjs <list_tools|call_tool|list_resources|read_resource> [args...]`);
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
