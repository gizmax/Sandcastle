/**
 * Shared utilities for Sandcastle tool connectors.
 */

export async function fetchWithRetry(url, options = {}, { timeoutMs = 30000, retries = 2, backoffMs = 1000 } = {}) {
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const resp = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(timer);
      if (resp.status === 429 && attempt < retries) {
        const wait = parseInt(resp.headers.get("retry-after") || "5", 10);
        await sleep(wait * 1000);
        continue;
      }
      if (resp.status >= 500 && attempt < retries) {
        await sleep(backoffMs * Math.pow(2, attempt));
        continue;
      }
      return resp;
    } catch (err) {
      lastError = err.name === "AbortError"
        ? new Error(`Timeout after ${timeoutMs}ms: ${url}`)
        : err;
      if (attempt < retries) {
        await sleep(backoffMs * Math.pow(2, attempt));
        continue;
      }
    }
  }
  throw lastError;
}

export async function parseJSON(resp) {
  const text = await resp.text();
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Invalid JSON (${resp.status}): ${text.slice(0, 200)}`);
  }
}

export async function assertOk(resp, context = "") {
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`${context} HTTP ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp;
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
