/**
 * Notion connector - search, read, and create pages via Notion API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_NOTION_API_KEY
 */

const TOKEN = process.env.TOOL_NOTION_API_KEY || "";
const BASE = "https://api.notion.com/v1";

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      "Authorization": `Bearer ${TOKEN}`,
      "Notion-Version": "2022-06-28",
      "Content-Type": "application/json",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  clearTimeout(timer);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Notion API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function search(query, filter_type = null) {
  const payload = { query };
  if (filter_type) payload.filter = { value: filter_type, property: "object" };
  const data = await api("/search", "POST", payload);
  return data.results.map((r) => ({
    id: r.id,
    type: r.object,
    title: r.properties?.title?.title?.[0]?.plain_text
      || r.properties?.Name?.title?.[0]?.plain_text
      || r.title?.[0]?.plain_text
      || "(untitled)",
    url: r.url,
  }));
}

export async function get_page(page_id) {
  const [page, blocks] = await Promise.all([
    api(`/pages/${page_id}`),
    api(`/blocks/${page_id}/children`),
  ]);

  // Extract text content from blocks
  const content = blocks.results.map((b) => {
    const type = b.type;
    const textArr = b[type]?.rich_text || b[type]?.text || [];
    return textArr.map((t) => t.plain_text).join("");
  }).filter(Boolean);

  return {
    id: page.id,
    url: page.url,
    created: page.created_time,
    content: content.join("\n"),
  };
}

export async function create_page(parent_id, title, content = "") {
  const children = content
    ? [{
        object: "block",
        type: "paragraph",
        paragraph: {
          rich_text: [{ type: "text", text: { content } }],
        },
      }]
    : [];

  const data = await api("/pages", "POST", {
    parent: { page_id: parent_id },
    properties: {
      title: { title: [{ text: { content: title } }] },
    },
    children,
  });
  return { id: data.id, url: data.url };
}

// CLI dispatch
if (process.argv[1]?.endsWith("notion.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { search, get_page, create_page };
  if (!dispatch[fn]) {
    console.error(`Usage: node notion.mjs <search|get_page|create_page> [args...]`);
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
