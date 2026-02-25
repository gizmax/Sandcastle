/**
 * GitHub connector - issues, PRs, and repo management via GitHub API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_GITHUB_TOKEN
 */

const TOKEN = process.env.TOOL_GITHUB_TOKEN || "";
const BASE = "https://api.github.com";

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      "Authorization": `token ${TOKEN}`,
      "Accept": "application/vnd.github.v3+json",
      "User-Agent": "Sandcastle-Tool",
    },
    signal: controller.signal,
  };
  if (body) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(`${BASE}${path}`, opts);
  clearTimeout(timer);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`GitHub API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function create_issue(repo, title, body = "", labels = "") {
  const payload = { title, body };
  if (labels) payload.labels = labels.split(",").map((l) => l.trim());
  const data = await api(`/repos/${repo}/issues`, "POST", payload);
  return { number: data.number, url: data.html_url, title: data.title };
}

export async function get_issues(repo, state = "open", limit = 20) {
  const data = await api(`/repos/${repo}/issues?state=${state}&per_page=${limit}`);
  return data.map((i) => ({
    number: i.number,
    title: i.title,
    state: i.state,
    user: i.user?.login,
    labels: i.labels?.map((l) => l.name),
    url: i.html_url,
  }));
}

export async function create_pr(repo, title, body = "", head = "", base = "main") {
  const data = await api(`/repos/${repo}/pulls`, "POST", { title, body, head, base });
  return { number: data.number, url: data.html_url, title: data.title };
}

// CLI dispatch
if (process.argv[1]?.endsWith("github.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { create_issue, get_issues, create_pr };
  if (!dispatch[fn]) {
    console.error(`Usage: node github.mjs <create_issue|get_issues|create_pr> [args...]`);
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
