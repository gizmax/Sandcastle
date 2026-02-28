/**
 * Shared utilities for Sandcastle tool connectors.
 */

// Maximum backoff/retry-after wait: 60 seconds
const MAX_WAIT_MS = 60000;

// Maximum response body size to read: 10MB
const MAX_RESPONSE_SIZE = 10 * 1024 * 1024;

/**
 * Validate that a URL is an HTTP(S) URL (prevent file:// and other schemes).
 */
function validateUrl(url) {
  if (typeof url !== "string" || url.length === 0) {
    throw new Error("URL must be a non-empty string");
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    throw new Error(`Invalid URL: ${url.slice(0, 200)}`);
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`URL scheme '${parsed.protocol}' not allowed, must be http or https`);
  }
  return url;
}

export async function fetchWithRetry(url, options = {}, { timeoutMs = 30000, retries = 2, backoffMs = 1000 } = {}) {
  validateUrl(url);
  let lastError;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const resp = await fetch(url, { ...options, signal: controller.signal });
      clearTimeout(timer);
      if (resp.status === 429 && attempt < retries) {
        const rawHeader = resp.headers.get("retry-after") || "5";
        let waitSec;
        // retry-after can be a number of seconds or an HTTP-date
        const parsed = parseInt(rawHeader, 10);
        if (!Number.isNaN(parsed) && parsed > 0) {
          waitSec = parsed;
        } else {
          // Try parsing as HTTP-date
          const date = new Date(rawHeader);
          if (!isNaN(date.getTime())) {
            waitSec = Math.max(1, Math.ceil((date.getTime() - Date.now()) / 1000));
          } else {
            waitSec = 5; // Fallback
          }
        }
        // Cap retry-after to prevent malicious servers from blocking indefinitely
        const wait = Math.min(Math.max(waitSec, 1), MAX_WAIT_MS / 1000);
        await sleep(wait * 1000);
        continue;
      }
      if (resp.status >= 500 && attempt < retries) {
        // Exponential backoff with ceiling
        const delay = Math.min(backoffMs * Math.pow(2, attempt), MAX_WAIT_MS);
        await sleep(delay);
        continue;
      }
      return resp;
    } catch (err) {
      lastError = err.name === "AbortError"
        ? new Error(`Timeout after ${timeoutMs}ms: ${url}`)
        : err;
      if (attempt < retries) {
        const delay = Math.min(backoffMs * Math.pow(2, attempt), MAX_WAIT_MS);
        await sleep(delay);
        continue;
      }
    }
  }
  throw lastError;
}

export async function parseJSON(resp) {
  const text = await resp.text();
  if (text.length > MAX_RESPONSE_SIZE) {
    throw new Error(`Response body too large (${text.length} bytes, max ${MAX_RESPONSE_SIZE})`);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(`Invalid JSON (${resp.status}): ${text.slice(0, 200)}`);
  }
}

export async function assertOk(resp, context = "") {
  if (!resp.ok) {
    let t = "";
    try {
      t = await resp.text();
    } catch {
      // Ignore read errors on error responses
    }
    // Mask potential credentials in error response body
    const sanitized = maskSecrets(t);
    const prefix = context ? `${context} ` : "";
    throw new Error(`${prefix}HTTP ${resp.status}: ${sanitized.slice(0, 500)}`);
  }
  return resp;
}

/**
 * Basic masking of common secret patterns in text to avoid leaking credentials
 * in error messages and logs.
 */
function maskSecrets(text) {
  if (!text || typeof text !== "string") return text || "";
  // Mask Bearer tokens, API keys, Basic auth
  return text
    .replace(/Bearer\s+[A-Za-z0-9\-._~+\/]+=*/g, "Bearer ***")
    .replace(/Basic\s+[A-Za-z0-9+\/]+=*/g, "Basic ***")
    .replace(/(?:api[_-]?key|token|secret|password|authorization)\s*[:=]\s*["']?[^\s"',}]{8,}/gi, (match) => {
      const eqIdx = match.search(/[:=]/);
      return match.slice(0, eqIdx + 1) + " ***";
    });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}
