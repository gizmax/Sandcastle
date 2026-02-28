/**
 * PostgreSQL connector - execute SQL queries via psql CLI.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_POSTGRESQL_URL (connection string)
 *
 * Uses psql CLI for zero-dependency operation. For production,
 * consider pg (node-postgres) npm package.
 */

import { execSync } from "node:child_process";

const PG_URL = process.env.TOOL_POSTGRESQL_URL || "";

function runPsql(sql, readOnly = true) {
  if (!PG_URL) throw new Error("TOOL_POSTGRESQL_URL is not configured");

  // Use PGDATABASE-style env vars or pass connection string via env to avoid
  // shell injection through the connection URL.
  // The SQL is passed via stdin to avoid shell metacharacter issues.
  const flags = [
    "--no-password",
    "--tuples-only",
    "--no-align",
    "-F", "\t",
  ];

  if (readOnly) {
    flags.push("--set=ON_ERROR_STOP=1");
  }

  try {
    const result = execSync("psql " + flags.map(f => `'${f.replace(/'/g, "'\\''")}'`).join(" "), {
      encoding: "utf-8",
      timeout: 30000,
      maxBuffer: 5 * 1024 * 1024,
      input: sql,
      env: { ...process.env, DATABASE_URL: PG_URL },
    });
    return result.trim();
  } catch (err) {
    // Mask connection string from error output to prevent credential leaks
    const errMsg = (err.stderr || err.message || "").replace(PG_URL, "***");
    throw new Error(`PostgreSQL error: ${errMsg}`);
  }
}

export async function query(sql) {
  // For safety, ensure query is read-only (basic check)
  const normalized = sql.trim().toUpperCase();
  const isRead = normalized.startsWith("SELECT") || normalized.startsWith("WITH") ||
                 normalized.startsWith("EXPLAIN") || normalized.startsWith("SHOW");

  if (!isRead) {
    throw new Error(
      "Use 'execute' for write operations (INSERT, UPDATE, DELETE). " +
      "'query' is for read-only SELECT statements."
    );
  }

  // Run psql once with headers (no --tuples-only) to get columns + data
  if (!PG_URL) throw new Error("TOOL_POSTGRESQL_URL is not configured");
  const flags = [
    "--no-password",
    "--no-align",
    "-F", "\t",
    "--set=ON_ERROR_STOP=1",
  ];

  let headerOutput;
  try {
    headerOutput = execSync("psql " + flags.map(f => `'${f.replace(/'/g, "'\\''")}'`).join(" "), {
      encoding: "utf-8",
      timeout: 30000,
      maxBuffer: 5 * 1024 * 1024,
      input: sql,
      env: { ...process.env, DATABASE_URL: PG_URL },
    }).trim();
  } catch (err) {
    const errMsg = (err.stderr || err.message || "").replace(PG_URL, "***");
    throw new Error(`PostgreSQL error: ${errMsg}`);
  }

  const headerLines = headerOutput.split("\n").filter(Boolean);
  if (headerLines.length === 0) return { rows: [], count: 0 };

  const columns = headerLines[0].split("\t");
  const rows = headerLines.slice(1).map((line) => {
    const values = line.split("\t");
    const row = {};
    columns.forEach((col, i) => { row[col] = values[i] ?? null; });
    return row;
  });

  // Remove the count line (e.g. "(5 rows)")
  const filtered = rows.filter((r) => {
    const firstVal = Object.values(r)[0];
    return !(typeof firstVal === "string" && firstVal.match(/^\(\d+ rows?\)$/));
  });

  return { rows: filtered, count: filtered.length };
}

export async function execute(sql) {
  const output = runPsql(sql, false);
  return { result: output, ok: true };
}

// CLI dispatch
if (process.argv[1]?.endsWith("postgresql.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { query, execute };
  if (!dispatch[fn]) {
    console.error(`Usage: node postgresql.mjs <query|execute> [args...]`);
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
