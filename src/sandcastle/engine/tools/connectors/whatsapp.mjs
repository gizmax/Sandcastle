/**
 * WhatsApp Business Cloud API connector (Meta Graph API v18.0).
 * Env: TOOL_WHATSAPP_TOKEN, TOOL_WHATSAPP_PHONE_ID
 */

const TOKEN = process.env.TOOL_WHATSAPP_TOKEN;
const PHONE_ID = process.env.TOOL_WHATSAPP_PHONE_ID;
const BASE = `https://graph.facebook.com/v18.0/${PHONE_ID}`;

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const url = path.startsWith("http") ? path : `${BASE}${path}`;
  const resp = await fetch(url, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...options.headers },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`WhatsApp ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function send_message(to, text) {
  const data = await api("/messages", {
    method: "POST",
    body: JSON.stringify({
      messaging_product: "whatsapp",
      to: String(to),
      type: "text",
      text: { body: text },
    }),
  });
  return { message_id: data.messages?.[0]?.id, status: "sent", to };
}

export async function send_template(to, templateName, parameters = []) {
  const params = typeof parameters === "string" ? JSON.parse(parameters) : parameters;
  const components = params.length > 0
    ? [{ type: "body", parameters: params.map((p) => ({ type: "text", text: String(p) })) }]
    : [];
  const data = await api("/messages", {
    method: "POST",
    body: JSON.stringify({
      messaging_product: "whatsapp",
      to: String(to),
      type: "template",
      template: { name: templateName, language: { code: "en" }, components },
    }),
  });
  return { message_id: data.messages?.[0]?.id, status: "sent", template: templateName };
}

export async function send_media(to, mediaUrl, caption = "", type = "image") {
  const body = {
    messaging_product: "whatsapp",
    to: String(to),
    type,
    [type]: { link: mediaUrl },
  };
  if (caption && (type === "image" || type === "video" || type === "document")) {
    body[type].caption = caption;
  }
  const data = await api("/messages", { method: "POST", body: JSON.stringify(body) });
  return { message_id: data.messages?.[0]?.id, status: "sent", type };
}

export async function get_media(mediaId) {
  const data = await api(`https://graph.facebook.com/v18.0/${mediaId}`);
  return { id: mediaId, url: data.url, mime_type: data.mime_type, file_size: data.file_size };
}

// CLI dispatch - only when run directly, not when imported as module
if (process.argv[1]?.endsWith("whatsapp.mjs")) {
  const funcs = { send_message, send_template, send_media, get_media };
  const [fn, ...args] = process.argv.slice(2);
  if (!fn || !funcs[fn]) {
    console.error(`Usage: node whatsapp.mjs <${Object.keys(funcs).join("|")}> [args...]`);
    process.exit(1);
  }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  funcs[fn](...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
