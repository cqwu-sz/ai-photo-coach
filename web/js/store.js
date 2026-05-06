// Tiny session-store backed by sessionStorage so we can pass capture
// settings between pages (index -> capture -> result -> guide) without
// pulling in a router.

const KEY_SETTINGS = "apc.settings";
const KEY_RESULT = "apc.result";
const KEY_CURRENT_SHOT = "apc.currentShot";
const KEY_FRAMES = "apc.frames";
const KEY_REF_INSPIRATION = "apc.refInspiration";
const KEY_AVATAR_PICKS = "apc.avatarPicks";
const KEY_PANORAMA_URL = "apc.panoramaUrl";

export function saveSettings(s) {
  sessionStorage.setItem(KEY_SETTINGS, JSON.stringify(s));
}

export function loadSettings() {
  const raw = sessionStorage.getItem(KEY_SETTINGS);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function saveResult(r) {
  sessionStorage.setItem(KEY_RESULT, JSON.stringify(r));
}

export function loadResult() {
  const raw = sessionStorage.getItem(KEY_RESULT);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export function saveCurrentShot(s) {
  sessionStorage.setItem(KEY_CURRENT_SHOT, JSON.stringify(s));
}

export function loadCurrentShot() {
  const raw = sessionStorage.getItem(KEY_CURRENT_SHOT);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Persist the keyframes the user just captured (or the demo set) so that
 * the result page can use them as backdrops in the visual mock-up.
 *
 * Each frame is `{ index, azimuthDeg, src }`. `src` may be a data: URL
 * (camera capture) or a same-origin /dev/sample-frame URL (demo run).
 *
 * sessionStorage is capped at ~5MB, so we cap stored frames to 12 and
 * silently drop the rest.
 */
export function saveFrames(frames) {
  try {
    const slim = (frames || []).slice(0, 12).map((f) => ({
      index: f.index,
      azimuthDeg: f.azimuthDeg,
      src: f.src,
    }));
    sessionStorage.setItem(KEY_FRAMES, JSON.stringify(slim));
  } catch (e) {
    // QuotaExceeded? Drop dataURLs and fall back to backend URLs only.
    console.warn("saveFrames quota issue, dropping dataURL fallback", e);
    try {
      const onlyUrls = (frames || []).filter((f) => f.src && !f.src.startsWith("data:"));
      sessionStorage.setItem(KEY_FRAMES, JSON.stringify(onlyUrls));
    } catch {
      sessionStorage.removeItem(KEY_FRAMES);
    }
  }
}

export function loadFrames() {
  const raw = sessionStorage.getItem(KEY_FRAMES);
  if (!raw) return [];
  try {
    return JSON.parse(raw) || [];
  } catch {
    return [];
  }
}

/**
 * Persist a small payload describing what reference images the user had
 * loaded at analysis time (count + thumbnail dataURLs). Lets the result
 * page render an "AI 借鉴了这些图" card without re-querying IndexedDB.
 */
export function saveRefInspiration(payload) {
  try {
    sessionStorage.setItem(KEY_REF_INSPIRATION, JSON.stringify(payload));
  } catch (e) {
    sessionStorage.removeItem(KEY_REF_INSPIRATION);
  }
}

export function loadRefInspiration() {
  const raw = sessionStorage.getItem(KEY_REF_INSPIRATION);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Avatar picks: array of avatar style ids, one per person slot.
 * Saved in localStorage so the user's choices survive across sessions —
 * picks are personality, not capture data.
 */
export function saveAvatarPicks(picks) {
  try {
    localStorage.setItem(KEY_AVATAR_PICKS, JSON.stringify(picks || []));
  } catch {
    // Quota issue, ignore.
  }
}

export function loadAvatarPicks() {
  const raw =
    (typeof localStorage !== "undefined"
      ? localStorage.getItem(KEY_AVATAR_PICKS)
      : null) ||
    sessionStorage.getItem(KEY_AVATAR_PICKS);
  if (!raw) return [];
  try {
    return JSON.parse(raw) || [];
  } catch {
    return [];
  }
}

/**
 * URL of the equirectangular panorama (data URL or backend URL) used by
 * the 3D scene as the sphere texture.
 */
export function savePanoramaUrl(url) {
  try {
    sessionStorage.setItem(KEY_PANORAMA_URL, url || "");
  } catch {}
}

export function loadPanoramaUrl() {
  return sessionStorage.getItem(KEY_PANORAMA_URL) || null;
}
