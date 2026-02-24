/**
 * SAP S/4HANA connector - business partners, sales orders, and materials.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SAP_BASE_URL, TOOL_SAP_API_KEY
 */

const SAP_BASE_URL = process.env.TOOL_SAP_BASE_URL || "";
const API_KEY = process.env.TOOL_SAP_API_KEY || "";
const BASE = `${SAP_BASE_URL}/sap/opu/odata/sap`;

async function api(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      "APIKey": API_KEY,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`SAP API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_business_partners(query = "", limit = 20) {
  let path = `/API_BUSINESS_PARTNER/A_BusinessPartner?$top=${limit}&$format=json`;
  if (query) {
    path += `&$filter=substringof('${query}',BusinessPartnerFullName)`;
  }
  const data = await api(path);
  return (data.d?.results || []).map(bp => ({
    id: bp.BusinessPartner,
    name: bp.BusinessPartnerFullName,
    category: bp.BusinessPartnerCategory,
  }));
}

export async function get_sales_orders(customer_id = "", status = "", limit = 20) {
  let path = `/API_SALES_ORDER_SRV/A_SalesOrder?$top=${limit}&$format=json`;
  const filters = [];
  if (customer_id) filters.push(`SoldToParty eq '${customer_id}'`);
  if (status) filters.push(`OverallSDProcessStatus eq '${status}'`);
  if (filters.length) path += `&$filter=${filters.join(" and ")}`;
  const data = await api(path);
  return (data.d?.results || []).map(so => ({
    id: so.SalesOrder,
    customer: so.SoldToParty,
    date: so.SalesOrderDate,
    status: so.OverallSDProcessStatus,
    amount: so.TotalNetAmount,
    currency: so.TransactionCurrency,
  }));
}

export async function create_sales_order(customer_id, items = []) {
  const body = {
    SoldToParty: customer_id,
    SalesOrderType: "OR",
    to_Item: {
      results: items.map(i => ({
        Material: i.material,
        RequestedQuantity: String(i.quantity || 1),
      })),
    },
  };
  const data = await api("/API_SALES_ORDER_SRV/A_SalesOrder", "POST", body);
  return { id: data.d.SalesOrder, customer: data.d.SoldToParty };
}

export async function get_material(material_id) {
  const data = await api(`/API_PRODUCT_SRV/A_Product('${material_id}')?$format=json`);
  const d = data.d;
  return {
    id: d.Product,
    description: d.ProductDescription,
    type: d.ProductType,
    group: d.ProductGroup,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("sap.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { get_business_partners, get_sales_orders, create_sales_order, get_material };
  if (!dispatch[fn]) {
    console.error(`Usage: node sap.mjs <get_business_partners|get_sales_orders|create_sales_order|get_material> [args...]`);
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
