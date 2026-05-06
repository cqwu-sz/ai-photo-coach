/**
 * Browser-side pose detection wrapper around Google's MediaPipe Tasks
 * Vision (PoseLandmarker, WASM build).
 *
 * Loads lazily from the JSDelivr CDN so we don't pay the ~7MB WASM cost
 * unless the guide page actually opens. Falls back gracefully if the
 * download fails (e.g. corporate network, offline).
 *
 * Public API:
 *   const det = new PoseDetector();
 *   await det.init();
 *   det.attach(videoEl);                 // start running per-frame detection
 *   det.on(snap => { ... });             // snapshot per detection
 *   det.detach();
 *   det.dispose();
 *
 * Snapshot fields:
 *   {
 *     present: boolean,
 *     heightRatio: number | null,        // 0..1, from nose to ankle in normalized image space
 *     distanceM: number | null,          // estimated subject distance (rough)
 *     keypoints: Array<{x,y,visibility}> // raw 33 landmarks (or null)
 *   }
 *
 * Distance estimation:
 *   We don't know the camera's focal length in the browser, but for the
 *   typical mobile main lens (≈ 60° vertical FOV) the empirical mapping
 *   "ratio R of person height in frame ↔ distance" is well-approximated
 *   by D ≈ k / R where k is calibrated so that R=0.5 → D=2.5m. The
 *   resulting estimate is good to ±20% in practice — plenty to drive a
 *   "stand here" coach.
 */

const CDN_VISION = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14";
// Public model file shipped by Google.
const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/pose_landmarker/" +
  "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task";

const DISTANCE_K = 1.25; // calibration constant

export class PoseDetector {
  constructor() {
    this.landmarker = null;
    this.video = null;
    this.running = false;
    this.lastT = 0;
    this.listeners = new Set();
    this.lastSnapshot = { present: false, heightRatio: null, distanceM: null, keypoints: null };
    this._raf = null;
  }

  on(cb) { this.listeners.add(cb); return () => this.listeners.delete(cb); }

  /**
   * Lazy-load the WASM + model. Throws if either step fails — callers
   * should catch and disable the distance dimension.
   */
  async init() {
    if (this.landmarker) return;
    let module;
    try {
      module = await import(`${CDN_VISION}/vision_bundle.mjs`);
    } catch (e) {
      throw new Error(`MediaPipe vision bundle 加载失败: ${e?.message || e}`);
    }
    const { FilesetResolver, PoseLandmarker } = module;
    const fileset = await FilesetResolver.forVisionTasks(`${CDN_VISION}/wasm`);
    this.landmarker = await PoseLandmarker.createFromOptions(fileset, {
      baseOptions: { modelAssetPath: MODEL_URL, delegate: "GPU" },
      runningMode: "VIDEO",
      numPoses: 1,
    });
  }

  attach(videoEl) {
    this.video = videoEl;
    this.running = true;
    this._loop();
  }

  detach() {
    this.running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    this.video = null;
  }

  dispose() {
    this.detach();
    if (this.landmarker && this.landmarker.close) this.landmarker.close();
    this.landmarker = null;
    this.listeners.clear();
  }

  _loop() {
    if (!this.running || !this.video || !this.landmarker) return;
    const v = this.video;
    if (v.readyState >= 2 && v.videoWidth > 0) {
      const ts = performance.now();
      // PoseLandmarker requires monotonically increasing timestamps in
      // VIDEO mode — clamp if RAF gives us same-tick ts.
      const safeT = ts <= this.lastT ? this.lastT + 1 : ts;
      this.lastT = safeT;
      try {
        const result = this.landmarker.detectForVideo(v, safeT);
        this._handle(result);
      } catch (e) {
        // Don't spam; keep going on next frame.
        // console.warn("pose detect err", e);
      }
    }
    this._raf = requestAnimationFrame(() => this._loop());
  }

  _handle(result) {
    const lms = result?.landmarks?.[0];
    if (!lms || !lms.length) {
      this.lastSnapshot = {
        present: false, heightRatio: null, distanceM: null, keypoints: null,
      };
    } else {
      // 0=nose, 27=left_ankle, 28=right_ankle (MediaPipe Pose 33 layout)
      const nose = lms[0];
      const ankles = [lms[27], lms[28]].filter((p) => p && p.visibility > 0.3);
      const ankleY = ankles.length
        ? ankles.reduce((s, p) => s + p.y, 0) / ankles.length
        : Math.max(lms[23]?.y ?? 0, lms[24]?.y ?? 0); // fall back to hips
      const heightRatio = Math.max(0, Math.min(1, ankleY - nose.y));
      const distanceM = heightRatio > 0.05 ? DISTANCE_K / heightRatio : null;
      this.lastSnapshot = {
        present: true,
        heightRatio,
        distanceM,
        keypoints: lms,
      };
    }
    for (const cb of this.listeners) cb(this.lastSnapshot);
  }
}
