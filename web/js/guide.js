/**
 * AR-guide page (Phase B: precise alignment + green-light release).
 *
 * Orchestrates four signals and only shows the "press shutter" banner
 * when all four are aligned for ≥700ms:
 *
 *   1. Compass heading        → DeviceOrientation.alpha (HeadingTracker)
 *   2. Phone tilt (pitch)     → DeviceOrientation.beta
 *   3. Subject distance       → MediaPipe Pose body-height ratio → meters
 *   4. Person present in frame→ MediaPipe presence flag
 *
 * If any sensor or detector is unavailable (desktop without compass /
 * corporate firewall blocking MediaPipe CDN / no camera) we mark that
 * dimension disabled, the rest still drive the green-light state.
 */
import { HeadingTracker } from "./heading.js";
import { loadAvatarPicks, loadCurrentShot } from "./store.js";
import { renderPoseSVG } from "./pose_viz.js";
import { sampleFrameURL } from "./api.js";
import { resolveAvatarPicks, getAvatarStyle } from "./avatar_styles.js";
import { createAvatarPosePreview } from "./avatar_preview.js";
import {
  AlignmentMachine,
  headingHint,
  pitchHint,
  distanceHint,
} from "./alignment.js";
import { PoseDetector } from "./pose_detector.js";

const current = loadCurrentShot();
if (!current || !current.shot) {
  alert("没有选中的机位，先回结果页选一个");
  location.href = "/web/result.html";
}

const shot = current.shot;
const idx = current.idx ?? 0;

const stage = document.getElementById("guide-stage");
const video = document.getElementById("preview");
const overlay = document.getElementById("ar-overlay");
const okBanner = document.getElementById("ok-banner");
const hudMsg = document.getElementById("hud-msg");
const shotBadge = document.getElementById("shot-badge");
const guideTitle = document.getElementById("guide-title");
const guideMeta = document.getElementById("guide-meta");
const guideRationale = document.getElementById("guide-rationale");
const guidePose = document.getElementById("guide-pose");
const guidePose3d = document.getElementById("guide-pose-3d");
const backBtn = document.getElementById("back-btn");

const cardHeading = document.getElementById("hud-heading");
const cardPitch = document.getElementById("hud-pitch");
const cardDistance = document.getElementById("hud-distance");
const cardPerson = document.getElementById("hud-person");

backBtn.addEventListener("click", () => (location.href = "/web/result.html"));

const targetAz = shot.angle.azimuth_deg;
const targetPitch = shot.angle.pitch_deg ?? 0;
const targetDist = shot.angle.distance_m ?? 2.0;

shotBadge.textContent = `机位 #${idx + 1}`;
guideTitle.textContent = `机位 #${idx + 1}${shot.title ? " · " + shot.title : ""}`;

setCardTarget(cardHeading, "目标 ", `${Math.round(targetAz)}°`);
setCardTarget(cardPitch, "目标 ", `${Math.round(targetPitch)}°`);
setCardTarget(cardDistance, "目标 ", `${targetDist.toFixed(1)} m`);
setCardTarget(cardPerson, "需要 ", "1+ 人");

function setCardTarget(card, prefix, val) {
  if (!card) return;
  const t = card.querySelector(".hud-status-target");
  if (t) t.textContent = `${prefix}${val}`;
}

function setCardValue(card, val, status) {
  if (!card) return;
  const v = card.querySelector(".hud-status-value");
  if (v) v.textContent = val;
  card.classList.remove("is-ok", "is-warn", "is-far", "is-off");
  card.classList.add(`is-${status}`);
}

// Coach brief
const coachBriefEl = document.getElementById("coach-brief");
if (coachBriefEl && shot.coach_brief) {
  coachBriefEl.textContent = `"${shot.coach_brief}"`;
  coachBriefEl.style.display = "block";
}

// Meta line
[
  ["焦段", `${Math.round(shot.camera.focal_length_mm)}mm`],
  ["光圈", shot.camera.aperture],
  ["快门", shot.camera.shutter],
  ["ISO", String(shot.camera.iso)],
  ["距离", `${shot.angle.distance_m.toFixed(1)} m`],
].forEach(([k, v]) => {
  const m = document.createElement("div");
  m.style.cssText = "display: flex; gap: 4px; align-items: center;";
  m.innerHTML = `<span class="label">${k}</span><strong>${v}</strong>`;
  guideMeta.appendChild(m);
});
guideRationale.textContent = shot.rationale || "";

// Step list detail strings
const stepTurn = document.getElementById("step-turn-detail");
const stepWalk = document.getElementById("step-walk-detail");
const stepPose = document.getElementById("step-pose-detail");
if (stepTurn) {
  stepTurn.textContent = `把手机转到 ${Math.round(targetAz)}° (${describeBearing(targetAz)})`;
}
if (stepWalk) {
  stepWalk.textContent = `距主体 ${targetDist.toFixed(1)} 米，${heightHintCN(shot.angle.height_hint)}`;
}
if (stepPose) {
  const p0 = shot.poses && shot.poses[0];
  stepPose.textContent = p0
    ? `${layoutCN(p0.layout)} · ${p0.persons?.length || 1} 人`
    : "见下方姿势示意";
}
function describeBearing(deg) {
  const d = ((deg % 360) + 360) % 360;
  if (d < 22.5 || d >= 337.5) return "正北方向";
  if (d < 67.5) return "东北方向";
  if (d < 112.5) return "正东方向";
  if (d < 157.5) return "东南方向";
  if (d < 202.5) return "正南方向";
  if (d < 247.5) return "西南方向";
  if (d < 292.5) return "正西方向";
  return "西北方向";
}
function heightHintCN(h) {
  return ({ low: "低角度仰拍", eye_level: "齐眼平拍", high: "高位俯拍", overhead: "正俯拍" }[h] || "齐眼平拍");
}
function layoutCN(l) {
  return ({ single: "单人站位", side_by_side: "并排站位", high_low_offset: "高低错位",
    triangle: "三角形", line: "线性错落", cluster: "簇拥", diagonal: "对角线",
    v_formation: "V 字形", circle: "环形", custom: "自由站位" }[l] || l);
}

if (shot.poses && shot.poses[0]) {
  guidePose.appendChild(renderPoseSVG(shot.poses[0], { height: 180 }));
  // 3D preview using user-selected avatars
  const personCount = (shot.poses[0].persons || []).length || 1;
  const picks = resolveAvatarPicks(loadAvatarPicks(), personCount);
  let preview3d = null;
  if (guidePose3d) {
    preview3d = createAvatarPosePreview(guidePose3d, {
      pose: shot.poses[0],
      picks,
    });
  }

  // 2D / 3D toggle (defaults to 3D)
  const modeBtns = [...document.querySelectorAll(".pose-mode-btn")];
  modeBtns.forEach((btn) =>
    btn.addEventListener("click", () => {
      modeBtns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const mode = btn.dataset.mode;
      if (mode === "3d") {
        guidePose3d.hidden = false;
        guidePose.hidden = true;
      } else {
        guidePose3d.hidden = true;
        guidePose.hidden = false;
      }
    }),
  );
}

// Apply the user's first-slot avatar's outfit color to the AR silhouette
// so the on-screen virtual person matches "their" character.
const _picksForOverlay = resolveAvatarPicks(loadAvatarPicks(), 1);
const _overlayStyle = getAvatarStyle(_picksForOverlay[0]);
if (_overlayStyle) {
  document.documentElement.style.setProperty(
    "--avatar-overlay-color",
    _overlayStyle.topColor,
  );
}

// ─────────────────────────────────────────────────────────
// Step state machine (legacy, still drives the 三步走 list)
// ─────────────────────────────────────────────────────────
const steps = [...document.querySelectorAll(".guide-step")];
function setStepStatus(stepName, state) {
  const node = steps.find((s) => s.dataset.step === stepName);
  if (!node) return;
  node.classList.remove("active", "done");
  if (state) node.classList.add(state);
}
function focusStep(stepName) {
  steps.forEach((s) => {
    if (s.dataset.step === stepName) {
      if (!s.classList.contains("done")) s.classList.add("active");
    } else if (!s.classList.contains("done")) {
      s.classList.remove("active");
    }
  });
}

// ─────────────────────────────────────────────────────────
// Camera (with fallback to a sample frame for desktops without one)
// ─────────────────────────────────────────────────────────
let stream = null;
const demoBg = document.getElementById("demo-bg");

function pickSampleAzimuthIndex(target) {
  const idx = Math.round(((target % 360) + 360) % 360 / 45) % 8;
  return idx;
}
function activateDemoBackground(reason) {
  if (video) video.style.display = "none";
  if (demoBg) {
    demoBg.hidden = false;
    demoBg.src = sampleFrameURL(pickSampleAzimuthIndex(targetAz));
  }
  hudMsg.textContent = `无摄像头模式 · ${reason}`;
  align.disable("distance");
  align.disable("person");
}

// ─────────────────────────────────────────────────────────
// Alignment state machine
// ─────────────────────────────────────────────────────────
const align = new AlignmentMachine({
  target: { headingDeg: targetAz, pitchDeg: targetPitch, distanceM: targetDist },
  holdMs: 700,
});

align.on((snap) => {
  // Heading card
  if (snap.heading.value != null) {
    setCardValue(
      cardHeading,
      `${Math.round(snap.heading.value)}° (${snap.heading.delta >= 0 ? "+" : ""}${Math.round(snap.heading.delta)})`,
      snap.heading.status,
    );
  } else {
    setCardValue(cardHeading, "--", "off");
  }

  // Pitch card
  if (snap.pitch.value != null) {
    setCardValue(
      cardPitch,
      `${Math.round(snap.pitch.value)}° (${snap.pitch.delta >= 0 ? "+" : ""}${Math.round(snap.pitch.delta)})`,
      snap.pitch.status,
    );
  } else {
    setCardValue(cardPitch, "--", "off");
  }

  // Distance card
  if (snap.disabled.has("distance")) {
    setCardValue(cardDistance, "未启用", "off");
  } else if (snap.distance.value != null) {
    setCardValue(
      cardDistance,
      `${snap.distance.value.toFixed(1)} m`,
      snap.distance.status,
    );
  } else {
    setCardValue(cardDistance, "--", "off");
  }

  // Person card
  if (snap.disabled.has("person")) {
    setCardValue(cardPerson, "未启用", "off");
  } else if (snap.person.value === true) {
    setCardValue(cardPerson, "已识别", "ok");
  } else if (snap.person.value === false) {
    setCardValue(cardPerson, "未识别", "warn");
  } else {
    setCardValue(cardPerson, "--", "off");
  }

  // Stage frame + center banner
  if (snap.aggregateOk) {
    stage.classList.add("is-aligned");
    okBanner.hidden = false;
  } else {
    stage.classList.remove("is-aligned");
    okBanner.hidden = true;
  }

  // Choose the worst-status hint message
  const msgs = [];
  if (snap.heading.value != null && snap.heading.status !== "ok")
    msgs.push({ s: snap.heading.status, t: headingHint(snap.heading.delta) });
  if (snap.pitch.value != null && snap.pitch.status !== "ok")
    msgs.push({ s: snap.pitch.status, t: pitchHint(snap.pitch.delta) });
  if (
    !snap.disabled.has("distance") &&
    snap.distance.value != null &&
    snap.distance.status !== "ok"
  )
    msgs.push({ s: snap.distance.status, t: distanceHint(snap.distance.delta) });
  if (
    !snap.disabled.has("person") &&
    snap.person.value === false
  )
    msgs.push({ s: "warn", t: "把人放到画面里" });

  hudMsg.classList.remove("is-far", "is-warn", "is-ok");
  if (msgs.length === 0) {
    hudMsg.textContent = snap.aggregateOk ? "✓ 全部对位 — 按下快门" : "对位中…";
    hudMsg.classList.add(snap.aggregateOk ? "is-ok" : "is-warn");
  } else {
    msgs.sort((a, b) => statusRank(b.s) - statusRank(a.s));
    hudMsg.textContent = msgs[0].t;
    hudMsg.classList.add(`is-${msgs[0].s === "far" ? "far" : "warn"}`);
  }

  // Drive the 三步走 list off the alignment state too
  if (snap.heading.status === "ok") {
    setStepStatus("turn", "done");
    if (
      (snap.distance.status === "ok" || snap.disabled.has("distance")) &&
      snap.pitch.status === "ok"
    ) {
      setStepStatus("walk", "done");
      focusStep("pose");
    } else {
      focusStep("walk");
    }
  } else {
    focusStep("turn");
  }
});

function statusRank(s) {
  return { far: 3, warn: 2, ok: 1, off: 0 }[s] || 0;
}

align.onGreen(() => {
  // Vibrate (Android Chrome / some Android browsers; iOS Safari ignores).
  try { navigator.vibrate?.([60, 40, 60]); } catch {}
  beepOk();
});

// One-shot pleasant chime on green.
let _audioCtx = null;
function beepOk() {
  try {
    _audioCtx = _audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const ctx = _audioCtx;
    const t0 = ctx.currentTime;
    [880, 1175, 1568].forEach((freq, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = freq;
      o.type = "sine";
      o.connect(g);
      g.connect(ctx.destination);
      g.gain.setValueAtTime(0, t0 + i * 0.06);
      g.gain.linearRampToValueAtTime(0.18, t0 + i * 0.06 + 0.02);
      g.gain.linearRampToValueAtTime(0, t0 + i * 0.06 + 0.22);
      o.start(t0 + i * 0.06);
      o.stop(t0 + i * 0.06 + 0.25);
    });
  } catch {}
}

// ─────────────────────────────────────────────────────────
// Sensor wiring
// ─────────────────────────────────────────────────────────
const heading = new HeadingTracker();
heading.start();
heading.on(({ azimuthDeg, pitchDeg }) => {
  align.update({ headingDeg: azimuthDeg, pitchDeg });
  drawSilhouette(((targetAz - azimuthDeg + 540) % 360) - 180);
});

(async () => {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    activateDemoBackground("浏览器不支持摄像头 API");
    return;
  }
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } },
      audio: false,
    });
    video.srcObject = stream;
    await video.play();

    // Now that the camera is alive, kick off pose detection.
    initPoseDetection().catch((e) => {
      console.warn("pose init failed", e);
      align.disable("distance");
      align.disable("person");
    });
  } catch (err) {
    activateDemoBackground(`摄像头不可用：${err.message || err}`);
  }
})();

let detector = null;
async function initPoseDetection() {
  detector = new PoseDetector();
  await detector.init();
  detector.attach(video);
  detector.on((s) => {
    align.update({
      personPresent: s.present,
      distanceM: s.present ? s.distanceM : null,
    });
  });
}

// ─────────────────────────────────────────────────────────
// AR overlay silhouette (kept from Phase A)
// ─────────────────────────────────────────────────────────
const SVG_NS = "http://www.w3.org/2000/svg";
function clearOverlay() { while (overlay.firstChild) overlay.removeChild(overlay.firstChild); }
function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

function drawSilhouette(deltaDeg) {
  const fovHalf = 30;
  const xCenter = 50 + (deltaDeg / fovHalf) * 35;
  const inFrame = Math.abs(deltaDeg) <= fovHalf + 4;
  const distance = Math.max(1.0, Math.min(4.0, targetDist));
  const yCenter = 60 - (distance - 1.0) * 4;
  const scale = clamp(28 / distance, 7, 16);
  const color = inFrame ? "#45c89c" : "rgba(255,255,255,0.4)";

  clearOverlay();

  const arrow = document.createElementNS(SVG_NS, "g");
  const tipX = clamp(xCenter, 6, 94);
  const baseY = 8;
  arrow.innerHTML = `
    <line x1="${tipX}" y1="${baseY}" x2="${tipX}" y2="${baseY + 6}" stroke="${color}" stroke-width="0.7" stroke-linecap="round" />
    <polygon points="${tipX - 1.4},${baseY + 6} ${tipX + 1.4},${baseY + 6} ${tipX},${baseY + 8.5}" fill="${color}" />
  `;
  overlay.appendChild(arrow);
  if (!inFrame) return;

  const g = document.createElementNS(SVG_NS, "g");
  g.setAttribute("transform", `translate(${xCenter}, ${yCenter})`);
  const body = document.createElementNS(SVG_NS, "ellipse");
  body.setAttribute("cx", "0"); body.setAttribute("cy", "0");
  body.setAttribute("rx", String(scale * 0.32)); body.setAttribute("ry", String(scale * 0.7));
  body.setAttribute("fill", color); body.setAttribute("fill-opacity", "0.18");
  body.setAttribute("stroke", color); body.setAttribute("stroke-width", "0.45");
  g.appendChild(body);
  const head = document.createElementNS(SVG_NS, "circle");
  head.setAttribute("cx", "0"); head.setAttribute("cy", String(-scale * 0.85));
  head.setAttribute("r", String(scale * 0.22)); head.setAttribute("fill", color);
  head.setAttribute("fill-opacity", "0.25"); head.setAttribute("stroke", color);
  head.setAttribute("stroke-width", "0.45");
  g.appendChild(head);
  const foot = document.createElementNS(SVG_NS, "ellipse");
  foot.setAttribute("cx", "0"); foot.setAttribute("cy", String(scale * 0.85));
  foot.setAttribute("rx", String(scale * 0.4)); foot.setAttribute("ry", String(scale * 0.06));
  foot.setAttribute("fill", color); foot.setAttribute("fill-opacity", "0.45");
  g.appendChild(foot);
  overlay.appendChild(g);
}

window.addEventListener("beforeunload", () => {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  heading.stop();
  detector?.dispose();
});
