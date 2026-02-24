/**
 * Twilio connector - send SMS, make calls, and manage messages via Twilio REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_TWILIO_ACCOUNT_SID, TOOL_TWILIO_AUTH_TOKEN, TOOL_TWILIO_FROM_NUMBER
 */

const ACCOUNT_SID = process.env.TOOL_TWILIO_ACCOUNT_SID || "";
const AUTH_TOKEN = process.env.TOOL_TWILIO_AUTH_TOKEN || "";
const FROM_NUMBER = process.env.TOOL_TWILIO_FROM_NUMBER || "";
const BASE = `https://api.twilio.com/2010-04-01/Accounts/${ACCOUNT_SID}`;

async function api(path, method = "GET", params = null) {
  const opts = {
    method,
    headers: {
      "Authorization": `Basic ${Buffer.from(ACCOUNT_SID + ":" + AUTH_TOKEN).toString("base64")}`,
    },
  };
  if (params && method !== "GET") {
    opts.headers["Content-Type"] = "application/x-www-form-urlencoded";
    opts.body = new URLSearchParams(params).toString();
  }
  let url = `${BASE}${path}`;
  if (params && method === "GET") {
    url += (url.includes("?") ? "&" : "?") + new URLSearchParams(params).toString();
  }
  const resp = await fetch(url, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Twilio API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function send_sms(to, body) {
  const data = await api("/Messages.json", "POST", {
    From: FROM_NUMBER,
    To: to,
    Body: body,
  });
  return { sid: data.sid, status: data.status, to: data.to, from: data.from };
}

export async function list_messages(to = "", from_number = "", limit = 20) {
  const params = { PageSize: String(limit) };
  if (to) params.To = to;
  if (from_number) params.From = from_number;
  const data = await api("/Messages.json", "GET", params);
  return data.messages.map((m) => ({
    sid: m.sid,
    to: m.to,
    from: m.from,
    body: m.body,
    status: m.status,
    date_sent: m.date_sent,
  }));
}

export async function get_message(message_sid) {
  const data = await api(`/Messages/${message_sid}.json`);
  return {
    sid: data.sid,
    to: data.to,
    from: data.from,
    body: data.body,
    status: data.status,
    date_sent: data.date_sent,
    price: data.price,
    price_unit: data.price_unit,
  };
}

export async function make_call(to, twiml_url) {
  const data = await api("/Calls.json", "POST", {
    From: FROM_NUMBER,
    To: to,
    Url: twiml_url,
  });
  return { sid: data.sid, status: data.status, to: data.to, from: data.from };
}

// CLI dispatch
if (process.argv[1]?.endsWith("twilio.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_sms, list_messages, get_message, make_call };
  if (!dispatch[fn]) {
    console.error(`Usage: node twilio.mjs <send_sms|list_messages|get_message|make_call> [args...]`);
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
