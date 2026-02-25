/**
 * Plaid API connector for financial data.
 * Env: TOOL_PLAID_CLIENT_ID, TOOL_PLAID_SECRET, TOOL_PLAID_ENV
 */

const CLIENT_ID = process.env.TOOL_PLAID_CLIENT_ID;
const SECRET = process.env.TOOL_PLAID_SECRET;
const ENV = process.env.TOOL_PLAID_ENV || "sandbox";
const BASE = `https://${ENV}.plaid.com`;

async function api(path, body = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: CLIENT_ID, secret: SECRET, ...body }),
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`Plaid ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_accounts(accessToken) {
  const data = await api("/accounts/get", { access_token: accessToken });
  return data.accounts.map((a) => ({
    account_id: a.account_id, name: a.name, official_name: a.official_name,
    type: a.type, subtype: a.subtype, mask: a.mask,
    balances: { available: a.balances.available, current: a.balances.current, currency: a.balances.iso_currency_code },
  }));
}

export async function get_transactions(accessToken, startDate, endDate) {
  const data = await api("/transactions/get", {
    access_token: accessToken,
    start_date: startDate,
    end_date: endDate,
    options: { count: 100, offset: 0 },
  });
  return {
    total: data.total_transactions,
    transactions: data.transactions.map((t) => ({
      id: t.transaction_id, name: t.name, amount: t.amount,
      date: t.date, category: t.category, merchant_name: t.merchant_name,
      account_id: t.account_id, pending: t.pending,
    })),
  };
}

export async function get_balance(accessToken) {
  const data = await api("/accounts/balance/get", { access_token: accessToken });
  return data.accounts.map((a) => ({
    account_id: a.account_id, name: a.name,
    available: a.balances.available, current: a.balances.current,
    limit: a.balances.limit, currency: a.balances.iso_currency_code,
  }));
}

export async function get_identity(accessToken) {
  const data = await api("/identity/get", { access_token: accessToken });
  return data.accounts.map((a) => ({
    account_id: a.account_id, name: a.name,
    owners: (a.owners || []).map((o) => ({
      names: o.names, emails: o.emails?.map((e) => e.data),
      phones: o.phone_numbers?.map((p) => p.data),
      addresses: o.addresses?.map((ad) => ad.data),
    })),
  }));
}

const funcs = { get_accounts, get_transactions, get_balance, get_identity };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
