/**
 * Keyless web search + clean content extraction. FREE, NO API KEY REQUIRED.
 *
 *   search:  Jina Reader (https://r.jina.ai, keyless) renders a DuckDuckGo SERP
 *            server-side - this bypasses the bot-blocks that hit a direct scrape -
 *            we parse the organic results, then Jina Reader pulls clean Markdown
 *            for the top hits. If TOOL_JINA_API_KEY is set, upgrade to s.jina.ai
 *            (one-shot). Output shape mirrors tavily.mjs exactly (drop-in swap).
 *   extract: Jina Reader (keyless).
 *
 * Credentials: NONE required. TOOL_JINA_API_KEY is OPTIONAL (higher quality + limits).
 */

const JINA_KEY = process.env.TOOL_JINA_API_KEY || "";
const JINA_READER = "https://r.jina.ai";
const JINA_SEARCH = "https://s.jina.ai";
const SERP_URL = "https://html.duckduckgo.com/html/?q="; // read THROUGH the keyless reader
const UA = "Mozilla/5.0 (compatible; SandcastleBot/1.0)";
const TIMEOUT = 45_000;
const EXTRACT_TOP_N = 5;

function makeSignal() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT);
  return { signal: controller.signal, clear: () => clearTimeout(timer) };
}

function jinaAuth() {
  return JINA_KEY ? { Authorization: `Bearer ${JINA_KEY}` } : {};
}

/** GET a URL through the keyless Jina Reader and return its { title, url, content }. */
async function readerGet(target) {
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(`${JINA_READER}/${target}`, {
      method: "GET",
      signal,
      headers: { Accept: "application/json", "User-Agent": UA, ...jinaAuth() },
    });
    if (!resp.ok) throw new Error(`Jina Reader ${resp.status}`);
    const data = await resp.json();
    return data?.data ?? data;
  } finally {
    clear();
  }
}

/** Parse the Reader's Markdown of a DuckDuckGo SERP into organic results. */
function parseSerp(markdown, max) {
  const blocks = (markdown || "").split(/\n##\s+/).slice(1); // each: "[title](url)\n snippet..."
  const out = [];
  for (const b of blocks) {
    const m = b.match(/^\[([^\]]*)\]\((https?:\/\/[^)]+)\)/);
    if (!m) continue;
    let url = m[2];
    const uddg = url.match(/[?&]uddg=([^&]+)/);
    if (uddg) {
      try { url = decodeURIComponent(uddg[1]); } catch { /* keep raw */ }
    }
    // Drop ads + DuckDuckGo-internal links; keep organic external results only.
    if (!/^https?:\/\//.test(url) || url.includes("duckduckgo.com") || url.includes("/y.js")) continue;
    const snippet = b
      .slice(m[0].length)
      .replace(/\[[^\]]*\]\([^)]*\)/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 300);
    out.push({ title: m[1] || url, url, snippet });
    if (out.length >= max) break;
  }
  return out;
}

async function jinaRead(url) {
  const doc = await readerGet(url);
  return doc?.content ?? "";
}

/** Web search returning clean, LLM-ready content. Same shape as tavily.search. */
export async function search(query, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const max = opts.max_results || 5;

  // Optional upgrade: keyed Jina returns search + content in one shot.
  if (JINA_KEY) {
    try {
      const { signal, clear } = makeSignal();
      try {
        const resp = await fetch(`${JINA_SEARCH}/?q=${encodeURIComponent(query)}`, {
          method: "GET",
          signal,
          headers: { Accept: "application/json", ...jinaAuth() },
        });
        if (resp.ok) {
          const data = await resp.json();
          const items = (Array.isArray(data?.data) ? data.data : []).slice(0, max);
          if (items.length) {
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
          }
        }
      } finally {
        clear();
      }
    } catch {
      /* fall through to keyless */
    }
  }

  // Keyless: read a DuckDuckGo SERP through Jina Reader, then read the top hits.
  const serp = await readerGet(`${SERP_URL}${encodeURIComponent(query)}`);
  const hits = parseSerp(serp?.content, max);
  const results = [];
  for (let i = 0; i < hits.length; i++) {
    let content = hits[i].snippet;
    if (i < EXTRACT_TOP_N) {
      try {
        const full = await jinaRead(hits[i].url);
        if (full) content = full;
      } catch { /* keep snippet */ }
    }
    results.push({ title: hits[i].title, url: hits[i].url, content, score: null, published_date: null });
  }
  if (results.length === 0) {
    throw new Error("websearch: no results (search backend unavailable). Try the tavily fallback.");
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
