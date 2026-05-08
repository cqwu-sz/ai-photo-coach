// ──────────────────────────────────────────────────────────────────────────
// Optional location helper.
//
// Used by the analyze pipeline (and the light_shadow scene mode in
// particular) to tell the backend where the user is so the LLM can
// receive ENVIRONMENT FACTS (sun azimuth/altitude, golden-hour
// countdown, color temp estimate). Always opt-in: we only ask for
// geolocation when the active scene mode benefits from it (light_shadow)
// and store the last-known fix in localStorage so we don't pester the
// user on every analyze call.
//
// Privacy promise:
//   - We never send the raw fix to a third-party.
//   - We round to 4 decimal places (~11m) before storing.
//   - The cache expires after 6 hours so old fixes don't sneak into a
//     completely new shoot.
// ──────────────────────────────────────────────────────────────────────────

const STORAGE_KEY = "aphc.geofix";
const MAX_AGE_MS  = 6 * 60 * 60 * 1000;
const ASK_TIMEOUT_MS = 12_000;

export async function ensureGeoFix({ force = false } = {}) {
  // 1. Try cached fix first if it's fresh enough.
  if (!force) {
    const cached = readCache();
    if (cached && Date.now() - cached.cachedAt < MAX_AGE_MS) {
      return cached.fix;
    }
  }
  // 2. No usable cache — only proceed if the runtime supports geolocation.
  if (!("geolocation" in navigator)) return null;

  return new Promise((resolve) => {
    let settled = false;
    const finish = (fix) => {
      if (settled) return;
      settled = true;
      resolve(fix);
    };
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const fix = {
          lat: round4(pos.coords.latitude),
          lon: round4(pos.coords.longitude),
          accuracy_m: Number.isFinite(pos.coords.accuracy)
            ? Math.round(pos.coords.accuracy)
            : null,
          timestamp: new Date(pos.timestamp || Date.now()).toISOString(),
        };
        writeCache(fix);
        finish(fix);
      },
      (err) => {
        console.warn("[geo] denied or unavailable:", err && err.message);
        finish(null);
      },
      {
        enableHighAccuracy: false,
        maximumAge: 5 * 60_000,
        timeout: ASK_TIMEOUT_MS,
      },
    );
    setTimeout(() => finish(null), ASK_TIMEOUT_MS + 1000);
  });
}

export function readCache() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const obj = JSON.parse(raw);
    if (!obj || typeof obj.fix !== "object") return null;
    return obj;
  } catch {
    return null;
  }
}

export function clearGeoFix() {
  try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
}

function writeCache(fix) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      fix,
      cachedAt: Date.now(),
    }));
  } catch (_) {}
}

function round4(n) {
  return Math.round(n * 10_000) / 10_000;
}

/// Returns true when the given scene mode benefits from a sun fix.
/// Today only `light_shadow`, but it's a single function so future
/// modes (e.g. dramatic skies) can opt in without touching call sites.
export function sceneModeNeedsGeo(sceneMode) {
  return sceneMode === "light_shadow";
}
