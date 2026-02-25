/**
 * Filesystem connector - file operations for sandbox environments.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * No credentials needed - operates within the sandbox boundary.
 * Safety: prevents path traversal, restricts to allowed roots.
 */

import { readFile, writeFile, readdir, copyFile as fsCopy, unlink, stat, mkdir } from "node:fs/promises";
import { resolve, normalize, join, relative, basename, extname } from "node:path";
import { existsSync } from "node:fs";

const ALLOWED_ROOTS = ["/home/user", "/tmp", "/workspace"];
const MAX_READ_SIZE = 1024 * 1024; // 1MB

function safePath(inputPath) {
  const resolved = resolve(normalize(inputPath));
  const allowed = ALLOWED_ROOTS.some((root) => resolved.startsWith(root));
  if (!allowed) {
    throw new Error(
      `Path ${resolved} is outside allowed directories: ${ALLOWED_ROOTS.join(", ")}`
    );
  }
  if (resolved.includes("\0")) {
    throw new Error("Null bytes in path are not allowed");
  }
  return resolved;
}

export async function read_file(path) {
  const safe = safePath(path);
  const info = await stat(safe);
  if (!info.isFile()) throw new Error(`Not a file: ${safe}`);
  if (info.size > MAX_READ_SIZE) {
    throw new Error(`File too large: ${info.size} bytes (max ${MAX_READ_SIZE})`);
  }
  const content = await readFile(safe, "utf-8");
  return { path: safe, size: info.size, content };
}

export async function write_file(path, content = "") {
  const safe = safePath(path);
  const dir = resolve(safe, "..");
  if (!existsSync(dir)) {
    await mkdir(dir, { recursive: true });
  }
  await writeFile(safe, content, "utf-8");
  const info = await stat(safe);
  return { path: safe, size: info.size, written: true };
}

export async function list_directory(path, options = "{}") {
  const parsedOpts = typeof options === "string" ? JSON.parse(options) : options;
  const safe = safePath(path);
  const recursive = parsedOpts.recursive || false;
  const entries = await readdir(safe, { withFileTypes: true, recursive });
  const items = entries.slice(0, 1000).map((e) => {
    const entryPath = recursive ? join(safe, e.parentPath ? relative(safe, e.parentPath) : "", e.name) : join(safe, e.name);
    return {
      name: e.name,
      path: entryPath,
      type: e.isDirectory() ? "directory" : e.isFile() ? "file" : "other",
    };
  });
  return {
    path: safe,
    count: items.length,
    truncated: entries.length > 1000,
    entries: items,
  };
}

export async function copy_file(source, destination) {
  const safeSrc = safePath(source);
  const safeDst = safePath(destination);
  const info = await stat(safeSrc);
  if (!info.isFile()) throw new Error(`Source is not a file: ${safeSrc}`);
  const dstDir = resolve(safeDst, "..");
  if (!existsSync(dstDir)) {
    await mkdir(dstDir, { recursive: true });
  }
  await fsCopy(safeSrc, safeDst);
  return { source: safeSrc, destination: safeDst, size: info.size, copied: true };
}

export async function delete_file(path) {
  const safe = safePath(path);
  const info = await stat(safe);
  if (!info.isFile()) throw new Error(`Not a file: ${safe}`);
  await unlink(safe);
  return { path: safe, deleted: true };
}

export async function file_info(path) {
  const safe = safePath(path);
  const info = await stat(safe);
  return {
    path: safe,
    name: basename(safe),
    extension: extname(safe),
    size: info.size,
    type: info.isDirectory() ? "directory" : info.isFile() ? "file" : "other",
    modified: info.mtime.toISOString(),
    created: info.birthtime.toISOString(),
    permissions: (info.mode & 0o777).toString(8),
  };
}

export async function search_files(directory, pattern) {
  if (!pattern) throw new Error("pattern is required");
  const safe = safePath(directory);
  const entries = await readdir(safe, { withFileTypes: true, recursive: true });

  // Convert glob to regex (simple: * -> .*, ? -> ., ** handled by recursive readdir)
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&");
  const regexStr = escaped.replace(/\*\*/g, ".*").replace(/\*/g, "[^/]*").replace(/\?/g, ".");
  const regex = new RegExp(`^${regexStr}$`, "i");

  const matches = entries
    .filter((e) => e.isFile() && regex.test(e.name))
    .slice(0, 500)
    .map((e) => {
      const entryPath = join(safe, e.parentPath ? relative(safe, e.parentPath) : "", e.name);
      return { name: e.name, path: entryPath };
    });
  return { directory: safe, pattern, count: matches.length, matches };
}

// CLI dispatch
if (process.argv[1]?.endsWith("filesystem.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { read_file, write_file, list_directory, copy_file, delete_file, file_info, search_files };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node filesystem.mjs <read_file|write_file|list_directory|copy_file|delete_file|file_info|search_files> [args...]`
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
