/**
 * Figma REST API v1 connector.
 * Env: TOOL_FIGMA_ACCESS_TOKEN
 */

const TOKEN = process.env.TOOL_FIGMA_ACCESS_TOKEN;
const BASE = "https://api.figma.com/v1";

async function api(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const resp = await fetch(`${BASE}${path}`, {
    ...options,
    headers: { "X-Figma-Token": TOKEN, "Content-Type": "application/json", ...options.headers },
    signal: controller.signal,
  });
  clearTimeout(timer);
  if (!resp.ok) {
    const t = await resp.text().catch(() => "");
    throw new Error(`Figma ${resp.status}: ${t.slice(0, 500)}`);
  }
  return resp.json();
}

export async function get_file(fileKey) {
  const data = await api(`/files/${fileKey}?depth=1`);
  return {
    name: data.name,
    lastModified: data.lastModified,
    version: data.version,
    pages: data.document.children.map((p) => ({
      id: p.id, name: p.name, type: p.type, childCount: p.children?.length || 0,
    })),
  };
}

export async function get_images(fileKey, nodeIds, format = "png", scale = "2") {
  const ids = typeof nodeIds === "string" ? nodeIds : nodeIds.join(",");
  const data = await api(`/images/${fileKey}?ids=${encodeURIComponent(ids)}&format=${format}&scale=${scale}`);
  return { images: data.images, err: data.err };
}

export async function get_comments(fileKey) {
  const data = await api(`/files/${fileKey}/comments`);
  return data.comments.map((c) => ({
    id: c.id,
    message: c.message,
    user: c.user?.handle,
    created_at: c.created_at,
    resolved_at: c.resolved_at,
    order_id: c.order_id,
  }));
}

export async function post_comment(fileKey, message, nodeId = null) {
  const body = { message };
  if (nodeId) body.client_meta = { node_id: nodeId, node_offset: { x: 0, y: 0 } };
  const data = await api(`/files/${fileKey}/comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  return { id: data.id, message: data.message, user: data.user?.handle };
}

export async function get_components(fileKey) {
  const data = await api(`/files/${fileKey}/components`);
  return (data.meta?.components || []).map((c) => ({
    key: c.key,
    name: c.name,
    description: c.description,
    node_id: c.node_id,
    containing_frame: c.containing_frame?.name,
  }));
}

const funcs = { get_file, get_images, get_comments, post_comment, get_components };
const [,, fn, ...args] = process.argv;
if (fn) {
  const f = funcs[fn];
  if (!f) { console.error(`Unknown: ${fn}. Available: ${Object.keys(funcs).join(", ")}`); process.exit(1); }
  const parsed = args.map((a) => { try { return JSON.parse(a); } catch { return a; } });
  f(...parsed).then((r) => console.log(JSON.stringify(r, null, 2))).catch((e) => { console.error(e.message); process.exit(1); });
}
