/**
 * Browser connector - Playwright-based browser automation.
 * Dual-mode: importable functions + CLI dispatch.
 *
 * Supports CSS selector, text, and XPath-based element selection,
 * plus raw coordinate-based actions for Computer Use mode.
 *
 * Dependencies: playwright (with chromium browser)
 * No credentials required - browser runs inside the sandbox.
 */

import { chromium } from "playwright";

let browser = null;
let page = null;
let _viewportWidth = 1280;
let _viewportHeight = 720;

/**
 * Ensure a browser instance is running and return the active page.
 * Reuses the existing browser/page across calls within the same session.
 */
async function ensureBrowser(headless = true, viewportWidth = null, viewportHeight = null) {
  // Update viewport settings if provided
  if (viewportWidth) _viewportWidth = viewportWidth;
  if (viewportHeight) _viewportHeight = viewportHeight;

  if (!browser || !browser.isConnected()) {
    browser = await chromium.launch({
      headless,
      args: [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
      ],
    });
    const context = await browser.newContext({
      viewport: { width: _viewportWidth, height: _viewportHeight },
      userAgent:
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    });
    page = await context.newPage();
  }
  return page;
}

/**
 * Resolve an element locator based on the selection method.
 */
function resolveLocator(p, selector, method = "css") {
  switch (method) {
    case "text":
      return p.getByText(selector);
    case "xpath":
      return p.locator(`xpath=${selector}`);
    case "css":
    default:
      return p.locator(selector);
  }
}

// ---------------------------------------------------------------------------
// Tool functions
// ---------------------------------------------------------------------------

export async function navigate(url) {
  const p = await ensureBrowser();
  const response = await p.goto(url, { waitUntil: "domcontentloaded", timeout: 30000 });
  return {
    ok: true,
    url: p.url(),
    title: await p.title(),
    status: response?.status() ?? null,
  };
}

export async function click(selector, method = "css") {
  const p = await ensureBrowser();
  const locator = resolveLocator(p, selector, method);
  await locator.first().click({ timeout: 10000 });
  return { ok: true, selector, method };
}

export async function type_text(selector, text, clear = true) {
  const p = await ensureBrowser();
  const locator = p.locator(selector).first();
  if (clear) {
    await locator.fill(text, { timeout: 10000 });
  } else {
    await locator.focus({ timeout: 10000 });
    await p.keyboard.type(text);
  }
  return { ok: true, selector, typed: text.length };
}

export async function screenshot(full_page = false) {
  const p = await ensureBrowser();
  const buffer = await p.screenshot({ fullPage: full_page });
  return {
    ok: true,
    image_base64: buffer.toString("base64"),
    format: "png",
    viewport: { width: 1280, height: 720 },
  };
}

export async function get_text(selector) {
  const p = await ensureBrowser();
  const locator = p.locator(selector).first();
  const text = await locator.textContent({ timeout: 10000 });
  return { ok: true, selector, text: (text || "").trim() };
}

export async function wait_for(selector, timeout = 10000) {
  const p = await ensureBrowser();
  await p.locator(selector).first().waitFor({ state: "visible", timeout });
  return { ok: true, selector, found: true };
}

export async function select_option(selector, value) {
  const p = await ensureBrowser();
  // Try by value first, fall back to label
  let selected;
  try {
    selected = await p.locator(selector).first().selectOption({ value }, { timeout: 10000 });
  } catch {
    selected = await p.locator(selector).first().selectOption({ label: value }, { timeout: 10000 });
  }
  return { ok: true, selector, selected };
}

export async function get_page_html(selector = "body") {
  const p = await ensureBrowser();
  const locator = p.locator(selector).first();
  const html = await locator.innerHTML({ timeout: 10000 });
  // Truncate to avoid overwhelming the agent context
  const truncated = html.length > 50000;
  return {
    ok: true,
    selector,
    html: truncated ? html.slice(0, 50000) : html,
    truncated,
    length: html.length,
  };
}

// ---------------------------------------------------------------------------
// Computer Use mode - raw coordinate-based actions
// ---------------------------------------------------------------------------

export async function computer_use_action(action_type, x = null, y = null, text = null) {
  const p = await ensureBrowser();

  switch (action_type) {
    case "mouse_click":
      if (x == null || y == null) throw new Error("mouse_click requires x and y coordinates");
      await p.mouse.click(x, y);
      return { ok: true, action: "mouse_click", x, y };

    case "mouse_move":
      if (x == null || y == null) throw new Error("mouse_move requires x and y coordinates");
      await p.mouse.move(x, y);
      return { ok: true, action: "mouse_move", x, y };

    case "key_press":
      if (!text) throw new Error("key_press requires text (key name)");
      await p.keyboard.press(text);
      return { ok: true, action: "key_press", key: text };

    case "type_text_raw":
      if (!text) throw new Error("type_text_raw requires text");
      await p.keyboard.type(text);
      return { ok: true, action: "type_text_raw", typed: text.length };

    case "screenshot_raw": {
      const buffer = await p.screenshot({ fullPage: false });
      return {
        ok: true,
        action: "screenshot_raw",
        image_base64: buffer.toString("base64"),
        format: "png",
        viewport: { width: 1280, height: 720 },
      };
    }

    case "scroll_down":
      await p.mouse.wheel(0, 300);
      return { ok: true, action: "scroll_down", delta: 300 };

    case "scroll_up":
      await p.mouse.wheel(0, -300);
      return { ok: true, action: "scroll_up", delta: -300 };

    default:
      throw new Error(`Unknown computer_use action: ${action_type}`);
  }
}

// ---------------------------------------------------------------------------
// DOM Extraction mode functions
// ---------------------------------------------------------------------------

export async function get_accessibility_tree(maxDepth = 5) {
  const p = await ensureBrowser();
  const snapshot = await p.accessibility.snapshot({ interestingOnly: true });

  function pruneTree(node, depth) {
    if (!node || depth > maxDepth) return null;
    const result = {
      role: node.role,
      name: node.name || "",
    };
    if (node.value) result.value = node.value;
    if (node.checked !== undefined) result.checked = node.checked;
    if (node.pressed !== undefined) result.pressed = node.pressed;
    if (node.disabled) result.disabled = true;
    if (node.children && node.children.length > 0 && depth < maxDepth) {
      result.children = node.children
        .map(c => pruneTree(c, depth + 1))
        .filter(Boolean);
    }
    return result;
  }

  return {
    ok: true,
    tree: pruneTree(snapshot, 0),
    url: p.url(),
    title: await p.title(),
  };
}

export async function get_interactive_elements() {
  const p = await ensureBrowser();
  const elements = await p.evaluate(() => {
    const selectors = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="checkbox"], [role="radio"], [role="tab"], [role="menuitem"], [contenteditable="true"]';
    const nodes = document.querySelectorAll(selectors);
    const results = [];
    let idx = 0;
    for (const el of nodes) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      if (el.offsetParent === null && el.tagName !== 'BODY') continue;
      results.push({
        idx: idx++,
        tag: el.tagName.toLowerCase(),
        type: el.type || null,
        role: el.getAttribute('role') || null,
        name: el.getAttribute('aria-label') || el.innerText?.slice(0, 100) || el.getAttribute('name') || el.getAttribute('placeholder') || '',
        value: el.value || null,
        selector: buildSelector(el),
        rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
      });
    }

    function buildSelector(el) {
      if (el.id) return '#' + CSS.escape(el.id);
      const tag = el.tagName.toLowerCase();
      const cls = el.className && typeof el.className === 'string'
        ? '.' + el.className.trim().split(/\s+/).filter(Boolean).slice(0, 2).map(c => CSS.escape(c)).join('.')
        : '';
      const nth = el.parentElement
        ? ':nth-child(' + (Array.from(el.parentElement.children).indexOf(el) + 1) + ')'
        : '';
      return tag + cls + nth;
    }

    return results;
  });

  return {
    ok: true,
    elements,
    count: elements.length,
    url: p.url(),
  };
}

export async function extract_structured(schema) {
  const p = await ensureBrowser();
  const html = await p.locator('body').first().innerHTML({ timeout: 10000 });
  const truncated = html.length > 30000 ? html.slice(0, 30000) : html;
  return {
    ok: true,
    html: truncated,
    schema,
    url: p.url(),
    title: await p.title(),
  };
}

export async function get_page_info() {
  const p = await ensureBrowser();
  const info = await p.evaluate(() => ({
    url: document.location.href,
    title: document.title,
    forms: document.forms.length,
    links: document.links.length,
    images: document.images.length,
    iframes: document.querySelectorAll('iframe').length,
    hasCaptcha: !!(
      document.querySelector('[class*="captcha"]') ||
      document.querySelector('[id*="captcha"]') ||
      document.querySelector('iframe[src*="recaptcha"]') ||
      document.querySelector('iframe[src*="hcaptcha"]') ||
      document.querySelector('[class*="turnstile"]')
    ),
  }));
  return { ok: true, ...info };
}

// ---------------------------------------------------------------------------
// Cleanup - close browser when the process exits
// ---------------------------------------------------------------------------

async function cleanup() {
  if (browser) {
    try {
      await browser.close();
    } catch {
      // Ignore close errors during shutdown
    }
    browser = null;
    page = null;
  }
}

process.on("exit", () => cleanup());
process.on("SIGINT", () => cleanup().then(() => process.exit(0)));
process.on("SIGTERM", () => cleanup().then(() => process.exit(0)));

// ---------------------------------------------------------------------------
// CLI dispatch
// ---------------------------------------------------------------------------

if (process.argv[1]?.endsWith("browser.mjs")) {
  const [fn, ...args] = process.argv.slice(2);
  const dispatch = {
    navigate,
    click,
    type_text,
    screenshot,
    get_text,
    wait_for,
    select_option,
    get_page_html,
    computer_use_action,
    get_accessibility_tree,
    get_interactive_elements,
    extract_structured,
    get_page_info,
  };
  if (!dispatch[fn]) {
    console.error(
      `Usage: node browser.mjs <${Object.keys(dispatch).join("|")}> [args...]`
    );
    process.exit(1);
  }
  try {
    // Parse JSON-encoded arguments where applicable
    const parsedArgs = args.map((a) => {
      try {
        return JSON.parse(a);
      } catch {
        return a;
      }
    });
    const result = await dispatch[fn](...parsedArgs);
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exit(1);
  } finally {
    await cleanup();
  }
}
