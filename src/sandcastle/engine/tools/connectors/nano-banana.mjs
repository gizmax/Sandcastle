/**
 * Nano Banana connector - AI image generation via Gemini.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Generates product photos, UGC shots, and transparent assets.
 * Wraps the nano-banana CLI tool (Bun-based).
 *
 * Credentials: TOOL_NANO_BANANA_API_KEY (maps to GEMINI_API_KEY)
 */

import { spawnSync, execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const API_KEY = process.env.TOOL_NANO_BANANA_API_KEY || "";
const TIMEOUT = 120_000; // 120s - generation takes 10-30s
const BUN_BIN = join(homedir(), ".bun", "bin");

// Allowed values for validated options
const VALID_SIZES = new Set(["512", "1K", "2K", "4K"]);
const VALID_MODELS = new Set(["flash", "pro"]);
const ASPECT_RE = /^\d{1,2}:\d{1,2}$/;

/**
 * Build env with bun on PATH and API key mapped.
 */
function makeEnv() {
  const env = { ...process.env };
  if (API_KEY) env.GEMINI_API_KEY = API_KEY;
  const pathSep = process.platform === "win32" ? ";" : ":";
  if (!env.PATH?.includes(BUN_BIN)) {
    env.PATH = `${BUN_BIN}${pathSep}${env.PATH || ""}`;
  }
  return env;
}

/**
 * Find the nano-banana script and return executable + prefix args.
 * Returns { exe: string, prefixArgs: string[] } for use with spawnSync.
 */
function findNanoBanana() {
  const candidates = [
    join(homedir(), ".bun", "bin", "nano-banana"),
    join(homedir(), "tools", "nano-banana-2", "nano-banana"),
    "/usr/local/bin/nano-banana",
  ];

  let script = null;
  for (const p of candidates) {
    if (existsSync(p)) {
      script = p;
      break;
    }
  }

  // Also try which (with bun on PATH)
  if (!script) {
    try {
      script = execSync("which nano-banana", {
        encoding: "utf-8",
        timeout: 5000,
        env: makeEnv(),
        stdio: ["pipe", "pipe", "pipe"],
      }).trim();
    } catch {
      // not found
    }
  }

  if (!script) {
    throw new Error(
      "nano-banana binary not found. Install it or add it to PATH."
    );
  }

  // Prefer invoking via bun explicitly (avoids shebang PATH issues)
  const bunPath = join(homedir(), ".bun", "bin", "bun");
  if (existsSync(bunPath)) {
    return { exe: bunPath, prefixArgs: [script] };
  }

  return { exe: script, prefixArgs: [] };
}

/**
 * Strip ANSI escape sequences from CLI output.
 */
function stripAnsi(str) {
  return str.replace(/\x1b\[[0-9;]*m/g, "");
}

/**
 * Parse file paths from CLI output.
 * Matches lines like: "  + /path/to/nano-gen-12345.png"
 */
function parseFiles(output) {
  const clean = stripAnsi(output);
  const files = [];
  const re = /\+\s+(.+\.(jpe?g|png|webp))/g;
  let m;
  while ((m = re.exec(clean)) !== null) {
    const p = m[1].trim();
    // Skip the "+ Loaded reference: /path.webp" echo lines - those are inputs,
    // not generated outputs.
    if (/loaded reference/i.test(p)) continue;
    files.push(p);
  }
  return files;
}

/**
 * Parse estimated cost from CLI output.
 * Matches lines like: "Cost: ~$0.0670 (...)"
 */
function parseCost(output) {
  const clean = stripAnsi(output);
  const m = clean.match(/Cost:\s*~?\$([0-9.]+)/);
  return m ? parseFloat(m[1]) : 0;
}

/**
 * Validate and sanitize a filesystem path argument.
 * Rejects shell metacharacters and path traversal.
 */
function sanitizePath(value, label) {
  const s = String(value);
  if (/[`$;|&<>!{}()\x00]/.test(s)) {
    throw new Error(`${label} contains disallowed characters`);
  }
  if (s.includes("..")) {
    throw new Error(`${label} must not contain '..'`);
  }
  return s;
}

/**
 * Generate a single image from a text prompt.
 *
 * @param {string} prompt - Image generation prompt
 * @param {string} options - JSON string with optional keys:
 *   size: "512"|"1K"|"2K"|"4K" (default "1K")
 *   aspect: "1:1"|"16:9"|"9:16"|"4:3"|"4:5" etc.
 *   model: "flash"|"pro" (default "flash")
 *   output: output filename (without extension)
 *   output_dir: output directory path
 *   reference_images: string[] - paths to reference images
 *   transparent: boolean - generate with transparent background
 * @returns {{ ok: boolean, files: string[], estimated_cost: number, settings: object }}
 */
export async function generate(prompt, options = "{}") {
  if (!prompt || typeof prompt !== "string") {
    throw new Error("prompt is required and must be a string");
  }

  const opts = typeof options === "string" ? JSON.parse(options) : options;
  const { exe, prefixArgs } = findNanoBanana();

  // Build args array - each element is a separate argument (no shell)
  const args = [...prefixArgs, prompt];

  if (opts.size) {
    if (!VALID_SIZES.has(opts.size)) {
      throw new Error(`Invalid size '${opts.size}'. Valid: 512, 1K, 2K, 4K`);
    }
    args.push("-s", opts.size);
  }
  if (opts.aspect) {
    if (!ASPECT_RE.test(opts.aspect)) {
      throw new Error(`Invalid aspect ratio '${opts.aspect}'. Expected N:N format`);
    }
    args.push("-a", opts.aspect);
  }
  if (opts.model) {
    if (!VALID_MODELS.has(opts.model)) {
      throw new Error(`Invalid model '${opts.model}'. Valid: flash, pro`);
    }
    args.push("-m", opts.model);
  }
  if (opts.output) {
    args.push("-o", sanitizePath(opts.output, "output"));
  }
  if (opts.output_dir) {
    args.push("-d", sanitizePath(opts.output_dir, "output_dir"));
  }
  if (opts.transparent) args.push("-t");

  if (Array.isArray(opts.reference_images)) {
    for (const ref of opts.reference_images) {
      args.push("-r", sanitizePath(ref, "reference_image"));
    }
  }

  // Use spawnSync with args array - no shell involved
  const result = spawnSync(exe, args, {
    timeout: TIMEOUT,
    encoding: "utf-8",
    env: makeEnv(),
    maxBuffer: 10 * 1024 * 1024,
    stdio: ["pipe", "pipe", "pipe"],
  });

  const stdout = result.stdout || "";
  const stderr = result.stderr || "";
  const files = parseFiles(stdout);
  const cost = parseCost(stdout);

  if (result.status === 0 || files.length > 0) {
    return {
      ok: files.length > 0,
      files,
      estimated_cost: cost,
      settings: {
        size: opts.size || "1K",
        aspect: opts.aspect || null,
        model: opts.model || "flash",
        transparent: opts.transparent || false,
      },
    };
  }

  if (result.error?.code === "ETIMEDOUT") {
    throw new Error("nano-banana generation timed out (120s limit)");
  }

  throw new Error(
    `nano-banana generation failed: ${(stderr || result.error?.message || "unknown error").slice(0, 500)}`
  );
}

/**
 * Generate multiple images from an array of prompts.
 *
 * @param {string} prompts - JSON string of array: [{ prompt, options? }, ...]
 * @param {string} sharedOptions - JSON string with shared options applied to all
 * @returns {{ ok: boolean, results: object[], total_cost: number, total_files: number }}
 */
export async function batch_generate(prompts, sharedOptions = "{}") {
  const items = typeof prompts === "string" ? JSON.parse(prompts) : prompts;
  const shared =
    typeof sharedOptions === "string"
      ? JSON.parse(sharedOptions)
      : sharedOptions;

  if (!Array.isArray(items) || items.length === 0) {
    throw new Error("prompts must be a non-empty array");
  }
  if (items.length > 20) {
    throw new Error("Maximum 20 prompts per batch");
  }

  const results = [];
  let totalCost = 0;
  let totalFiles = 0;

  for (const item of items) {
    const prompt = typeof item === "string" ? item : item.prompt;
    const itemOpts = typeof item === "string" ? {} : item.options || {};
    const mergedOpts = { ...shared, ...itemOpts };

    try {
      const result = await generate(prompt, JSON.stringify(mergedOpts));
      results.push({ prompt, ...result });
      totalCost += result.estimated_cost;
      totalFiles += result.files.length;
    } catch (err) {
      results.push({
        prompt,
        ok: false,
        files: [],
        estimated_cost: 0,
        error: err.message,
      });
    }
  }

  return {
    ok: results.some((r) => r.ok),
    results,
    total_cost: totalCost,
    total_files: totalFiles,
  };
}

/**
 * Show cost tracking summary.
 *
 * @returns {{ summary: string }}
 */
export async function costs() {
  const { exe, prefixArgs } = findNanoBanana();

  const result = spawnSync(exe, [...prefixArgs, "--costs"], {
    timeout: 10_000,
    encoding: "utf-8",
    env: makeEnv(),
    stdio: ["pipe", "pipe", "pipe"],
  });

  if (result.status === 0) {
    return { summary: (result.stdout || "").trim() };
  }

  throw new Error(
    `Failed to retrieve costs: ${(result.stderr || result.error?.message || "unknown").slice(0, 500)}`
  );
}

// CLI dispatch
if (process.argv[1]?.endsWith("nano-banana.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { generate, batch_generate, costs };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node nano-banana.mjs <${Object.keys(dispatch).join("|")}> [args...]`
    );
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
