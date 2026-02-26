/**
 * ElevenLabs connector - text-to-speech, voice listing, and transcription via ElevenLabs API v1.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_ELEVENLABS_API_KEY
 */

import { Buffer } from "node:buffer";

const API_KEY = process.env.TOOL_ELEVENLABS_API_KEY || "";
const BASE = "https://api.elevenlabs.io/v1";
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
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
      },
    };
    if (body !== null) opts.body = typeof body === "string" ? body : JSON.stringify(body);
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`ElevenLabs API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp;
  } finally {
    clear();
  }
}

export async function text_to_speech(text, voiceId, options = "{}") {
  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const payload = {
    text,
    model_id: opts.model_id || "eleven_monolingual_v1",
    voice_settings: {
      stability: opts.stability ?? 0.5,
      similarity_boost: opts.similarity_boost ?? 0.75,
    },
  };
  if (opts.output_format) payload.output_format = opts.output_format;
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(`${BASE}/text-to-speech/${voiceId}`, {
      method: "POST",
      signal,
      headers: {
        "xi-api-key": API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
      },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`ElevenLabs TTS ${resp.status}: ${text.slice(0, 500)}`);
    }
    const arrayBuffer = await resp.arrayBuffer();
    const base64 = Buffer.from(arrayBuffer).toString("base64");
    return {
      audio_base64: base64,
      content_type: resp.headers.get("content-type") || "audio/mpeg",
      size_bytes: arrayBuffer.byteLength,
    };
  } finally {
    clear();
  }
}

export async function list_voices() {
  const resp = await api("/voices");
  const data = await resp.json();
  return (data.voices || []).map((v) => ({
    voice_id: v.voice_id,
    name: v.name,
    category: v.category,
    labels: v.labels,
    preview_url: v.preview_url,
  }));
}

export async function get_voice(voiceId) {
  const resp = await api(`/voices/${voiceId}`);
  const data = await resp.json();
  return {
    voice_id: data.voice_id,
    name: data.name,
    category: data.category,
    labels: data.labels,
    description: data.description,
    preview_url: data.preview_url,
    settings: data.settings,
  };
}

export async function speech_to_text(audioUrl) {
  // Download audio from URL
  const { signal: dlSignal, clear: dlClear } = makeSignal();
  let audioBuffer;
  try {
    const dlResp = await fetch(audioUrl, { signal: dlSignal });
    if (!dlResp.ok) throw new Error(`Failed to download audio: ${dlResp.status}`);
    audioBuffer = await dlResp.arrayBuffer();
  } finally {
    dlClear();
  }
  // Submit to ElevenLabs speech-to-text
  const form = new FormData();
  form.append("audio", new Blob([audioBuffer]), "audio.mp3");
  form.append("model_id", "scribe_v1");
  const { signal, clear } = makeSignal();
  try {
    const resp = await fetch(`${BASE}/speech-to-text`, {
      method: "POST",
      signal,
      headers: { "xi-api-key": API_KEY },
      body: form,
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`ElevenLabs STT ${resp.status}: ${text.slice(0, 500)}`);
    }
    const data = await resp.json();
    return {
      text: data.text,
      language: data.language_code,
    };
  } finally {
    clear();
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("elevenlabs.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { text_to_speech, list_voices, get_voice, speech_to_text };
  if (!dispatch[fn]) {
    console.error(`Usage: node elevenlabs.mjs <${Object.keys(dispatch).join("|")}> [args...]`);
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
