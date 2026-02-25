/**
 * AWS S3 connector - object storage via S3 REST API with Signature V4.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_AWS_ACCESS_KEY_ID, TOOL_AWS_SECRET_ACCESS_KEY,
 *              TOOL_AWS_REGION, TOOL_AWS_S3_BUCKET
 */

import crypto from "node:crypto";

const ACCESS_KEY = process.env.TOOL_AWS_ACCESS_KEY_ID || "";
const SECRET_KEY = process.env.TOOL_AWS_SECRET_ACCESS_KEY || "";
const REGION = process.env.TOOL_AWS_REGION || "us-east-1";
const BUCKET = process.env.TOOL_AWS_S3_BUCKET || "";
const TIMEOUT = 30_000;
const SERVICE = "s3";

function hmacSha256(key, data) {
  return crypto.createHmac("sha256", key).update(data).digest();
}

function sha256(data) {
  return crypto.createHash("sha256").update(data).digest("hex");
}

function getSignatureKey(date) {
  let key = hmacSha256(`AWS4${SECRET_KEY}`, date);
  key = hmacSha256(key, REGION);
  key = hmacSha256(key, SERVICE);
  key = hmacSha256(key, "aws4_request");
  return key;
}

function signRequest(method, path, query = "", headers = {}, payload = "") {
  const now = new Date();
  const date = now.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z/, "Z");
  const dateShort = date.slice(0, 8);
  const payloadHash = sha256(payload);

  headers["x-amz-date"] = date;
  headers["x-amz-content-sha256"] = payloadHash;
  headers["host"] = `${BUCKET}.s3.${REGION}.amazonaws.com`;

  const sortedHeaders = Object.keys(headers).sort();
  const canonicalHeaders = sortedHeaders.map((k) => `${k.toLowerCase()}:${headers[k]}\n`).join("");
  const signedHeaders = sortedHeaders.map((k) => k.toLowerCase()).join(";");
  const canonicalRequest = [method, path, query, canonicalHeaders, signedHeaders, payloadHash].join("\n");
  const scope = `${dateShort}/${REGION}/${SERVICE}/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", date, scope, sha256(canonicalRequest)].join("\n");
  const signature = hmacSha256(getSignatureKey(dateShort), stringToSign).toString("hex");

  headers["Authorization"] = `AWS4-HMAC-SHA256 Credential=${ACCESS_KEY}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`;
  return headers;
}

async function s3fetch(method, key = "", query = "", body = "", extraHeaders = {}) {
  const path = `/${encodeURIComponent(key).replace(/%2F/g, "/")}`;
  const headers = signRequest(method, key ? path : "/", query, { ...extraHeaders }, body);
  const url = `https://${BUCKET}.s3.${REGION}.amazonaws.com${path}${query ? "?" + query : ""}`;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT);
  try {
    const opts = { method, headers, signal: ctrl.signal };
    if (body) opts.body = body;
    const resp = await fetch(url, opts);
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`S3 ${resp.status}: ${text.slice(0, 500)}`);
    }
    return resp;
  } finally { clearTimeout(timer); }
}

export async function list_objects(prefix = "", maxKeys = "1000") {
  const params = [`list-type=2`, `max-keys=${maxKeys}`];
  if (prefix) params.push(`prefix=${encodeURIComponent(prefix)}`);
  const resp = await s3fetch("GET", "", params.join("&"));
  const xml = await resp.text();
  const objects = [];
  const regex = /<Contents>[\s\S]*?<Key>(.*?)<\/Key>[\s\S]*?<Size>(.*?)<\/Size>[\s\S]*?<LastModified>(.*?)<\/LastModified>[\s\S]*?<\/Contents>/g;
  let match;
  while ((match = regex.exec(xml)) !== null) {
    objects.push({ key: match[1], size: Number(match[2]), lastModified: match[3] });
  }
  return { count: objects.length, objects };
}

export async function get_object(key) {
  const resp = await s3fetch("GET", key);
  const contentType = resp.headers.get("content-type") || "";
  const text = await resp.text();
  return { key, contentType, size: text.length, content: text.slice(0, 50_000) };
}

export async function put_object(key, body = "", contentType = "text/plain") {
  await s3fetch("PUT", key, "", body, { "Content-Type": contentType });
  return { ok: true, key, size: body.length };
}

export async function delete_object(key) {
  await s3fetch("DELETE", key);
  return { ok: true, key, deleted: true };
}

export async function generate_presigned_url(key, expiresIn = "3600") {
  const now = new Date();
  const date = now.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z/, "Z");
  const dateShort = date.slice(0, 8);
  const scope = `${dateShort}/${REGION}/${SERVICE}/aws4_request`;
  const params = new URLSearchParams({
    "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
    "X-Amz-Credential": `${ACCESS_KEY}/${scope}`,
    "X-Amz-Date": date,
    "X-Amz-Expires": String(expiresIn),
    "X-Amz-SignedHeaders": "host",
  });
  const path = `/${encodeURIComponent(key).replace(/%2F/g, "/")}`;
  const host = `${BUCKET}.s3.${REGION}.amazonaws.com`;
  const canonicalRequest = ["GET", path, params.toString(), `host:${host}\n`, "host", "UNSIGNED-PAYLOAD"].join("\n");
  const stringToSign = ["AWS4-HMAC-SHA256", date, scope, sha256(canonicalRequest)].join("\n");
  const signature = hmacSha256(getSignatureKey(dateShort), stringToSign).toString("hex");
  params.set("X-Amz-Signature", signature);
  return { url: `https://${host}${path}?${params.toString()}`, expiresIn: Number(expiresIn) };
}

// CLI dispatch
if (process.argv[1]?.endsWith("aws-s3.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { list_objects, get_object, put_object, delete_object, generate_presigned_url };
  if (!dispatch[fn]) {
    console.error("Usage: node aws-s3.mjs <list_objects|get_object|put_object|delete_object|generate_presigned_url> [args...]");
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
