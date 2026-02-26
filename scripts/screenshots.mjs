import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, "..", "docs", "screenshots");
const BASE = process.env.BASE_URL || "https://gizmax.github.io/Sandcastle";

// All pages captured in light mode
const pages = [
  { name: "overview", path: "/" },
  { name: "runs", path: "/runs" },
  { name: "run-detail", path: "/runs/a1b2c3d4-1111-4000-8000-000000000001" },
  { name: "run-detail-running", path: "/runs/a1b2c3d4-2222-4000-8000-000000000002" },
  { name: "run-detail-failed", path: "/runs/a1b2c3d4-4444-4000-8000-000000000004" },
  { name: "run-detail-replay", path: "/runs/a1b2c3d4-3333-4000-8000-000000000003" },
  { name: "workflows", path: "/workflows" },
  { name: "integrations", path: "/integrations" },
  { name: "approvals", path: "/approvals" },
  { name: "autopilot", path: "/autopilot" },
  { name: "evaluations", path: "/evaluations" },
  { name: "violations", path: "/violations" },
  { name: "optimizer", path: "/optimizer" },
  { name: "schedules", path: "/schedules" },
  { name: "dead-letter", path: "/dead-letter" },
  { name: "api-keys", path: "/api-keys" },
  { name: "settings", path: "/settings" },
  { name: "system", path: "/system" },
];

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

  // Set light theme in localStorage before any page loads
  await context.addInitScript(() => {
    localStorage.setItem("theme", "light");
  });

  const page = await context.newPage();

  // Standard pages
  for (const p of pages) {
    console.log(`Capturing ${p.name}...`);
    await page.goto(`${BASE}${p.path}`, { waitUntil: "domcontentloaded" });
    await setLight(page);
    await page.waitForTimeout(3000);
    await page.screenshot({ path: path.join(OUT, `${p.name}.png`) });
  }

  // DAG preview - click DAG button on first workflow card
  console.log("Capturing dag-preview...");
  await page.goto(`${BASE}/workflows`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  const dagBtn = page.locator("button", { hasText: "DAG" }).first();
  await dagBtn.click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "dag-preview.png") });

  // Approvals detail - expand first approval
  console.log("Capturing approvals-detail...");
  await page.goto(`${BASE}/approvals`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2000);
  const firstApproval = page.locator("[class*=rounded-xl]").filter({ hasText: "Review" }).first();
  await firstApproval.click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, "approvals-detail.png") });

  // AutoPilot detail - expand first experiment
  console.log("Capturing autopilot-detail...");
  await page.goto(`${BASE}/autopilot`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2500);
  await page.locator(".cursor-pointer").first().click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "autopilot-detail.png") });

  // Violations detail - expand first violation
  console.log("Capturing violations-detail...");
  await page.goto(`${BASE}/violations`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2500);
  await page.locator(".cursor-pointer").first().click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "violations-detail.png") });

  // Optimizer detail - expand first decision
  console.log("Capturing optimizer-detail...");
  await page.goto(`${BASE}/optimizer`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2500);
  await page.locator(".cursor-pointer").first().click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(OUT, "optimizer-detail.png") });

  // Workflow builder - open via Edit on first workflow card
  console.log("Capturing workflow-builder...");
  await page.goto(`${BASE}/workflows`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2500);
  const editBtn = page.locator("button", { hasText: "Edit" }).first();
  if (await editBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await editBtn.click();
    await page.waitForTimeout(3000);
    await setLight(page);
  } else {
    await page.goto(`${BASE}/workflows/builder`, { waitUntil: "domcontentloaded" });
    await setLight(page);
    await page.waitForTimeout(2500);
  }
  await page.screenshot({ path: path.join(OUT, "workflow-builder.png") });

  // Template browser
  console.log("Capturing template-browser...");
  const templateBtn = page.locator("button", { hasText: "From Template" }).first();
  if (await templateBtn.isVisible()) {
    await templateBtn.click();
    await page.waitForTimeout(2500);
  }
  await page.screenshot({ path: path.join(OUT, "template-browser.png") });

  // Onboarding
  console.log("Capturing onboarding...");
  await page.goto(`${BASE}/onboarding`, { waitUntil: "domcontentloaded" });
  await setLight(page);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, "onboarding.png") });

  // Dark mode overview (secondary)
  console.log("Capturing overview-dark...");
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    localStorage.setItem("theme", "dark");
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT, "overview-dark.png") });

  await browser.close();
  console.log("Done! All screenshots saved.");
}

main().catch(console.error);
