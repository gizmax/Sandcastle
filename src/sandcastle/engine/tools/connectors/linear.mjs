/**
 * Linear connector - issue tracking via Linear GraphQL API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_LINEAR_API_KEY
 */

const API_KEY = process.env.TOOL_LINEAR_API_KEY || "";
const ENDPOINT = "https://api.linear.app/graphql";
const TIMEOUT = 30_000;

async function gql(query, variables = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const resp = await fetch(ENDPOINT, {
      method: "POST",
      headers: {
        "Authorization": API_KEY,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, variables }),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Linear API ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    if (data.errors?.length) {
      throw new Error(`Linear GraphQL: ${data.errors.map((e) => e.message).join(", ")}`);
    }
    return data.data;
  } finally { clearTimeout(timer); }
}

export async function create_issue(title, description = "", teamId = "", options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const input = { title, description, teamId, ...opts };
  const data = await gql(`
    mutation CreateIssue($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { id identifier title url state { name } priority }
      }
    }
  `, { input });
  const issue = data.issueCreate.issue;
  return { id: issue.id, identifier: issue.identifier, title: issue.title, url: issue.url };
}

export async function get_issues(filter = "{}") {
  const parsed = typeof filter === "string" ? JSON.parse(filter) : filter;
  const data = await gql(`
    query Issues($filter: IssueFilter) {
      issues(filter: $filter, first: 50) {
        nodes {
          id identifier title url priority
          state { name }
          assignee { name }
          labels { nodes { name } }
          createdAt
        }
      }
    }
  `, { filter: parsed });
  return data.issues.nodes.map((i) => ({
    id: i.id,
    identifier: i.identifier,
    title: i.title,
    url: i.url,
    priority: i.priority,
    state: i.state?.name,
    assignee: i.assignee?.name,
    labels: i.labels?.nodes?.map((l) => l.name) || [],
  }));
}

export async function update_issue(issueId, updates = "{}") {
  const parsed = typeof updates === "string" ? JSON.parse(updates) : updates;
  const data = await gql(`
    mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue { id identifier title url state { name } priority }
      }
    }
  `, { id: issueId, input: parsed });
  const issue = data.issueUpdate.issue;
  return { id: issue.id, identifier: issue.identifier, title: issue.title, state: issue.state?.name };
}

export async function get_teams() {
  const data = await gql(`
    query { teams { nodes { id name key description } } }
  `);
  return data.teams.nodes.map((t) => ({ id: t.id, name: t.name, key: t.key }));
}

export async function add_comment(issueId, body) {
  const data = await gql(`
    mutation AddComment($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
        comment { id body createdAt }
      }
    }
  `, { input: { issueId, body } });
  const c = data.commentCreate.comment;
  return { id: c.id, body: c.body, createdAt: c.createdAt };
}

// CLI dispatch
if (process.argv[1]?.endsWith("linear.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { create_issue, get_issues, update_issue, get_teams, add_comment };
  if (!dispatch[fn]) {
    console.error("Usage: node linear.mjs <create_issue|get_issues|update_issue|get_teams|add_comment> [args...]");
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
