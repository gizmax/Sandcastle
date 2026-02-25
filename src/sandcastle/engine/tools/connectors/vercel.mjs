/**
 * Vercel connector - deployments, projects, and domains via Vercel REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_VERCEL_TOKEN, TOOL_VERCEL_TEAM_ID
 */

const TOKEN = process.env.TOOL_VERCEL_TOKEN || "";
const TEAM_ID = process.env.TOOL_VERCEL_TEAM_ID || "";
const BASE = "https://api.vercel.com";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

function teamParam(sep = "?") {
  return TEAM_ID ? `${sep}teamId=${TEAM_ID}` : "";
}

async function api(path, method = "GET", body = null) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "Authorization": `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
    };
    if (body !== null) opts.body = typeof body === "string" ? body : JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Vercel API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function list_deployments(limit = "20") {
  const lim = typeof limit === "string" ? parseInt(limit, 10) : limit;
  const sep = TEAM_ID ? "&" : "?";
  const data = await api(`/v6/deployments${teamParam()}${sep}limit=${lim}`);
  return (data.deployments || []).map((d) => ({
    uid: d.uid,
    name: d.name,
    url: d.url,
    state: d.state,
    created: d.created,
  }));
}

export async function get_deployment(deploymentId) {
  const data = await api(`/v13/deployments/${deploymentId}${teamParam()}`);
  return {
    uid: data.uid,
    name: data.name,
    url: data.url,
    state: data.readyState || data.state,
    created: data.createdAt,
    alias: data.alias,
    meta: data.meta,
  };
}

export async function list_projects() {
  const data = await api(`/v9/projects${teamParam()}`);
  return (data.projects || []).map((p) => ({
    id: p.id,
    name: p.name,
    framework: p.framework,
    updated: p.updatedAt,
  }));
}

export async function create_deployment(projectName, files) {
  const fileList = typeof files === "string" ? JSON.parse(files) : files;
  const payload = {
    name: projectName,
    files: fileList.map((f) => ({
      file: f.file,
      data: f.data,
    })),
    projectSettings: {},
  };
  const data = await api(`/v13/deployments${teamParam()}`, "POST", payload);
  return {
    uid: data.id,
    url: data.url,
    readyState: data.readyState,
  };
}

export async function get_domains(projectId) {
  const sep = TEAM_ID ? "&" : "?";
  const data = await api(`/v9/projects/${projectId}/domains${teamParam()}`);
  return (data.domains || []).map((d) => ({
    name: d.name,
    redirect: d.redirect,
    verified: d.verified,
    createdAt: d.createdAt,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("vercel.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_deployments, get_deployment, list_projects, create_deployment, get_domains };
  if (!dispatch[fn]) {
    console.error(`Usage: node vercel.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
