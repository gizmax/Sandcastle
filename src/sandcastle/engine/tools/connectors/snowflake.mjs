/**
 * Snowflake connector - execute SQL queries via Snowflake SQL REST API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SNOWFLAKE_ACCOUNT, TOOL_SNOWFLAKE_USERNAME,
 *              TOOL_SNOWFLAKE_PASSWORD, TOOL_SNOWFLAKE_WAREHOUSE
 */

const ACCOUNT = process.env.TOOL_SNOWFLAKE_ACCOUNT || "";
const USERNAME = process.env.TOOL_SNOWFLAKE_USERNAME || "";
const PASSWORD = process.env.TOOL_SNOWFLAKE_PASSWORD || "";
const WAREHOUSE = process.env.TOOL_SNOWFLAKE_WAREHOUSE || "";
const BASE = `https://${ACCOUNT}.snowflakecomputing.com/api/v2`;

async function api(path, method = "GET", body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  const opts = {
    method,
    headers: {
      "Authorization": `Basic ${Buffer.from(`${USERNAME}:${PASSWORD}`).toString("base64")}`,
      "Content-Type": "application/json",
    },
    signal: controller.signal,
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const resp = await fetch(`${BASE}${path}`, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Snowflake API ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp.json();
  } finally {
    clearTimeout(timer);
  }
}

export async function execute_query(sql, database = "", schema = "", limit = 100) {
  const payload = {
    statement: sql,
    timeout: 60,
    warehouse: WAREHOUSE,
  };
  if (database) payload.database = database;
  if (schema) payload.schema = schema;

  const data = await api("/statements", "POST", payload);

  const columns = data.resultSetMetaData?.rowType?.map((c) => c.name) || [];
  const rows = (data.data || []).slice(0, limit);
  const rowCount = data.resultSetMetaData?.numRows || 0;

  return { columns, rows, rowCount };
}

export async function list_databases() {
  const result = await execute_query("SHOW DATABASES");
  return result.rows;
}

export async function list_tables(database, schema = "PUBLIC") {
  const result = await execute_query(`SHOW TABLES IN "${database}"."${schema}"`);
  return result.rows;
}

export async function describe_table(database, schema = "PUBLIC", table) {
  const result = await execute_query(`DESCRIBE TABLE "${database}"."${schema}"."${table}"`);
  return result.rows;
}

// CLI dispatch
if (process.argv[1]?.endsWith("snowflake.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { execute_query, list_databases, list_tables, describe_table };
  if (!dispatch[fn]) {
    console.error(`Usage: node snowflake.mjs <execute_query|list_databases|list_tables|describe_table> [args...]`);
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
