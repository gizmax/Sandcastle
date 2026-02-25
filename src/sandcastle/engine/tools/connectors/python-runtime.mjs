/**
 * Python Runtime connector - execute Python code snippets inline.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * No credentials needed - runs python3 in the sandbox environment.
 * Safety: enforces timeout, temp file cleanup, output limits.
 */

import { execSync } from "node:child_process";
import { writeFileSync, readFileSync, unlinkSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const DEFAULT_TIMEOUT = 30000; // 30 seconds
const MAX_OUTPUT = 5 * 1024 * 1024; // 5MB
const WORK_DIR = join(tmpdir(), "sandcastle_python");

function ensureWorkDir() {
  if (!existsSync(WORK_DIR)) mkdirSync(WORK_DIR, { recursive: true });
}

function truncate(str, max = MAX_OUTPUT) {
  if (!str) return "";
  if (str.length <= max) return str;
  return str.slice(0, max) + `\n... truncated (${str.length} bytes total)`;
}

function runPython(code, timeout = DEFAULT_TIMEOUT) {
  ensureWorkDir();
  const scriptFile = join(WORK_DIR, `run_${Date.now()}_${Math.random().toString(36).slice(2, 8)}.py`);
  const resultFile = `${scriptFile}.result.json`;
  // Wrap code to capture the last expression result
  const wrapper = `
import sys, json
_result_file = ${JSON.stringify(resultFile)}
try:
${code.split("\n").map((l) => "    " + l).join("\n")}
except Exception as _e:
    with open(_result_file, 'w') as _f:
        json.dump({"error": str(_e), "type": type(_e).__name__}, _f)
    sys.exit(1)
`;
  try {
    writeFileSync(scriptFile, wrapper, "utf-8");
    let stdout = "";
    let stderr = "";
    let exitCode = 0;
    try {
      stdout = execSync(`python3 ${scriptFile}`, {
        timeout,
        cwd: WORK_DIR,
        maxBuffer: MAX_OUTPUT,
        encoding: "utf-8",
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      stdout = err.stdout || "";
      stderr = err.stderr || "";
      exitCode = err.status ?? 1;
    }
    let result = null;
    if (existsSync(resultFile)) {
      try {
        result = JSON.parse(readFileSync(resultFile, "utf-8"));
      } catch { /* ignore parse errors */ }
    }
    return {
      stdout: truncate(stdout),
      stderr: truncate(stderr),
      result,
      exitCode,
    };
  } finally {
    if (existsSync(scriptFile)) unlinkSync(scriptFile);
    if (existsSync(resultFile)) unlinkSync(resultFile);
  }
}

export async function execute(code, options = "{}") {
  const parsedOpts = typeof options === "string" ? JSON.parse(options) : options;
  const timeout = parsedOpts.timeout ? parsedOpts.timeout * 1000 : DEFAULT_TIMEOUT;
  const packages = parsedOpts.packages || [];

  if (!code) throw new Error("code is required");

  // Install packages first if requested
  if (packages.length > 0) {
    await install_packages(packages.join(","));
  }

  return runPython(code, timeout);
}

export async function execute_with_data(code, inputData = "{}") {
  const parsedData = typeof inputData === "string" ? JSON.parse(inputData) : inputData;

  if (!code) throw new Error("code is required");

  ensureWorkDir();
  const dataFile = join(WORK_DIR, `data_${Date.now()}.json`);
  try {
    writeFileSync(dataFile, JSON.stringify(parsedData), "utf-8");
    const preamble = `import json\nwith open(${JSON.stringify(dataFile)}) as _df:\n    data = json.load(_df)\n`;
    const fullCode = preamble + code;
    return runPython(fullCode);
  } finally {
    if (existsSync(dataFile)) unlinkSync(dataFile);
  }
}

export async function install_packages(packages) {
  if (!packages) throw new Error("packages is required (comma-separated list)");
  const pkgList = typeof packages === "string"
    ? packages.split(",").map((p) => p.trim()).filter(Boolean)
    : packages;
  if (pkgList.length === 0) throw new Error("No packages specified");
  if (pkgList.length > 20) throw new Error("Too many packages (max 20)");

  // Validate package names (basic alphanumeric + hyphens + version specs)
  const validPkg = /^[a-zA-Z0-9_-]+([><=!~]+[a-zA-Z0-9._*]+)?$/;
  for (const pkg of pkgList) {
    if (!validPkg.test(pkg)) {
      throw new Error(`Invalid package name: ${pkg}`);
    }
  }

  try {
    const stdout = execSync(`python3 -m pip install --quiet ${pkgList.join(" ")}`, {
      timeout: 120000,
      maxBuffer: MAX_OUTPUT,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    });
    return {
      packages: pkgList,
      installed: true,
      output: truncate(stdout),
    };
  } catch (err) {
    return {
      packages: pkgList,
      installed: false,
      error: truncate(err.stderr || err.message),
    };
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("python-runtime.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { execute, execute_with_data, install_packages };
  if (!dispatch[fn]) {
    console.error(`Usage: node python-runtime.mjs <execute|execute_with_data|install_packages> [args...]`);
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
