/**
 * Firecrawl connector - web scraping and crawling via Firecrawl API v1.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_FIRECRAWL_API_KEY
 */

const API_KEY = process.env.TOOL_FIRECRAWL_API_KEY || "";
const BASE = "https://api.firecrawl.dev/v1";
const TIMEOUT = 30_000;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function api(path, method = "GET", body = null) {
  const { signal, clear } = makeSignal();
  try {
    const opts = {
      method,
      signal,
      headers: {
        "Authorization": `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
      },
    };
    if (body !== null) opts.body = typeof body === "string" ? body : JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Firecrawl API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function scrape(url, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = { url };
  if (opts.formats) payload.formats = opts.formats;
  if (opts.onlyMainContent !== undefined) payload.onlyMainContent = opts.onlyMainContent;
  if (opts.includeTags) payload.includeTags = opts.includeTags;
  if (opts.excludeTags) payload.excludeTags = opts.excludeTags;
  if (opts.waitFor) payload.waitFor = opts.waitFor;
  const data = await api("/scrape", "POST", payload);
  return {
    success: data.success,
    markdown: data.data?.markdown,
    html: data.data?.html,
    links: data.data?.links,
    metadata: data.data?.metadata,
  };
}

export async function crawl(url, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = { url };
  if (opts.limit) payload.limit = opts.limit;
  if (opts.maxDepth) payload.maxDepth = opts.maxDepth;
  if (opts.includePaths) payload.includePaths = opts.includePaths;
  if (opts.excludePaths) payload.excludePaths = opts.excludePaths;
  if (opts.allowBackwardLinks !== undefined) payload.allowBackwardLinks = opts.allowBackwardLinks;
  const data = await api("/crawl", "POST", payload);
  return {
    success: data.success,
    id: data.id,
    url: data.url,
  };
}

export async function get_crawl_status(crawlId) {
  if (!crawlId || typeof crawlId !== "string") throw new Error("crawlId is required");
  const safeId = encodeURIComponent(crawlId);
  const data = await api(`/crawl/${safeId}`);
  return {
    status: data.status,
    total: data.total,
    completed: data.completed,
    creditsUsed: data.creditsUsed,
    data: (data.data || []).map((page) => ({
      url: page.metadata?.sourceURL,
      markdown: page.markdown?.slice(0, 500),
      title: page.metadata?.title,
    })),
  };
}

export async function map(url) {
  const data = await api("/map", "POST", { url });
  return {
    success: data.success,
    links: data.links || [],
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("firecrawl.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { scrape, crawl, get_crawl_status, map };
  if (!dispatch[fn]) {
    console.error(`Usage: node firecrawl.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
