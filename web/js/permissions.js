// Permissions explainer + serial requester.
//
// v9 UX polish #1 — the original flow asked for camera/orientation/geo
// in three different pages, and a single denial on iOS Safari would
// effectively lock the user out with no recovery path. This page:
//
//   1. Surfaces *why* each permission is needed BEFORE the OS prompt
//      fires (so the user can make an informed choice).
//   2. Requests them serially so each OS prompt has the user's full
//      attention.
//   3. On denial, shows a concrete iOS / Android recovery path inline.
//   4. Sets `aphc.permsExplainerSeen` so we don't pester returning
//      users on every launch.
//
// All steps are best-effort; the user can skip and proceed straight
// to the wizard. The wizard / capture flow already handles each
// permission's absence (mouse-fake heading, no geo, etc.).

const SEEN_KEY = "aphc.permsExplainerSeen";
const FORCE = new URLSearchParams(location.search).get("force") === "1";

// If a returning user opened /web/permissions.html directly (e.g. from
// a future "Re-grant permissions" entry in Settings), don't auto-skip.
// First-visit users come here from welcome.html / index.html.

const cards = {
  camera:      document.getElementById("perm-camera"),
  orientation: document.getElementById("perm-orientation"),
  geo:         document.getElementById("perm-geo"),
};
const startBtn = document.getElementById("perms-start");
const startLabel = document.getElementById("perms-start-label");
const continueBtn = document.getElementById("perms-continue");
const skipBtn = document.getElementById("perms-skip");

// ─── State display helpers ──────────────────────────────────────────

function setState(key, state, statusText) {
  const card = cards[key];
  if (!card) return;
  card.dataset.state = state;
  const status = card.querySelector(".perm-status");
  if (status) status.textContent = statusText;
}

function allDone() {
  return Object.values(cards).every((c) => {
    const s = c?.dataset.state;
    return s === "granted" || s === "denied" || s === "skipped";
  });
}

function anyGranted() {
  return Object.values(cards).some((c) => c?.dataset.state === "granted");
}

// ─── Permission requesters ──────────────────────────────────────────

async function requestCamera() {
  setState("camera", "pending", "请求中…");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setState("camera", "denied", "浏览器不支持");
    return false;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" } },
      audio: false,
    });
    // Stop tracks immediately — we only needed the permission, not the
    // feed. The capture page will reopen its own stream when needed.
    stream.getTracks().forEach((t) => t.stop());
    setState("camera", "granted", "已开启 ✓");
    return true;
  } catch (err) {
    const name = err && err.name ? err.name : "";
    const reason =
      /NotAllowed|PermissionDenied/.test(name) ? "已拒绝" :
      /NotFound/.test(name) ? "无摄像头" :
      /NotReadable/.test(name) ? "被占用" :
      "失败";
    setState("camera", "denied", reason);
    return false;
  }
}

async function requestOrientation() {
  setState("orientation", "pending", "请求中…");
  // Non-iOS browsers don't have requestPermission — orientation events
  // fire freely (Android Chrome) or not at all (desktop), neither of
  // which needs an explicit prompt. We just mark granted on iOS and
  // "auto" otherwise so the user still sees a green check.
  if (
    typeof DeviceOrientationEvent !== "undefined" &&
    typeof DeviceOrientationEvent.requestPermission === "function"
  ) {
    try {
      const state = await DeviceOrientationEvent.requestPermission();
      if (state === "granted") {
        setState("orientation", "granted", "已开启 ✓");
        return true;
      }
      setState("orientation", "denied", "已拒绝");
      return false;
    } catch (err) {
      setState("orientation", "denied", "失败");
      return false;
    }
  }
  // No explicit prompt needed — best we can do is probe whether events
  // ever fire. We give the browser ~1s; if anything comes through we
  // mark granted, otherwise "无传感器" so the user knows fake-heading
  // will be used.
  return await new Promise((resolve) => {
    let got = false;
    const handler = (e) => {
      if (e.alpha != null || e.beta != null || e.gamma != null) {
        got = true;
        window.removeEventListener("deviceorientation", handler, true);
        setState("orientation", "granted", "已开启 ✓");
        resolve(true);
      }
    };
    window.addEventListener("deviceorientation", handler, true);
    setTimeout(() => {
      if (!got) {
        window.removeEventListener("deviceorientation", handler, true);
        setState("orientation", "skipped", "无传感器");
        resolve(false);
      }
    }, 1100);
  });
}

async function requestGeo() {
  setState("geo", "pending", "请求中…");
  if (!("geolocation" in navigator)) {
    setState("geo", "skipped", "不支持");
    return false;
  }
  return await new Promise((resolve) => {
    let settled = false;
    const finish = (ok, label) => {
      if (settled) return;
      settled = true;
      setState("geo", ok ? "granted" : "denied", label);
      resolve(ok);
    };
    navigator.geolocation.getCurrentPosition(
      () => finish(true, "已开启 ✓"),
      (err) => {
        const denied = err && err.code === 1; // PERMISSION_DENIED
        finish(false, denied ? "已拒绝" : "超时");
      },
      { enableHighAccuracy: false, timeout: 8000, maximumAge: 60_000 },
    );
    setTimeout(() => finish(false, "超时"), 9000);
  });
}

// ─── Orchestrator ───────────────────────────────────────────────────

let running = false;

async function runAll() {
  if (running) return;
  running = true;
  startBtn.disabled = true;
  startLabel.textContent = "正在依次请求…";

  // Camera first — it's the hard requirement.
  await requestCamera();
  // Orientation next — iOS will only show its own prompt if we were
  // triggered by a user gesture (we are — the click that called runAll).
  await requestOrientation();
  // Geo last — most users are comfortable saying yes after camera + orientation.
  await requestGeo();

  try { localStorage.setItem(SEEN_KEY, "1"); } catch {}

  running = false;
  startBtn.disabled = false;
  startLabel.textContent = anyGranted() ? "再请求一次未通过的项" : "重试";
  continueBtn.style.display = "block";
}

startBtn.addEventListener("click", runAll);

continueBtn.addEventListener("click", () => {
  // Hand off to the wizard. We set permsExplainerSeen so the wizard
  // (and capture) don't bounce the user back here.
  try { localStorage.setItem(SEEN_KEY, "1"); } catch {}
  location.href = "/web/";
});

skipBtn.addEventListener("click", () => {
  try { localStorage.setItem(SEEN_KEY, "skipped"); } catch {}
  location.href = "/web/";
});

// If this is a returning user opened the page accidentally and has
// already seen the explainer, surface a "skip" affordance more
// prominently — but never auto-redirect, because they may have come
// here on purpose to re-grant.
try {
  if (!FORCE && localStorage.getItem(SEEN_KEY)) {
    continueBtn.style.display = "block";
    continueBtn.textContent = "已设置过，直接进入";
  }
} catch {}
