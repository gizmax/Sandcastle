/**
 * Gmail/SMTP connector - send emails via SMTP.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_SMTP_HOST, TOOL_SMTP_PORT, TOOL_SMTP_USER, TOOL_SMTP_PASSWORD
 *
 * Note: Uses raw SMTP via node:net/tls for zero-dependency operation in sandbox.
 * For production, consider nodemailer.
 */

const SMTP_HOST = process.env.TOOL_SMTP_HOST || "smtp.gmail.com";
const SMTP_PORT = parseInt(process.env.TOOL_SMTP_PORT || "587", 10);
const SMTP_USER = process.env.TOOL_SMTP_USER || "";
const SMTP_PASSWORD = process.env.TOOL_SMTP_PASSWORD || "";

/**
 * Minimal SMTP send using curl without a shell.
 * This approach avoids requiring nodemailer while keeping user-controlled
 * recipient, subject, and body values out of command strings.
 */
import { spawnSync } from "node:child_process";

export async function send_email(to, subject, body, html = false) {
  if (!SMTP_USER || !SMTP_PASSWORD) {
    throw new Error("TOOL_SMTP_USER and TOOL_SMTP_PASSWORD are not configured");
  }
  const contentType = html ? "text/html" : "text/plain";
  // Build raw email
  const email = [
    `From: ${SMTP_USER}`,
    `To: ${to}`,
    `Subject: ${subject}`,
    `MIME-Version: 1.0`,
    `Content-Type: ${contentType}; charset=utf-8`,
    ``,
    body,
  ].join("\r\n");

  const result = spawnSync("curl", [
    "--ssl-reqd",
    "--url", `smtp://${SMTP_HOST}:${SMTP_PORT}`,
    "--user", `${SMTP_USER}:${SMTP_PASSWORD}`,
    "--mail-from", SMTP_USER,
    "--mail-rcpt", to,
    "--upload-file", "-",
  ], {
      input: email,
      timeout: 30000,
      encoding: "utf-8",
  });
  if (result.error || result.status !== 0) {
    const detail = result.error?.message || result.stderr || `curl exited ${result.status}`;
    throw new Error(`SMTP send failed: ${String(detail).slice(0, 500)}`);
  }
  return { ok: true, to, subject };
}

export async function search_emails(query, limit = 10) {
  // Email search requires IMAP which is complex without dependencies.
  // Return a helpful message directing to use Gmail API instead.
  return {
    error: "Email search requires Gmail API OAuth. Use the 'gdrive' tool with Gmail API or configure an IMAP-capable connector.",
    query,
    limit,
  };
}

// CLI dispatch
if (process.argv[1]?.endsWith("gmail.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_email, search_emails };
  if (!dispatch[fn]) {
    console.error(`Usage: node gmail.mjs <send_email|search_emails> [args...]`);
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
