/**
 * Zapier webhook connector - trigger Zaps from Sandcastle workflows.
 * Env: TOOL_ZAPIER_WEBHOOK_URL
 */

const WEBHOOK_URL = process.env.TOOL_ZAPIER_WEBHOOK_URL;

function makeCtrl() {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), 30000);
  return { signal: c.signal, clear: () => clearTimeout(t) };
}

export async function trigger(data) {
  if (!WEBHOOK_URL) throw new Error("TOOL_ZAPIER_WEBHOOK_URL not set");
  const { signal, clear } = makeCtrl();
  try {
    const resp = await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(typeof data === "string" ? JSON.parse(data) : data),
      signal,
    });
    clear();
    const text = await resp.text();
    return { status: resp.status, response: text.slice(0, 1000) };
  } catch (err) {
    clear();
    throw new Error(`Zapier trigger failed: ${err.message}`);
  }
}

export async function trigger_with_response(data) {
  if (!WEBHOOK_URL) throw new Error("TOOL_ZAPIER_WEBHOOK_URL not set");
  const { signal, clear } = makeCtrl();
  try {
    const resp = await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(typeof data === "string" ? JSON.parse(data) : data),
      signal,
    });
    clear();
    const text = await resp.text();
    try {
      return { status: resp.status, data: JSON.parse(text) };
    } catch {
      return { status: resp.status, data: text.slice(0, 5000) };
    }
  } catch (err) {
    clear();
    throw new Error(`Zapier trigger failed: ${err.message}`);
  }
}

const funcs = { trigger, trigger_with_response };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
