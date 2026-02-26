/**
 * Helios iNuvio (Asseco Solutions) connector - contacts, invoices, orders, stock.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_HELIOS_BASE_URL, TOOL_HELIOS_API_KEY
 */

const BASE_URL = process.env.TOOL_HELIOS_BASE_URL || "";
const API_KEY = process.env.TOOL_HELIOS_API_KEY || "";

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${API_KEY}`,
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
      throw new Error(`Helios API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function get_contacts(query = "", limit = 20) {
  let path = `/contacts?limit=${limit}`;
  if (query) path += `&search=${encodeURIComponent(query)}`;
  const data = await api(path);
  return (data.items || []).map((c) => ({
    id: c.id,
    name: c.name,
    ico: c.ico || null,
    dic: c.dic || null,
    email: c.email || null,
    city: c.city || null,
  }));
}

export async function get_invoices(contact_id = "", status = "", limit = 20) {
  let path = `/invoices/issued?limit=${limit}`;
  if (contact_id) path += `&contact_id=${contact_id}`;
  if (status) path += `&status=${status}`;
  const data = await api(path);
  return (data.items || []).map((inv) => ({
    id: inv.id,
    number: inv.document_number,
    contact: inv.contact_name,
    total: inv.total_amount,
    currency: inv.currency || "CZK",
    status: inv.status,
    issued_on: inv.issued_on,
    due_on: inv.due_on,
  }));
}

export async function create_invoice(contact_id, items = [], due_days = 14) {
  const body = {
    contact_id,
    due_days,
    items: items.map((i) => ({
      description: i.description,
      quantity: i.quantity || 1,
      unit_price: i.unit_price,
    })),
  };
  const data = await api("/invoices/issued", "POST", body);
  return {
    id: data.id,
    number: data.document_number,
    total: data.total_amount,
  };
}

export async function get_orders(status = "", limit = 20) {
  let path = `/orders/sales?limit=${limit}`;
  if (status) path += `&status=${status}`;
  const data = await api(path);
  return (data.items || []).map((o) => ({
    id: o.id,
    number: o.document_number,
    contact: o.contact_name,
    total: o.total_amount,
    status: o.status,
    created_on: o.created_on,
  }));
}

export async function get_stock(product_code = "", warehouse_id = "") {
  let path = "/stock";
  const params = [];
  if (product_code) params.push(`product_code=${encodeURIComponent(product_code)}`);
  if (warehouse_id) params.push(`warehouse_id=${warehouse_id}`);
  if (params.length) path += `?${params.join("&")}`;
  const data = await api(path);
  return (data.items || []).map((s) => ({
    product_code: s.product_code,
    product_name: s.product_name,
    warehouse: s.warehouse_name,
    quantity: s.quantity,
    unit: s.unit,
  }));
}

// CLI dispatch
if (process.argv[1]?.endsWith("helios.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { get_contacts, get_invoices, create_invoice, get_orders, get_stock };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node helios.mjs <${Object.keys(dispatch).join("|")}> [args...]`,
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
