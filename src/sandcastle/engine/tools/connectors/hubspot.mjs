/**
 * HubSpot connector - manage contacts, companies, and deals.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_HUBSPOT_API_KEY
 */

const TOKEN = process.env.TOOL_HUBSPOT_API_KEY || "";
const BASE = "https://api.hubapi.com";

async function api(path, method = "GET", body = null) {
  const opts = {
    method,
    headers: {
      "Authorization": `Bearer ${TOKEN}`,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`HubSpot API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_contacts(query = "", limit = 20) {
  if (query) {
    const data = await api("/crm/v3/objects/contacts/search", "POST", {
      filterGroups: [{
        filters: [{
          propertyName: "email",
          operator: "CONTAINS_TOKEN",
          value: query,
        }],
      }],
      limit,
    });
    return data.results.map(formatContact);
  }
  const data = await api(`/crm/v3/objects/contacts?limit=${limit}`);
  return data.results.map(formatContact);
}

function formatContact(c) {
  return {
    id: c.id,
    email: c.properties?.email,
    firstname: c.properties?.firstname,
    lastname: c.properties?.lastname,
    company: c.properties?.company,
  };
}

export async function create_contact(email, firstname = "", lastname = "", company = "") {
  const properties = { email };
  if (firstname) properties.firstname = firstname;
  if (lastname) properties.lastname = lastname;
  if (company) properties.company = company;
  const data = await api("/crm/v3/objects/contacts", "POST", { properties });
  return { id: data.id, ...data.properties };
}

export async function create_deal(dealname, amount = null, pipeline = "default", dealstage = "") {
  const properties = { dealname };
  if (amount !== null) properties.amount = String(amount);
  if (pipeline) properties.pipeline = pipeline;
  if (dealstage) properties.dealstage = dealstage;
  const data = await api("/crm/v3/objects/deals", "POST", { properties });
  return { id: data.id, ...data.properties };
}

// CLI dispatch
if (process.argv[1]?.endsWith("hubspot.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { get_contacts, create_contact, create_deal };
  if (!dispatch[fn]) {
    console.error(`Usage: node hubspot.mjs <get_contacts|create_contact|create_deal> [args...]`);
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
