// Same-origin client for the FastAPI backend.

const BASE = ""; // same origin – web is mounted under /web

export async function getHealth() {
  const r = await fetch(`${BASE}/healthz`);
  if (!r.ok) throw new Error(`health failed: ${r.status}`);
  return r.json();
}

export async function getPoseManifest() {
  const r = await fetch(`${BASE}/pose-library/manifest`);
  if (!r.ok) throw new Error(`manifest failed: ${r.status}`);
  return r.json();
}

export function poseThumbnailURL(poseId) {
  return `${BASE}/pose-library/thumbnail/${poseId}.png`;
}

export async function getDemoManifest(sceneMode = "portrait") {
  const r = await fetch(
    `${BASE}/dev/sample-manifest?scene_mode=${encodeURIComponent(sceneMode)}`,
  );
  if (!r.ok) throw new Error(`demo manifest failed: ${r.status}`);
  return r.json();
}

export async function getModels() {
  const r = await fetch(`${BASE}/models`);
  if (!r.ok) throw new Error(`models failed: ${r.status}`);
  return r.json();
}

export async function fetchAsBlob(url) {
  const r = await fetch(`${BASE}${url}`);
  if (!r.ok) throw new Error(`fetch ${url} failed: ${r.status}`);
  return r.blob();
}

export function sampleFrameURL(idx) {
  return `${BASE}/dev/sample-frame/${idx}.jpg`;
}

/**
 * Build & send the multipart /analyze request.
 *
 * @param {Object} args
 * @param {Object} args.meta - CaptureMeta-shaped JSON
 * @param {Blob[]} args.frames - keyframe blobs (image/jpeg)
 * @param {Blob[]} [args.references]
 * @param {string} [args.modelId]    - vision-model id (BYOK)
 * @param {string} [args.modelApiKey] - user-side API key (sent only on this request)
 * @param {string} [args.modelBaseUrl] - custom OpenAI-compat base url
 */
export async function analyze({
  meta,
  frames,
  references = [],
  modelId = "",
  modelApiKey = "",
  modelBaseUrl = "",
}) {
  const fd = new FormData();
  fd.append("meta", JSON.stringify(meta));
  for (let i = 0; i < frames.length; i++) {
    fd.append("frames", frames[i], `frame_${i}.jpg`);
  }
  for (let i = 0; i < references.length; i++) {
    fd.append("reference_thumbnails", references[i], `ref_${i}.jpg`);
  }
  if (modelId) fd.append("model_id", modelId);
  if (modelApiKey) fd.append("model_api_key", modelApiKey);
  if (modelBaseUrl) fd.append("model_base_url", modelBaseUrl);
  const r = await fetch(`${BASE}/analyze`, { method: "POST", body: fd });
  if (!r.ok) {
    let msg = `${r.status}`;
    try {
      const body = await r.json();
      msg += ` ${JSON.stringify(body)}`;
    } catch {
      msg += ` ${await r.text()}`;
    }
    throw new Error(msg);
  }
  return r.json();
}
