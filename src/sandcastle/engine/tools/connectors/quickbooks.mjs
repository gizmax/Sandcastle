/**
 * QuickBooks Online API connector with OAuth2 refresh flow.
 * Env: TOOL_QUICKBOOKS_CLIENT_ID, TOOL_QUICKBOOKS_CLIENT_SECRET,
 *      TOOL_QUICKBOOKS_REFRESH_TOKEN, TOOL_QUICKBOOKS_REALM_ID
 */

const CLIENT_ID = process.env.TOOL_QUICKBOOKS_CLIENT_ID;
const CLIENT_SECRET = process.env.TOOL_QUICKBOOKS_CLIENT_SECRET;
const REFRESH_TOKEN = process.env.TOOL_QUICKBOOKS_REFRESH_TOKEN;
const REALM_ID = process.env.TOOL_QUICKBOOKS_REALM_ID;
const BASE = `https://quickbooks.api.intuit.com/v3/company/${REALM_ID}`;

let cachedToken = null;
let tokenExpiry = 0;

async function getAccessToken() {
  if (cachedToken && Date.now() < tokenExpiry) return cachedToken;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch("https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer", {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: "Basic " + Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64"),
    },
    body: `grant_type=refresh_token&refresh_token=${encodeURIComponent(REFRESH_TOKEN)}`,
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) throw new Error(`QBO token refresh failed: ${resp.status}`);
  const data = await resp.json();
  cachedToken = data.access_token;
  tokenExpiry = Date.now() + (data.expires_in - 60) * 1000;
  return cachedToken;
}

async function api(path, options = {}) {
  const token = await getAccessToken();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, Accept: "application/json", "Content-Type": "application/json", ...options.headers },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`QBO ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function query(sql) {
  const data = await api(`/query?query=${encodeURIComponent(sql)}`);
  return data.QueryResponse;
}

export async function create_invoice(customerRef, lineItems) {
  const cust = typeof customerRef === "string" ? JSON.parse(customerRef) : customerRef;
  const lines = typeof lineItems === "string" ? JSON.parse(lineItems) : lineItems;
  const body = {
    CustomerRef: typeof cust === "object" ? cust : { value: cust },
    Line: lines.map((l) => ({
      Amount: l.amount,
      DetailType: "SalesItemLineDetail",
      SalesItemLineDetail: { ItemRef: { value: l.itemId }, Qty: l.qty || 1 },
      Description: l.description || "",
    })),
  };
  const data = await api("/invoice", { method: "POST", body: JSON.stringify(body) });
  return { id: data.Invoice.Id, docNumber: data.Invoice.DocNumber, totalAmt: data.Invoice.TotalAmt };
}

export async function get_invoice(invoiceId) {
  const data = await api(`/invoice/${invoiceId}`);
  return data.Invoice;
}

export async function create_payment(customerRef, amount, invoiceRef) {
  const body = {
    CustomerRef: { value: String(customerRef) },
    TotalAmt: Number(amount),
    Line: [{ Amount: Number(amount), LinkedTxn: [{ TxnId: String(invoiceRef), TxnType: "Invoice" }] }],
  };
  const data = await api("/payment", { method: "POST", body: JSON.stringify(body) });
  return { id: data.Payment.Id, totalAmt: data.Payment.TotalAmt };
}

const funcs = { query, create_invoice, get_invoice, create_payment };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
