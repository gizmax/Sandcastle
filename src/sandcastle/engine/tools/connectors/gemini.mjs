/**
 * Gemini connector - vision analysis / judging via the Gemini API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Complements nano-banana (which only generates): this lets a workflow SEE
 * images and reason about them - e.g. score a generated UGC shot against the
 * original product reference. Pure HTTPS JSON, no extra dependencies.
 *
 * Credentials: TOOL_GEMINI_API_KEY (falls back to GEMINI_API_KEY /
 * TOOL_NANO_BANANA_API_KEY so it reuses an existing nano-banana setup).
 */

import { readFileSync } from "node:fs";

const API_KEY =
  process.env.TOOL_GEMINI_API_KEY ||
  process.env.GEMINI_API_KEY ||
  process.env.TOOL_NANO_BANANA_API_KEY ||
  "";
const BASE = "https://generativelanguage.googleapis.com/v1beta/models";
const TIMEOUT = 120_000;

const MIME_BY_EXT = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp",
};

// Build an inline_data image part from a local path (base64) or fetch a URL.
async function imagePart(ref, signal) {
  let bytes;
  let ext;
  if (/^https?:\/\//i.test(ref)) {
    const resp = await fetch(ref, { signal });
    if (!resp.ok) throw new Error(`Failed to fetch image ${ref}: ${resp.status}`);
    bytes = Buffer.from(await resp.arrayBuffer());
    ext = (ref.split(".").pop() || "png").toLowerCase();
  } else {
    bytes = readFileSync(ref);
    ext = (ref.split(".").pop() || "png").toLowerCase();
  }
  return {
    inline_data: { mime_type: MIME_BY_EXT[ext] || "image/png", data: bytes.toString("base64") },
  };
}

/**
 * Vision analysis / judging. Sends image(s) + a text prompt to a Gemini vision
 * model and returns the answer. Returns parsed JSON when the model replies JSON.
 *
 * options: model (default gemini-3.5-flash), images (string[] paths or URLs),
 *   max_output_tokens.
 */
export async function analyze_image(prompt, options = "{}") {
  if (!prompt || typeof prompt !== "string") {
    throw new Error("prompt is required and must be a string");
  }
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const images = Array.isArray(opts.images) ? opts.images : [];
  if (images.length === 0) throw new Error("analyze_image requires options.images (>=1)");
  if (!API_KEY) throw new Error("Gemini API key not set (TOOL_GEMINI_API_KEY / GEMINI_API_KEY)");

  const model = opts.model || "gemini-3.5-flash";
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const parts = [{ text: prompt }];
    for (const ref of images) parts.push(await imagePart(ref, ctrl.signal));

    const resp = await fetch(
      `${BASE}/${model}:generateContent?key=${API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [{ parts }],
          generationConfig: { maxOutputTokens: opts.max_output_tokens || 1024 },
        }),
        signal: ctrl.signal,
      }
    );
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Gemini API ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    let text = data.candidates?.[0]?.content?.parts?.map((p) => p.text || "").join("") ?? "";
    text = text.trim();
    if (text.startsWith("```")) {
      text = text.replace(/^```[a-z]*\n?/i, "").replace(/```$/, "").trim();
    }
    let parsed = text;
    try {
      parsed = JSON.parse(text);
    } catch {
      // not JSON - return raw text
    }
    return { result: parsed, model };
  } finally {
    clearTimeout(timer);
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("gemini.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { analyze_image };
  if (!dispatch[fn]) {
    console.error(`Usage: node gemini.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
