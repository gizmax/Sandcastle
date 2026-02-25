/**
 * DocuSign eSignature REST API v2.1 connector.
 * Env: TOOL_DOCUSIGN_ACCESS_TOKEN, TOOL_DOCUSIGN_ACCOUNT_ID, TOOL_DOCUSIGN_BASE_URL
 */

const TOKEN = process.env.TOOL_DOCUSIGN_ACCESS_TOKEN;
const ACCOUNT_ID = process.env.TOOL_DOCUSIGN_ACCOUNT_ID;
const BASE_URL = (process.env.TOOL_DOCUSIGN_BASE_URL || "https://demo.docusign.net/restapi").replace(/\/$/, "");
const BASE = `${BASE_URL}/v2.1/accounts/${ACCOUNT_ID}`;

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json", ...options.headers },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`DocuSign ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function create_envelope(subject, recipients, documentBase64, documentName = "document.pdf") {
  const recips = typeof recipients === "string" ? JSON.parse(recipients) : recipients;
  const signers = recips.map((r, i) => ({
    email: r.email, name: r.name, recipientId: String(i + 1), routingOrder: String(i + 1),
  }));
  const body = {
    emailSubject: subject,
    documents: [{
      documentBase64: documentBase64,
      name: documentName,
      fileExtension: documentName.split(".").pop(),
      documentId: "1",
    }],
    recipients: { signers },
    status: "sent",
  };
  const data = await api("/envelopes", { method: "POST", body: JSON.stringify(body) });
  return { envelopeId: data.envelopeId, status: data.status, statusDateTime: data.statusDateTime };
}

export async function get_envelope(envelopeId) {
  const data = await api(`/envelopes/${envelopeId}`);
  return {
    envelopeId: data.envelopeId, status: data.status,
    emailSubject: data.emailSubject, sentDateTime: data.sentDateTime,
    completedDateTime: data.completedDateTime, statusChangedDateTime: data.statusChangedDateTime,
  };
}

export async function list_envelopes(options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (o.from_date) params.set("from_date", o.from_date);
  if (o.status) params.set("status", o.status);
  if (o.count) params.set("count", o.count);
  params.set("order", "desc");
  const data = await api(`/envelopes?${params}`);
  return (data.envelopes || []).map((e) => ({
    envelopeId: e.envelopeId, status: e.status, emailSubject: e.emailSubject,
    sentDateTime: e.sentDateTime, completedDateTime: e.completedDateTime,
  }));
}

export async function download_document(envelopeId, documentId = "combined") {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}/envelopes/${envelopeId}/documents/${documentId}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) throw new Error(`DocuSign download ${resp.status}`);
  const buf = await resp.arrayBuffer();
  return { documentId, size: buf.byteLength, base64: Buffer.from(buf).toString("base64").slice(0, 100000) };
}

const funcs = { create_envelope, get_envelope, list_envelopes, download_document };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
