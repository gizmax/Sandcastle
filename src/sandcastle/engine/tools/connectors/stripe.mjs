/**
 * Stripe connector - manage customers, invoices, and payments via Stripe REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_STRIPE_SECRET_KEY
 */

const SECRET_KEY = process.env.TOOL_STRIPE_SECRET_KEY || "";
const BASE = "https://api.stripe.com/v1";

function flattenParams(obj, prefix = "") {
  const result = {};
  for (const [key, value] of Object.entries(obj)) {
    const fullKey = prefix ? `${prefix}[${key}]` : key;
    if (value !== null && value !== undefined) {
      if (typeof value === "object" && !Array.isArray(value)) {
        Object.assign(result, flattenParams(value, fullKey));
      } else {
        result[fullKey] = String(value);
      }
    }
  }
  return result;
}

async function api(path, method = "GET", params = null) {
  const opts = {
    method,
    headers: {
      "Authorization": `Basic ${Buffer.from(SECRET_KEY + ":").toString("base64")}`,
    },
  };
  if (params && method !== "GET") {
    opts.headers["Content-Type"] = "application/x-www-form-urlencoded";
    opts.body = new URLSearchParams(flattenParams(params)).toString();
  }
  if (params && method === "GET") {
    path += "?" + new URLSearchParams(flattenParams(params)).toString();
  }
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Stripe API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function list_customers(email = "", limit = 20) {
  const params = { limit };
  if (email) params.email = email;
  const data = await api("/customers", "GET", params);
  return data.data.map((c) => ({
    id: c.id,
    email: c.email,
    name: c.name,
    created: c.created,
  }));
}

export async function get_customer(customer_id) {
  const data = await api(`/customers/${customer_id}`);
  return {
    id: data.id,
    email: data.email,
    name: data.name,
    created: data.created,
    subscriptions: data.subscriptions?.data?.map((s) => ({
      id: s.id,
      status: s.status,
      plan: s.plan?.nickname,
    })) || [],
  };
}

export async function list_invoices(customer_id = "", status = "", limit = 20) {
  const params = { limit };
  if (customer_id) params.customer = customer_id;
  if (status) params.status = status;
  const data = await api("/invoices", "GET", params);
  return data.data.map((i) => ({
    id: i.id,
    customer: i.customer,
    status: i.status,
    amount_due: i.amount_due,
    currency: i.currency,
    created: i.created,
  }));
}

export async function create_invoice(customer_id, items = [], description = "") {
  const invoice = await api("/invoices", "POST", {
    customer: customer_id,
    description,
    auto_advance: "false",
  });
  for (const item of items) {
    await api("/invoiceitems", "POST", {
      customer: customer_id,
      invoice: invoice.id,
      amount: item.amount,
      currency: item.currency || "usd",
      description: item.description || "",
    });
  }
  return {
    id: invoice.id,
    customer: invoice.customer,
    status: invoice.status,
    items_added: items.length,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("stripe.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_customers, get_customer, list_invoices, create_invoice };
  if (!dispatch[fn]) {
    console.error(`Usage: node stripe.mjs <list_customers|get_customer|list_invoices|create_invoice> [args...]`);
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
