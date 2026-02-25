/**
 * Code Interpreter connector - Jupyter-style execution with data analysis.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * No credentials needed - runs code in the sandbox environment.
 * Supports Python, JavaScript (Node), and Bash cells.
 */

import { execSync } from "node:child_process";
import { writeFileSync, readFileSync, unlinkSync, existsSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const DEFAULT_TIMEOUT = 60000; // 60 seconds for analysis workloads
const MAX_OUTPUT = 5 * 1024 * 1024; // 5MB
const WORK_DIR = join(tmpdir(), "sandcastle_interpreter");
let _cellCounter = 0;

function ensureWorkDir() {
  if (!existsSync(WORK_DIR)) mkdirSync(WORK_DIR, { recursive: true });
}

function truncate(str, max = MAX_OUTPUT) {
  if (!str) return "";
  if (str.length <= max) return str;
  return str.slice(0, max) + `\n... truncated (${str.length} bytes total)`;
}

function execCell(command, timeout = DEFAULT_TIMEOUT) {
  try {
    const stdout = execSync(command, {
      timeout,
      cwd: WORK_DIR,
      maxBuffer: MAX_OUTPUT,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    });
    return { stdout: truncate(stdout), stderr: "", exitCode: 0 };
  } catch (err) {
    return {
      stdout: truncate(err.stdout || ""),
      stderr: truncate(err.stderr || err.message),
      exitCode: err.status ?? 1,
    };
  }
}

export async function run_cell(code, language = "python") {
  if (!code) throw new Error("code is required");
  ensureWorkDir();

  const cellId = ++_cellCounter;
  const validLangs = {
    python: { ext: ".py", cmd: "python3" },
    javascript: { ext: ".mjs", cmd: "node" },
    bash: { ext: ".sh", cmd: "bash" },
  };
  const lang = validLangs[language];
  if (!lang) {
    throw new Error(`Unsupported language: ${language}. Must be one of: ${Object.keys(validLangs).join(", ")}`);
  }

  const cellFile = join(WORK_DIR, `cell_${cellId}${lang.ext}`);
  const displayFile = join(WORK_DIR, `cell_${cellId}_display.json`);

  // For Python, inject display data capture
  let finalCode = code;
  if (language === "python") {
    finalCode = `
import sys, json
_display_data = []
_display_file = ${JSON.stringify(displayFile)}
def display(obj, mime_type="text/plain"):
    """Capture display output (tables, HTML, etc.)"""
    _display_data.append({"type": mime_type, "data": str(obj)[:10000]})
${code}
if _display_data:
    with open(_display_file, 'w') as _f:
        json.dump(_display_data, _f)
`;
  }

  try {
    writeFileSync(cellFile, finalCode, "utf-8");
    const result = execCell(`${lang.cmd} ${cellFile}`);

    let displayData = [];
    if (existsSync(displayFile)) {
      try {
        displayData = JSON.parse(readFileSync(displayFile, "utf-8"));
      } catch { /* ignore parse errors */ }
    }

    return {
      cellId,
      language,
      output: result.stdout,
      error: result.exitCode !== 0 ? result.stderr : undefined,
      displayData: displayData.length > 0 ? displayData : undefined,
      exitCode: result.exitCode,
    };
  } finally {
    if (existsSync(cellFile)) unlinkSync(cellFile);
    if (existsSync(displayFile)) unlinkSync(displayFile);
  }
}

export async function run_analysis(data, question) {
  if (!question) throw new Error("question is required");
  const parsedData = typeof data === "string" ? JSON.parse(data) : data;
  ensureWorkDir();

  const dataFile = join(WORK_DIR, `analysis_data_${Date.now()}.json`);
  writeFileSync(dataFile, JSON.stringify(parsedData), "utf-8");

  const analysisCode = `
import json, sys
import pandas as pd

with open(${JSON.stringify(dataFile)}) as f:
    raw = json.load(f)

if isinstance(raw, list):
    df = pd.DataFrame(raw)
elif isinstance(raw, dict) and any(isinstance(v, list) for v in raw.values()):
    df = pd.DataFrame(raw)
else:
    df = pd.DataFrame([raw])

print("=== Data Shape ===")
print(f"Rows: {len(df)}, Columns: {len(df.columns)}")
print(f"Columns: {', '.join(df.columns.tolist())}")
print()
print("=== Data Types ===")
print(df.dtypes.to_string())
print()
print("=== Summary Statistics ===")
print(df.describe(include='all').to_string())
print()
print("=== Question: ${question.replace(/'/g, "\\'")} ===")
# Auto-analysis based on data
numeric_cols = df.select_dtypes(include='number').columns.tolist()
if numeric_cols:
    print("\\n=== Numeric Correlations ===")
    if len(numeric_cols) > 1:
        print(df[numeric_cols].corr().to_string())
    print("\\n=== Value Ranges ===")
    for col in numeric_cols[:10]:
        print(f"  {col}: {df[col].min():.2f} - {df[col].max():.2f} (mean: {df[col].mean():.2f})")
cat_cols = df.select_dtypes(include='object').columns.tolist()
if cat_cols:
    print("\\n=== Categorical Distributions ===")
    for col in cat_cols[:5]:
        print(f"  {col}: {df[col].nunique()} unique values")
        print(f"    Top: {df[col].value_counts().head(5).to_dict()}")
`;

  try {
    const result = await run_cell(analysisCode, "python");
    return {
      question,
      dataShape: { rows: parsedData.length || 1, type: Array.isArray(parsedData) ? "array" : "object" },
      analysis: result.output,
      error: result.error,
    };
  } finally {
    if (existsSync(dataFile)) unlinkSync(dataFile);
  }
}

export async function create_chart(data, chartType = "bar", options = "{}") {
  const parsedData = typeof data === "string" ? JSON.parse(data) : data;
  const parsedOpts = typeof options === "string" ? JSON.parse(options) : options;
  ensureWorkDir();

  const validCharts = ["bar", "line", "scatter", "pie", "histogram", "heatmap", "box"];
  if (!validCharts.includes(chartType)) {
    throw new Error(`Invalid chart type: ${chartType}. Must be one of: ${validCharts.join(", ")}`);
  }

  const dataFile = join(WORK_DIR, `chart_data_${Date.now()}.json`);
  const chartFile = join(WORK_DIR, `chart_${Date.now()}.png`);
  writeFileSync(dataFile, JSON.stringify(parsedData), "utf-8");

  const title = parsedOpts.title || `${chartType.charAt(0).toUpperCase() + chartType.slice(1)} Chart`;
  const xlabel = parsedOpts.xlabel || "";
  const ylabel = parsedOpts.ylabel || "";
  const width = parsedOpts.width || 10;
  const height = parsedOpts.height || 6;

  const chartCode = `
import json, base64, sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

with open(${JSON.stringify(dataFile)}) as f:
    raw = json.load(f)
df = pd.DataFrame(raw) if isinstance(raw, list) else pd.DataFrame([raw] if isinstance(raw, dict) and not any(isinstance(v, list) for v in raw.values()) else raw)

fig, ax = plt.subplots(figsize=(${width}, ${height}))
chart_type = ${JSON.stringify(chartType)}
numeric_cols = df.select_dtypes(include='number').columns.tolist()
cat_cols = df.select_dtypes(include='object').columns.tolist()

if chart_type == 'bar':
    if cat_cols and numeric_cols:
        df.plot.bar(x=cat_cols[0], y=numeric_cols[0], ax=ax)
    elif numeric_cols:
        df[numeric_cols[:5]].plot.bar(ax=ax)
elif chart_type == 'line':
    df[numeric_cols[:5]].plot.line(ax=ax)
elif chart_type == 'scatter' and len(numeric_cols) >= 2:
    ax.scatter(df[numeric_cols[0]], df[numeric_cols[1]])
    ax.set_xlabel(numeric_cols[0])
    ax.set_ylabel(numeric_cols[1])
elif chart_type == 'pie' and numeric_cols:
    col = numeric_cols[0]
    labels = df[cat_cols[0]].tolist() if cat_cols else df.index.tolist()
    ax.pie(df[col], labels=labels, autopct='%1.1f%%')
elif chart_type == 'histogram' and numeric_cols:
    df[numeric_cols[0]].hist(ax=ax, bins=20)
elif chart_type == 'box' and numeric_cols:
    df[numeric_cols[:5]].plot.box(ax=ax)
elif chart_type == 'heatmap' and len(numeric_cols) >= 2:
    corr = df[numeric_cols].corr()
    im = ax.imshow(corr, cmap='coolwarm', aspect='auto')
    ax.set_xticks(range(len(corr.columns)))
    ax.set_xticklabels(corr.columns, rotation=45, ha='right')
    ax.set_yticks(range(len(corr.columns)))
    ax.set_yticklabels(corr.columns)
    fig.colorbar(im)

ax.set_title(${JSON.stringify(title)})
if ${JSON.stringify(xlabel)}: ax.set_xlabel(${JSON.stringify(xlabel)})
if ${JSON.stringify(ylabel)}: ax.set_ylabel(${JSON.stringify(ylabel)})
plt.tight_layout()
plt.savefig(${JSON.stringify(chartFile)}, dpi=100, bbox_inches='tight')
plt.close()

with open(${JSON.stringify(chartFile)}, 'rb') as f:
    b64 = base64.b64encode(f.read()).decode()
print(b64)
`;

  try {
    const result = await run_cell(chartCode, "python");
    const base64Data = result.output?.trim();
    return {
      chartType,
      title,
      format: "png",
      base64: base64Data && !result.error ? base64Data : undefined,
      error: result.error,
      size: base64Data ? Math.round(base64Data.length * 0.75) : 0,
    };
  } finally {
    if (existsSync(dataFile)) unlinkSync(dataFile);
    if (existsSync(chartFile)) unlinkSync(chartFile);
  }
}

export async function install(packages) {
  if (!packages) throw new Error("packages is required (comma-separated list)");
  const pkgList = typeof packages === "string"
    ? packages.split(",").map((p) => p.trim()).filter(Boolean)
    : packages;
  if (pkgList.length === 0) throw new Error("No packages specified");

  try {
    const stdout = execSync(`python3 -m pip install --quiet ${pkgList.join(" ")}`, {
      timeout: 120000,
      maxBuffer: MAX_OUTPUT,
      encoding: "utf-8",
      stdio: ["pipe", "pipe", "pipe"],
    });
    return { packages: pkgList, installed: true, output: truncate(stdout) };
  } catch (err) {
    return { packages: pkgList, installed: false, error: truncate(err.stderr || err.message) };
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("code-interpreter.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { run_cell, run_analysis, create_chart, install };
  if (!dispatch[fn]) {
    console.error(`Usage: node code-interpreter.mjs <run_cell|run_analysis|create_chart|install> [args...]`);
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
