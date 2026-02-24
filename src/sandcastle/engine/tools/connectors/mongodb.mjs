/**
 * MongoDB Atlas connector - query and manage documents via Data API.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_MONGODB_URI (cluster/dataSource name),
 *              TOOL_MONGODB_API_KEY (Data API key)
 */

const DATA_SOURCE = process.env.TOOL_MONGODB_URI || "";
const API_KEY = process.env.TOOL_MONGODB_API_KEY || "";
const BASE = "https://data.mongodb-api.com/app/data-api/endpoint/data/v1";

async function api(path, method = "POST", body = null) {
  const opts = {
    method,
    headers: {
      "api-key": API_KEY,
      "Content-Type": "application/json",
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`MongoDB API ${resp.status}: ${text.slice(0, 500)}`);
  }
  return resp.json();
}

export async function find_documents(database, collection, filter = {}, limit = 20) {
  const parsedFilter = typeof filter === "string" ? JSON.parse(filter) : filter;
  const data = await api("/action/find", "POST", {
    dataSource: DATA_SOURCE,
    database,
    collection,
    filter: parsedFilter,
    limit: Number(limit),
  });
  return data.documents;
}

export async function insert_document(database, collection, document) {
  const parsedDoc = typeof document === "string" ? JSON.parse(document) : document;
  const data = await api("/action/insertOne", "POST", {
    dataSource: DATA_SOURCE,
    database,
    collection,
    document: parsedDoc,
  });
  return { insertedId: data.insertedId };
}

export async function update_document(database, collection, filter, update) {
  const parsedFilter = typeof filter === "string" ? JSON.parse(filter) : filter;
  const parsedUpdate = typeof update === "string" ? JSON.parse(update) : update;
  const data = await api("/action/updateMany", "POST", {
    dataSource: DATA_SOURCE,
    database,
    collection,
    filter: parsedFilter,
    update: parsedUpdate,
  });
  return { matchedCount: data.matchedCount, modifiedCount: data.modifiedCount };
}

export async function aggregate(database, collection, pipeline) {
  const parsedPipeline = typeof pipeline === "string" ? JSON.parse(pipeline) : pipeline;
  const data = await api("/action/aggregate", "POST", {
    dataSource: DATA_SOURCE,
    database,
    collection,
    pipeline: parsedPipeline,
  });
  return data.documents;
}

// CLI dispatch
if (process.argv[1]?.endsWith("mongodb.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { find_documents, insert_document, update_document, aggregate };
  if (!dispatch[fn]) {
    console.error(`Usage: node mongodb.mjs <find_documents|insert_document|update_document|aggregate> [args...]`);
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
