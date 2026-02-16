import { chromium } from "playwright";
import { fileURLToPath } from "url";
import path from "path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(__dirname, "..", "docs", "screenshots");
const BASE = process.env.BASE_URL || "http://localhost:5173";

const pages = [
  { name: "overview-dark", path: "/" },
  { name: "runs", path: "/runs" },
  { name: "run-detail", path: "/runs/a1b2c3d4-1111-4000-8000-000000000001" },
  { name: "run-detail-running", path: "/runs/a1b2c3d4-2222-4000-8000-000000000002" },
  { name: "run-detail-failed", path: "/runs/a1b2c3d4-4444-4000-8000-000000000004" },
  { name: "run-detail-replay", path: "/runs/a1b2c3d4-3333-4000-8000-000000000003" },
  { name: "workflows", path: "/workflows" },
  { name: "approvals", path: "/approvals" },
  { name: "autopilot", path: "/autopilot" },
  { name: "violations", path: "/violations" },
  { name: "optimizer", path: "/optimizer" },
  { name: "schedules", path: "/schedules" },
  { name: "dead-letter", path: "/dead-letter" },
  { name: "api-keys", path: "/api-keys" },
  { name: "settings", path: "/settings" },
];

async function main() {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });

  // Set dark theme in localStorage before any page loads
  await context.addInitScript(() => {
    localStorage.setItem("theme", "dark");
  });

  const page = await context.newPage();

  for (const p of pages) {
    console.log(`Capturing ${p.name}...`);
    await page.goto(`${BASE}${p.path}`, { waitUntil: "networkidle" });
    // Ensure dark class is on html
    await page.evaluate(() => {
      document.documentElement.classList.remove("light");
      document.documentElement.classList.add("dark");
    });
    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(OUT, `${p.name}.png`) });
  }

  // DAG preview - click DAG button on first workflow card
  console.log("Capturing dag-preview...");
  await page.goto(`${BASE}/workflows`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(300);
  const dagBtn = page.locator("button", { hasText: "DAG" }).first();
  await dagBtn.click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: path.join(OUT, "dag-preview.png") });

  // Approvals detail - expand first approval
  console.log("Capturing approvals-detail...");
  await page.goto(`${BASE}/approvals`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(300);
  // Click on the first approval card to expand
  const firstApproval = page.locator("[class*=rounded-xl]").filter({ hasText: "Review" }).first();
  await firstApproval.click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "approvals-detail.png") });

  // AutoPilot detail - expand first experiment
  console.log("Capturing autopilot-detail...");
  await page.goto(`${BASE}/autopilot`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(500);
  await page.locator(".cursor-pointer").first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, "autopilot-detail.png") });

  // Violations detail - expand first violation
  console.log("Capturing violations-detail...");
  await page.goto(`${BASE}/violations`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(500);
  await page.locator(".cursor-pointer").first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, "violations-detail.png") });

  // Optimizer detail - expand first decision
  console.log("Capturing optimizer-detail...");
  await page.goto(`${BASE}/optimizer`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(500);
  await page.locator(".cursor-pointer").first().click();
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, "optimizer-detail.png") });

  // Workflow builder with template browser open
  console.log("Capturing workflow-builder (updated)...");
  await page.goto(`${BASE}/workflows/builder`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    document.documentElement.classList.remove("light");
    document.documentElement.classList.add("dark");
  });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "workflow-builder.png") });

  console.log("Capturing template-browser...");
  const templateBtn = page.locator("button", { hasText: "From Template" }).first();
  if (await templateBtn.isVisible()) {
    await templateBtn.click();
    await page.waitForTimeout(1000);
  }
  await page.screenshot({ path: path.join(OUT, "template-browser.png") });

  // Light mode overview
  console.log("Capturing overview-light...");
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.evaluate(() => {
    localStorage.setItem("theme", "light");
    document.documentElement.classList.remove("dark");
    document.documentElement.classList.add("light");
  });
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT, "overview-light.png") });

  await browser.close();
  console.log("Done! All screenshots saved.");
}

main().catch(console.error);
