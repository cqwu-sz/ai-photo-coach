// Web equivalent of ios FrameSemantics.swift.
//
// Given an array of keyframe samples (each carrying a JPEG dataUrl),
// produce per-frame semantic signals that the backend's prompt builder
// folds into ENVIRONMENT FACTS:
//
//   personBox        - largest detected person box [x,y,w,h] in [0,1]
//                       (top-left origin to match the iOS convention).
//   saliencyQuadrant - which 2x2 quadrant of the frame holds the visual
//                       centre of mass; computed by a cheap canvas-only
//                       method (sum of Sobel magnitude per quadrant) so
//                       it always works even when MediaPipe fails.
//   horizonTiltDeg   - we don't run a horizon detector on web; we use
//                       the existing rollDeg from DeviceOrientation as
//                       a tilt proxy, rounded to 1 decimal.
//   foregroundCandidates - up to 3 detected objects per frame that could
//                       work as a near-foreground (plant/tree/fence/...).
//                       MediaPipe ObjectDetector + a small COCO→foreground
//                       allow-list. Drives FOREGROUND DOCTRINE in prompt.
//   depthLayers      - {near_pct, mid_pct, far_pct, source} from MiDaS
//                       Small (ONNX). Lets the LLM verify whether the
//                       scene physically supports a foreground layer.
//
// All sub-detectors lazy-load and fail-soft: if any one fails to fetch
// (offline / firewall / WebGL disabled) the others keep working and
// we just send fewer signals.

let _poseDetectorPromise = null;
let _objectDetectorPromise = null;
let _midasSessionPromise = null;
let _faceDetectorPromise = null;

const CDN_VISION = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/" +
  "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task";

// EfficientDet-Lite0 — Google's official COCO-80 detector for MediaPipe
// Tasks Vision. ~5 MB, ~30 ms per 320×320 inference on a mid-range
// phone. We use it on each keyframe only (10 images per scan) so the
// total cost is well under 0.5 s.
const OBJECT_MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/object_detector/" +
  "efficientdet_lite0/float16/latest/efficientdet_lite0.tflite";

// BlazeFace short-range — Google's lightweight face detector; ~250 KB
// and < 5 ms per frame on mobile WebGL. Used to refine the distance
// estimate for tight portraits where pose ankles are out of frame.
const FACE_MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_detector/" +
  "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite";

// MiDaS-small ONNX — single-image depth estimation. ONNX Runtime Web
// is ~3 MB; the model is ~25 MB; first analysis after a cold cache
// pays the download once, subsequent ones hit the browser cache.
const ORT_CDN = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/ort.min.mjs";
// v12 — model path is configurable via window.MIDAS_MODEL_URL so we
// can A/B test MiDaS Small v2.1 (default, 21MB) vs MiDaS v3.1 small
// (28MB, +30% accuracy on outdoor scenes) without redeploying. To
// switch, drop midas_v31_small.onnx into web/models/ and set
//   window.MIDAS_MODEL_URL = "/web/models/midas_v31_small.onnx"
// before frame_semantics.js loads.
const MIDAS_MODEL_URL = (typeof window !== "undefined" && window.MIDAS_MODEL_URL)
  || "/web/models/midas_small.onnx";
const MIDAS_INPUT_SIZE = 256;

// COCO classes that *can* serve as a foreground element. Anything
// outside this list (person, car, tv, etc.) is filtered out — the LLM
// gets a curated list, not raw COCO.
const FOREGROUND_LABELS = new Set([
  "potted plant",
  "vase",
  "bench",
  "chair",
  "umbrella",
  "fire hydrant",
  "stop sign",
  "parking meter",
  "tie",          // gate / column-like
  // softer matches — rename to friendlier categories below
  "bird",
  "cat",
  "dog",
]);

// Friendlier display label (sent to backend → prompt). We never
// surface raw COCO names; map them to natural Chinese-friendly
// English so the LLM can quote them directly.
const LABEL_REWRITE = {
  "potted plant": "potted_plant",
  "vase": "flower_vase",
  "bench": "bench",
  "chair": "chair",
  "umbrella": "umbrella",
  "fire hydrant": "fire_hydrant",
  "stop sign": "sign_post",
  "parking meter": "post",
  "tie": "vertical_post",
  "bird": "bird",
  "cat": "small_animal",
  "dog": "small_animal",
};

async function ensurePoseDetector() {
  if (_poseDetectorPromise) return _poseDetectorPromise;
  _poseDetectorPromise = (async () => {
    try {
      const module = await import(`${CDN_VISION}/vision_bundle.mjs`);
      const { FilesetResolver, PoseLandmarker } = module;
      const fileset = await FilesetResolver.forVisionTasks(`${CDN_VISION}/wasm`);
      return await PoseLandmarker.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
        runningMode: "IMAGE",
        numPoses: 4,
      });
    } catch (e) {
      console.warn("[frame-semantics] PoseLandmarker load failed (non-fatal):", e);
      return null;
    }
  })();
  return _poseDetectorPromise;
}

async function ensureObjectDetector() {
  if (_objectDetectorPromise) return _objectDetectorPromise;
  _objectDetectorPromise = (async () => {
    try {
      const module = await import(`${CDN_VISION}/vision_bundle.mjs`);
      const { FilesetResolver, ObjectDetector } = module;
      const fileset = await FilesetResolver.forVisionTasks(`${CDN_VISION}/wasm`);
      return await ObjectDetector.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: OBJECT_MODEL_URL, delegate: "GPU" },
        runningMode: "IMAGE",
        scoreThreshold: 0.35,
        maxResults: 8,
      });
    } catch (e) {
      console.warn("[frame-semantics] ObjectDetector load failed (non-fatal):", e);
      return null;
    }
  })();
  return _objectDetectorPromise;
}

async function ensureFaceDetector() {
  if (_faceDetectorPromise) return _faceDetectorPromise;
  _faceDetectorPromise = (async () => {
    try {
      const module = await import(`${CDN_VISION}/vision_bundle.mjs`);
      const { FilesetResolver, FaceDetector } = module;
      const fileset = await FilesetResolver.forVisionTasks(`${CDN_VISION}/wasm`);
      return await FaceDetector.createFromOptions(fileset, {
        baseOptions: { modelAssetPath: FACE_MODEL_URL, delegate: "GPU" },
        runningMode: "IMAGE",
        minDetectionConfidence: 0.5,
      });
    } catch (e) {
      console.warn("[frame-semantics] FaceDetector load failed (non-fatal):", e);
      return null;
    }
  })();
  return _faceDetectorPromise;
}

async function ensureMidas() {
  if (_midasSessionPromise) return _midasSessionPromise;
  _midasSessionPromise = (async () => {
    try {
      // First check the model file actually exists — we don't want to
      // pull 3 MB of ONNX runtime if the model 404s.
      const head = await fetch(MIDAS_MODEL_URL, { method: "HEAD" });
      if (!head.ok) {
        console.info("[frame-semantics] MiDaS model not bundled, depth disabled");
        return null;
      }
      const ort = await import(ORT_CDN);
      // wasm SIMD + threads when available; falls back gracefully.
      ort.env.wasm.wasmPaths = "https://cdn.jsdelivr.net/npm/onnxruntime-web@1.18.0/dist/";
      const session = await ort.InferenceSession.create(MIDAS_MODEL_URL, {
        executionProviders: ["webgl", "wasm"],
      });
      return { ort, session };
    } catch (e) {
      console.warn("[frame-semantics] MiDaS load failed (non-fatal):", e);
      return null;
    }
  })();
  return _midasSessionPromise;
}

/// Compute all per-frame semantic signals for a single keyframe.
/// Returns an object with five nullable fields:
///   { personBox, saliencyQuadrant, horizonTiltDeg,
///     foregroundCandidates, depthLayers }
/// — caller writes each one into FrameMeta independently.
async function analyzeOne(sample, detector, scratchCanvas, objectDetector, midas, faceDetector, prevSubject) {
  // Decode the dataUrl into an Image we can hand to MediaPipe + draw
  // onto a canvas for the saliency pass. dataUrl is small (768/1024px)
  // so this is fast.
  const img = await new Promise((resolve, reject) => {
    const i = new Image();
    i.onload = () => resolve(i);
    i.onerror = reject;
    i.src = sample.dataUrl;
  }).catch(() => null);
  if (!img) {
    return { personBox: null, saliencyQuadrant: null, horizonTiltDeg: null };
  }

  // v10.2 — multi-person aware pose. We build a candidate per detected
  // pose, then pickSubject() narrows to one (preferring the candidate
  // closest to prevSubject so single-frame mis-IDs don't flip subjects
  // mid-sweep).
  const poseCandidates = [];
  if (detector) {
    try {
      const result = detector.detect(img);
      const allLms = result?.landmarks || [];
      for (const lms of allLms) {
        if (!lms || lms.length < 33) continue;
        let minX = 1, minY = 1, maxX = 0, maxY = 0, hits = 0;
        for (const p of lms) {
          if ((p.visibility ?? 1) < 0.4) continue;
          if (p.x < minX) minX = p.x;
          if (p.y < minY) minY = p.y;
          if (p.x > maxX) maxX = p.x;
          if (p.y > maxY) maxY = p.y;
          hits++;
        }
        if (hits < 6) continue;
        const box = [
          round4(clamp01(minX)),
          round4(clamp01(minY)),
          round4(clamp01(maxX - minX)),
          round4(clamp01(maxY - minY)),
        ];
        const nose = lms[0];
        const noseY = (nose && (nose.visibility ?? 1) > 0.4) ? round4(clamp01(nose.y)) : null;
        const lAnkle = lms[27], rAnkle = lms[28];
        const ankles = [lAnkle, rAnkle].filter(p => p && (p.visibility ?? 1) > 0.4);
        const ankleY = ankles.length ? round4(clamp01(ankles.reduce((s, p) => s + p.y, 0) / ankles.length)) : null;
        // v12 — fine-grained pose: shoulder tilt, hip offset, chin
        // forward, spine curve. Indices match MediaPipe Pose 33-pt:
        // 11 = left shoulder, 12 = right shoulder, 23 = left hip,
        // 24 = right hip.
        const lS = lms[11], rS = lms[12];
        let shoulderTiltDeg = null;
        if (lS && rS && (lS.visibility ?? 1) > 0.4 && (rS.visibility ?? 1) > 0.4) {
          const dy = rS.y - lS.y;
          const dx = rS.x - lS.x;
          if (Math.abs(dx) > 1e-3) shoulderTiltDeg = round1(-Math.atan2(dy, dx) * 180 / Math.PI);
        }
        let hipOffsetX = null;
        const lH = lms[23], rH = lms[24];
        if (lH && rH && (lH.visibility ?? 1) > 0.4 && (rH.visibility ?? 1) > 0.4) {
          const mid = (lH.x + rH.x) / 2;
          hipOffsetX = round4(mid * 2 - 1);
        }
        let chinForward = null;
        if (nose && lS && rS) {
          const midX = (lS.x + rS.x) / 2;
          const sw = Math.abs(rS.x - lS.x);
          if (sw > 0.02) chinForward = round4((nose.x - midX) / sw);
        }
        let spineCurve = null;
        if (nose && lS && rS && lH && rH) {
          const neckX = (lS.x + rS.x) / 2, neckY = (lS.y + rS.y) / 2;
          const rootX = (lH.x + rH.x) / 2, rootY = (lH.y + rH.y) / 2;
          const area = Math.abs((neckX - nose.x) * (rootY - nose.y) - (rootX - nose.x) * (neckY - nose.y)) / 2;
          const bodyH = Math.max(0.05, rootY - nose.y);
          spineCurve = round4(area / (bodyH * bodyH));
        }
        poseCandidates.push({ box, noseY, ankleY, fine: { shoulderTiltDeg, hipOffsetX, chinForward, spineCurve } });
      }
    } catch (e) {
      // detector errors are non-fatal
    }
  }
  const pickedPose = pickSubject(poseCandidates, prevSubject);
  const personBox  = pickedPose?.box ?? null;
  const poseNoseY  = pickedPose?.noseY ?? null;
  const poseAnkleY = pickedPose?.ankleY ?? null;
  const finePose   = pickedPose?.fine ?? {
    shoulderTiltDeg: null, hipOffsetX: null, chinForward: null, spineCurve: null,
  };

  // Saliency + horizon row + color stats in one pass over the same
  // downscaled canvas.
  const sceneStats = computeSceneStats(img, scratchCanvas);
  const saliencyQuadrant = sceneStats.quadrant;
  const horizonY = sceneStats.horizonY;
  const colorStats = sceneStats.colorStats;
  const skyMaskTopPct = sceneStats.skyMaskTopPct;

  // Horizon proxy: sample.rollDeg already exists (from DeviceOrientation
  // gyro). Sign matches "right side higher" by convention used in the
  // backend schema: positive = right side higher.
  const horizonTiltDeg = Number.isFinite(sample.rollDeg)
    ? round1(sample.rollDeg)
    : null;

  // Face detection — sharper distance estimate than pose body height
  // once ankles leave the frame. We pick the face whose bbox best
  // overlaps the chosen pose subject (or, when no pose, fall back to
  // the same pickSubject heuristic).
  let faceHeightRatio = null;
  let personCount = poseCandidates.length || 0;
  if (faceDetector) {
    try {
      const r = faceDetector.detect(img);
      const dets = r?.detections || [];
      const W = img.naturalWidth || 1;
      const H = img.naturalHeight || 1;
      const faceCandidates = [];
      for (const d of dets) {
        const bb = d.boundingBox;
        if (!bb) continue;
        const x = clamp01((bb.originX ?? 0) / W);
        const y = clamp01((bb.originY ?? 0) / H);
        const w = clamp01((bb.width ?? 0) / W);
        const h = clamp01((bb.height ?? 0) / H);
        if (h < 0.005) continue;
        faceCandidates.push({ box: [round4(x), round4(y), round4(w), round4(h)], height: h });
      }
      personCount = Math.max(personCount, faceCandidates.length);
      // Prefer a face that overlaps the chosen pose subject; else
      // fall back to single-subject heuristic.
      let chosenFace = null;
      if (personBox && faceCandidates.length) {
        chosenFace = pickByIoU(faceCandidates, personBox) ?? pickSubject(faceCandidates, prevSubject);
      } else if (faceCandidates.length) {
        chosenFace = pickSubject(faceCandidates, prevSubject);
      }
      if (chosenFace) faceHeightRatio = round4(chosenFace.height);
    } catch (e) {
      // ignore
    }
  }
  const subjectBox = personBox; // already the consensus subject for pose

  // Foreground candidates — COCO objects filtered to the foreground
  // allow-list, capped at top-3 by area.
  let foregroundCandidates = null;
  if (objectDetector) {
    try {
      const objResult = objectDetector.detect(img);
      const detections = objResult?.detections || [];
      const W = img.naturalWidth || 1;
      const H = img.naturalHeight || 1;
      const filtered = [];
      for (const d of detections) {
        const cat = d.categories?.[0];
        if (!cat) continue;
        const name = (cat.categoryName || "").toLowerCase();
        if (!FOREGROUND_LABELS.has(name)) continue;
        const bb = d.boundingBox || {};
        // MediaPipe returns boundingBox in pixel coords with origin
        // top-left. Normalise to [0,1].
        const x = clamp01((bb.originX ?? 0) / W);
        const y = clamp01((bb.originY ?? 0) / H);
        const w = clamp01((bb.width ?? 0) / W);
        const h = clamp01((bb.height ?? 0) / H);
        if (w < 0.01 || h < 0.01) continue;
        filtered.push({
          label: LABEL_REWRITE[name] || name,
          box: [round4(x), round4(y), round4(w), round4(h)],
          confidence: round4(cat.score ?? 0),
        });
      }
      filtered.sort((a, b) => b.box[2] * b.box[3] - a.box[2] * a.box[3]);
      foregroundCandidates = filtered.slice(0, 3);
      if (!foregroundCandidates.length) foregroundCandidates = null;
    } catch (e) {
      // ignore — leave null
    }
  }

  // Depth layers — MiDaS Small ONNX. Returns relative depth; we
  // bucket by quantile cuts (top 25% closest = near, bottom 25%
  // farthest = far). Distance scale is calibrated coarsely so the
  // "near = within ~1.5m" threshold means roughly the right thing
  // on a smartphone-height handheld capture.
  let depthLayers = null;
  if (midas) {
    try {
      depthLayers = await runMidasDepth(img, midas, scratchCanvas);
    } catch (e) {
      // ignore
    }
  }

  return {
    personBox,
    saliencyQuadrant,
    horizonTiltDeg,
    foregroundCandidates,
    depthLayers,
    poseNoseY,
    poseAnkleY,
    faceHeightRatio,
    horizonY,
    personCount: personCount || null,
    subjectBox,
    rgbMean: colorStats?.rgbMean ?? null,
    saturationMean: colorStats?.saturationMean ?? null,
    lumaP05: colorStats?.lumaP05 ?? null,
    lumaP95: colorStats?.lumaP95 ?? null,
    highlightClipPct: colorStats?.highlightClipPct ?? null,
    shadowClipPct: colorStats?.shadowClipPct ?? null,
    skyMaskTopPct: skyMaskTopPct ?? null,
    shoulderTiltDeg: finePose.shoulderTiltDeg ?? null,
    hipOffsetX: finePose.hipOffsetX ?? null,
    chinForward: finePose.chinForward ?? null,
    spineCurve: finePose.spineCurve ?? null,
    // horizon_y_vision: Web has no Vision framework; leave null.
    horizonYVision: null,
  };
}

// ---- Subject selection heuristics ------------------------------------
//
// Multi-person scenes (group portraits, passers-by) confuse a naive
// "biggest detection" rule, especially when frames sweep past a stranger
// briefly. We pick a single subject per frame using:
//   1. closeness to prevSubject (IoU) — keeps the subject sticky;
//   2. size — bigger candidates rank higher;
//   3. centrality — closer to (0.5, 0.5) ranks higher.
// The function takes a list of {box, ...} candidates and returns the
// chosen one (preserving its other fields). When no candidates qualify
// returns null.

function pickSubject(cands, prev) {
  if (!cands || !cands.length) return null;
  let best = null, bestScore = -1;
  for (const c of cands) {
    const [x, y, w, h] = c.box;
    const area = w * h;
    if (area < 0.005) continue;
    const cx = x + w / 2, cy = y + h / 2;
    const dist = Math.hypot(cx - 0.5, cy - 0.5);     // 0..~0.71
    const central = 1 - Math.min(1, dist / 0.71);
    let stickiness = 0;
    if (prev) {
      stickiness = iou(c.box, prev);                  // 0..1
    }
    // Weights chosen empirically: stickiness rules when present, then
    // size, then centrality. With no prev we naturally fall back to
    // size+centre.
    const score = stickiness * 1.4 + Math.sqrt(area) * 0.9 + central * 0.4;
    if (score > bestScore) { bestScore = score; best = c; }
  }
  return best;
}

function pickByIoU(cands, refBox) {
  let best = null, bestIoU = 0;
  for (const c of cands) {
    const u = iou(c.box, refBox);
    if (u > bestIoU) { bestIoU = u; best = c; }
  }
  return bestIoU > 0.05 ? best : null;
}

function iou(a, b) {
  const [ax, ay, aw, ah] = a, [bx, by, bw, bh] = b;
  const x1 = Math.max(ax, bx), y1 = Math.max(ay, by);
  const x2 = Math.min(ax + aw, bx + bw), y2 = Math.min(ay + ah, by + bh);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const u = aw * ah + bw * bh - inter;
  return u > 0 ? inter / u : 0;
}

/// Run MiDaS-Small on the input image and return {near_pct, mid_pct,
/// far_pct, source}. We use quantile cuts on the relative-depth output
/// so the bucket sizes are calibrated by scene-statistics rather than
/// requiring an absolute-depth model (which MiDaS isn't anyway).
async function runMidasDepth(img, midas, scratch) {
  const { ort, session } = midas;
  const N = MIDAS_INPUT_SIZE;
  scratch.width = N;
  scratch.height = N;
  const ctx = scratch.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, N, N);
  let data;
  try { data = ctx.getImageData(0, 0, N, N).data; }
  catch { return null; }

  // CHW float32 normalised — MiDaS standard preprocessing.
  const planar = new Float32Array(3 * N * N);
  const mean = [0.485, 0.456, 0.406];
  const std = [0.229, 0.224, 0.225];
  for (let i = 0, p = 0; i < N * N; i++, p += 4) {
    planar[i]             = ((data[p]     / 255) - mean[0]) / std[0];   // R
    planar[N * N + i]     = ((data[p + 1] / 255) - mean[1]) / std[1];   // G
    planar[2 * N * N + i] = ((data[p + 2] / 255) - mean[2]) / std[2];   // B
  }
  const inputTensor = new ort.Tensor("float32", planar, [1, 3, N, N]);
  const inputName = session.inputNames[0];
  const outputName = session.outputNames[0];
  const outputs = await session.run({ [inputName]: inputTensor });
  const out = outputs[outputName].data;   // Float32Array, length N*N (or upsampled)

  // MiDaS outputs *inverse* depth (larger = closer). Bucket via
  // quantile to be invariant to the absolute scale.
  const sorted = Float32Array.from(out).sort();
  const q1 = sorted[Math.floor(sorted.length * 0.33)];
  const q2 = sorted[Math.floor(sorted.length * 0.66)];
  // Upper third (closer than q2) = near; middle = mid; lower = far.
  let near = 0, mid = 0, far = 0;
  for (let i = 0; i < out.length; i++) {
    if (out[i] >= q2) near++;
    else if (out[i] >= q1) mid++;
    else far++;
  }
  const total = out.length;
  return {
    near_pct: round4(near / total),
    mid_pct: round4(mid / total),
    far_pct: round4(far / total),
    source: "midas_web",
  };
}

// Color/lighting stats: RGB mean (excluding clipped pixels), luma p05/p95,
// highlight & shadow clipping fractions, mean HSV saturation.
// Computed in the same single pass through the downscaled canvas as
// scene stats — see computeColorStats below.

function computeColorStats(data, w, h) {
  let rSum = 0, gSum = 0, bSum = 0, satSum = 0, n = 0;
  let hiClip = 0, loClip = 0;
  const lumas = new Uint8Array(w * h);
  let pi = 0;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const luma = (0.2989 * r + 0.587 * g + 0.114 * b) | 0;
      lumas[pi++] = luma;
      if (luma >= 250) hiClip++;
      else if (luma <= 5) loClip++;
      else {
        // Only include "non-clipped, well-exposed" pixels in the
        // gray-world average — keeps CCT estimation robust.
        rSum += r; gSum += g; bSum += b;
        const max = Math.max(r, g, b), min = Math.min(r, g, b);
        const sat = max === 0 ? 0 : (max - min) / max;
        satSum += sat;
        n++;
      }
    }
  }
  // Cheap percentile via histogram.
  const hist = new Uint32Array(256);
  for (let k = 0; k < lumas.length; k++) hist[lumas[k]]++;
  const total = lumas.length;
  const target05 = total * 0.05, target95 = total * 0.95;
  let acc = 0, p05 = 0, p95 = 255;
  for (let v = 0; v < 256; v++) {
    acc += hist[v];
    if (acc >= target05 && p05 === 0) p05 = v;
    if (acc >= target95) { p95 = v; break; }
  }
  if (n === 0) return null;
  return {
    rgbMean: [round1(rSum / n), round1(gSum / n), round1(bSum / n)],
    saturationMean: round4(satSum / n),
    lumaP05: p05,
    lumaP95: p95,
    highlightClipPct: round4(hiClip / total),
    shadowClipPct: round4(loClip / total),
  };
}

// Combined single-pass scene stats: saliency quadrant + horizon row.
// We use the same downscaled canvas pixels for both to avoid double
// work. Horizon is found by accumulating the *horizontal* gradient
// |dI/dx| (which is small on flat sky/water/ground) and the *vertical*
// gradient |dI/dy| (which spikes at the sky→ground transition) per
// row, then picking the row with the largest dy and small dx variance.
function computeSceneStats(img, scratch) {
  const w = 96;
  const h = Math.max(48, Math.round((img.naturalHeight / img.naturalWidth) * w));
  scratch.width = w;
  scratch.height = h;
  const ctx = scratch.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);
  let data;
  try {
    data = ctx.getImageData(0, 0, w, h).data;
  } catch {
    return { quadrant: null, horizonY: null, colorStats: null };
  }
  // Color stats — share the same downscale to amortise getImageData.
  const colorStats = computeColorStats(data, w, h);
  // v12 sky-mask top fraction (gates horizon trust): bright + slightly blue.
  let skyMaskTopPct = 0;
  {
    let hits = 0, total = 0;
    const halfH = Math.floor(h / 2);
    for (let y = 0; y < halfH; y++) {
      for (let x = 0; x < w; x++) {
        const i = (y * w + x) * 4;
        const r = data[i], g = data[i + 1], b = data[i + 2];
        const luma = 0.2989 * r + 0.587 * g + 0.114 * b;
        total++;
        if (luma > 180 && (r > 0 ? b / r : 0) > 1.05) hits++;
      }
    }
    skyMaskTopPct = total ? hits / total : 0;
  }
  const acc = [0, 0, 0, 0];   // tl, tr, bl, br
  const rowDy = new Float32Array(h);
  const halfW = w / 2;
  const halfH = h / 2;
  for (let y = 0; y < h - 1; y++) {
    let rowSum = 0;
    for (let x = 0; x < w - 1; x++) {
      const i = (y * w + x) * 4;
      const iR = i + 4;
      const iD = i + w * 4;
      const l0 = 0.2989 * data[i]   + 0.587 * data[i + 1]   + 0.114 * data[i + 2];
      const lR = 0.2989 * data[iR]  + 0.587 * data[iR + 1]  + 0.114 * data[iR + 2];
      const lD = 0.2989 * data[iD]  + 0.587 * data[iD + 1]  + 0.114 * data[iD + 2];
      const dx = Math.abs(lR - l0);
      const dy = Math.abs(lD - l0);
      acc[(y < halfH ? 0 : 2) + (x < halfW ? 0 : 1)] += dx + dy;
      rowSum += dy;
    }
    rowDy[y] = rowSum;
  }
  const total = acc.reduce((a, b) => a + b, 0) || 1;
  let bestI = 0;
  for (let i = 1; i < 4; i++) if (acc[i] > acc[bestI]) bestI = i;
  const quadrant = (acc[bestI] / total) < 0.30
    ? "center"
    : ["top_left", "top_right", "bottom_left", "bottom_right"][bestI];

  // Horizon: skip the top/bottom 10% (often image margins / vignettes)
  // and pick the row whose vertical-gradient sum is the strongest. To
  // be robust against a busy single-row spike, smooth with a box-3.
  const skip = Math.max(2, Math.floor(h * 0.10));
  let bestY = -1, bestVal = 0;
  for (let y = skip; y < h - skip; y++) {
    const v = (rowDy[y - 1] + rowDy[y] + rowDy[y + 1]) / 3;
    if (v > bestVal) { bestVal = v; bestY = y; }
  }
  // Require the winner to be at least 1.6× the row average — otherwise
  // there is no clear horizon (indoor / no sky / heavy texture
  // everywhere) and we return null.
  let mean = 0;
  for (let y = 0; y < h; y++) mean += rowDy[y];
  mean = mean / h;
  const horizonY = (bestY >= 0 && bestVal > mean * 1.6)
    ? round4(bestY / h)
    : null;
  return { quadrant, horizonY, colorStats, skyMaskTopPct };
}

function clamp01(x) { return Math.max(0, Math.min(1, x)); }
function round4(x)  { return Math.round(x * 10_000) / 10_000; }
function round1(x)  { return Math.round(x * 10) / 10; }

/// Public entry point: analyze a small batch of keyframes (typically 10).
/// Returns an array of full semantic results in the same order as the
/// input. Always resolves — never throws. All five sub-detectors fail
/// independently; missing models just leave their respective field null.
export async function analyzeKeyframes(samples) {
  // Kick off all three model loads in parallel — the slowest is MiDaS
  // (~25 MB cold) so we overlap it with the keyframe loop.
  const [poseDetector, objectDetector, midas, faceDetector] = await Promise.all([
    ensurePoseDetector(),
    ensureObjectDetector(),
    ensureMidas(),
    ensureFaceDetector(),
  ]);
  const scratch = document.createElement("canvas");
  const out = [];
  let prevSubject = null;     // last frame's subject box, anchors stickiness
  for (const sample of samples) {
    const r = await analyzeOne(sample, poseDetector, scratch, objectDetector, midas, faceDetector, prevSubject);
    if (r?.subjectBox) prevSubject = r.subjectBox;
    out.push(r);
  }
  return out;
}
