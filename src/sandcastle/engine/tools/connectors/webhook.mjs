/**
 * Webhook connector - generic HTTP client for REST API calls.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * No default credentials - headers/auth are passed per call.
 */

export async function post(url, body = null, headers = null) {
  const parsedBody = typeof body === "string" ? JSON.parse(body) : body;
  const parsedHeaders = typeof headers === "string" ? JSON.parse(headers) : headers;

  const opts = {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(parsedHeaders || {}),
    },
  };
  if (parsedBody) opts.body = JSON.stringify(parsedBody);

  const resp = await fetch(url, opts);
  const contentType = resp.headers.get("content-type") || "";
  let data;
  if (contentType.includes("json")) {
    data = await resp.json();
  } else {
    data = await resp.text();
  }

  return {
    status: resp.status,
    ok: resp.ok,
    data: typeof data === "string" ? data.slice(0, 50000) : data,
  };
}

export async function get(url, headers = null) {
  const parsedHeaders = typeof headers === "string" ? JSON.parse(headers) : headers;

  const resp = await fetch(url, {
    method: "GET",
    headers: parsedHeaders || {},
  });
  const contentType = resp.headers.get("content-type") || "";
  let data;
  if (contentType.includes("json")) {
    data = await resp.json();
  } else {
    data = await resp.text();
  }

  return {
    status: resp.status,
    ok: resp.ok,
    data: typeof data === "string" ? data.slice(0, 50000) : data,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("webhook.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { post, get };
  if (!dispatch[fn]) {
    console.error(`Usage: node webhook.mjs <post|get> [args...]`);
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
