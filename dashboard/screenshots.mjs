import { chromium } from "playwright";

const BASE = "http://localhost:5175";
const OUT = "../docs/screenshots";

const pages = [
  { name: "overview", path: "/", wait: 1500 },
  { name: "overview-dark", path: "/", wait: 500, dark: true },
  { name: "runs", path: "/runs", wait: 1000 },
  { name: "run-detail", path: "/runs/a1b2c3d4-1111-4000-8000-000000000001", wait: 1000 },
  { name: "run-detail-running", path: "/runs/a1b2c3d4-2222-4000-8000-000000000002", wait: 1000 },
  { name: "workflows", path: "/workflows", wait: 1000 },
  { name: "schedules", path: "/schedules", wait: 1000 },
  { name: "dead-letter", path: "/dead-letter", wait: 1000 },
];

const browser = await chromium.launch();

for (const p of pages) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: p.dark ? "dark" : "light",
  });
  const page = await context.newPage();

  // Set dark class if needed
  if (p.dark) {
    await page.goto(BASE + p.path);
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.waitForTimeout(p.wait);
  } else {
    await page.goto(BASE + p.path);
    await page.waitForTimeout(p.wait);
  }

  await page.screenshot({ path: `${OUT}/${p.name}.png`, fullPage: false });
  console.log(`  captured: ${p.name}.png`);
  await context.close();
}

// DAG view - click the DAG button on first workflow card
{
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    colorScheme: "light",
  });
  const page = await context.newPage();
  await page.goto(BASE + "/workflows");
  await page.waitForTimeout(800);
  await page.locator("button", { hasText: "DAG" }).first().click();
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/dag-preview.png`, fullPage: true });
  console.log("  captured: dag-preview.png");
  await context.close();
}

await browser.close();
console.log("Done - all screenshots saved to docs/screenshots/");
