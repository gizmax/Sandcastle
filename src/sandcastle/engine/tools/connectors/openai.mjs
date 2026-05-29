/**
 * OpenAI connector - chat completions, embeddings, images, audio via OpenAI API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_OPENAI_API_KEY
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { basename, join } from "node:path";

const API_KEY = process.env.TOOL_OPENAI_API_KEY || "";
const BASE = "https://api.openai.com/v1";
const TIMEOUT = 30_000;
const IMAGE_TIMEOUT = 180_000; // image generation can take 30-120s

async function api(path, method = "POST", body = null) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const opts = {
      method,
      headers: {
        "Authorization": `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
      },
      signal: ctrl.signal,
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`OpenAI API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally { clearTimeout(timer); }
}

export async function chat_completion(messages, model = "gpt-4o", options = "{}") {
  const parsed = typeof messages === "string" ? JSON.parse(messages) : messages;
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const data = await api("/chat/completions", "POST", {
    model,
    messages: parsed,
    ...opts,
  });
  return {
    id: data.id,
    model: data.model,
    content: data.choices?.[0]?.message?.content,
    finish_reason: data.choices?.[0]?.finish_reason,
    usage: data.usage,
  };
}

export async function create_embedding(input, model = "text-embedding-3-small") {
  const data = await api("/embeddings", "POST", { input, model });
  return {
    model: data.model,
    embeddings: data.data.map((d) => ({ index: d.index, vector_length: d.embedding.length })),
    usage: data.usage,
    _vectors: data.data.map((d) => d.embedding),
  };
}

// Map a common aspect ratio to a gpt-image-1 supported size.
const ASPECT_TO_SIZE = {
  "1:1": "1024x1024",
  "4:5": "1024x1536",
  "2:3": "1024x1536",
  "9:16": "1024x1536",
  "3:4": "1024x1536",
  "16:9": "1536x1024",
  "3:2": "1536x1024",
  "landscape": "1536x1024",
  "portrait": "1024x1536",
  "square": "1024x1024",
};

const MIME_BY_EXT = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp",
};

// Rough per-image cost estimate for gpt-image-1 by quality.
function estimateImageCost(model, quality, n) {
  if (model !== "gpt-image-1") return 0.04 * n; // dall-e-3 standard ~$0.04
  const perImage = quality === "high" ? 0.17 : quality === "low" ? 0.02 : 0.07;
  return perImage * n;
}

function resolveSize(opts) {
  if (opts.size) return opts.size;
  if (opts.aspect && ASPECT_TO_SIZE[opts.aspect]) return ASPECT_TO_SIZE[opts.aspect];
  return "1024x1024";
}

// Write base64 image data to disk, return the file path.
function writeImage(b64, opts, index) {
  const dir = opts.output_dir || process.cwd();
  mkdirSync(dir, { recursive: true });
  const base = opts.output || `openai-img-${Date.now()}`;
  const name = index > 0 ? `${base}-${index + 1}` : base;
  const path = join(dir, `${name}.png`);
  writeFileSync(path, Buffer.from(b64, "base64"));
  return path;
}

/**
 * Generate one or more images. Defaults to gpt-image-1.
 * If options.reference_images is provided, uses the /images/edits endpoint
 * (multipart) so the product photo(s) steer the result - the path for faithful
 * product UGC. Otherwise uses /images/generations. Images are written to disk
 * and the returned shape matches the nano-banana connector ({ ok, files, ... }).
 *
 * options: model (default gpt-image-1), size or aspect, quality (low|medium|high),
 *   n, output, output_dir, reference_images (string[]).
 */
export async function generate_image(prompt, options = "{}") {
  if (!prompt || typeof prompt !== "string") {
    throw new Error("prompt is required and must be a string");
  }
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const model = opts.model || "gpt-image-1";
  const size = resolveSize(opts);
  const n = opts.n || 1;
  const refs = Array.isArray(opts.reference_images) ? opts.reference_images : [];

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), IMAGE_TIMEOUT);
  try {
    let resp;
    if (refs.length > 0) {
      // Reference-image editing - multipart form-data.
      const form = new FormData();
      form.append("model", "gpt-image-1");
      form.append("prompt", prompt);
      form.append("n", String(n));
      form.append("size", size);
      if (opts.quality) form.append("quality", opts.quality);
      for (const ref of refs) {
        const bytes = readFileSync(ref);
        const ext = (ref.split(".").pop() || "png").toLowerCase();
        const blob = new Blob([bytes], { type: MIME_BY_EXT[ext] || "image/png" });
        form.append("image[]", blob, basename(ref));
      }
      resp = await fetch(`${BASE}/images/edits`, {
        method: "POST",
        headers: { Authorization: `Bearer ${API_KEY}` },
        body: form,
        signal: ctrl.signal,
      });
    } else {
      const body = {
        model, prompt, n, size,
        ...(opts.quality ? { quality: opts.quality } : {}),
      };
      // gpt-image-1 always returns b64_json; dall-e-* needs it requested.
      if (model !== "gpt-image-1") body.response_format = "b64_json";
      resp = await fetch(`${BASE}/images/generations`, {
        method: "POST",
        headers: { Authorization: `Bearer ${API_KEY}`, "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
    }

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`OpenAI image API ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    const files = (data.data || []).map((img, i) => {
      if (!img.b64_json) throw new Error("OpenAI image response missing b64_json");
      return writeImage(img.b64_json, opts, i);
    });
    const cost = estimateImageCost(model, opts.quality || "medium", files.length);
    return {
      ok: files.length > 0,
      files,
      estimated_cost: cost,
      settings: { model, size, quality: opts.quality || "medium", references: refs.length },
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function transcribe_audio(fileUrl) {
  // Download the audio file first, then send to Whisper
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const audioResp = await fetch(fileUrl, { signal: ctrl.signal });
    if (!audioResp.ok) throw new Error(`Failed to download audio: ${audioResp.status}`);
    const audioBlob = await audioResp.blob();
    const form = new FormData();
    form.append("file", audioBlob, "audio.mp3");
    form.append("model", "whisper-1");
    const resp = await fetch(`${BASE}/audio/transcriptions`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${API_KEY}` },
      body: form,
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Whisper API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally { clearTimeout(timer); }
}

export async function text_to_speech(text, voice = "alloy") {
  if (!text || typeof text !== "string") throw new Error("text is required");
  // Cannot use api() helper because the response is binary audio, not JSON.
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 60_000); // TTS may take longer
  try {
    const resp = await fetch(`${BASE}/audio/speech`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: "tts-1",
        input: text,
        voice,
        response_format: "mp3",
      }),
      signal: ctrl.signal,
    });
    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`OpenAI TTS API ${resp.status}: ${errText.slice(0, 500)}`);
    }
    const buffer = Buffer.from(await resp.arrayBuffer());
    return { format: "mp3", voice, bytes: buffer.length, base64: buffer.toString("base64").slice(0, 200) + "..." };
  } finally { clearTimeout(timer); }
}

// CLI dispatch
if (process.argv[1]?.endsWith("openai.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { chat_completion, create_embedding, generate_image, transcribe_audio, text_to_speech };
  if (!dispatch[fn]) {
    console.error("Usage: node openai.mjs <chat_completion|create_embedding|generate_image|transcribe_audio|text_to_speech> [args...]");
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
