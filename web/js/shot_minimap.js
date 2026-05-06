/**
 * Top-down "shooting plan" mini-map for one ShotRecommendation.
 *
 * Shows:
 *   - Camera position (the user) at bottom center, with FOV cone forward.
 *   - Subject placement at distance.distance_m, on the recommended
 *     azimuth_deg compass heading.
 *   - Compass rose with N marker so the user knows which way to turn.
 *   - Distance scale ring at 1m / 2m / 4m.
 *
 * The angle here is interpreted as: 0 deg = looking the same way the user
 * was facing at the start of the scan; positive values turn clockwise.
 * That matches HeadingTracker's emission semantics, so the same number
 * shown on the map can be used as the AR-guide target heading.
 */

const STAGE = { w: 240, h: 240 };
const CENTER = { x: 120, y: 168 };  // camera roughly bottom-third
const SCALE_M_PX = 26;              // 1m -> 26px in screen space

function svg(tag, attrs = {}) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function deg2rad(d) { return (d * Math.PI) / 180; }

function project(azDeg, distM) {
  // Map (azimuth, distance) to screen XY relative to camera.
  // We treat the camera as facing "up" on the screen (north on the map).
  // Negative-y is forward, positive-x is right.
  const r = distM * SCALE_M_PX;
  const a = deg2rad(azDeg);
  return {
    x: CENTER.x + r * Math.sin(a),
    y: CENTER.y - r * Math.cos(a),
  };
}

function ring(distM, label) {
  const g = svg("g", { class: "minimap-ring" });
  g.appendChild(svg("circle", {
    cx: CENTER.x, cy: CENTER.y, r: distM * SCALE_M_PX,
    fill: "none", stroke: "rgba(255,255,255,0.08)",
    "stroke-width": 1, "stroke-dasharray": "2 4",
  }));
  if (label) {
    g.appendChild(svg("text", {
      x: CENTER.x + distM * SCALE_M_PX + 4,
      y: CENTER.y - 2,
      "font-size": 9, fill: "rgba(255,255,255,0.4)", "font-family": "system-ui",
    })).textContent = label;
  }
  return g;
}

function compass() {
  const g = svg("g", { class: "compass" });
  const r = 92;
  // Rose ring
  g.appendChild(svg("circle", {
    cx: CENTER.x, cy: CENTER.y, r,
    fill: "none", stroke: "rgba(255,255,255,0.10)", "stroke-width": 1,
  }));
  // Cardinal ticks at N/E/S/W (relative to camera-forward, so N == start heading)
  const cardinals = [
    { label: "前", deg: 0 },
    { label: "右", deg: 90 },
    { label: "后", deg: 180 },
    { label: "左", deg: 270 },
  ];
  for (const c of cardinals) {
    const p = project(c.deg, r / SCALE_M_PX);
    g.appendChild(svg("text", {
      x: p.x, y: p.y + 3,
      "text-anchor": "middle", "font-size": 9,
      fill: c.deg === 0 ? "#5b9cff" : "rgba(255,255,255,0.45)",
      "font-weight": c.deg === 0 ? 700 : 400,
      "font-family": "system-ui",
    })).textContent = c.label;
  }
  return g;
}

function camera() {
  const g = svg("g", { class: "minimap-camera" });

  // FOV cone — show roughly a 60° forward fan based on focal length.
  const fov = 60;
  const reach = 5 * SCALE_M_PX;
  const left = project(-fov / 2, reach / SCALE_M_PX);
  const right = project(fov / 2, reach / SCALE_M_PX);
  g.appendChild(svg("path", {
    d: `M ${CENTER.x} ${CENTER.y} L ${left.x} ${left.y} A ${reach} ${reach} 0 0 1 ${right.x} ${right.y} Z`,
    fill: "rgba(91,156,255,0.10)",
    stroke: "rgba(91,156,255,0.25)",
    "stroke-width": 1,
  }));

  g.appendChild(svg("rect", {
    x: CENTER.x - 9, y: CENTER.y - 6, width: 18, height: 12, rx: 3,
    fill: "#1f2733", stroke: "#5b9cff", "stroke-width": 1.4,
  }));
  g.appendChild(svg("circle", { cx: CENTER.x, cy: CENTER.y, r: 3.2, fill: "#5b9cff" }));
  return g;
}

function subject(angle, label) {
  const dist = clamp(angle.distance_m, 0.6, 4.5);
  const p = project(angle.azimuth_deg, dist);
  const g = svg("g", { class: "minimap-subject" });

  // Pulsing dashed line from camera to subject = "stand here"
  g.appendChild(svg("line", {
    x1: CENTER.x, y1: CENTER.y, x2: p.x, y2: p.y,
    stroke: "rgba(245,90,200,0.65)", "stroke-width": 1.6,
    "stroke-dasharray": "4 4", "stroke-linecap": "round",
  }));

  // Distance label, mid-way
  const mx = (CENTER.x + p.x) / 2;
  const my = (CENTER.y + p.y) / 2;
  g.appendChild(svg("rect", {
    x: mx - 18, y: my - 10, width: 36, height: 14, rx: 3,
    fill: "rgba(0,0,0,0.55)", stroke: "rgba(245,90,200,0.4)",
  }));
  g.appendChild(svg("text", {
    x: mx, y: my,
    "text-anchor": "middle", "font-size": 9, "font-weight": 700,
    fill: "#f55ac8", "font-family": "system-ui",
  })).textContent = `${angle.distance_m.toFixed(1)} m`;

  // Subject pin
  g.appendChild(svg("circle", { cx: p.x, cy: p.y, r: 9,
    fill: "rgba(245,90,200,0.18)", stroke: "#f55ac8", "stroke-width": 2 }));
  g.appendChild(svg("circle", { cx: p.x, cy: p.y, r: 3, fill: "#f55ac8" }));

  if (label) {
    g.appendChild(svg("text", {
      x: p.x, y: p.y + 22,
      "text-anchor": "middle", "font-size": 9, "font-weight": 700,
      fill: "#f55ac8", "font-family": "system-ui",
    })).textContent = label;
  }

  // Heading badge near the top of the rose
  const top = project(angle.azimuth_deg, 4);
  g.appendChild(svg("rect", {
    x: top.x - 22, y: top.y - 9, width: 44, height: 16, rx: 8,
    fill: "rgba(91,156,255,0.18)", stroke: "rgba(91,156,255,0.45)",
  }));
  g.appendChild(svg("text", {
    x: top.x, y: top.y + 2,
    "text-anchor": "middle", "font-size": 9, "font-weight": 800,
    fill: "#5b9cff", "font-family": "system-ui",
  })).textContent = `${Math.round(angle.azimuth_deg)}°`;

  return g;
}

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

/**
 * @param {{azimuth_deg, pitch_deg, distance_m, height_hint}} angle
 * @param {{label?: string}} options
 */
export function renderShotMinimap(angle, options = {}) {
  const root = svg("svg", {
    viewBox: `0 0 ${STAGE.w} ${STAGE.h}`,
    class: "minimap-svg",
    width: "100%",
    role: "img",
    "aria-label": `Shooting position map at azimuth ${angle.azimuth_deg}°`,
  });

  // Background
  root.appendChild(svg("rect", {
    x: 0, y: 0, width: STAGE.w, height: STAGE.h,
    fill: "rgba(255,255,255,0.02)",
  }));

  root.appendChild(ring(1, "1m"));
  root.appendChild(ring(2, "2m"));
  root.appendChild(ring(4, "4m"));
  root.appendChild(compass());
  root.appendChild(camera());
  root.appendChild(subject(angle, options.label));

  // Title strip
  const head = svg("g", { transform: "translate(8,8)" });
  head.appendChild(svg("rect", {
    x: 0, y: 0, width: 80, height: 18, rx: 9,
    fill: "rgba(0,0,0,0.4)", stroke: "rgba(255,255,255,0.12)",
  }));
  head.appendChild(svg("text", {
    x: 40, y: 12.5, "text-anchor": "middle",
    "font-size": 10, "font-weight": 700,
    fill: "rgba(255,255,255,0.7)", "font-family": "system-ui",
  })).textContent = "俯视引导图";
  root.appendChild(head);

  // Height hint badge
  if (angle.height_hint) {
    const h = svg("g", { transform: `translate(${STAGE.w - 70}, 8)` });
    h.appendChild(svg("rect", {
      x: 0, y: 0, width: 62, height: 18, rx: 9,
      fill: "rgba(69,200,156,0.15)", stroke: "rgba(69,200,156,0.4)",
    }));
    h.appendChild(svg("text", {
      x: 31, y: 12.5, "text-anchor": "middle",
      "font-size": 10, "font-weight": 700,
      fill: "#45c89c", "font-family": "system-ui",
    })).textContent = heightLabel(angle.height_hint);
    root.appendChild(h);
  }

  return root;
}

function heightLabel(hint) {
  return ({
    low: "低机位",
    eye_level: "平视",
    high: "高机位",
    overhead: "俯拍",
  })[hint] || hint;
}
