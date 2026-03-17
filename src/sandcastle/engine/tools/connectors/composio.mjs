/**
 * Composio connector - execute 500+ business app actions via Composio API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_COMPOSIO_API_KEY
 */

const API_KEY = process.env.TOOL_COMPOSIO_API_KEY || "";
const BASE = "https://backend.composio.dev/api/v2";

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 60000);
  const opts = {
    method,
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  clearTimeout(timer);
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`Composio API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function execute_action(actionName, params = {}, connectedAccountId = "") {
  const payload = { actionName, input: params };
  if (connectedAccountId) payload.connectedAccountId = connectedAccountId;
  const data = await api("/actions/execute", "POST", payload);
  if (data.error) throw new Error(`Composio action error: ${data.error}`);
  return data.data || data;
}

export async function list_actions(appName = "", limit = 20) {
  let path = `/actions?limit=${limit}`;
  if (appName) path += `&appName=${encodeURIComponent(appName)}`;
  const data = await api(path);
  return (data.items || []).map((a) => ({
    name: a.name,
    displayName: a.displayName,
    description: a.description,
    appName: a.appName,
  }));
}

export async function list_apps(limit = 50) {
  const data = await api(`/apps?limit=${limit}`);
  return (data.items || []).map((a) => ({
    name: a.name,
    displayName: a.displayName,
    categories: a.categories,
  }));
}

export async function get_action_schema(actionName) {
  const data = await api(`/actions/${encodeURIComponent(actionName)}`);
  return {
    name: data.name,
    displayName: data.displayName,
    description: data.description,
    parameters: data.parameters,
    response: data.response,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("composio.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { execute_action, list_actions, list_apps, get_action_schema };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node composio.mjs <execute_action|list_actions|list_apps|get_action_schema> [args...]`
    );
    process.exit(1);
  }
  try {
    // Parse JSON args if provided
    const parsed = args.map((a) => {
      try { return JSON.parse(a); } catch { return a; }
    });
    const result = await dispatch[fn](...parsed);
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}
