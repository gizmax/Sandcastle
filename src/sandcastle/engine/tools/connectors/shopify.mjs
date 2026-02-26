/**
 * Shopify Admin REST API connector.
 * Env: TOOL_SHOPIFY_STORE, TOOL_SHOPIFY_ACCESS_TOKEN
 */

const STORE = process.env.TOOL_SHOPIFY_STORE;
const TOKEN = process.env.TOOL_SHOPIFY_ACCESS_TOKEN;
const API_VERSION = "2024-01";
const base = () => `https://${STORE}.myshopify.com/admin/api/${API_VERSION}`;

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${base()}${path}`, {
    ...options,
    headers: { "X-Shopify-Access-Token": TOKEN, "Content-Type": "application/json", ...options.headers },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`Shopify ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_products(options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (o.limit) params.set("limit", o.limit);
  if (o.collection_id) params.set("collection_id", o.collection_id);
  const qs = params.toString();
  const data = await api(`/products.json${qs ? "?" + qs : ""}`);
  return data.products.map((p) => ({ id: p.id, title: p.title, status: p.status, vendor: p.vendor, product_type: p.product_type, variants: p.variants?.length || 0, created_at: p.created_at }));
}

export async function get_product(productId) {
  const data = await api(`/products/${productId}.json`);
  return data.product;
}

export async function get_orders(options = {}) {
  const o = typeof options === "string" ? JSON.parse(options) : options;
  const params = new URLSearchParams();
  if (o.status) params.set("status", o.status);
  if (o.created_at_min) params.set("created_at_min", o.created_at_min);
  if (o.limit) params.set("limit", o.limit);
  const qs = params.toString();
  const data = await api(`/orders.json${qs ? "?" + qs : ""}`);
  return data.orders.map((o) => ({ id: o.id, name: o.name, email: o.email, total_price: o.total_price, financial_status: o.financial_status, fulfillment_status: o.fulfillment_status, created_at: o.created_at }));
}

export async function get_order(orderId) {
  const data = await api(`/orders/${orderId}.json`);
  return data.order;
}

export async function create_product(data) {
  const d = typeof data === "string" ? JSON.parse(data) : data;
  const result = await api("/products.json", { method: "POST", body: JSON.stringify({ product: d }) });
  return { id: result.product.id, title: result.product.title, handle: result.product.handle };
}

export async function update_inventory(inventoryItemId, quantity) {
  const qty = typeof quantity === "string" ? parseInt(quantity, 10) : quantity;
  const locations = await api("/locations.json");
  const locationId = locations.locations[0]?.id;
  if (!locationId) throw new Error("No location found");
  const result = await api("/inventory_levels/set.json", {
    method: "POST",
    body: JSON.stringify({ location_id: locationId, inventory_item_id: inventoryItemId, available: qty }),
  });
  return result.inventory_level;
}

const funcs = { get_products, get_product, get_orders, get_order, create_product, update_inventory };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
