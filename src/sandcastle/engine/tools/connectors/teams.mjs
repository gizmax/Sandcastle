/**
 * Microsoft Teams connector - send messages via incoming webhook.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Credentials: TOOL_TEAMS_WEBHOOK_URL
 */

const WEBHOOK_URL = process.env.TOOL_TEAMS_WEBHOOK_URL || "";

export async function send_message(text, title = "") {
  if (!WEBHOOK_URL) throw new Error("TOOL_TEAMS_WEBHOOK_URL is not configured");

  const card = {
    type: "message",
    attachments: [{
      contentType: "application/vnd.microsoft.card.adaptive",
      content: {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        type: "AdaptiveCard",
        version: "1.4",
        body: [],
      },
    }],
  };

  const body = card.attachments[0].content.body;

  if (title) {
    body.push({
      type: "TextBlock",
      text: title,
      weight: "Bolder",
      size: "Medium",
    });
  }

  body.push({
    type: "TextBlock",
    text,
    wrap: true,
  });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const resp = await fetch(WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(card),
      signal: controller.signal,
    });

    if (!resp.ok) {
      const errText = await resp.text();
      throw new Error(`Teams webhook ${resp.status}: ${errText.slice(0, 500)}`);
    }

    return { ok: true, title, text_length: text.length };
  } finally {
    clearTimeout(timer);
  }
}

// CLI dispatch
if (process.argv[1]?.endsWith("teams.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = { send_message };
  if (!dispatch[fn]) {
    console.error(`Usage: node teams.mjs <send_message> [args...]`);
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
