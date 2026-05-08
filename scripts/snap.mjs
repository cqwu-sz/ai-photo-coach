// Headless screenshots of the new Cinema House visual.
// Run after the backend is up at http://127.0.0.1:8000 (`uvicorn app.main:app`).
//
//   node scripts/snap.mjs [--out=docs/preview]
//
// Captures:
//   1. /web/         — the live PWA home (mobile viewport 393x852)
//   2. /preview      — the iPhone 15 Pro mock-up wrapping the same PWA
//                      (desktop viewport 1440x960)
//
// Output files land next to this script unless --out is given.

import { chromium, devices } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

function arg(key, fallback) {
  const a = process.argv.find((s) => s.startsWith(`--${key}=`));
  return a ? a.slice(key.length + 3) : fallback;
}

const BASE = arg("base", "http://127.0.0.1:8000");
const OUT_DIR = resolve(__dirname, "..", arg("out", "docs/preview"));

mkdirSync(OUT_DIR, { recursive: true });

const browser = await chromium.launch({ headless: true });

// 1. Mobile viewport: PWA home directly (what the app looks like on iPhone).
{
  const ctx = await browser.newContext({
    ...devices["iPhone 15 Pro"],
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/web/`, { waitUntil: "networkidle" });
  // Let the entrance animation settle.
  await page.waitForTimeout(900);
  const out = resolve(OUT_DIR, "01_pwa_home_mobile.png");
  await page.screenshot({ path: out, fullPage: false });
  console.log("→", out);
  await ctx.close();
}

// 2. Desktop preview page wrapping the PWA in a CSS iPhone 15 Pro shell.
{
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/preview`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const out = resolve(OUT_DIR, "02_preview_iphone15pro_desktop.png");
  await page.screenshot({ path: out, fullPage: false });
  console.log("→", out);
  await ctx.close();
}

// 3. Mobile preview, full-page so user sees the entire info+device layout.
{
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 1700 },
    deviceScaleFactor: 1.5,
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/preview`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  const out = resolve(OUT_DIR, "03_preview_full.png");
  await page.screenshot({ path: out, fullPage: true });
  console.log("→", out);
  await ctx.close();
}

await browser.close();
console.log("\n✓ wrote screenshots to", OUT_DIR);
