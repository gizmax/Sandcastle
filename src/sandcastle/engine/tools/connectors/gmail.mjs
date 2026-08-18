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
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Recipients and subjects reach this function from workflow templates, which
// means they can carry step output - and step output can carry whatever a model
// or a fetched document put there. A CR or LF in either value ends the current
// SMTP line and starts a new one, so `to` could append its own RCPT TO and BCC
// every message, and `subject` could inject headers or split the body. Both are
// validated here rather than escaped: there is no legitimate newline in an
// address or a subject line, so rejecting is safe and unambiguous.
const _CTL = /[\r\n\0]/;

function assertNoControlChars(value, field) {
  if (_CTL.test(String(value))) {
    throw new Error(`${field} must not contain newline or NUL characters`);
  }
}

// One address, deliberately strict: no display names, no comments, no groups.
// A stricter rule than RFC 5321 allows is the right trade here - anything this
// rejects can still be sent by configuring a different recipient.
const _ADDR = /^[^\s<>",;:@]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

// The message body travels via a temp file because stdin now carries the
// credential config. The body is not secret, so a 0600 file for the duration of
// one curl call is the cheaper of the two risks - the alternative would put the
// password back on the command line.
function _writeTemp(contents) {
  const dir = mkdtempSync(join(tmpdir(), "sc-smtp-"));
  const file = join(dir, "message.eml");
  writeFileSync(file, contents, { mode: 0o600 });
  return { file, dir };
}

function assertValidRecipient(to) {
  assertNoControlChars(to, "recipient");
  if (!_ADDR.test(String(to).trim())) {
    throw new Error(`invalid recipient address: ${String(to).slice(0, 80)}`);
  }
}

export async function send_email(to, subject, body, html = false) {
  if (!SMTP_USER || !SMTP_PASSWORD) {
    throw new Error("TOOL_SMTP_USER and TOOL_SMTP_PASSWORD are not configured");
  }
  assertValidRecipient(to);
  assertNoControlChars(subject ?? "", "subject");

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

  // The password goes in via --config on stdin, not argv: anything in argv is
  // world-readable through `ps` and /proc/<pid>/cmdline for the life of the
  // process. curl reads "-" as a config file from stdin, so the credential is
  // never visible outside this process - but that also means stdin is taken,
  // and the message body has to travel through a pipe instead of --upload-file.
  const configLines = [
    `user = "${SMTP_USER}:${SMTP_PASSWORD}"`,
    "",
  ].join("\n");

  const tmp = _writeTemp(email);
  let result;
  try {
    result = spawnSync("curl", [
      "--ssl-reqd",
      "--config", "-",
      "--url", `smtp://${SMTP_HOST}:${SMTP_PORT}`,
      "--mail-from", SMTP_USER,
      "--mail-rcpt", to,
      "--upload-file", tmp.file,
    ], {
        input: configLines,
        timeout: 30000,
        encoding: "utf-8",
    });
  } finally {
    rmSync(tmp.dir, { recursive: true, force: true });
  }
  if (result.error || result.status !== 0) {
    // curl echoes its config on some errors, so scrub the credential before
    // this string is persisted as the step error.
    let detail = result.error?.message || result.stderr || `curl exited ${result.status}`;
    detail = String(detail).split(SMTP_PASSWORD).join("***");
    throw new Error(`SMTP send failed: ${detail.slice(0, 500)}`);
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
