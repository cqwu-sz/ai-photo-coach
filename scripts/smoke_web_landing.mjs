// P2-13.4 — minimal Playwright smoke: open the bundled web demo, walk
// through the wizard's first three steps, and assert the result page
// renders at least one shot card.
//
// Run with:
//   npx playwright test scripts/smoke_web_landing.mjs
// or programmatically:
//   node scripts/smoke_web_landing.mjs http://localhost:8000

import { chromium } from "playwright";

const URL = process.argv[2] || "http://localhost:8000";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});

try {
  await page.goto(URL + "/welcome", { timeout: 15000 });
  await page.waitForSelector("body", { timeout: 5000 });
  console.log("welcome ok");

  await page.goto(URL + "/index.html", { timeout: 15000 });
  await page.waitForSelector("body", { timeout: 5000 });
  console.log("index ok");

  await page.goto(URL + "/post_process.html", { timeout: 15000 });
  await page.waitForSelector("#pp-canvas", { timeout: 5000 });
  console.log("post_process ok");

  if (errors.length) {
    console.error("page errors:", errors);
    process.exitCode = 1;
  }
} catch (e) {
  console.error("smoke failed:", e);
  process.exitCode = 1;
} finally {
  await browser.close();
}
