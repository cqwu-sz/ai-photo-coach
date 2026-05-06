// Browser equivalent of the iOS HeadingTracker.
//
// On phones with a magnetometer (most modern Android + iOS Safari w/ user
// permission) we read DeviceOrientationEvent.alpha for compass heading.
// On laptops without sensors, we fall back to a synthetic sweep that just
// rotates with the user's mouse-x position so the demo still works in dev.

export class HeadingTracker {
  constructor() {
    this.azimuthDeg = 0;
    this.pitchDeg = 0;
    this.rollDeg = 0;
    this.coveredAngles = new Set();
    this.listeners = new Set();
    this._handler = null;
    this._fakeHandler = null;
  }

  on(cb) {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }

  emit() {
    for (const cb of this.listeners) cb(this.snapshot());
  }

  snapshot() {
    return {
      azimuthDeg: this.azimuthDeg,
      pitchDeg: this.pitchDeg,
      rollDeg: this.rollDeg,
      coveredAngles: new Set(this.coveredAngles),
      coverageProgress: this.coveredAngles.size / 12,
    };
  }

  reset() {
    this.coveredAngles.clear();
    this.emit();
  }

  async start() {
    // iOS 13+ requires explicit permission for DeviceOrientationEvent.
    if (
      typeof DeviceOrientationEvent !== "undefined" &&
      typeof DeviceOrientationEvent.requestPermission === "function"
    ) {
      try {
        const state = await DeviceOrientationEvent.requestPermission();
        if (state !== "granted") {
          this._startFake();
          return { mode: "fake", reason: "permission_denied" };
        }
      } catch (e) {
        this._startFake();
        return { mode: "fake", reason: String(e) };
      }
    }

    if ("DeviceOrientationEvent" in window) {
      this._handler = (e) => {
        // alpha: rotation around z-axis, 0..360 (compass-like, browser-dependent)
        let alpha = e.alpha;
        if (alpha == null) {
          // Some browsers leave alpha null on desktop; fall back to fake.
          this._startFake();
          window.removeEventListener("deviceorientation", this._handler);
          this._handler = null;
          return;
        }
        this.azimuthDeg = (360 - alpha + 360) % 360; // make rotating right increase
        this.pitchDeg = e.beta || 0;
        this.rollDeg = e.gamma || 0;
        const bucket = Math.floor(this.azimuthDeg / 30) * 30;
        this.coveredAngles.add(bucket);
        this.emit();
      };
      window.addEventListener("deviceorientation", this._handler, true);
      return { mode: "sensor" };
    }

    this._startFake();
    return { mode: "fake", reason: "no_sensor" };
  }

  _startFake() {
    let last = 0;
    this._fakeHandler = (e) => {
      const x = e.clientX ?? e.touches?.[0]?.clientX ?? 0;
      const w = window.innerWidth || 1;
      const az = (x / w) * 360;
      this.azimuthDeg = az;
      this.coveredAngles.add(Math.floor(az / 30) * 30);
      const now = performance.now();
      if (now - last > 33) {
        last = now;
        this.emit();
      }
    };
    window.addEventListener("mousemove", this._fakeHandler);
    window.addEventListener("touchmove", this._fakeHandler);
  }

  stop() {
    if (this._handler) {
      window.removeEventListener("deviceorientation", this._handler, true);
      this._handler = null;
    }
    if (this._fakeHandler) {
      window.removeEventListener("mousemove", this._fakeHandler);
      window.removeEventListener("touchmove", this._fakeHandler);
      this._fakeHandler = null;
    }
  }
}

// Render a 12-segment ring matching the iOS heading visualisation.
// Targets an <svg> element (220x220 viewBox).
export function renderHeadingRing(svg, covered) {
  if (!svg) return;
  const cx = 110;
  const cy = 110;
  const r = 92;
  const segments = 12;
  const gap = 4; // degrees of gap between segments
  const segDeg = 360 / segments - gap;
  let html = "";

  for (let i = 0; i < segments; i++) {
    const start = i * (360 / segments) - 90 + gap / 2;
    const end = start + segDeg;
    const filled = covered.has(i * 30);
    const color = filled ? "#45c89c" : "rgba(255,255,255,0.18)";
    html += arcPath(cx, cy, r, start, end, color);
  }
  svg.innerHTML = html;
}

function arcPath(cx, cy, r, startDeg, endDeg, color) {
  const start = polar(cx, cy, r, endDeg);
  const end = polar(cx, cy, r, startDeg);
  const largeArc = endDeg - startDeg <= 180 ? 0 : 1;
  return `<path d="M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 0 ${end.x} ${end.y}" stroke="${color}" stroke-width="14" stroke-linecap="round" fill="none" />`;
}

function polar(cx, cy, r, deg) {
  const rad = (deg * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}
