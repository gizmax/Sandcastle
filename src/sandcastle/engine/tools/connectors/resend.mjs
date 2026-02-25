/**
 * Resend connector - transactional email sending via Resend API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_RESEND_API_KEY
 */

const API_KEY = process.env.TOOL_RESEND_API_KEY || "";
const BASE = "https://api.resend.com";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function api(path, method = "GET", body = null) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "Authorization": `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
      },
    };
    if (body !== null) opts.body = typeof body === "string" ? body : JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Resend API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function send_email(from, to, subject, html, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = {
    from,
    to: Array.isArray(to) ? to : [to],
    subject,
    html,
  };
  if (opts.cc) payload.cc = Array.isArray(opts.cc) ? opts.cc : [opts.cc];
  if (opts.bcc) payload.bcc = Array.isArray(opts.bcc) ? opts.bcc : [opts.bcc];
  if (opts.reply_to) payload.reply_to = opts.reply_to;
  if (opts.attachments) payload.attachments = opts.attachments;
  if (opts.text) payload.text = opts.text;
  if (opts.tags) payload.tags = opts.tags;
  const data = await api("/emails", "POST", payload);
  return { id: data.id };
}

export async function get_email(emailId) {
  const data = await api(`/emails/${emailId}`);
  return {
    id: data.id,
    from: data.from,
    to: data.to,
    subject: data.subject,
    created_at: data.created_at,
    last_event: data.last_event,
  };
}

export async function list_emails() {
  const data = await api("/emails");
  return (data.data || []).map((e) => ({
    id: e.id,
    to: e.to,
    subject: e.subject,
    created_at: e.created_at,
    last_event: e.last_event,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("resend.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_email, get_email, list_emails };
  if (!dispatch[fn]) {
    console.error(`Usage: node resend.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
