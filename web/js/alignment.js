/**
 * Alignment state machine for the "is the shot ready?" green-light feedback.
 *
 * Tracks four independent dimensions:
 *
 *   1. heading   – compass azimuth vs target (DeviceOrientation.alpha)
 *   2. pitch     – phone tilt vs target (DeviceOrientation.beta)
 *   3. distance  – subject distance vs target (MediaPipe Pose ratio → m)
 *   4. person    – at least one person detected in frame
 *
 * Each dimension can be in one of:
 *   - "off"   sensor / detector hasn't reported yet, treat as "no data"
 *   - "warn"  current value differs from target by more than the OK band
 *             but inside the warn band (we know roughly how to correct)
 *   - "far"   current value is way off (mostly used for heading > 25°)
 *   - "ok"    inside the green band
 *
 * The aggregate state is "ok" only when all four are "ok" (or marked
 * disabled). To avoid flicker, the state machine fires the `green` event
 * only after the aggregate has been ok for `holdMs` continuous
 * milliseconds (default 700ms). A `green` event includes a snapshot so the
 * caller can play sound / vibrate / overlay big-text.
 *
 * Caller is responsible for feeding values:
 *   align.update({ headingDeg, pitchDeg, distanceM, personPresent })
 *
 * Use `align.disable("distance")` for environments where pose detection
 * isn't available (no camera, no MediaPipe support); it'll be excluded
 * from the aggregate.
 */

const DEFAULT_BANDS = {
  heading: { ok: 6, warn: 18 },     // degrees
  pitch:   { ok: 4, warn: 12 },     // degrees
  distance:{ ok: 0.35, warn: 1.0 }, // meters
};

export class AlignmentMachine {
  constructor({ target, holdMs = 700, bands } = {}) {
    this.target = {
      headingDeg: target?.headingDeg ?? 0,
      pitchDeg: target?.pitchDeg ?? 0,
      distanceM: target?.distanceM ?? 2.0,
    };
    this.holdMs = holdMs;
    this.bands = { ...DEFAULT_BANDS, ...(bands || {}) };

    this.state = {
      heading: { value: null, delta: null, status: "off" },
      pitch:   { value: null, delta: null, status: "off" },
      distance:{ value: null, delta: null, status: "off" },
      person:  { value: null, status: "off" }, // bool / off
    };

    // Disabled dimensions are excluded from the aggregate ok check.
    this.disabled = new Set();

    this.aggregateOk = false;
    this.greenSince = null;
    this.greenFired = false;

    this.listeners = new Set();
    this.greenListeners = new Set();
  }

  setTarget(target) {
    this.target = { ...this.target, ...target };
    this._recompute();
  }

  disable(dim) { this.disabled.add(dim); this._recompute(); }
  enable(dim)  { this.disabled.delete(dim); this._recompute(); }

  on(cb) { this.listeners.add(cb); return () => this.listeners.delete(cb); }
  onGreen(cb) { this.greenListeners.add(cb); return () => this.greenListeners.delete(cb); }

  update(partial) {
    if (partial.headingDeg != null) {
      const d = circDelta(partial.headingDeg, this.target.headingDeg);
      this.state.heading = {
        value: partial.headingDeg,
        delta: d,
        status: classify(Math.abs(d), this.bands.heading),
      };
    }
    if (partial.pitchDeg != null) {
      const d = partial.pitchDeg - this.target.pitchDeg;
      this.state.pitch = {
        value: partial.pitchDeg,
        delta: d,
        status: classify(Math.abs(d), this.bands.pitch),
      };
    }
    if (partial.distanceM != null) {
      const d = partial.distanceM - this.target.distanceM;
      this.state.distance = {
        value: partial.distanceM,
        delta: d,
        status: classify(Math.abs(d), this.bands.distance),
      };
    }
    if (partial.personPresent != null) {
      this.state.person = {
        value: partial.personPresent,
        status: partial.personPresent ? "ok" : "warn",
      };
    }
    this._recompute();
  }

  _recompute() {
    const dims = ["heading", "pitch", "distance", "person"];
    let allOk = true;
    let allKnown = true;
    for (const d of dims) {
      if (this.disabled.has(d)) continue;
      const s = this.state[d].status;
      if (s === "off") allKnown = false;
      if (s !== "ok") allOk = false;
    }
    const ok = allOk && allKnown;
    const now = performance.now();
    if (ok) {
      if (!this.greenSince) this.greenSince = now;
      if (!this.greenFired && now - this.greenSince >= this.holdMs) {
        this.greenFired = true;
        for (const cb of this.greenListeners) cb(this.snapshot());
      }
    } else {
      this.greenSince = null;
      this.greenFired = false;
    }
    this.aggregateOk = ok;
    for (const cb of this.listeners) cb(this.snapshot());
  }

  snapshot() {
    return {
      target: { ...this.target },
      heading: { ...this.state.heading },
      pitch: { ...this.state.pitch },
      distance: { ...this.state.distance },
      person: { ...this.state.person },
      disabled: new Set(this.disabled),
      aggregateOk: this.aggregateOk,
      greenFired: this.greenFired,
    };
  }
}

// Returns the signed shortest delta a-b mapped to (-180, 180].
export function circDelta(a, b) {
  return ((a - b + 540) % 360) - 180;
}

function classify(absDelta, band) {
  if (absDelta <= band.ok) return "ok";
  if (absDelta <= band.warn) return "warn";
  return "far";
}

// Simple corrective hint helpers, used by the UI layer.

export function headingHint(deltaDeg, band = DEFAULT_BANDS.heading) {
  if (Math.abs(deltaDeg) <= band.ok) return "✓ 方向已对准";
  const dir = deltaDeg > 0 ? "向右转" : "向左转";
  return `${dir} ${Math.round(Math.abs(deltaDeg))}°`;
}

export function pitchHint(deltaDeg, band = DEFAULT_BANDS.pitch) {
  if (Math.abs(deltaDeg) <= band.ok) return "✓ 仰角已对准";
  // beta > target: phone tilted further forward (top edge away from you)
  // → user needs to lower the top of the phone (raise the bottom)
  const dir = deltaDeg > 0 ? "镜头放低一点" : "镜头抬高一点";
  return `${dir} (差 ${Math.round(Math.abs(deltaDeg))}°)`;
}

export function distanceHint(deltaM, band = DEFAULT_BANDS.distance) {
  if (Math.abs(deltaM) <= band.ok) return "✓ 距离已对准";
  const dir = deltaM > 0 ? "向前走近" : "向后退";
  return `${dir} ${Math.abs(deltaM).toFixed(1)} m`;
}
