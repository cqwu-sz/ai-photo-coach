import { HeadingTracker, renderHeadingRing } from "./heading.js";
import { FrameSampler, selectKeyframes, dataUrlToBlob } from "./keyframe.js";
import { analyze } from "./api.js";
import {
  loadSettings,
  saveFrames,
  savePanoramaUrl,
  saveRefInspiration,
  saveResult,
} from "./store.js";
import { getReferenceBlobs, listReferences } from "./reference_db.js";

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

recordBtn.addEventListener("click", async () => {
  if (!isRecording) {
    isRecording = true;
    recordBtn.classList.add("recording");
    heading.reset();
    sampler = new FrameSampler({ video, heading, intervalMs: 150 });
    sampler.start();
  } else {
    isRecording = false;
    recordBtn.classList.remove("recording");
    const samples = sampler.stop();
    sampler = null;

    if (samples.length < 4) {
      showError("录制时间太短，请至少环视 3 秒", true);
      return;
    }
    lastSamples = samples;
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
  "AI 正在数你环境里有多少棵树…",
  "正在跟 Gemini 商量该用 35mm 还是 50mm…",
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

    const meta = {
      person_count: settings.personCount,
      quality_mode: settings.qualityMode,
      style_keywords: settings.styleKeywords,
      frame_meta: keyframes.map((kf, i) => ({
        index: i,
        azimuth_deg: kf.azimuthDeg,
        pitch_deg: kf.pitchDeg,
        roll_deg: kf.rollDeg,
        timestamp_ms: kf.timestampMs,
      })),
    };
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

    const response = await analyze({ meta, frames, references });

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
    return "Gemini 当前繁忙（503），稍等几秒点重试。";
  }
  if (/quota|RESOURCE_EXHAUSTED/i.test(raw)) {
    return "免费额度用完了，明天再试或升级到付费配额。";
  }
  if (/network|fetch|Failed to fetch/i.test(raw)) {
    return "网络断了或者后端没起来，确认 8000 端口可达。";
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
