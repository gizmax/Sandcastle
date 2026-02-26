/**
 * Tavily connector - AI-powered web search and content extraction via Tavily API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_TAVILY_API_KEY
 */

const API_KEY = process.env.TOOL_TAVILY_API_KEY || "";
const BASE = "https://api.tavily.com";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function api(path, body) {
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(`${BASE}${path}`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: API_KEY, ...body }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Tavily API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function search(query, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = { query };
  if (opts.search_depth) payload.search_depth = opts.search_depth;
  if (opts.include_answer !== undefined) payload.include_answer = opts.include_answer;
  if (opts.include_raw_content !== undefined) payload.include_raw_content = opts.include_raw_content;
  if (opts.max_results) payload.max_results = opts.max_results;
  if (opts.include_domains) payload.include_domains = opts.include_domains;
  if (opts.exclude_domains) payload.exclude_domains = opts.exclude_domains;
  if (opts.topic) payload.topic = opts.topic;
  const data = await api("/search", payload);
  return {
    answer: data.answer || null,
    results: (data.results || []).map((r) => ({
      title: r.title,
      url: r.url,
      content: r.content,
      score: r.score,
      published_date: r.published_date,
    })),
    query: data.query,
  };
}

export async function extract(urls) {
  const urlList = typeof urls === "string" ? JSON.parse(urls) : urls;
  const data = await api("/extract", { urls: urlList });
  return {
    results: (data.results || []).map((r) => ({
      url: r.url,
      raw_content: r.raw_content,
    })),
    failed: data.failed_results || [],
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("tavily.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { search, extract };
  if (!dispatch[fn]) {
    console.error(`Usage: node tavily.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
