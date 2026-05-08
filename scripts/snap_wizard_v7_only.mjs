// v7-only screenshot script — captures wizard_19_shots_swipe + wizard_20_shot_preview_3d.
// Faster smoke test that bypasses the wizard form (which drives a full
// AVCapture flow in fresh-user mode and intermittently blocks Playwright
// on visible-element checks). Uses the same fakeResponse the main script
// loads on result.html.
//
//   node scripts/snap_wizard_v7_only.mjs

import { chromium, devices } from "playwright";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const BASE = "http://127.0.0.1:8000";
const OUT = resolve(__dirname, "..", "docs/preview");
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({
  headless: true,
  args: ["--disable-gpu", "--no-sandbox", "--disk-cache-size=1"],
});

const bust = () => `?t=${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;

async function shoot(page, name) {
  const path = resolve(OUT, name);
  await page.screenshot({ path, fullPage: false });
  console.log("[shot]", path);
}

// Build a 3-shot fakeResponse so the swipe pager has something to swipe.
const fakeResponse = {
  scene: {
    type: "outdoor_park",
    lighting: "golden_hour",
    background_summary: "西侧低角度阳光透过桦林。",
    cautions: [],
  },
  shots: [
    {
      id: "shot_1", title: "主机位",
      angle: { azimuth_deg: 245, pitch_deg: -3, distance_m: 2.2 },
      composition: { primary: "rule_of_thirds", secondary: [], notes: "三分线下交点放眼睛" },
      camera: {
        focal_length_mm: 50, aperture: "f/2.0", shutter: "1/320", iso: 200,
        white_balance_k: 5500, ev_compensation: -0.3, rationale: "黄金光下 50/2 焦虚化", device_hints: null,
      },
      poses: [{
        id: "pose_single_relaxed_001", layout: "single",
        persons: [{ role: "subject", description: "自然站姿，目光略偏左前方" }],
      }],
      rationale: "暖光从左前方铺开，主体放在三分线下交点。", coach_brief: "肩部放松，下巴略收",
      confidence: 0.84, overall_score: 4.32,
      criteria_score: {
        composition: 5, light: 5, color: 4, depth: 4,
        subject_fit: 5, background: 4, theme: 5,
      },
      criteria_notes: { composition: "[comp_rule_of_thirds] 三分线" },
      strongest_axis: "theme", weakest_axis: "depth",
    },
    {
      id: "shot_2", title: "侧逆光剪影",
      angle: { azimuth_deg: 200, pitch_deg: 0, distance_m: 4.0 },
      composition: { primary: "centered", secondary: [], notes: "" },
      camera: {
        focal_length_mm: 85, aperture: "f/4.0", shutter: "1/500", iso: 100,
        white_balance_k: 4500, ev_compensation: -1.0, rationale: "压暗背景突出剪影",
      },
      poses: [], rationale: "黄昏剪影构图。", confidence: 0.72, overall_score: 3.78,
      criteria_score: {
        composition: 3, light: 5, color: 3, depth: 4,
        subject_fit: 4, background: 4, theme: 4,
      },
      criteria_notes: { theme: "[theme_solitude_vs_group] 黄昏剪影" },
      strongest_axis: "light", weakest_axis: "composition",
    },
    {
      id: "shot_3", title: "环境对话",
      angle: { azimuth_deg: 110, pitch_deg: 5, distance_m: 5.5 },
      composition: { primary: "leading_lines", secondary: [], notes: "" },
      camera: {
        focal_length_mm: 35, aperture: "f/5.6", shutter: "1/250", iso: 200,
        white_balance_k: 5200, ev_compensation: 0.0, rationale: "广角带出环境",
      },
      poses: [], rationale: "用透视线引向主体。", confidence: 0.68, overall_score: 3.50,
      criteria_score: {
        composition: 4, light: 4, color: 4, depth: 5,
        subject_fit: 3, background: 4, theme: 3,
      },
      criteria_notes: { depth: "[depth_three_layers_explicit] 前中远三层" },
      strongest_axis: "depth", weakest_axis: "subject_fit",
    },
  ],
  style_inspiration: null,
  environment: {
    sun: { azimuth_deg: 245, altitude_deg: 18, time_of_day: "golden_hour" },
  },
  generated_at: new Date().toISOString(),
  model: "mock-1",
};

const ctx = await browser.newContext({
  bypassCSP: true,
  ...devices["iPhone 15 Pro"],
  deviceScaleFactor: 2,
  colorScheme: "dark",
});
const page = await ctx.newPage();
page.on("pageerror", (err) => console.log("[pageerror]", err.message));
page.on("console", (msg) => {
  const t = msg.type();
  if (t === "warning" || t === "error") {
    console.log(`[browser ${t}]`, msg.text().slice(0, 400));
  }
});
await page.addInitScript((r) => {
  try {
    sessionStorage.setItem("apc.result", JSON.stringify(r));
    sessionStorage.setItem("apc.refInspiration", JSON.stringify({ count: 0, list: [] }));
    localStorage.setItem("apc.avatarPicks", JSON.stringify(["female_casual_22"]));
  } catch {}
}, fakeResponse);
await page.goto(`${BASE}/web/result.html${bust()}`, { waitUntil: "networkidle" });
await page.waitForTimeout(1000);

// wizard_19 — shots swipe pager with the second slide peeking from the right
await page.evaluate(() => {
  const se = document.scrollingElement || document.documentElement;
  if (se) se.scrollTop = 0;
});
await page.waitForTimeout(200);
const pagerExists = await page.evaluate(() => {
  const pager = document.querySelector(".shots-pager");
  if (!pager) return false;
  pager.scrollLeft = pager.clientWidth * 0.30;
  return true;
});
console.log("pager exists:", pagerExists);
await page.waitForTimeout(400);
await shoot(page, "wizard_19_shots_swipe.png");

// wizard_20 — 3D shot preview with composition guide + HUD
await page.evaluate(() => {
  const pager = document.querySelector(".shots-pager");
  if (pager) pager.scrollLeft = 0;
  const se = document.scrollingElement || document.documentElement;
  if (se) se.scrollTop = 0;
});
await page.waitForTimeout(200);
const toggled = await page.evaluate(() => {
  const slide = document.querySelector(".shot-slide");
  if (!slide) return { found: false };
  const btn = slide.querySelector('.hero-toggle-btn[data-mode="3d"]');
  if (!btn) return { found: false };
  btn.click();
  return { found: true };
});
console.log("3D toggled:", toggled);
if (toggled.found) {
  // BokehPass + GLTFLoader compile shader programs lazily on first
  // paint; give a generous budget.
  await page.waitForTimeout(3500);
  const dbg = await page.evaluate(() => {
    const stage = document.querySelector(".hero-3d-stage");
    const errEl = document.querySelector(".hero-3d-error");
    return {
      hasStage: !!stage,
      errText: errEl?.textContent?.slice(0, 200) || null,
    };
  });
  console.log("[3D dbg]", dbg);
  // Crop the canvas region exactly so the user sees the 3D preview
  // with composition guide + HUD chips, free of surrounding chrome.
  // Use Playwright's elementHandle.scrollIntoViewIfNeeded — it reliably
  // scrolls the page even when child containers have their own
  // scroll context (the .shots-pager horizontal pager intercepts
  // scrollIntoView in some browsers).
  const stageHandle = await page.$(".hero-3d-stage");
  if (stageHandle) {
    await stageHandle.scrollIntoViewIfNeeded({ timeout: 3000 }).catch(() => {});
  }
  await page.waitForTimeout(400);
  const stageBox = await page.evaluate(() => {
    const stage = document.querySelector(".hero-3d-stage");
    if (!stage) return null;
    const r = stage.getBoundingClientRect();
    return { x: r.left, y: r.top, width: r.width, height: r.height,
             pageScrollY: window.scrollY };
  });
  await page.waitForTimeout(300);
  console.log("[stageBox]", stageBox);
  // Inspect the canvas pixels — make sure something was actually painted.
  const canvasInfo = await page.evaluate(() => {
    const stage = document.querySelector(".hero-3d-stage");
    if (!stage) return null;
    const c = stage.querySelector("canvas");
    if (!c) return { canvasFound: false };
    const ctx = c.getContext("webgl") || c.getContext("webgl2");
    return {
      canvasFound: true,
      width: c.width, height: c.height,
      cssW: c.clientWidth, cssH: c.clientHeight,
      hasGL: !!ctx,
      childCount: stage.children.length,
    };
  });
  console.log("[canvas]", canvasInfo);
  if (stageBox && stageBox.width > 100 && stageBox.height > 100) {
    await page.screenshot({
      path: resolve(OUT, "wizard_20_shot_preview_3d.png"),
      clip: stageBox,
    });
    console.log("[shot] wizard_20_shot_preview_3d.png (clipped to stage)");
  } else {
    await shoot(page, "wizard_20_shot_preview_3d.png");
  }
}
await ctx.close();
await browser.close();
console.log("[done]");
