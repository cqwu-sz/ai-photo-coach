// Browser equivalent of the iOS KeyframeExtractor.
//
// While the user is "recording" we sample the live <video> stream every ~150ms
// to a Canvas, capturing image + heading. When recording stops we apply the
// same azimuth-bucketed selection algorithm as the Swift KeyframeExtractor
// to pick `target` representative frames.

// Per-quality-mode capture knobs. Higher quality bumps both the JPEG
// width and quality so the LLM has more to look at; cost: roughly 4x
// upload size from fast → high before video kicks in. fast keeps the
// previous behaviour to stay binary-compatible with prior captures.
export const QUALITY_PROFILES = {
  fast: { jpegWidth: 768,  jpegQuality: 0.82, intervalMs: 150 },
  high: { jpegWidth: 1024, jpegQuality: 0.88, intervalMs: 80  },
};

export class FrameSampler {
  constructor({ video, heading, intervalMs, qualityMode = "fast" }) {
    this.video = video;
    this.heading = heading;
    const profile = QUALITY_PROFILES[qualityMode] || QUALITY_PROFILES.fast;
    this.intervalMs = intervalMs ?? profile.intervalMs;
    this.jpegWidth = profile.jpegWidth;
    this.jpegQuality = profile.jpegQuality;
    this.samples = [];
    this._timer = null;
    this._canvas = document.createElement("canvas");
    this._startedAt = 0;
  }

  start() {
    this.samples = [];
    this._startedAt = performance.now();
    this._tick();
    this._timer = setInterval(() => this._tick(), this.intervalMs);
  }

  stop() {
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    return this.samples;
  }

  get count() {
    return this.samples.length;
  }

  _tick() {
    if (!this.video.videoWidth) return;
    const w = this.jpegWidth;
    const h = Math.round((this.video.videoHeight / this.video.videoWidth) * w);
    this._canvas.width = w;
    this._canvas.height = h;
    const ctx = this._canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(this.video, 0, 0, w, h);
    const dataUrl = this._canvas.toDataURL("image/jpeg", this.jpegQuality);
    const snap = this.heading.snapshot();

    // Cheap client-side quality signals — we already have the pixels in
    // canvas, so it's free to read them. Backed by a 96-px downscaled
    // grab so we run < 0.5 ms per sample even on a slow phone.
    const quality = computeFrameQuality(ctx, w, h);

    this.samples.push({
      dataUrl,
      azimuthDeg: snap.azimuthDeg,
      pitchDeg: snap.pitchDeg,
      rollDeg: snap.rollDeg,
      timestampMs: Math.round(performance.now() - this._startedAt),
      meanLuma: quality.meanLuma,
      blurScore: quality.blurScore,
    });
  }
}

// Returns lightweight quality signals computed inline during sampling.
//
//   meanLuma  : 0..1, average ITU-BT.601 luma over a 96-px-wide downscale
//   blurScore : roughly proportional to sharpness — sum of |dI/dx| over
//               the same downscale, normalised by pixel count. Higher =
//               sharper. Calibrated so >= 8 is "in focus", < 3 is "blurry"
//               for a 1280-px input video on a typical phone.
//
// The backend can consume both verbatim (FrameMeta.blur_score / mean_luma).
// We use a sub-canvas so we don't allocate fresh ImageData every tick.
const _qCanvas = (typeof document !== "undefined")
  ? document.createElement("canvas")
  : null;

function computeFrameQuality(srcCtx, srcW, srcH) {
  // v9 UX polish #17 — when we can't read pixels back (no document
  // in worker context, tainted canvas in Safari, etc.) return null
  // instead of neutral 0.5 values. The backend's capture_quality
  // (rule 13) treats null as "no data" and skips luma/blur-based
  // judgements; a fake 0.5 would have been silently scored as
  // "medium light" on every Safari user.
  if (!_qCanvas) return { meanLuma: null, blurScore: null };
  const targetW = 96;
  const targetH = Math.max(48, Math.round((srcH / srcW) * targetW));
  _qCanvas.width = targetW;
  _qCanvas.height = targetH;
  const qctx = _qCanvas.getContext("2d", { willReadFrequently: true });
  qctx.drawImage(srcCtx.canvas, 0, 0, targetW, targetH);
  let img;
  try {
    img = qctx.getImageData(0, 0, targetW, targetH);
  } catch (e) {
    // Tainted canvas — explicit "no signal" so backend doesn't think
    // it has medium-light frames.
    return { meanLuma: null, blurScore: null };
  }
  const data = img.data;
  const px = targetW * targetH;

  // Luma in one pass; cache per-pixel luma into a Uint8 buffer for blur.
  const luma = new Uint8Array(px);
  let sum = 0;
  for (let i = 0, j = 0; i < data.length; i += 4, j++) {
    // BT.601 luma — close enough; saves one mul vs BT.709.
    const y = (data[i] * 299 + data[i + 1] * 587 + data[i + 2] * 114) / 1000;
    luma[j] = y;
    sum += y;
  }
  const meanLuma = (sum / px) / 255;

  // Horizontal gradient as a cheap sharpness proxy. We skip the last
  // column per row.
  let grad = 0;
  for (let y = 0; y < targetH; y++) {
    const rowOff = y * targetW;
    for (let x = 0; x < targetW - 1; x++) {
      const i = rowOff + x;
      const d = luma[i + 1] - luma[i];
      grad += d < 0 ? -d : d;
    }
  }
  const blurScore = grad / px;

  return {
    meanLuma: round3(meanLuma),
    blurScore: round3(blurScore),
  };
}

function round3(v) {
  return Math.round(v * 1000) / 1000;
}

// Mirrors backend/ios algorithm: azimuth-bucket the samples; pick the median
// timestamp from each populated bucket; if too few, fall back to time-uniform.
export function selectKeyframes(samples, target = 10) {
  if (!samples.length) return [];
  if (samples.length <= target) return samples.slice();

  const azs = samples.map((s) => s.azimuthDeg);
  const min = Math.min(...azs);
  const max = Math.max(...azs);
  const span = max - min;

  if (span < 30) {
    return uniformByTime(samples, target);
  }

  const bucketCount = Math.max(target, 8);
  const bucketWidth = Math.max(1, span / bucketCount);
  const buckets = new Map();
  for (const s of samples) {
    const idx = Math.min(
      Math.floor((s.azimuthDeg - min) / bucketWidth),
      bucketCount - 1,
    );
    if (!buckets.has(idx)) buckets.set(idx, []);
    buckets.get(idx).push(s);
  }

  const result = [];
  for (let i = 0; i < bucketCount; i++) {
    const bucket = buckets.get(i);
    if (!bucket || !bucket.length) continue;
    const median = bucket[Math.floor(bucket.length / 2)];
    result.push(median);
  }

  if (result.length < target) {
    const extras = uniformByTime(samples, target - result.length);
    for (const e of extras) {
      if (!result.find((r) => r.timestampMs === e.timestampMs)) {
        result.push(e);
        if (result.length >= target) break;
      }
    }
  }

  if (result.length > target) result.length = target;
  result.sort((a, b) => a.timestampMs - b.timestampMs);
  return result;
}

function uniformByTime(samples, target) {
  if (!samples.length || target <= 0) return [];
  const n = samples.length;
  if (n <= target) return samples.slice();
  const step = (n - 1) / (target - 1);
  const out = [];
  for (let i = 0; i < target; i++) {
    const idx = Math.round(i * step);
    out.push(samples[Math.min(idx, n - 1)]);
  }
  return out;
}

// Convert a data URL produced by Canvas.toDataURL back to a Blob suitable
// for multipart upload.
export function dataUrlToBlob(dataUrl) {
  const [meta, b64] = dataUrl.split(",");
  const mime = /data:(.*?);base64/.exec(meta)[1];
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  return new Blob([arr], { type: mime });
}
