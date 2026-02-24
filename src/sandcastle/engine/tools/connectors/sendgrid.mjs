/**
 * SendGrid connector - send emails and manage contacts via SendGrid v3 API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SENDGRID_API_KEY, TOOL_SENDGRID_FROM_EMAIL
 */

const API_KEY = process.env.TOOL_SENDGRID_API_KEY || "";
const FROM_EMAIL = process.env.TOOL_SENDGRID_FROM_EMAIL || "";
const BASE = "https://api.sendgrid.com/v3";

async function api(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      "Authorization": `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`SendGrid API ${resp.status}: ${text.slice(0, 500)}`);
  }
  // SendGrid returns 202 with empty body for some endpoints
  const text = await resp.text();
  if (!text) return null;
  return JSON.parse(text);
}

export async function send_email(to, subject, html_content = "", text_content = "") {
  const content = [];
  if (text_content) content.push({ type: "text/plain", value: text_content });
  if (html_content) content.push({ type: "text/html", value: html_content });
  if (!content.length) content.push({ type: "text/plain", value: subject });

  const resp = await fetch(`${BASE}/mail/send`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      personalizations: [{ to: [{ email: to }] }],
      from: { email: FROM_EMAIL },
      subject,
      content,
    }),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`SendGrid API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return { success: true, status: resp.status };
}

export async function list_contacts(query = "", limit = 50) {
  let data;
  if (query) {
    data = await api("/marketing/contacts/search", "POST", {
      query: `email LIKE '%${query}%' OR first_name LIKE '%${query}%' OR last_name LIKE '%${query}%'`,
    });
  } else {
    data = await api(`/marketing/contacts?page_size=${limit}`);
  }
  const contacts = data?.contacts || data?.result || [];
  return contacts.map((c) => ({
    id: c.id,
    email: c.email,
    first_name: c.first_name,
    last_name: c.last_name,
  }));
}

export async function add_contacts(contacts) {
  const parsed = typeof contacts === "string" ? JSON.parse(contacts) : contacts;
  const data = await api("/marketing/contacts", "PUT", { contacts: parsed });
  return { job_id: data.job_id };
}

export async function get_stats(start_date, end_date = "") {
  let path = `/stats?start_date=${start_date}`;
  if (end_date) path += `&end_date=${end_date}`;
  const data = await api(path);
  return data;
}

// CLI dispatch
if (process.argv[1]?.endsWith("sendgrid.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_email, list_contacts, add_contacts, get_stats };
  if (!dispatch[fn]) {
    console.error(`Usage: node sendgrid.mjs <send_email|list_contacts|add_contacts|get_stats> [args...]`);
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
