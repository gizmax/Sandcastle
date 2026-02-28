/**
 * OpenAI connector - chat completions, embeddings, images, audio via OpenAI API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_OPENAI_API_KEY
 */

const API_KEY = process.env.TOOL_OPENAI_API_KEY || "";
const BASE = "https://api.openai.com/v1";
const TIMEOUT = 30_000;

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

export async function generate_image(prompt, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const data = await api("/images/generations", "POST", {
    model: opts.model || "dall-e-3",
    prompt,
    n: opts.n || 1,
    size: opts.size || "1024x1024",
    quality: opts.quality || "standard",
    response_format: opts.response_format || "url",
  });
  return data.data.map((img) => ({
    url: img.url,
    revised_prompt: img.revised_prompt,
  }));
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
