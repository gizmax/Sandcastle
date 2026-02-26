/**
 * ABRA Gen (ABRA Software) connector - firms, invoices, orders, store cards.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_ABRA_BASE_URL, TOOL_ABRA_USERNAME, TOOL_ABRA_PASSWORD
 */

const BASE_URL = process.env.TOOL_ABRA_BASE_URL || "";
const USERNAME = process.env.TOOL_ABRA_USERNAME || "";
const PASSWORD = process.env.TOOL_ABRA_PASSWORD || "";

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const auth = Buffer.from(`${USERNAME}:${PASSWORD}`).toString("base64");
  const opts = {
    method,
    headers: {
      Authorization: `Basic ${auth}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const resp = await fetch(`${BASE_URL}/api/v1${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`ABRA API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function get_firms(query = "", limit = 20) {
  let path = `/firms?limit=${limit}`;
  if (query) path += `&search=${encodeURIComponent(query)}`;
  const data = await api(path);
  return (data.items || []).map((f) => ({
    id: f.id,
    name: f.name,
    ico: f.ico || null,
    dic: f.dic || null,
    email: f.email || null,
    city: f.city || null,
  }));
}

export async function get_issued_invoices(firm_id = "", status = "", limit = 20) {
  let path = `/issued-invoices?limit=${limit}`;
  if (firm_id) path += `&firm_id=${firm_id}`;
  if (status) path += `&status=${status}`;
  const data = await api(path);
  return (data.items || []).map((inv) => ({
    id: inv.id,
    document_number: inv.document_number,
    firm_name: inv.firm_name,
    total: inv.total_amount,
    currency: inv.currency || "CZK",
    status: inv.status,
    date_issued: inv.date_issued,
    date_due: inv.date_due,
  }));
}

export async function create_issued_invoice(firm_id, rows = [], due_days = 14) {
  const body = {
    firm_id,
    due_days,
    rows: rows.map((r) => ({
      text: r.text,
      quantity: r.quantity || 1,
      unit_price: r.unit_price,
    })),
  };
  const data = await api("/issued-invoices", "POST", body);
  return {
    id: data.id,
    document_number: data.document_number,
    total: data.total_amount,
  };
}

export async function get_orders(firm_id = "", limit = 20) {
  let path = `/sales-orders?limit=${limit}`;
  if (firm_id) path += `&firm_id=${firm_id}`;
  const data = await api(path);
  return (data.items || []).map((o) => ({
    id: o.id,
    document_number: o.document_number,
    firm_name: o.firm_name,
    total: o.total_amount,
    status: o.status,
    date_created: o.date_created,
  }));
}

export async function get_store_cards(query = "", store_id = "") {
  let path = "/store-cards";
  const params = [];
  if (query) params.push(`search=${encodeURIComponent(query)}`);
  if (store_id) params.push(`store_id=${store_id}`);
  if (params.length) path += `?${params.join("&")}`;
  const data = await api(path);
  return (data.items || []).map((s) => ({
    id: s.id,
    code: s.code,
    name: s.name,
    store_name: s.store_name,
    quantity: s.quantity_available,
    unit: s.unit,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("abra.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = {
    get_firms,
    get_issued_invoices,
    create_issued_invoice,
    get_orders,
    get_store_cards,
  };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node abra.mjs <${Object.keys(dispatch).join("|")}> [args...]`,
    );
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
