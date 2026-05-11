// Capture all 4 wizard steps + returning-user landing + scenery shortcut.
// Output goes to docs/preview/.
//
//   node scripts/snap_wizard.mjs
//
// Pre-reqs:
//   - backend running on http://127.0.0.1:8000
//   - npm i playwright (already in package.json)

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

/** Wizard URL: include skipWelcome=1 so fresh users land in the wizard
    and aren't redirected to /web/welcome.html. */
const wizardUrl = () =>
  `${BASE}/web/?skipWelcome=1&t=${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

async function shoot(page, name) {
  const path = resolve(OUT, name);
  await page.screenshot({ path, fullPage: false });
  console.log("[shot]", path);
}

async function freshContext() {
  return await browser.newContext({
    bypassCSP: true,
    ...devices["iPhone 15 Pro"],
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
}

// FRESH USER: walk every step from 1 to 4.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch {}
  });

  await page.goto(`${wizardUrl()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await shoot(page, "wizard_01_scene.png");

  await page.click('.scene-card[data-value="portrait"]');
  await page.click("#next-btn");
  await page.waitForTimeout(600);
  await shoot(page, "wizard_02_cast.png");

  await page.click('.person-pill[data-value="2"]');
  await page.click("#next-btn");
  await page.waitForTimeout(600);
  await shoot(page, "wizard_03_tone.png");

  // Step 3 now uses visual style cards instead of English suggest pills.
  await page.click('.style-card[data-style-id="cinematic_moody"] [data-pick]');
  await page.click("#next-btn");
  await page.waitForTimeout(600);
  await shoot(page, "wizard_04_review.png");

  await ctx.close();
}

// RETURNING USER: seed storage so we land directly on Step 4.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try {
      localStorage.clear();
      localStorage.setItem(
        "aphc.wizardProgress",
        JSON.stringify({ furthestStep: 4, completed: true, lastUpdated: Date.now() }),
      );
      localStorage.setItem(
        "apc.lastPrefs",
        JSON.stringify({
          sceneMode: "documentary",
          personCount: 2,
          qualityMode: "high",
          styleKeywords: ["film", "warm"],
        }),
      );
      localStorage.setItem("apc.sceneMode", "documentary");
    } catch {}
  });
  await page.goto(`${wizardUrl()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await shoot(page, "wizard_05_returning_user.png");
  await ctx.close();
}

// SCENERY: skips Step 2.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch {}
  });
  await page.goto(`${wizardUrl()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(500);
  await page.click('.scene-card[data-value="scenery"]');
  await page.click("#next-btn");                 // 1 -> (skip 2) -> 3
  await page.waitForTimeout(600);
  await shoot(page, "wizard_06_scenery_step3.png");
  await page.click("#next-btn");                 // 3 -> 4
  await page.waitForTimeout(600);
  await shoot(page, "wizard_07_scenery_review.png");
  await ctx.close();
}

// WELCOME PAGE (first-visit splash).
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch {}
  });
  await page.goto(`${BASE}/welcome${bust()}`, { waitUntil: "networkidle" });
  // Give marquee 1.5s so posters slide a bit (looks alive, not static).
  await page.waitForTimeout(1500);
  await shoot(page, "wizard_09_welcome.png");
  await ctx.close();
}

// REUSE FLOW: returning user with cached environment frames lands on Step 4
// and the "reuse" chip is visible.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try {
      localStorage.clear();
      localStorage.setItem(
        "aphc.wizardProgress",
        JSON.stringify({ furthestStep: 4, completed: true, lastUpdated: Date.now() }),
      );
      localStorage.setItem(
        "apc.lastPrefs",
        JSON.stringify({
          sceneMode: "documentary",
          personCount: 2,
          qualityMode: "high",
          styleKeywords: ["film", "warm"],
        }),
      );
      localStorage.setItem("apc.sceneMode", "documentary");
    } catch {}
  });

  // Visit a blank page first so we can populate IndexedDB synchronously
  // before the wizard's bootstrap runs.
  await page.goto(`${BASE}/web/welcome.html?seedOnly=1`, { waitUntil: "domcontentloaded" });
  await page.evaluate(async () => {
    await new Promise((resolve, reject) => {
      const req = indexedDB.open("aphc-frames", 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains("frames")) db.createObjectStore("frames", { keyPath: "index" });
        if (!db.objectStoreNames.contains("meta"))   db.createObjectStore("meta", { keyPath: "id" });
      };
      req.onerror = () => reject(req.error);
      req.onsuccess = () => {
        const db = req.result;
        const tx = db.transaction(["frames", "meta"], "readwrite");
        const tinyPng = new Uint8Array([
          137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82,0,0,0,1,0,0,0,1,
          8,2,0,0,0,144,119,83,222,0,0,0,12,73,68,65,84,8,153,99,248,255,
          255,63,0,5,0,1,0,213,222,55,229,0,0,0,0,73,69,78,68,174,66,96,130,
        ]);
        for (let i = 0; i < 8; i++) {
          tx.objectStore("frames").put({
            index: i,
            blob: new Blob([tinyPng], { type: "image/png" }),
            meta: { azimuth_deg: i * 45, pitch_deg: 0, roll_deg: 0, timestamp_ms: i * 220 },
          });
        }
        tx.objectStore("meta").put({
          id: "singleton",
          capturedAt: Date.now() - 5 * 60 * 1000,
          sceneMode: "portrait",
          panoramaUrl: null,
          count: 8,
        });
        tx.oncomplete = () => { db.close(); resolve(); };
        tx.onerror = () => reject(tx.error);
      };
    });
  });

  // Now navigate to the wizard — IndexedDB already has the seeded record,
  // so the reuse chip will appear when Step 4 reads it.
  await page.goto(`${wizardUrl()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1400);
  await shoot(page, "wizard_10_reuse_chip.png");
  await ctx.close();
}

// LIGHT-SHADOW WIZARD: pick the new "光影" scene card.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    try { localStorage.clear(); sessionStorage.clear(); } catch {}
  });
  await page.goto(`${wizardUrl()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  // Step 1 is now 6 cards including the new 光影 mode at the end.
  await page.click('.scene-card[data-value="light_shadow"]');
  await page.waitForTimeout(250);
  await shoot(page, "wizard_11_lightshadow_step1.png");
  await ctx.close();
}

// RESULT PAGE with 4-dimension scoring panel + sun compass + countdown.
// Mock mode synthesizes criteria_score automatically; we pre-seed
// localStorage with a fake AnalyzeResponse that includes environment so
// the screenshot shows the full upgraded UI without going through the
// camera capture flow.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  const fakeResponse = {
    scene: {
      type: "outdoor_park",
      lighting: "golden_hour",
      background_summary: "西侧低角度阳光透过桦林，地面是浅色石板路，远景一座灰色凉亭。",
      cautions: ["注意逆光下人脸欠曝", "石板路反光可能过曝"],
    },
    shots: [
      {
        id: "shot_1",
        title: "黄昏侧逆光半身",
        representative_frame_index: 3,
        angle: { azimuth_deg: 245, pitch_deg: -3, distance_m: 2.2, height_hint: "eye_level" },
        composition: { primary: "rule_of_thirds", secondary: ["leading_line"], notes: "把模特放在右三分线" },
        camera: {
          focal_length_mm: 50, aperture: "f/2.0", shutter: "1/320", iso: 200,
          white_balance_k: 5500, ev_compensation: -0.3,
          rationale: "侧逆光让发丝形成轮廓光，50mm 压缩空间。",
          device_hints: { iphone_lens: "tele_2x" },
          iphone_apply_plan: {
            zoom_factor: 2.0,
            iso: 200,
            shutter_seconds: 1 / 320,
            ev_compensation: -0.3,
            white_balance_k: 5500,
            aperture_note: "iPhone 主摄物理光圈固定 f/1.78；AI 用 ISO/快门组合实现 f/2.0 的曝光等效",
            can_apply: true,
          },
        },
        poses: [],
        rationale: "我建议你转到面向落日的方向（约 245°），让模特站到那条石板路右上方的三分线上，借这道侧逆光把发丝勾出来。",
        coach_brief: "靠着长椅蹲下来，看向他",
        confidence: 0.84,
        overall_score: 4.32,
        criteria_score: {
          composition: 5, light: 5, color: 4, depth: 4,
          subject_fit: 5, background: 4, theme: 5,
        },
        criteria_notes: {
          composition: "[comp_rule_of_thirds] 三分线 + 石板路引导线，两人高低错位形成视觉重心",
          light: "[light_golden_hour] 侧逆光做发丝 rim light，避开顶光与硬阴影",
          color: "[color_complementary] 暖调主导 + 凉亭灰做辅色，60-30-10 比例稳",
          depth: "[depth_three_layers] 50mm 配 f/2.0 在 2.2m 距离虚化恰到好处",
          subject_fit: "[sub_subject_size] 半身占比 60%，主体清晰",
          background: "[bg_clean_simple] 桦林背景做减法，主体跳出",
          theme: "[theme_one_idea] 一句话主题：暖光下的并肩",
        },
        strongest_axis: "theme",
        weakest_axis: "depth",
        iphone_tips: [
          "切到 2x 长焦端拍 50mm 等效，避免主摄数码裁剪丢细节",
          "iPhone 物理光圈 f/1.78 已是最大，要更强发丝高光请靠近主体半步",
          "长按主体脸部锁定 AE/AF 后向下滑 -0.3 EV 以保留高光",
        ],
      },
      {
        id: "shot_2",
        title: "对称剪影",
        representative_frame_index: 5,
        angle: { azimuth_deg: 200, pitch_deg: 0, distance_m: 4.0, height_hint: "low" },
        composition: { primary: "symmetry", secondary: [], notes: "中央留白配合天空渐变" },
        camera: { focal_length_mm: 85, aperture: "f/4.0", shutter: "1/500", iso: 100,
          white_balance_k: 4500, ev_compensation: -1.0,
          rationale: "85mm 长焦把背景拉近，剪影靠天空渐变做层次。" },
        poses: [],
        rationale: "试一下完全逆光的剪影，让人物站在落日正中间，等他们牵手回头那一瞬间按下快门。",
        coach_brief: "面向太阳走两步，回头看我",
        confidence: 0.72,
        overall_score: 3.78,
        criteria_score: {
          composition: 3, light: 5, color: 3, depth: 4,
          subject_fit: 4, background: 4, theme: 4,
        },
        criteria_notes: {
          composition: "[comp_centered] 中心构图稍呆板，可以让模特微侧身打破对称",
          light: "[light_golden_hour] 逆光做剪影最强，按高光保留",
          color: "[color_analogous] 单色剪影，依赖天空渐变做层次",
          depth: "[depth_atmospheric] 85mm 长焦压缩天际线",
          subject_fit: "[sub_silhouette_pose] 张开双臂剪影更可读",
          background: "[bg_horizon_level] 地平线放 1/3 线",
          theme: "[theme_solitude_vs_belonging] 意境主题：黄昏并肩",
        },
        strongest_axis: "light",
        weakest_axis: "composition",
      },
    ],
    style_inspiration: null,
    environment: {
      sun: {
        azimuth_deg: 245.3,
        altitude_deg: 7.2,
        phase: "golden_hour_dusk",
        color_temp_k_estimate: 3200,
        minutes_to_golden_end: 23,
        minutes_to_blue_end: null,
        minutes_to_sunset: 23,
        minutes_to_sunrise: null,
      },
      // NEW: Open-Meteo weather + LLM-derived vision_light demo data so
      // the env-strip shows weather chip and the compass cross-checks
      // the LLM estimate against the real sun.
      weather: {
        cloud_cover_pct: 32,
        visibility_m: 22000,
        uv_index: 5.4,
        temperature_c: 18.0,
        weather_code: 2,
        softness: "mixed",
        code_label_zh: "局部多云",
      },
      vision_light: {
        direction_deg: 248,
        quality: "hard",
        confidence: 0.78,
        notes: "第 5 帧 azimuth 248° 高光最强，主光来自西偏南。",
      },
      timestamp: new Date().toISOString(),
    },
    generated_at: new Date().toISOString(),
    model: "mock-1",
    debug: { mode: "mock" },
  };
  await page.addInitScript((r) => {
    try {
      sessionStorage.setItem("apc.result", JSON.stringify(r));
      // Avoid empty-state on result.html; also seed referenceLearned to skip
      // inspiration card.
      sessionStorage.setItem("apc.refInspiration", JSON.stringify({ count: 0, list: [] }));
      // Frames are optional — render.js falls back gracefully without them.
    } catch {}
  }, fakeResponse);
  await page.goto(`${BASE}/web/result.html${bust()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  // 1) Hero shot: env strip (sun compass + countdown).
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(200);
  await shoot(page, "wizard_12_result_sun_compass.png");
  // 2) Criteria panel — find it and scroll the right scrolling element.
  // result.html uses <main class="app">; on iPhone 15 Pro the page itself
  // is the scrolling container for `<body>`, so window.scrollTo works.
  // Use document.scrollingElement to handle either case.
  const yOff = await page.evaluate(() => {
    const panel = document.querySelector(".criteria-panel");
    if (!panel) return -1;
    panel.scrollIntoView({ block: "start", behavior: "instant" });
    const se = document.scrollingElement || document.documentElement;
    return se ? se.scrollTop : window.scrollY;
  });
  if (yOff >= 0) {
    // Pad a bit so the title isn't pushed off the top.
    await page.evaluate((y) => {
      const se = document.scrollingElement || document.documentElement;
      const target = Math.max(0, y - 80);
      if (se) se.scrollTop = target;
      window.scrollTo(0, target);
    }, yOff);
    await page.waitForTimeout(400);
  } else {
    console.warn("[snap] .criteria-panel not found in DOM — page render?");
  }
  await shoot(page, "wizard_13_result_4d_criteria.png");

  // 3) iPhone-tips card — sits below criteria panel.
  const tipsOff = await page.evaluate(() => {
    const card = document.querySelector(".iphone-tips-card");
    if (!card) return -1;
    card.scrollIntoView({ block: "start", behavior: "instant" });
    const se = document.scrollingElement || document.documentElement;
    return se ? se.scrollTop : window.scrollY;
  });
  if (tipsOff >= 0) {
    await page.evaluate((y) => {
      const se = document.scrollingElement || document.documentElement;
      const target = Math.max(0, y - 60);
      if (se) se.scrollTop = target;
      window.scrollTo(0, target);
    }, tipsOff);
    await page.waitForTimeout(400);
    await shoot(page, "wizard_15_result_iphone_tips.png");
  } else {
    console.warn("[snap] .iphone-tips-card not found in DOM");
  }
  await ctx.close();
}

// NEW (v6): Capture-quality advisory banner. Triggered when the LLM
// judged the env video unfit (cluttered / dark / ground-only). Sits
// above environment-strip and shots so the user can act on it first.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  const fakeResponse = {
    scene: {
      type: "outdoor_park",
      lighting: "shade",
      background_summary: "环境视频镜头偏向地面，主体方向不明显。",
      cautions: [],
      capture_quality: {
        score: 2,
        issues: ["cluttered_bg", "ground_only", "narrow_pan"],
        summary_zh: "环境视频主要拍到了地面与杂物，AI 没有足够证据规划拍照方案",
        should_retake: true,
      },
    },
    shots: [
      {
        id: "shot_a", title: "保守保底机位",
        angle: { azimuth_deg: 0, pitch_deg: 0, distance_m: 2 },
        composition: { primary: "centered", secondary: [], notes: "" },
        camera: {
          focal_length_mm: 35, aperture: "f/2.8", shutter: "1/200", iso: 200,
          white_balance_k: 5500, ev_compensation: 0, rationale: "证据不足时先求稳",
          device_hints: null,
        },
        poses: [], rationale: "建议先重新环视一段再细化方案",
        coach_brief: "重新环视周围环境", confidence: 0.4,
      },
    ],
    style_inspiration: null,
    generated_at: new Date().toISOString(),
    model: "mock-1",
  };
  await page.addInitScript((r) => {
    try {
      sessionStorage.setItem("apc.result", JSON.stringify(r));
      sessionStorage.setItem("apc.refInspiration", JSON.stringify({ count: 0, list: [] }));
    } catch {}
  }, fakeResponse);
  await page.goto(`${BASE}/web/result.html${bust()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(200);
  await shoot(page, "wizard_16_capture_advisory.png");
  await ctx.close();
}

// NEW (v6): Result page with the 7-axis criteria panel + ranking chip.
// Reuses the v6 fakeResponse above but scrolls to the ranking toolbar
// so the screenshot captures both UI surfaces in one frame.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
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
        composition: { primary: "rule_of_thirds", secondary: [], notes: "" },
        camera: {
          focal_length_mm: 50, aperture: "f/2.0", shutter: "1/320", iso: 200,
          white_balance_k: 5500, ev_compensation: -0.3, rationale: "x", device_hints: null,
        },
        poses: [], rationale: "x", confidence: 0.84, overall_score: 4.32,
        criteria_score: {
          composition: 5, light: 5, color: 4, depth: 4,
          subject_fit: 5, background: 4, theme: 5,
        },
        criteria_notes: {
          composition: "[comp_rule_of_thirds] 三分线",
          subject_fit: "[sub_subject_size] 主体占比合适",
          theme: "[theme_one_idea] 暖光下的并肩",
        },
        strongest_axis: "theme", weakest_axis: "depth",
      },
      {
        id: "shot_2", title: "副机位",
        angle: { azimuth_deg: 200, pitch_deg: 0, distance_m: 4.0 },
        composition: { primary: "symmetry", secondary: [], notes: "" },
        camera: {
          focal_length_mm: 85, aperture: "f/4.0", shutter: "1/500", iso: 100,
          white_balance_k: 4500, ev_compensation: -1.0, rationale: "x", device_hints: null,
        },
        poses: [], rationale: "x", confidence: 0.72, overall_score: 3.78,
        criteria_score: {
          composition: 3, light: 5, color: 3, depth: 4,
          subject_fit: 4, background: 4, theme: 4,
        },
        criteria_notes: {
          composition: "[comp_centered] 中心构图稍呆板",
          theme: "[theme_solitude_vs_belonging] 黄昏剪影意境",
        },
        strongest_axis: "light", weakest_axis: "composition",
      },
    ],
    style_inspiration: null,
    generated_at: new Date().toISOString(),
    model: "mock-1",
  };
  await page.addInitScript((r) => {
    try {
      sessionStorage.setItem("apc.result", JSON.stringify(r));
      sessionStorage.setItem("apc.refInspiration", JSON.stringify({ count: 0, list: [] }));
    } catch {}
  }, fakeResponse);
  await page.goto(`${BASE}/web/result.html${bust()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  // 1) Ranking toolbar — sits between scene card and shots
  const rankOff = await page.evaluate(() => {
    const t = document.querySelector(".ranking-toolbar");
    if (!t) return -1;
    t.scrollIntoView({ block: "start", behavior: "instant" });
    const se = document.scrollingElement || document.documentElement;
    return se ? se.scrollTop : window.scrollY;
  });
  if (rankOff >= 0) {
    await page.evaluate((y) => {
      const se = document.scrollingElement || document.documentElement;
      const target = Math.max(0, y - 60);
      if (se) se.scrollTop = target;
      window.scrollTo(0, target);
    }, rankOff);
    await page.waitForTimeout(300);
    await shoot(page, "wizard_17_ranking_chip.png");
  }
  // 2) 7-axis panel — scroll into view
  const pOff = await page.evaluate(() => {
    const p = document.querySelector(".criteria-panel");
    if (!p) return -1;
    p.scrollIntoView({ block: "start", behavior: "instant" });
    const se = document.scrollingElement || document.documentElement;
    return se ? se.scrollTop : window.scrollY;
  });
  if (pOff >= 0) {
    await page.evaluate((y) => {
      const se = document.scrollingElement || document.documentElement;
      const target = Math.max(0, y - 80);
      if (se) se.scrollTop = target;
      window.scrollTo(0, target);
    }, pOff);
    await page.waitForTimeout(400);
    await shoot(page, "wizard_18_result_7d_criteria.png");
  }

  // v7 — Phase A: shots swipe pager. Scroll back to the top of the
  // page so the sticky pager header + horizontal slide container are
  // both fully in frame.
  await page.evaluate(() => {
    const se = document.scrollingElement || document.documentElement;
    if (se) se.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await page.waitForTimeout(300);
  // Scroll the pager itself by half a slide so we capture it mid-swipe
  // and the second-shot peeking from the right is visible.
  await page.evaluate(() => {
    const pager = document.querySelector(".shots-pager");
    if (pager) pager.scrollLeft = pager.clientWidth * 0.30;
  });
  await page.waitForTimeout(300);
  await shoot(page, "wizard_19_shots_swipe.png");

  // v7 — Phase C: 3D shot preview with composition guide + HUD chips.
  // Click the "3D 场景" toggle on the active slide, give Three.js a
  // moment to spin up + load BokehPass, then snap.
  // Then bring the canvas + overlay into view.
  await page.evaluate(() => {
    const pager = document.querySelector(".shots-pager");
    if (pager) pager.scrollLeft = 0;
    const se = document.scrollingElement || document.documentElement;
    if (se) se.scrollTop = 0;
  });
  await page.waitForTimeout(200);
  const toggled = await page.evaluate(() => {
    const slide = document.querySelector(".shot-slide");
    if (!slide) return false;
    const btn = slide.querySelector('.hero-toggle-btn[data-mode="3d"]');
    if (!btn) return false;
    btn.click();
    return true;
  });
  if (toggled) {
    // BokehPass + glb loading — give a generous budget; the 3D scene
    // also lazy-loads three.js via dynamic import on first 3D toggle.
    await page.waitForTimeout(2400);
    const stageOff = await page.evaluate(() => {
      const stage = document.querySelector(".hero-3d-stage");
      if (!stage) return -1;
      stage.scrollIntoView({ block: "center", behavior: "instant" });
      const se = document.scrollingElement || document.documentElement;
      return se ? se.scrollTop : window.scrollY;
    });
    if (stageOff >= 0) {
      await page.waitForTimeout(800);
      await shoot(page, "wizard_20_shot_preview_3d.png");
    }
  }
  await ctx.close();
}

// NEW: Light-pass recapture banner. Identical scene shape but with no
// geo-derived sun and a low-confidence vision_light + recapture hint.
{
  const ctx = await freshContext();
  const page = await ctx.newPage();
  const fakeResponse = {
    scene: {
      type: "indoor_warm",
      lighting: "indoor_warm",
      background_summary: "暖色室内灯光均匀分布，没有明显主光方向，墙面反射光占主导。",
      cautions: ["光向不明，AI 建议补一段定向视频"],
      vision_light: { direction_deg: null, quality: "unknown", confidence: 0.1, notes: "环视帧亮度差异小" },
    },
    shots: [
      {
        id: "shot_a",
        title: "等光线明确再拍",
        representative_frame_index: 0,
        angle: { azimuth_deg: 0, pitch_deg: 0, distance_m: 1.6, height_hint: "eye_level" },
        composition: { primary: "centered", secondary: [], notes: "暂用居中构图" },
        camera: { focal_length_mm: 50, aperture: "f/2.0", shutter: "1/120", iso: 800,
          white_balance_k: 3200, ev_compensation: 0,
          rationale: "光线信息不足时先求稳。" },
        poses: [],
        rationale: "我建议你先按上方提示朝最亮处转一圈，给我更多光线证据，再来精打细算。",
        coach_brief: "先去补一段光向视频",
        confidence: 0.5,
      },
    ],
    style_inspiration: null,
    environment: { vision_light: { direction_deg: null, quality: "unknown", confidence: 0.1 } },
    light_recapture_hint: {
      enabled: true,
      title: "光线证据不足，建议补一段定向环视",
      detail: "对着最亮的方向慢转 10 秒，给我更多光线证据，建议会更稳。",
      suggested_azimuth_deg: 90,
    },
    generated_at: new Date().toISOString(),
    model: "mock-1",
    debug: { mode: "mock" },
  };
  await page.addInitScript((r) => {
    try {
      sessionStorage.setItem("apc.result", JSON.stringify(r));
      sessionStorage.setItem("apc.refInspiration", JSON.stringify({ count: 0, list: [] }));
    } catch {}
  }, fakeResponse);
  await page.goto(`${BASE}/web/result.html${bust()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.waitForTimeout(200);
  await shoot(page, "wizard_14_result_recapture_banner.png");
  await ctx.close();
}

// DESKTOP PREVIEW page (iPhone mockup wrapping the live PWA).
{
  const ctx = await browser.newContext({
    bypassCSP: true,
    viewport: { width: 1440, height: 960 },
    deviceScaleFactor: 2,
    colorScheme: "dark",
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/preview${bust()}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  await shoot(page, "wizard_08_desktop_preview.png");
  await ctx.close();
}

await browser.close();
console.log("\n[done] wrote screenshots to", OUT);

