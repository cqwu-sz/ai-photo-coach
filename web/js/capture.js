import { HeadingTracker, renderHeadingRing } from "./heading.js";
import { FrameSampler, selectKeyframes, dataUrlToBlob } from "./keyframe.js";
import { analyzeKeyframes as analyzeKeyframeSemantics } from "./frame_semantics.js";
import { analyze } from "./api.js";
import {
  loadSettings,
  saveFrames,
  savePanoramaUrl,
  saveRefInspiration,
  saveResult,
} from "./store.js";
import { saveCapturedFrames } from "./frames_db.js";
import { getReferenceBlobs, listReferences } from "./reference_db.js";
import { getActiveModelConfig } from "./model_settings.js";
import { ensureGeoFix, sceneModeNeedsGeo } from "./geo.js";
import { normaliseError, buildErrorView } from "./error_messages.js";

const settings = loadSettings();
if (!settings) {
  location.href = "/web/";
}

const video = document.getElementById("preview");
const ringSvg = document.getElementById("ring-svg");
const needle = document.getElementById("needle");
const hint = document.getElementById("heading-hint");
const recordBtn = document.getElementById("record-btn");
const backBtn = document.getElementById("back-btn");
const errorEl = document.getElementById("error");
const retryBar = document.getElementById("retry-bar");
const retryBtn = document.getElementById("retry-btn");
const backFromError = document.getElementById("back-from-error");
const spinner = document.getElementById("spinner");
const spinnerMsg = document.getElementById("spinner-msg");
const stagesEl = document.getElementById("stages");
const metaBadge = document.getElementById("meta-badge");

(async () => {
  let refCount = 0;
  try {
    const blobs = await getReferenceBlobs();
    refCount = blobs.length;
  } catch {}
  metaBadge.textContent =
    `${settings.personCount} 人 · ${settings.qualityMode === "fast" ? "Fast" : "High"}` +
    (refCount > 0 ? ` · ${refCount} 张参考图` : "");
})();

backBtn.addEventListener("click", () => (location.href = "/web/"));
backFromError.addEventListener("click", () => (location.href = "/web/"));

const heading = new HeadingTracker();
let sampler = null;
let isRecording = false;
let stream = null;
let lastSamples = null; // for retry
let lastVideoBlob = null; // optional 720p clip captured in high quality mode
// v9 UX polish #16 — record whether heading came from a real sensor
// ("sensor") or our mouse-fake fallback ("fake"). The backend
// reranks shots by azimuth; faked headings should be flagged so the
// LLM can de-emphasise direction-dependent recommendations and the
// UI can render a "未获取方向数据" caveat.
let headingSource = "unknown";

// v9 UX polish #2 — real-time coaching while recording. The heading
// callback now only paints the ring + needle; the textual hint is
// owned by `liveCoachLoop`, which fuses three signals every 500ms:
//   - rolling mean luma over the latest samples (太暗?)
//   - rolling blur median       (糊?)
//   - heading angular speed     (转太快? 没动?)
//   - coverage progress         (够了吗?)
// One hint at a time, picked by severity ladder so we don't flicker.
//
// When not recording, the message is the static "ready" copy.

heading.on(({ azimuthDeg, coveredAngles }) => {
  renderHeadingRing(ringSvg, coveredAngles);
  needle.style.transform = `rotate(${azimuthDeg}deg)`;
  if (!isRecording) {
    hint.textContent = "对准场景，点录制开始环视一圈";
  }
});

// Buffer of recent {tMs, az} for angular-velocity calc. We pop entries
// older than 2s on every tick.
const headingHistory = [];
let coachTimer = null;

// v9 UX polish #15 — capture-quality thresholds keyed by scene mode.
// The previous defaults (`luma < 0.06` block, `0.12` warn) were tuned
// for daytime portrait shoots and falsely blocked legitimate
// light_shadow (silhouettes, dusk) and scenery (sky-only) captures.
// Each entry overrides only the keys that differ from the default.
const QUALITY_THRESHOLDS_DEFAULT = {
  lumaBlock: 0.06,
  lumaWarn:  0.12,
  azBlock:   30,
  azWarn:    90,
  blurBlock: 1.5,
  blurWarn:  4,
  pitchWarn: 35,
  // Live coach thresholds (these are softer because they nudge during
  // recording rather than block the upload).
  liveLumaWarn: 0.08,
  liveSpeedWarn: 90,
};

const QUALITY_THRESHOLDS_BY_MODE = {
  light_shadow: {
    // Silhouettes / golden hour / blue hour intentionally underexpose
    // the foreground; the LLM still has plenty to work with.
    lumaBlock: 0.02,
    lumaWarn:  0.05,
    liveLumaWarn: 0.04,
    // Slower hand pans expected (people savouring the light).
    liveSpeedWarn: 75,
  },
  scenery: {
    // No-people landscape mode — tilt up to skies or down to ground
    // is normal. Loosen pitch tolerance.
    pitchWarn: 50,
    // The user may scan a 360° vista; require a wider span before
    // we call it "narrow_pan".
    azWarn: 120,
  },
  closeup: {
    // Tight face / detail crops — pan can be very small (60°) and
    // that's fine; the LLM is mostly looking at one angle anyway.
    azBlock: 20,
    azWarn:  60,
  },
};

function qualityThresholds(sceneMode) {
  const override = QUALITY_THRESHOLDS_BY_MODE[sceneMode] || {};
  return { ...QUALITY_THRESHOLDS_DEFAULT, ...override };
}

function pushHeading(tMs, az) {
  headingHistory.push({ tMs, az });
  while (headingHistory.length && tMs - headingHistory[0].tMs > 2200) {
    headingHistory.shift();
  }
}

function angularSpeedDegPerSec() {
  // Use last ~1s window so single jitter samples don't dominate.
  const now = performance.now();
  const recent = headingHistory.filter((h) => now - h.tMs <= 1000);
  if (recent.length < 2) return 0;
  let total = 0;
  for (let i = 1; i < recent.length; i++) {
    let d = recent[i].az - recent[i - 1].az;
    // Unwrap across 360° boundary (e.g. 358° → 2° is +4°, not -356°)
    if (d > 180) d -= 360;
    if (d < -180) d += 360;
    total += Math.abs(d);
  }
  const span = (recent[recent.length - 1].tMs - recent[0].tMs) / 1000;
  return span > 0 ? total / span : 0;
}

function headingDeltaLast2s() {
  if (headingHistory.length < 2) return 0;
  const first = headingHistory[0].az;
  const last = headingHistory[headingHistory.length - 1].az;
  let d = last - first;
  if (d > 180) d -= 360;
  if (d < -180) d += 360;
  return d;
}

function evaluateLiveHint() {
  if (!sampler) return;
  const samples = sampler.samples;
  // Need at least ~5 samples (0.5s of recording) to say anything sensible.
  if (samples.length < 5) {
    hint.textContent = "开始环视，对准最想拍的方向…";
    return;
  }

  // Track heading for angular velocity.
  const snap = heading.snapshot();
  pushHeading(performance.now(), snap.azimuthDeg);

  // Rolling means over the most recent ~1.5s of samples.
  const recent = samples.slice(-Math.min(samples.length, 12));
  const lumaArr = recent.map((s) => s.meanLuma).filter((v) => v != null);
  const blurArr = recent.map((s) => s.blurScore).filter((v) => v != null);
  const meanLuma = lumaArr.length ? lumaArr.reduce((a, b) => a + b, 0) / lumaArr.length : null;
  const medianBlur = blurArr.length ? median(blurArr) : null;

  const speed = angularSpeedDegPerSec();
  const last2sDelta = Math.abs(headingDeltaLast2s());
  const coverage = snap.coveredAngles.size / 12;

  // v9 UX polish #15 — scene-aware thresholds. light_shadow has a much
  // looser luma floor (silhouettes are dark on purpose); scenery
  // tolerates wider pitch (you're looking up at skies).
  const T = qualityThresholds(settings.sceneMode);

  // Severity ladder — pick the single most actionable hint. Lower
  // priority hints still show via the ring colour later if needed.
  if (meanLuma != null && meanLuma < T.liveLumaWarn) {
    hint.textContent = "环境太暗 — 转向更亮的方向再试";
    return;
  }
  if (speed > T.liveSpeedWarn) {
    hint.textContent = "转得有点快 — 慢一点，让 AI 看清楚";
    return;
  }
  if (medianBlur != null && medianBlur < 2.2 && speed > 40) {
    hint.textContent = "画面有些糊 — 放慢手势 / 别让手抖";
    return;
  }
  if (samples.length >= 12 && last2sDelta < 4) {
    hint.textContent = "继续顺时针转一点，把没覆盖的角度补上";
    return;
  }
  if (coverage >= 0.9) {
    hint.textContent = "覆盖完成 ✓ 可以停止录制";
    return;
  }
  if (coverage >= 0.5) {
    hint.textContent = `继续顺时针转，已覆盖 ${Math.round(coverage * 100)}%`;
    return;
  }
  hint.textContent = `缓慢顺时针转动 · 覆盖 ${Math.round(coverage * 100)}%`;
}

function startLiveCoach() {
  stopLiveCoach();
  headingHistory.length = 0;
  coachTimer = setInterval(evaluateLiveHint, 500);
  evaluateLiveHint();
}

function stopLiveCoach() {
  if (coachTimer) {
    clearInterval(coachTimer);
    coachTimer = null;
  }
}

renderHeadingRing(ringSvg, new Set());

(async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();
  } catch (err) {
    // Camera authorisation is a user-permission concern, not an API
    // error — keep the explicit copy so the user knows what to do next.
    const name = err && err.name ? err.name : "";
    let copy;
    if (/NotAllowedError|PermissionDenied/.test(name)) {
      copy = "摄像头被拒绝授权。去手机系统「设置 → 隐私 → 相机」里打开 Safari 的权限再回来。";
    } else if (/NotFoundError|DevicesNotFound/.test(name)) {
      copy = "没找到可用的摄像头。换一台设备或检查浏览器是否支持。";
    } else if (/NotReadableError/.test(name)) {
      copy = "摄像头被另一个 App 占用了，关闭后重试。";
    } else {
      copy = `摄像头无法打开：${err && err.message ? err.message : err}`;
    }
    showError(copy, false);
    return;
  }

  const headingResult = await heading.start();
  headingSource = headingResult && headingResult.mode === "sensor" ? "sensor" : "fake";
  if (headingSource === "fake") {
    hint.textContent = "无陀螺仪 - 移动鼠标模拟方向";
  }
})();

// Holds the MediaRecorder + chunks for a high-quality capture. Null in
// fast mode (we save the upload bandwidth + battery). Reset every cycle.
let videoRecorder = null;
let videoChunks = [];

function startVideoRecording() {
  videoChunks = [];
  videoRecorder = null;
  if ((settings.qualityMode || "fast") !== "high") return;
  const stream = video.srcObject;
  if (!stream || typeof MediaRecorder === "undefined") return;
  // Pick the best supported MIME — Safari needs mp4, everywhere else
  // accepts WebM/VP9. Backend treats both as a Gemini "video" Part.
  const candidates = [
    "video/mp4;codecs=h264",
    "video/webm;codecs=vp9",
    "video/webm;codecs=vp8",
    "video/webm",
  ];
  const mime = candidates.find((m) => MediaRecorder.isTypeSupported?.(m));
  try {
    videoRecorder = new MediaRecorder(stream, mime ? {
      mimeType: mime,
      videoBitsPerSecond: 2_500_000,   // ~2.5 Mbps → ~2.5 MB for 8s
    } : undefined);
    videoRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size) videoChunks.push(e.data);
    };
    videoRecorder.start(1000);
  } catch (e) {
    console.warn("[capture] MediaRecorder failed (non-fatal, will fallback to fast):", e);
    videoRecorder = null;
  }
}

async function stopVideoRecording() {
  if (!videoRecorder) return null;
  return await new Promise((resolve) => {
    videoRecorder.onstop = () => {
      const type = videoRecorder.mimeType || "video/webm";
      const blob = new Blob(videoChunks, { type });
      videoRecorder = null;
      videoChunks = [];
      // Keep ≤ 12 MB to match the backend cap.
      resolve(blob.size <= 12 * 1024 * 1024 ? blob : null);
    };
    try { videoRecorder.stop(); }
    catch { resolve(null); }
  });
}

recordBtn.addEventListener("click", async () => {
  if (!isRecording) {
    isRecording = true;
    recordBtn.classList.add("recording");
    heading.reset();
    // FrameSampler picks resolution + interval from QUALITY_PROFILES based
    // on the wizard's selection. high mode uses 1024px @ 80ms (denser +
    // sharper) so the LLM has materially more pixels to work with.
    sampler = new FrameSampler({
      video,
      heading,
      qualityMode: settings.qualityMode || "fast",
    });
    sampler.start();
    startLiveCoach();
    startVideoRecording();
  } else {
    isRecording = false;
    recordBtn.classList.remove("recording");
    stopLiveCoach();
    const samples = sampler.stop();
    sampler = null;
    const videoBlob = await stopVideoRecording();
    lastVideoBlob = videoBlob;

    if (samples.length < 4) {
      showError("录制时间太短，请至少环视 3 秒", true);
      return;
    }
    lastSamples = samples;

    // Client-side capture quality precheck — runs *before* we burn an
    // /analyze call. Cheaper, faster, and tells the user *why* their
    // env video might not work without waiting on Gemini.
    const verdict = assessCaptureQuality(samples);
    if (verdict.severity === "block") {
      const proceed = await showCaptureSheet(verdict, /*allowProceed=*/ false);
      if (!proceed) return; // user chose retake
    } else if (verdict.severity === "warn") {
      const proceed = await showCaptureSheet(verdict, /*allowProceed=*/ true);
      if (!proceed) return;
    }

    await runAnalyze(samples);
  }
});

retryBtn.addEventListener("click", async () => {
  if (!lastSamples || lastSamples.length < 4) {
    hideError();
    return;
  }
  await runAnalyze(lastSamples);
});

function setStage(name, status) {
  if (!stagesEl) return;
  const el = stagesEl.querySelector(`.stage[data-stage="${name}"]`);
  if (!el) return;
  el.classList.remove("active", "done");
  if (status) el.classList.add(status);
}

function resetStages() {
  if (!stagesEl) return;
  stagesEl.querySelectorAll(".stage").forEach((el) => {
    el.classList.remove("active", "done");
  });
}

// v9 UX polish #5 — tips降噪。原来 4 条 funny tips 每 2.4s 循环一次，
// 高质量模式 60s 会循环 25 圈，搞笑变烦人。
// 改成"按真实阶段切文案 + 最后一段才出一句轻松话术"，配合 stage 链：
//   - extract:    "正在挑选最好的几帧…"
//   - upload:     "正在把现场上传给 AI…"
//   - ai:         "AI 在为你出方案…"   (停留最久，加一句安抚)
//   - render:     "整理完成，马上呈现"
// 切换由 setStage(...) 驱动，不再用 setInterval 轮播。
const STAGE_COPY = {
  extract: { msg: "正在挑选最好的几帧…", sub: null },
  upload:  { msg: "正在把现场上传给 AI…", sub: null },
  ai:      { msg: "AI 在为你出方案…", sub: "高质量模式 ≈ 60 秒，留意构图与光线" },
  render:  { msg: "整理完成，马上呈现", sub: null },
};

function setSpinnerCopy(stage) {
  if (!spinnerMsg) return;
  const entry = STAGE_COPY[stage];
  if (!entry) return;
  spinnerMsg.textContent = entry.msg;
  // We render sub only when present, in a smaller line under the main
  // message. Reuses the stage's span so we don't introduce extra DOM.
  let subEl = spinnerMsg.querySelector(".spinner-sub");
  if (!subEl) {
    subEl = document.createElement("span");
    subEl.className = "spinner-sub";
    spinnerMsg.appendChild(subEl);
  }
  subEl.textContent = entry.sub || "";
  subEl.style.display = entry.sub ? "block" : "none";
}

async function runAnalyze(samples) {
  hideError();
  spinner.style.display = "flex";
  resetStages();
  setStage("extract", "active");
  setSpinnerCopy("extract");
  try {
    const keyframes = selectKeyframes(samples, 10);
    if (keyframes.length < 4) {
      throw new Error("提取关键帧失败，请重试");
    }
    setStage("extract", "done");
    setStage("upload", "active");
    setSpinnerCopy("upload");

    // Per-keyframe semantic signals (person box / saliency quadrant /
    // horizon tilt). Runs MediaPipe + canvas Sobel; total < ~1s for 10
    // keyframes. Failure-tolerant: returns nulls per signal so the
    // backend treats absence as "no information".
    const semantics = await analyzeKeyframeSemantics(keyframes).catch(() => []);

    const sceneMode = settings.sceneMode || "portrait";
    const meta = {
      person_count: settings.personCount,
      scene_mode: sceneMode,
      quality_mode: settings.qualityMode,
      style_keywords: settings.styleKeywords,
      // v9 UX polish #16 — heading_source lets the backend know whether
      // the azimuth values it sees are real sensor readings or our
      // mouse-fake fallback (e.g. desktop demo). When "fake", azimuth-
      // dependent reranking and the "AI 按光向重排方案" line should be
      // suppressed or caveated.
      heading_source: headingSource,
      frame_meta: keyframes.map((kf, i) => ({
        index: i,
        azimuth_deg: kf.azimuthDeg,
        pitch_deg: kf.pitchDeg,
        roll_deg: kf.rollDeg,
        timestamp_ms: kf.timestampMs,
        // Visual signals computed client-side during sampling (keyframe.js).
        // Backend uses them as evidence in capture_quality (rule 13) and as
        // a tiebreak for the keyframe scorer downstream.
        mean_luma: kf.meanLuma,
        blur_score: kf.blurScore,
        // v8 semantic signals (Phase 2 — A 路线). All three are
        // independently nullable so we don't fight schema validation
        // when MediaPipe load fails or saliency canvas is tainted.
        person_box: semantics[i]?.personBox ?? null,
        saliency_quadrant: semantics[i]?.saliencyQuadrant ?? null,
        horizon_tilt_deg: semantics[i]?.horizonTiltDeg ?? null,
        face_hit: semantics[i]?.personBox != null ? true : null,
      })),
    };

    // Geo fix powers ENVIRONMENT FACTS (sun + weather + softness) which
    // in turn drive the style feasibility check. Requested for ALL scene
    // modes now; cached 6h so we don't re-prompt. Browser permission is
    // still opt-in — if user denies, analyze keeps working without geo.
    if (sceneModeNeedsGeo(sceneMode)) {
      try {
        const fix = await ensureGeoFix();
        if (fix) meta.geo = fix;
      } catch (e) {
        console.warn("[capture] geo fix failed (non-fatal)", e);
      }
    }
    const frames = keyframes.map((kf) => dataUrlToBlob(kf.dataUrl));

    let references = [];
    let refRecords = [];
    try {
      references = await getReferenceBlobs();
      refRecords = await listReferences();
    } catch (e) {
      // Reference DB is best-effort; never block analysis on it.
      console.warn("reference db read failed", e);
    }

    setStage("upload", "done");
    setStage("ai", "active");
    setSpinnerCopy("ai");

    const modelCfg = getActiveModelConfig();
    const response = await analyze({
      meta,
      frames,
      references,
      modelId: modelCfg.model_id,
      modelApiKey: modelCfg.api_key,
      modelBaseUrl: modelCfg.base_url,
      // Only present in high quality mode + when MediaRecorder succeeded.
      // The api wrapper attaches it under the optional `video` multipart
      // field; backend silently ignores when missing.
      video: lastVideoBlob || null,
    });

    setStage("ai", "done");
    setStage("render", "active");
    setSpinnerCopy("render");
    await new Promise((r) => setTimeout(r, 200));
    setStage("render", "done");

    saveFrames(
      keyframes.map((kf, i) => ({
        index: i,
        azimuthDeg: kf.azimuthDeg,
        src: kf.dataUrl,
      })),
    );

    // Persist the raw blobs to IndexedDB so a returning user can change
    // the scene mode (or other tone settings) and re-run /analyze without
    // re-recording the environment. Best-effort only.
    saveCapturedFrames({
      frames,
      meta: meta.frame_meta,
      sceneMode: meta.scene_mode,
      panoramaUrl: null,
    }).catch((e) => console.warn("frames cache save failed (non-fatal)", e));

    // Build a panorama on the backend so the result page's 3D scene has
    // a real background. We do this best-effort, in the background.
    buildPanoramaInBackground(meta, frames).catch((e) =>
      console.warn("panorama build failed (non-fatal)", e),
    );
    saveRefInspiration({
      count: refRecords.length,
      thumbs: refRecords.slice(0, 4).map((r) => r.thumbDataUrl),
      names: refRecords.slice(0, 4).map((r) => r.name),
    });
    saveResult(response);
    location.href = "/web/result.html";
  } catch (err) {
    const norm = normaliseError(err);
    showError(norm, true);
  } finally {
    spinner.style.display = "none";
  }
}

// v9 UX polish #4 — showError now accepts either a plain string (legacy
// camera-authorization message) or a normalised error object. Strings
// still render as-is so existing call sites don't break.
function showError(msgOrNorm, retryable) {
  errorEl.innerHTML = "";
  if (typeof msgOrNorm === "string") {
    errorEl.textContent = msgOrNorm;
  } else {
    const view = buildErrorView(msgOrNorm);
    const heading = document.createElement("strong");
    heading.style.display = "block";
    heading.style.marginBottom = "4px";
    heading.textContent = "分析失败";
    errorEl.appendChild(heading);
    errorEl.appendChild(view);
  }
  errorEl.style.display = "block";
  retryBar.style.display = retryable && lastSamples ? "flex" : "none";
}

function hideError() {
  errorEl.style.display = "none";
  retryBar.style.display = "none";
}

window.addEventListener("beforeunload", () => {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  heading.stop();
  stopLiveCoach();
});

// ───────────────────────────────────────────────────────────────────────
// Client-side capture quality precheck.
//
// Cheap, deterministic, runs in <2 ms over the sampled frames. Surfaces
// the obvious issues *before* we hit the LLM:
//
//   - "block": mean_luma < 0.06 (essentially dark)
//             OR azimuth span < 30° (user barely panned)
//             OR sharpness median < 1.5 (camera was waving)
//   - "warn":  mean_luma < 0.12 OR sharpness < 4 OR pitch ↑↓ > ±35°
//             (probably ground / sky-only).
//
// Returning "warn" still lets the user proceed; "block" forces retake.
// ───────────────────────────────────────────────────────────────────────
function assessCaptureQuality(samples) {
  if (!samples || samples.length === 0) {
    return { severity: "block", issues: ["录制为空"] };
  }
  // v9 UX polish #15 — scene-aware thresholds prevent light_shadow /
  // scenery shoots from being false-blocked by daytime portrait
  // defaults. light_shadow's silhouettes are *meant* to be dark.
  const T = qualityThresholds(settings.sceneMode);

  const lumas = samples.map((s) => s.meanLuma).filter((v) => v != null);
  const blurs = samples.map((s) => s.blurScore).filter((v) => v != null);
  const azs = samples.map((s) => s.azimuthDeg);
  const pitches = samples.map((s) => s.pitchDeg);

  // v9 UX polish #17 — when Safari handed us null luma/blur for every
  // sample we have no signal at all, so we shouldn't loudly say "too
  // dark / blurry". Treat empty arrays as "no data".
  const meanLuma   = lumas.length ? avg(lumas) : null;
  const medianBlur = blurs.length ? median(blurs) : null;
  const azSpan = Math.max(...azs) - Math.min(...azs);
  const pitchAbsAvg = avg(pitches.map((p) => Math.abs(p)));

  const issues = [];
  let severity = "ok";

  if (meanLuma != null) {
    if (meanLuma < T.lumaBlock) {
      issues.push(`环境太暗（亮度 ${Math.round(meanLuma * 100)}%）`);
      severity = "block";
    } else if (meanLuma < T.lumaWarn) {
      issues.push(`环境偏暗（亮度 ${Math.round(meanLuma * 100)}%）`);
      severity = bump(severity, "warn");
    }
  }
  if (azSpan < T.azBlock) {
    issues.push(`环视范围太窄（仅转了 ${Math.round(azSpan)}°）`);
    severity = "block";
  } else if (azSpan < T.azWarn) {
    issues.push(`环视范围偏窄（${Math.round(azSpan)}°，建议 > 180°）`);
    severity = bump(severity, "warn");
  }
  if (medianBlur != null) {
    if (medianBlur < T.blurBlock) {
      issues.push("画面偏糊，可能晃动太快或失焦");
      severity = "block";
    } else if (medianBlur < T.blurWarn) {
      issues.push("画面有些糊，建议慢一点");
      severity = bump(severity, "warn");
    }
  }
  if (pitchAbsAvg > T.pitchWarn) {
    issues.push(`镜头倾角偏大（平均 ${Math.round(pitchAbsAvg)}°），可能怼着地面或天空`);
    severity = bump(severity, "warn");
  }

  return { severity, issues, meanLuma, medianBlur, azSpan, pitchAbsAvg };
}

function avg(arr) { return arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0; }
function median(arr) {
  if (!arr.length) return 0;
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}
function bump(current, next) {
  const order = { ok: 0, warn: 1, block: 2 };
  return order[next] > order[current] ? next : current;
}

async function showCaptureSheet(verdict, allowProceed) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "capture-sheet-backdrop";
    const sheet = document.createElement("div");
    sheet.className = `capture-sheet capture-sheet--${verdict.severity}`;
    backdrop.appendChild(sheet);

    const title = document.createElement("h3");
    title.className = "capture-sheet-title";
    title.textContent = verdict.severity === "block"
      ? "这段环视看起来不太够 AI 出片"
      : "环视有几个小问题，要继续吗？";
    sheet.appendChild(title);

    const list = document.createElement("ul");
    list.className = "capture-sheet-issues";
    verdict.issues.forEach((it) => {
      const li = document.createElement("li");
      li.textContent = "· " + it;
      list.appendChild(li);
    });
    sheet.appendChild(list);

    const btnRow = document.createElement("div");
    btnRow.className = "capture-sheet-buttons";
    const retake = document.createElement("button");
    retake.type = "button";
    retake.className = "capture-sheet-btn capture-sheet-btn-retake";
    retake.textContent = "重新环视";
    retake.addEventListener("click", () => {
      document.body.removeChild(backdrop);
      resolve(false);
    });
    btnRow.appendChild(retake);

    if (allowProceed) {
      const proceed = document.createElement("button");
      proceed.type = "button";
      proceed.className = "capture-sheet-btn capture-sheet-btn-proceed";
      proceed.textContent = "知道了，继续分析";
      proceed.addEventListener("click", () => {
        document.body.removeChild(backdrop);
        resolve(true);
      });
      btnRow.appendChild(proceed);
    }
    sheet.appendChild(btnRow);
    document.body.appendChild(backdrop);
  });
}

async function buildPanoramaInBackground(meta, frames) {
  const fd = new FormData();
  fd.append("meta", JSON.stringify(meta));
  for (let i = 0; i < frames.length; i++) {
    fd.append("frames", frames[i], `frame_${i.toString().padStart(2, "0")}.jpg`);
  }
  const r = await fetch("/panorama", { method: "POST", body: fd });
  if (!r.ok) throw new Error(`panorama HTTP ${r.status}`);
  const blob = await r.blob();
  // Convert blob → data URL for cross-page persistence in sessionStorage
  // (it's small: ~50–200 KB equirectangular JPEG).
  const reader = new FileReader();
  await new Promise((resolve, reject) => {
    reader.onload = () => {
      savePanoramaUrl(reader.result);
      resolve();
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
