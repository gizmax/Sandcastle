/**
 * Keyless web search + clean content extraction. FREE, no API key required.
 *
 *   search:  DuckDuckGo Lite (keyless) for result links, then Jina Reader
 *            (https://r.jina.ai, keyless) to pull clean Markdown for the top hits.
 *            If TOOL_JINA_API_KEY is set, upgrade to s.jina.ai (one-shot, higher
 *            quality). Output shape mirrors tavily.mjs exactly (drop-in tool swap).
 *   extract: Jina Reader (keyless).
 *
 * Credentials: NONE required. TOOL_JINA_API_KEY is OPTIONAL (lifts quality + limits).
 * Note: the keyless DuckDuckGo path is best-effort (HTML scrape, rate-limited); add a
 * free Jina key or fall back to the paid `tavily` tool for production reliability.
 */

const JINA_KEY = process.env.TOOL_JINA_API_KEY || "";
const DDG_LITE = "https://lite.duckduckgo.com/lite/";
const JINA_SEARCH = "https://s.jina.ai";
const JINA_READER = "https://r.jina.ai";
const UA = "Mozilla/5.0 (compatible; SandcastleBot/1.0)";
const TIMEOUT = 40_000;
const EXTRACT_TOP_N = 5;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

async function fetchText(url, init = {}) {
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(url, { signal, headers: { "User-Agent": UA, ...(init.headers || {}) }, ...init });
    if (!resp.ok) throw new Error(`${url} -> HTTP ${resp.status}`);
    return resp.text();
  } finally {
    clear();
  }
}

async function fetchJson(url, init = {}) {
  const text = await fetchText(url, { headers: { Accept: "application/json", ...(init.headers || {}) }, ...init });
  return JSON.parse(text);
}

function jinaAuth() {
  return JINA_KEY ? { Authorization: `Bearer ${JINA_KEY}` } : {};
}

function decodeEntities(s) {
  return (s || "")
    .replace(/<[^>]+>/g, "")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&nbsp;/g, " ")
    .trim();
}

/** DuckDuckGo Lite keyless search -> [{title, url, snippet}]. Best-effort HTML parse. */
async function ddgSearch(query, maxResults) {
  const html = await fetchText(DDG_LITE, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: `q=${encodeURIComponent(query)}`,
  });
  const links = [...html.matchAll(/<a[^>]*class=["']result-link["'][^>]*href=["']([^"']+)["'][^>]*>(.*?)<\/a>/gis)];
  const snippets = [...html.matchAll(/<td[^>]*class=["']result-snippet["'][^>]*>(.*?)<\/td>/gis)].map((m) => decodeEntities(m[1]));
  const out = [];
  for (let i = 0; i < links.length && out.length < (maxResults || 8); i++) {
    let href = links[i][1];
    const uddg = href.match(/[?&]uddg=([^&]+)/);
    if (uddg) href = decodeURIComponent(uddg[1]);
    if (!/^https?:\/\//.test(href)) continue;
    out.push({ title: decodeEntities(links[i][2]) || href, url: href, snippet: snippets[i] || "" });
  }
  return out;
}

async function jinaRead(url) {
  const data = await fetchJson(`${JINA_READER}/${url}`, { headers: jinaAuth() });
  const doc = data?.data ?? data;
  return doc?.content ?? "";
}

/** Web search returning clean, LLM-ready content. Same shape as tavily.search. */
export async function search(query, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const max = opts.max_results || 5;

  // Best path: keyed Jina returns search + content in one shot.
  if (JINA_KEY) {
    try {
      const data = await fetchJson(`${JINA_SEARCH}/?q=${encodeURIComponent(query)}`, { headers: jinaAuth() });
      const items = (Array.isArray(data?.data) ? data.data : []).slice(0, max);
      return {
        answer: null,
        results: items.map((r) => ({
          title: r.title ?? r.url ?? "",
          url: r.url,
          content: r.content ?? r.description ?? "",
          score: null,
          published_date: r.date ?? null,
        })),
        query,
      };
    } catch {
      // fall through to the keyless path
    }
  }

  // Keyless path: DDG Lite for links, Jina Reader for clean content on the top hits.
  const hits = await ddgSearch(query, max);
  const results = [];
  for (let i = 0; i < hits.length; i++) {
    let content = hits[i].snippet;
    if (i < EXTRACT_TOP_N) {
      try {
        const full = await jinaRead(hits[i].url);
        if (full) content = full;
      } catch {
        // keep the snippet as content
      }
    }
    results.push({ title: hits[i].title, url: hits[i].url, content, score: null, published_date: null });
  }
  // Keyless search backends are frequently bot-blocked. If we got nothing, throw so
  // the agent falls through to the next tool in the list (e.g. paid tavily) instead
  // of silently returning an empty result set.
  if (results.length === 0) {
    throw new Error(
      "websearch: no keyless results (DuckDuckGo blocked). Set TOOL_JINA_API_KEY (free, no card) or configure the tavily fallback."
    );
  }
  return { answer: null, results, query };
}

/** Extract clean content for one or more URLs (keyless). Same shape as tavily.extract. */
export async function extract(urls) {
  const urlList = typeof urls === "string" ? JSON.parse(urls) : urls;
  const list = Array.isArray(urlList) ? urlList : [urlList];
  const results = [];
  const failed = [];
  for (const url of list) {
    try {
      results.push({ url, raw_content: await jinaRead(url) });
    } catch (err) {
      failed.push({ url, error: err.message });
    }
  }
  return { results, failed };
}

// CLI dispatch
if (process.argv[1]?.endsWith("websearch.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { search, extract };
  if (!dispatch[fn]) {
    console.error(`Usage: node websearch.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
    process.exit(1);
  }
  try {
    console.log(JSON.stringify(await dispatch[fn](...args), null, 2));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  }
}
