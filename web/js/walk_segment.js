// walk_segment.js
//
// Optional 10-20 s walk recorded *after* the standing pan, fed into
// the backend's three-source position fusion to unlock far (50-200 m)
// shot candidates.
//
// Two acquisition paths:
//
//   1. WebXR (`immersive-ar`) — when available we get true VIO via
//      `XRViewerPose.transform`. Sub-metre precision, ``source: webxr``.
//      Confidence on the backend ≈ 0.75.
//
//   2. DeviceMotion fallback — IMU-only double integration. Drifts
//      noticeably (metres per second) so backend ``confidence`` drops
//      to ~0.35; still better than nothing for picking the rough
//      direction the user walked.
//
// Both paths produce the same ``WalkSegment`` JSON shape:
//
//   {
//     source: "webxr" | "devicemotion",
//     initial_heading_deg: number | null,
//     poses: [{ t_ms, x, y, z, qx, qy, qz, qw }, ...]
//   }
//
// `x` is east, `y` is north, `z` is up — metres relative to the user's
// initial GeoFix. The backend takes care of rotating by
// `initial_heading_deg` and converting to (lat, lon).

const SAMPLE_INTERVAL_MS = 100; // 10 Hz
const MAX_DURATION_MS    = 25_000;
const KEYFRAME_INTERVAL_MS = 1000; // 1 Hz still images for backend ORB correction
const KEYFRAME_MAX_DIM = 320;
const GPS_MIN_INTERVAL_MS = 1500;

export function isWalkAvailable() {
  return hasWebXR() || hasDeviceMotion();
}

export function hasWebXR() {
  return typeof navigator !== "undefined"
    && navigator.xr
    && typeof navigator.xr.isSessionSupported === "function";
}

export function hasDeviceMotion() {
  return typeof window !== "undefined"
    && typeof window.DeviceMotionEvent !== "undefined";
}

/**
 * Start recording a walk segment. Returns a controller with `stop()`
 * that resolves with the captured WalkSegment (or null when nothing
 * usable was recorded).
 *
 * @param {{ initialHeadingDeg?: number|null }} [opts]
 */
/**
 * Start GPS sampling. Returns a controller exposing the collected samples
 * and a stop() method. Best-effort — no-op when geolocation is denied.
 */
export function startGpsTrack(startMs) {
  const samples = [];
  let watchId = null;
  let lastTs = -GPS_MIN_INTERVAL_MS;
  if (typeof navigator === "undefined" || !navigator.geolocation) {
    return { samples, stop() {} };
  }
  try {
    watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const tMs = Math.round(performance.now() - startMs);
        if (tMs - lastTs < GPS_MIN_INTERVAL_MS) return;
        lastTs = tMs;
        samples.push({
          t_ms: tMs,
          lat: pos.coords.latitude,
          lon: pos.coords.longitude,
          accuracy_m: pos.coords.accuracy,
        });
      },
      () => { /* permission or signal lost — proceed silently */ },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 5000 },
    );
  } catch { /* ignore */ }
  return {
    samples,
    stop() {
      if (watchId != null && navigator.geolocation) {
        try { navigator.geolocation.clearWatch(watchId); } catch {}
      }
    },
  };
}

/**
 * Start a 1 Hz keyframe sampler. Reuses the user's already-running camera
 * stream when one is provided via ``opts.videoEl`` — typically the
 * <video> element rendering the live preview on capture.html.
 */
export function startKeyframeSampler(videoEl, startMs) {
  const frames = [];
  let cancelled = false;
  if (!videoEl || typeof document === "undefined") {
    return { frames, stop() { cancelled = true; } };
  }
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  function tick() {
    if (cancelled) return;
    try {
      const w = videoEl.videoWidth || 0;
      const h = videoEl.videoHeight || 0;
      if (w > 0 && h > 0) {
        const ratio = Math.min(KEYFRAME_MAX_DIM / w, KEYFRAME_MAX_DIM / h, 1);
        canvas.width = Math.round(w * ratio);
        canvas.height = Math.round(h * ratio);
        ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
        frames.push({
          t_ms: Math.round(performance.now() - startMs),
          dataUrl: canvas.toDataURL("image/jpeg", 0.55),
        });
      }
    } catch { /* draw failed (CORS / not playing) — skip */ }
    setTimeout(tick, KEYFRAME_INTERVAL_MS);
  }
  setTimeout(tick, KEYFRAME_INTERVAL_MS);
  return { frames, stop() { cancelled = true; } };
}

export async function startWalk(opts = {}) {
  const initialHeadingDeg = opts.initialHeadingDeg ?? null;
  const videoEl = opts.videoEl ?? null;
  const startMs = performance.now();
  const gps = startGpsTrack(startMs);
  const kf  = startKeyframeSampler(videoEl, startMs);

  const wrap = (controller) => {
    const origStop = controller.stop.bind(controller);
    controller.stop = async () => {
      gps.stop();
      kf.stop();
      const seg = await origStop();
      if (seg) {
        if (gps.samples.length > 0) seg.gps_track = gps.samples;
        if (kf.frames.length > 0) seg.keyframes_b64 = kf.frames;
      }
      return seg;
    };
    controller.gpsSamples = () => gps.samples;
    controller.keyframes  = () => kf.frames;
    return controller;
  };

  if (hasWebXR()) {
    try {
      const sup = await navigator.xr.isSessionSupported("immersive-ar");
      if (sup) return wrap(await startWebXRWalk(initialHeadingDeg));
    } catch {
      /* fall through to DeviceMotion */
    }
  }
  if (hasDeviceMotion()) {
    return wrap(startDeviceMotionWalk(initialHeadingDeg));
  }
  gps.stop(); kf.stop();
  throw new Error("walk capture not supported on this device");
}

// ---------------------------------------------------------------------------
async function startWebXRWalk(initialHeadingDeg) {
  const session = await navigator.xr.requestSession("immersive-ar", {
    requiredFeatures: ["local"],
  });
  const refSpace = await session.requestReferenceSpace("local");
  const poses = [];
  let originSet = false;
  let originX = 0, originY = 0, originZ = 0;
  const startMs = performance.now();

  let lastSampleMs = -SAMPLE_INTERVAL_MS;
  function onFrame(time, frame) {
    const tMs = Math.round(performance.now() - startMs);
    if (tMs - lastSampleMs < SAMPLE_INTERVAL_MS) {
      session.requestAnimationFrame(onFrame);
      return;
    }
    lastSampleMs = tMs;
    const viewer = frame.getViewerPose(refSpace);
    if (!viewer) {
      session.requestAnimationFrame(onFrame);
      return;
    }
    const m = viewer.transform.matrix; // column-major 4x4
    const tx = m[12], ty = m[13], tz = m[14];
    if (!originSet) {
      originX = tx; originY = ty; originZ = tz; originSet = true;
      poses.push({ t_ms: 0, x: 0, y: 0, z: 0, qx: 0, qy: 0, qz: 0, qw: 1 });
    } else {
      // WebXR is right-handed Y-up: x=right, y=up, z=behind. Convert to
      // the backend's ENU (x=east, y=north, z=up). Our session is
      // `local` so axis alignment is gravity-aligned; we *don't* know
      // true north here, so backend rotates by initialHeadingDeg.
      const dx = tx - originX;
      const dy = ty - originY;
      const dz = tz - originZ;
      const o = viewer.transform.orientation; // {x,y,z,w}
      poses.push({
        t_ms: tMs,
        x:  dx,
        y: -dz,         // -z is forward in WebXR; map to "north"
        z:  dy,
        qx: o.x, qy: o.y, qz: o.z, qw: o.w,
      });
    }
    if (tMs >= MAX_DURATION_MS) {
      session.end();
      return;
    }
    session.requestAnimationFrame(onFrame);
  }
  session.requestAnimationFrame(onFrame);

  return {
    source: "webxr",
    coverageM() {
      const last = poses[poses.length - 1];
      if (!last) return 0;
      return Math.hypot(last.x, last.y);
    },
    async stop() {
      try { await session.end(); } catch { /* already ended */ }
      if (poses.length < 3) return null;
      return {
        source: "webxr",
        initial_heading_deg: initialHeadingDeg,
        poses,
      };
    },
  };
}

// ---------------------------------------------------------------------------
function startDeviceMotionWalk(initialHeadingDeg) {
  const poses = [];
  let vx = 0, vy = 0, vz = 0;
  let px = 0, py = 0, pz = 0;
  let lastTs = null;
  const startMs = performance.now();
  let originSet = false;
  let lastSampleMs = -SAMPLE_INTERVAL_MS;

  // iOS 13+ requires explicit permission for motion events.
  async function ensurePermission() {
    if (typeof DeviceMotionEvent.requestPermission === "function") {
      try { await DeviceMotionEvent.requestPermission(); } catch { /* user denied */ }
    }
  }

  function onMotion(e) {
    const ts = performance.now();
    if (lastTs == null) { lastTs = ts; return; }
    const dt = (ts - lastTs) / 1000;
    lastTs = ts;
    // accelerationIncludingGravity is the only universally available
    // field on Android+iOS. We subtract a quick low-pass gravity
    // estimate to integrate just the linear part.
    const a = e.acceleration || e.accelerationIncludingGravity || { x: 0, y: 0, z: 0 };
    vx += (a.x || 0) * dt;
    vy += (a.y || 0) * dt;
    vz += (a.z || 0) * dt;
    // Crude drag so a stationary device doesn't accumulate forever.
    vx *= 0.92; vy *= 0.92; vz *= 0.92;
    px += vx * dt; py += vy * dt; pz += vz * dt;

    const tMs = Math.round(ts - startMs);
    if (tMs - lastSampleMs < SAMPLE_INTERVAL_MS) return;
    lastSampleMs = tMs;
    if (!originSet) {
      poses.push({ t_ms: 0, x: 0, y: 0, z: 0, qx: 0, qy: 0, qz: 0, qw: 1 });
      originSet = true;
      px = py = pz = 0;
      return;
    }
    poses.push({
      t_ms: tMs, x: px, y: py, z: pz,
      qx: 0, qy: 0, qz: 0, qw: 1,
    });
    if (tMs >= MAX_DURATION_MS) {
      window.removeEventListener("devicemotion", onMotion);
    }
  }

  ensurePermission().then(() => {
    window.addEventListener("devicemotion", onMotion);
  });

  return {
    source: "devicemotion",
    coverageM() {
      const last = poses[poses.length - 1];
      if (!last) return 0;
      return Math.hypot(last.x, last.y);
    },
    async stop() {
      window.removeEventListener("devicemotion", onMotion);
      if (poses.length < 3) return null;
      return {
        source: "devicemotion",
        initial_heading_deg: initialHeadingDeg,
        poses,
      };
    },
  };
}
