/**
 * Zendesk connector - create and manage support tickets.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_ZENDESK_SUBDOMAIN, TOOL_ZENDESK_EMAIL, TOOL_ZENDESK_API_TOKEN
 */

const SUBDOMAIN = process.env.TOOL_ZENDESK_SUBDOMAIN || "";
const EMAIL = process.env.TOOL_ZENDESK_EMAIL || "";
const API_TOKEN = process.env.TOOL_ZENDESK_API_TOKEN || "";
const BASE = `https://${SUBDOMAIN}.zendesk.com/api/v2`;

const AUTH = Buffer.from(`${EMAIL}/token:${API_TOKEN}`).toString("base64");

async function api(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      "Authorization": `Basic ${AUTH}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Zendesk API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function create_ticket(subject, description, priority = "normal", requester_email = "") {
  const ticket = { subject, description, priority };
  if (requester_email) ticket.requester = { email: requester_email };
  const data = await api("/tickets.json", "POST", { ticket });
  return { id: data.ticket.id, url: data.ticket.url, status: data.ticket.status };
}

export async function get_tickets(query = "", status = "", limit = 20) {
  if (query) {
    let searchQuery = query;
    if (status) searchQuery += ` status:${status}`;
    const data = await api(`/search.json?query=type:ticket ${encodeURIComponent(searchQuery)}&per_page=${limit}`);
    return data.results.map(formatTicket);
  }
  let path = `/tickets.json?per_page=${limit}`;
  const data = await api(path);
  let tickets = data.tickets;
  if (status) tickets = tickets.filter((t) => t.status === status);
  return tickets.map(formatTicket);
}

function formatTicket(t) {
  return {
    id: t.id,
    subject: t.subject,
    status: t.status,
    priority: t.priority,
    requester_id: t.requester_id,
    created_at: t.created_at,
  };
}

export async function update_ticket(ticket_id, status = "", comment = "", priority = "") {
  const ticket = {};
  if (status) ticket.status = status;
  if (priority) ticket.priority = priority;
  if (comment) ticket.comment = { body: comment, public: true };
  const data = await api(`/tickets/${ticket_id}.json`, "PUT", { ticket });
  return { id: data.ticket.id, status: data.ticket.status, updated: true };
}

// CLI dispatch
if (process.argv[1]?.endsWith("zendesk.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { create_ticket, get_tickets, update_ticket };
  if (!dispatch[fn]) {
    console.error(`Usage: node zendesk.mjs <create_ticket|get_tickets|update_ticket> [args...]`);
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
