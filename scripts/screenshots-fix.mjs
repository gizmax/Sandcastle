import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, "..", "docs", "screenshots");
const BASE = process.env.BASE_URL || "http://localhost:5173";

function setLight(page) {
  return page.evaluate(() => {
    localStorage.setItem("theme", "light");
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
}

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "light",
  });
  await context.addInitScript(() => {
    localStorage.setItem("theme", "light");
  });
  const page = await context.newPage();

  // Approvals detail
  console.log("Capturing approvals-detail...");
  await page.goto(`${BASE}/approvals`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  const chevron = page.locator("button svg.lucide-chevron-down").first();
  if (await chevron.isVisible({ timeout: 3000 }).catch(() => false)) {
    await chevron.click();
    await page.waitForTimeout(800);
  }
  await page.screenshot({ path: path.join(OUT, "approvals-detail.png") });

  // AutoPilot detail
  console.log("Capturing autopilot-detail...");
  await page.goto(`${BASE}/autopilot`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  const apChevron = page.locator("button svg.lucide-chevron-down").first();
  if (await apChevron.isVisible({ timeout: 3000 }).catch(() => false)) {
    await apChevron.click();
    await page.waitForTimeout(800);
  }
  await page.screenshot({ path: path.join(OUT, "autopilot-detail.png") });

  // Violations detail
  console.log("Capturing violations-detail...");
  await page.goto(`${BASE}/violations`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  const vioChevron = page.locator("button svg.lucide-chevron-down").first();
  if (await vioChevron.isVisible({ timeout: 3000 }).catch(() => false)) {
    await vioChevron.click();
    await page.waitForTimeout(800);
  }
  await page.screenshot({ path: path.join(OUT, "violations-detail.png") });

  // Optimizer detail
  console.log("Capturing optimizer-detail...");
  await page.goto(`${BASE}/optimizer`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  const optChevron = page.locator("button svg.lucide-chevron-down").first();
  if (await optChevron.isVisible({ timeout: 3000 }).catch(() => false)) {
    await optChevron.click();
    await page.waitForTimeout(800);
  }
  await page.screenshot({ path: path.join(OUT, "optimizer-detail.png") });

  // Workflow builder
  console.log("Capturing workflow-builder...");
  await page.goto(`${BASE}/workflows/builder`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "workflow-builder.png") });

  // Template browser
  console.log("Capturing template-browser...");
  const templateBtn = page.locator("button", { hasText: "From Template" }).first();
  if (await templateBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await templateBtn.click();
    await page.waitForTimeout(1500);
  }
  await page.screenshot({ path: path.join(OUT, "template-browser.png") });

  // Onboarding
  console.log("Capturing onboarding...");
  await page.goto(`${BASE}/onboarding`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "onboarding.png") });

  // Dark mode overview
  console.log("Capturing overview-dark...");
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    localStorage.setItem("theme", "dark");
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "overview-dark.png") });

  await browser.close();
  console.log("Done! Remaining screenshots saved.");
}

main().catch(console.error);
