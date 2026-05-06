// Browser equivalent of the iOS KeyframeExtractor.
//
// While the user is "recording" we sample the live <video> stream every ~150ms
// to a Canvas, capturing image + heading. When recording stops we apply the
// same azimuth-bucketed selection algorithm as the Swift KeyframeExtractor
// to pick `target` representative frames.

export class FrameSampler {
  constructor({ video, heading, intervalMs = 150 }) {
    this.video = video;
    this.heading = heading;
    this.intervalMs = intervalMs;
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
    const w = 384;
    const h = Math.round((this.video.videoHeight / this.video.videoWidth) * w);
    this._canvas.width = w;
    this._canvas.height = h;
    const ctx = this._canvas.getContext("2d");
    ctx.drawImage(this.video, 0, 0, w, h);
    const dataUrl = this._canvas.toDataURL("image/jpeg", 0.7);
    const snap = this.heading.snapshot();
    this.samples.push({
      dataUrl,
      azimuthDeg: snap.azimuthDeg,
      pitchDeg: snap.pitchDeg,
      rollDeg: snap.rollDeg,
      timestampMs: Math.round(performance.now() - this._startedAt),
    });
  }
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
