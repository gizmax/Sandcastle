/**
 * Jira connector - create, search, and manage issues via Jira REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_JIRA_API_TOKEN, TOOL_JIRA_BASE_URL, TOOL_JIRA_EMAIL
 */

const TOKEN = process.env.TOOL_JIRA_API_TOKEN || "";
const BASE_URL = (process.env.TOOL_JIRA_BASE_URL || "").replace(/\/$/, "");
const EMAIL = process.env.TOOL_JIRA_EMAIL || "";

const AUTH = Buffer.from(`${EMAIL}:${TOKEN}`).toString("base64");

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      "Authorization": `Basic ${AUTH}`,
      "Content-Type": "application/json",
      "Accept": "application/json",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE_URL}/rest/api/3${path}`, opts);
  clearTimeout(timer);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Jira API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function create_issue(project, summary, description = "", issue_type = "Task") {
  const data = await api("/issue", "POST", {
    fields: {
      project: { key: project },
      summary,
      description: {
        type: "doc",
        version: 1,
        content: [{ type: "paragraph", content: [{ type: "text", text: description || summary }] }],
      },
      issuetype: { name: issue_type },
    },
  });
  return { key: data.key, id: data.id, self: data.self };
}

export async function get_issue(issue_key) {
  const data = await api(`/issue/${issue_key}`);
  return {
    key: data.key,
    summary: data.fields.summary,
    status: data.fields.status?.name,
    assignee: data.fields.assignee?.displayName,
    priority: data.fields.priority?.name,
    description: data.fields.description,
  };
}

export async function search_issues(jql, max_results = 20) {
  const data = await api(`/search?jql=${encodeURIComponent(jql)}&maxResults=${max_results}`);
  return data.issues.map((i) => ({
    key: i.key,
    summary: i.fields.summary,
    status: i.fields.status?.name,
    assignee: i.fields.assignee?.displayName,
  }));
}

export async function add_comment(issue_key, body) {
  const data = await api(`/issue/${issue_key}/comment`, "POST", {
    body: {
      type: "doc",
      version: 1,
      content: [{ type: "paragraph", content: [{ type: "text", text: body }] }],
    },
  });
  return { id: data.id, created: data.created };
}

// CLI dispatch
if (process.argv[1]?.endsWith("jira.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { create_issue, get_issue, search_issues, add_comment };
  if (!dispatch[fn]) {
    console.error(`Usage: node jira.mjs <create_issue|get_issue|search_issues|add_comment> [args...]`);
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
