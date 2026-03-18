/**
 * Exa connector - neural web search via Exa API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_EXA_API_KEY
 */

const API_KEY = process.env.TOOL_EXA_API_KEY || "";
const BASE = "https://api.exa.ai";
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
      headers: {
        "Content-Type": "application/json",
        "x-api-key": API_KEY,
      },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      throw new Error(`Exa API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clear();
  }
}

export async function search(query, num_results = "10", type = "auto", use_autoprompt = "false") {
  const numRes = typeof num_results === "string" ? parseInt(num_results, 10) : num_results;
  const autoprompt = use_autoprompt === "true" || use_autoprompt === true;
  const data = await api("/search", {
    query,
    numResults: numRes,
    type,
    useAutoprompt: autoprompt,
  });
  return {
    results: (data.results || []).map((r) => ({
      id: r.id,
      url: r.url,
      title: r.title,
      score: r.score,
      publishedDate: r.publishedDate,
      author: r.author,
    })),
    autopromptString: data.autopromptString || null,
    resolvedSearchType: data.resolvedSearchType || null,
  };
}

export async function get_contents(ids, text = "true", highlights = "false") {
  const idList = typeof ids === "string" ? JSON.parse(ids) : ids;
  const includeText = text === "true" || text === true;
  const includeHighlights = highlights === "true" || highlights === true;
  const payload = { ids: idList };
  if (includeText) payload.text = true;
  if (includeHighlights) payload.highlights = true;
  const data = await api("/contents", payload);
  return {
    results: (data.results || []).map((r) => ({
      id: r.id,
      url: r.url,
      title: r.title,
      text: r.text || null,
      highlights: r.highlights || null,
      publishedDate: r.publishedDate,
      author: r.author,
    })),
  };
}

export async function find_similar(url, num_results = "10") {
  const numRes = typeof num_results === "string" ? parseInt(num_results, 10) : num_results;
  const data = await api("/findSimilar", { url, numResults: numRes });
  return {
    results: (data.results || []).map((r) => ({
      id: r.id,
      url: r.url,
      title: r.title,
      score: r.score,
      publishedDate: r.publishedDate,
      author: r.author,
    })),
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("exa.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { search, get_contents, find_similar };
  if (!dispatch[fn]) {
    console.error(`Usage: node exa.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
