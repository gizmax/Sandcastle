/**
 * Shell connector - execute commands in sandbox environments.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * No credentials needed - operates within the sandbox boundary.
 * Safety: enforces timeout, output size limits, and signal handling.
 */

import { execSync, spawn as nodeSpawn } from "node:child_process";
import { writeFileSync, unlinkSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const DEFAULT_TIMEOUT = 30000; // 30 seconds
const MAX_OUTPUT = 5 * 1024 * 1024; // 5MB

function truncate(str, max = MAX_OUTPUT) {
  if (str.length <= max) return str;
  return str.slice(0, max) + `\n... truncated (${str.length} bytes total)`;
}

export async function execute(command, options = "{}") {
  const parsedOpts = typeof options === "string" ? JSON.parse(options) : options;
  const timeout = parsedOpts.timeout ? parsedOpts.timeout * 1000 : DEFAULT_TIMEOUT;
  const cwd = parsedOpts.cwd || "/home/user";
  const env = parsedOpts.env || {};

  if (!command) throw new Error("command is required");

  try {
    const stdout = execSync(command, {
      timeout,
      cwd,
      env: { ...process.env, ...env },
      maxBuffer: MAX_OUTPUT,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    });
    return {
      stdout: truncate(stdout || ""),
      stderr: "",
      exitCode: 0,
    };
  } catch (err) {
    return {
      stdout: truncate(err.stdout || ""),
      stderr: truncate(err.stderr || err.message),
      exitCode: err.status ?? 1,
      killed: err.killed || false,
      signal: err.signal || null,
    };
  }
}

export async function execute_script(script, interpreter = "bash") {
  if (!script) throw new Error("script is required");

  const validInterpreters = {
    bash: "bash",
    sh: "sh",
    python3: "python3",
    python: "python3",
    node: "node",
  };
  const bin = validInterpreters[interpreter];
  if (!bin) {
    throw new Error(
      `Invalid interpreter: ${interpreter}. Must be one of: ${Object.keys(validInterpreters).join(", ")}`
    );
  }

  const ext = { bash: ".sh", sh: ".sh", python3: ".py", python: ".py", node: ".mjs" }[interpreter];
  const tmpFile = join(tmpdir(), `sandcastle_script_${Date.now()}${ext}`);
  try {
    writeFileSync(tmpFile, script, "utf-8");
    const result = await execute(`${bin} ${tmpFile}`);
    return { ...result, interpreter: bin };
  } finally {
    if (existsSync(tmpFile)) unlinkSync(tmpFile);
  }
}

export async function background(command) {
  if (!command) throw new Error("command is required");

  return new Promise((resolve, reject) => {
    try {
      const child = nodeSpawn("sh", ["-c", command], {
        detached: true,
        stdio: "ignore",
        cwd: "/home/user",
      });
      child.unref();
      const pid = child.pid;
      if (!pid) {
        reject(new Error("Failed to start background process"));
        return;
      }
      resolve({ pid, command, started: true });
    } catch (err) {
      reject(new Error(`Failed to spawn: ${err.message}`));
    }
  });
}

export async function kill(pid) {
  const numPid = parseInt(pid, 10);
  if (isNaN(numPid) || numPid <= 0) {
    throw new Error(`Invalid PID: ${pid}`);
  }
  try {
    process.kill(numPid, "SIGTERM");
    // Give process time to terminate gracefully
    await new Promise((r) => setTimeout(r, 500));
    try {
      process.kill(numPid, 0); // Check if still running
      process.kill(numPid, "SIGKILL"); // Force kill
      return { pid: numPid, killed: true, signal: "SIGKILL" };
    } catch {
      return { pid: numPid, killed: true, signal: "SIGTERM" };
    }
  } catch (err) {
    if (err.code === "ESRCH") {
      return { pid: numPid, killed: false, error: "Process not found" };
    }
    throw new Error(`Failed to kill PID ${numPid}: ${err.message}`);
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("shell.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { execute, execute_script, background, kill };
  if (!dispatch[fn]) {
    console.error(`Usage: node shell.mjs <execute|execute_script|background|kill> [args...]`);
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
