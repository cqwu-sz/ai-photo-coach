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

heading.on(({ azimuthDeg, coveredAngles, coverageProgress }) => {
  renderHeadingRing(ringSvg, coveredAngles);
  needle.style.transform = `rotate(${azimuthDeg}deg)`;
  if (!isRecording) {
    hint.textContent = "对准场景，点录制开始环视一圈";
  } else if (coverageProgress >= 0.9) {
    hint.textContent = "覆盖完成 ✓ 可以停止录制";
  } else if (coverageProgress >= 0.5) {
    hint.textContent = "继续顺时针转动手机...";
  } else {
    hint.textContent = `缓慢顺时针转动 (覆盖 ${Math.round(
      coverageProgress * 100,
    )}%)`;
  }
});

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
    showError(`摄像头授权失败：${err.message}`, false);
    return;
  }

  const headingResult = await heading.start();
  if (headingResult.mode === "fake") {
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
    startVideoRecording();
  } else {
    isRecording = false;
    recordBtn.classList.remove("recording");
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

const FUNNY_TIPS = [
  "正在数你现场里有多少棵树…",
  "在纠结用 35mm 还是 50mm…",
  "在脑子里给你模特排个位置…",
  "盯着光线方向看了 10 秒…",
];

function rotateTip() {
  if (!spinnerMsg) return null;
  let i = 0;
  spinnerMsg.textContent = FUNNY_TIPS[0];
  return setInterval(() => {
    i = (i + 1) % FUNNY_TIPS.length;
    spinnerMsg.textContent = FUNNY_TIPS[i];
  }, 2400);
}

async function runAnalyze(samples) {
  hideError();
  spinner.style.display = "flex";
  resetStages();
  const tipTimer = rotateTip();
  setStage("extract", "active");
  try {
    const keyframes = selectKeyframes(samples, 10);
    if (keyframes.length < 4) {
      throw new Error("提取关键帧失败，请重试");
    }
    setStage("extract", "done");
    setStage("upload", "active");

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
    const msg = friendlyError(err.message || String(err));
    showError(`分析失败：${msg}`, true);
  } finally {
    if (tipTimer) clearInterval(tipTimer);
    spinner.style.display = "none";
  }
}

function friendlyError(raw) {
  if (/503|UNAVAILABLE|high demand/i.test(raw)) {
    return "服务当前繁忙，稍等几秒点重试。";
  }
  if (/quota|RESOURCE_EXHAUSTED/i.test(raw)) {
    return "今天免费额度用完了，明天再试。";
  }
  if (/network|fetch|Failed to fetch/i.test(raw)) {
    return "网络连接不上，检查网络后重试。";
  }
  return raw.length > 220 ? raw.slice(0, 220) + "…" : raw;
}

function showError(msg, retryable) {
  errorEl.textContent = msg;
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
  const lumas = samples.map((s) => s.meanLuma).filter((v) => v != null);
  const blurs = samples.map((s) => s.blurScore).filter((v) => v != null);
  const azs = samples.map((s) => s.azimuthDeg);
  const pitches = samples.map((s) => s.pitchDeg);

  const meanLuma = avg(lumas);
  const medianBlur = median(blurs);
  const azSpan = Math.max(...azs) - Math.min(...azs);
  const pitchAbsAvg = avg(pitches.map((p) => Math.abs(p)));

  const issues = [];
  let severity = "ok";

  if (meanLuma < 0.06) {
    issues.push("环境太暗（亮度 < 6%）");
    severity = "block";
  } else if (meanLuma < 0.12) {
    issues.push("环境偏暗（亮度 < 12%）");
    severity = bump(severity, "warn");
  }
  if (azSpan < 30) {
    issues.push(`环视范围太窄（仅转了 ${Math.round(azSpan)}°）`);
    severity = "block";
  } else if (azSpan < 90) {
    issues.push(`环视范围偏窄（${Math.round(azSpan)}°，建议 > 180°）`);
    severity = bump(severity, "warn");
  }
  if (medianBlur < 1.5) {
    issues.push("画面偏糊，可能晃动太快或失焦");
    severity = "block";
  } else if (medianBlur < 4) {
    issues.push("画面有些糊，建议慢一点");
    severity = bump(severity, "warn");
  }
  if (pitchAbsAvg > 35) {
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
