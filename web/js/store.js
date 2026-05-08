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
const KEY_SCENE_MODE = "apc.sceneMode";
const KEY_MODEL_CONFIG = "apc.modelConfig";
const KEY_LAST_PREFS = "apc.lastPrefs";

export const SCENE_MODES = [
  { id: "portrait", label: "人像", blurb: "半身或全身，人物为主" },
  { id: "closeup", label: "特写", blurb: "脸 / 上半身 / 神态特写" },
  { id: "full_body", label: "全身", blurb: "完整人物 + 背景" },
  { id: "documentary", label: "人文", blurb: "抓拍质感 + 故事感" },
  { id: "scenery", label: "风景", blurb: "纯环境出片，可不出人" },
];

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

/**
 * Scene mode (portrait/closeup/full_body/documentary/scenery). Stored in
 * localStorage so it survives across sessions — most users have a habit.
 */
export function saveSceneMode(mode) {
  try {
    localStorage.setItem(KEY_SCENE_MODE, mode || "portrait");
  } catch {}
}

export function loadSceneMode() {
  try {
    const raw =
      (typeof localStorage !== "undefined"
        ? localStorage.getItem(KEY_SCENE_MODE)
        : null) || sessionStorage.getItem(KEY_SCENE_MODE);
    return raw || "portrait";
  } catch {
    return "portrait";
  }
}

/**
 * Model config: { model_id, api_key, base_url }. Saved in localStorage
 * (BYOK key never leaves the browser). The api_key is intentionally
 * stored as plaintext — same trust level as cookies; we explicitly tell
 * the user this in the settings drawer.
 */
export function saveModelConfig(cfg) {
  try {
    localStorage.setItem(
      KEY_MODEL_CONFIG,
      JSON.stringify(cfg || {}),
    );
  } catch {}
}

export function loadModelConfig() {
  try {
    const raw = localStorage.getItem(KEY_MODEL_CONFIG);
    if (!raw) return { model_id: "", api_key: "", base_url: "" };
    const parsed = JSON.parse(raw) || {};
    return {
      model_id: parsed.model_id || "",
      api_key: parsed.api_key || "",
      base_url: parsed.base_url || "",
    };
  } catch {
    return { model_id: "", api_key: "", base_url: "" };
  }
}

export function clearModelConfig() {
  try {
    localStorage.removeItem(KEY_MODEL_CONFIG);
  } catch {}
}

/**
 * Last-used onboarding preferences — drives the "returning user lands on
 * Step 4 with everything pre-filled" experience.
 *
 * Shape: { sceneMode, personCount, qualityMode, styleKeywords }
 */
export function saveLastPrefs(p) {
  try {
    localStorage.setItem(
      KEY_LAST_PREFS,
      JSON.stringify({
        sceneMode: p?.sceneMode || "portrait",
        personCount: Number.isFinite(p?.personCount) ? p.personCount : 1,
        qualityMode: p?.qualityMode || "fast",
        styleKeywords: Array.isArray(p?.styleKeywords) ? p.styleKeywords : [],
      }),
    );
  } catch {}
}

export function loadLastPrefs() {
  try {
    const raw = localStorage.getItem(KEY_LAST_PREFS);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return {
      sceneMode: parsed?.sceneMode || "portrait",
      personCount: Number.isFinite(parsed?.personCount) ? parsed.personCount : 1,
      qualityMode: parsed?.qualityMode || "fast",
      styleKeywords: Array.isArray(parsed?.styleKeywords) ? parsed.styleKeywords : [],
    };
  } catch {
    return null;
  }
}
