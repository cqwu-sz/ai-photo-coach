/**
 * v7 Phase C — composition guide SVG overlay + parameter HUD.
 *
 * Sits absolutely-positioned on top of the 3D canvas. Renders the
 * composition rule the LLM picked (rule_of_thirds, golden_ratio,
 * leading_lines, …) so the user can SEE what AI is composing for —
 * not just read about it in a list of bullet points.
 *
 * Public API:
 *   const overlay = mountCompositionOverlay(host, { shot, sceneView });
 *   overlay.update({ shot });   // when the user pages to a new shot
 *   overlay.dispose();
 */

const NS = "http://www.w3.org/2000/svg";

/**
 * @param {HTMLElement} host  positioned-relative parent of the canvas
 * @param {{ shot: any, sceneView?: any }} opts
 */
export function mountCompositionOverlay(host, opts) {
  const layer = document.createElement("div");
  layer.className = "scene-3d-overlay";
  layer.style.cssText = `
    position: absolute; inset: 0;
    pointer-events: none;
    overflow: hidden;
  `;
  host.style.position = host.style.position || "relative";
  host.appendChild(layer);

  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 100 100");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.style.cssText = `
    width: 100%; height: 100%; display: block;
    opacity: 0.8;
  `;
  layer.appendChild(svg);

  const hud = document.createElement("div");
  hud.className = "scene-3d-hud";
  layer.appendChild(hud);

  function render(shot, sv) {
    // wipe both
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    hud.innerHTML = "";

    const rule = (shot?.composition?.primary || "rule_of_thirds").toLowerCase();
    drawCompositionGuide(svg, rule);
    renderHUD(hud, shot, sv);
  }
  render(opts.shot, opts.sceneView);

  return {
    update(newOpts) { render(newOpts.shot ?? opts.shot, newOpts.sceneView ?? opts.sceneView); },
    dispose() { layer.remove(); },
  };
}

// ---------------------------------------------------------------------------
// Composition guides
// ---------------------------------------------------------------------------

function drawCompositionGuide(svg, rule) {
  switch (rule) {
    case "rule_of_thirds":
    case "thirds":
    case "三分法":
      drawThirds(svg);
      break;
    case "golden_ratio":
    case "golden":
    case "黄金分割":
      drawGoldenRatio(svg);
      break;
    case "leading_lines":
    case "diagonal":
      drawLeadingLines(svg);
      break;
    case "centered":
    case "symmetry":
    case "中心":
      drawCentered(svg);
      break;
    case "frame_within_frame":
    case "frame":
      drawFrame(svg);
      break;
    default:
      drawThirds(svg);
  }
}

function makeLine(x1, y1, x2, y2, color = "rgba(255,255,255,0.55)", w = 0.18) {
  const l = document.createElementNS(NS, "line");
  l.setAttribute("x1", x1); l.setAttribute("y1", y1);
  l.setAttribute("x2", x2); l.setAttribute("y2", y2);
  l.setAttribute("stroke", color);
  l.setAttribute("stroke-width", String(w));
  l.setAttribute("stroke-dasharray", "0.8 1.0");
  return l;
}

function drawThirds(svg) {
  // 4 lines at 33.33% / 66.66%
  const c1 = "rgba(255, 215, 120, 0.78)";
  svg.appendChild(makeLine(33.33, 0, 33.33, 100, c1, 0.20));
  svg.appendChild(makeLine(66.66, 0, 66.66, 100, c1, 0.20));
  svg.appendChild(makeLine(0, 33.33, 100, 33.33, c1, 0.20));
  svg.appendChild(makeLine(0, 66.66, 100, 66.66, c1, 0.20));
  // 4 power-points (subject hot spots)
  for (const [cx, cy] of [[33.33, 33.33], [66.66, 33.33], [33.33, 66.66], [66.66, 66.66]]) {
    const p = document.createElementNS(NS, "circle");
    p.setAttribute("cx", cx); p.setAttribute("cy", cy);
    p.setAttribute("r", 0.7);
    p.setAttribute("fill", c1);
    p.setAttribute("opacity", 0.65);
    svg.appendChild(p);
  }
  addLabel(svg, "三分构图", 2, 6);
}

function drawGoldenRatio(svg) {
  const PHI = 0.618;
  const c = "rgba(255, 215, 120, 0.78)";
  svg.appendChild(makeLine(PHI * 100, 0, PHI * 100, 100, c, 0.22));
  svg.appendChild(makeLine((1 - PHI) * 100, 0, (1 - PHI) * 100, 100, c, 0.22));
  svg.appendChild(makeLine(0, PHI * 100, 100, PHI * 100, c, 0.22));
  svg.appendChild(makeLine(0, (1 - PHI) * 100, 100, (1 - PHI) * 100, c, 0.22));
  // Spiral suggestion (very rough — single arc)
  const spiral = document.createElementNS(NS, "path");
  spiral.setAttribute(
    "d",
    "M 100 0 A 100 100 0 0 0 0 100",
  );
  spiral.setAttribute("fill", "none");
  spiral.setAttribute("stroke", c);
  spiral.setAttribute("stroke-width", "0.18");
  spiral.setAttribute("opacity", "0.4");
  svg.appendChild(spiral);
  addLabel(svg, "黄金分割", 2, 6);
}

function drawLeadingLines(svg) {
  const c = "rgba(120, 220, 255, 0.78)";
  svg.appendChild(makeLine(0, 100, 60, 35, c, 0.28));
  svg.appendChild(makeLine(100, 100, 40, 35, c, 0.28));
  svg.appendChild(makeLine(0, 70, 100, 30, c, 0.18));
  addLabel(svg, "引导线", 2, 6);
}

function drawCentered(svg) {
  const c = "rgba(255, 215, 120, 0.6)";
  svg.appendChild(makeLine(50, 0, 50, 100, c, 0.18));
  svg.appendChild(makeLine(0, 50, 100, 50, c, 0.18));
  // Center frame
  const r = document.createElementNS(NS, "rect");
  r.setAttribute("x", 35); r.setAttribute("y", 35);
  r.setAttribute("width", 30); r.setAttribute("height", 30);
  r.setAttribute("fill", "none");
  r.setAttribute("stroke", c);
  r.setAttribute("stroke-width", "0.18");
  r.setAttribute("stroke-dasharray", "1 1.2");
  svg.appendChild(r);
  addLabel(svg, "中心构图", 2, 6);
}

function drawFrame(svg) {
  const c = "rgba(255, 215, 120, 0.78)";
  // Inset frame as if a doorway/window
  const r = document.createElementNS(NS, "rect");
  r.setAttribute("x", 12); r.setAttribute("y", 14);
  r.setAttribute("width", 76); r.setAttribute("height", 72);
  r.setAttribute("fill", "none");
  r.setAttribute("stroke", c);
  r.setAttribute("stroke-width", "0.32");
  r.setAttribute("stroke-dasharray", "2 1.5");
  svg.appendChild(r);
  addLabel(svg, "框架式", 2, 6);
}

function addLabel(svg, text, x, y) {
  // Tiny corner label so the user knows what rule is overlaid.
  const t = document.createElementNS(NS, "text");
  t.setAttribute("x", x);
  t.setAttribute("y", y);
  t.setAttribute("fill", "rgba(255, 215, 120, 0.95)");
  t.setAttribute("font-size", "3.0");
  t.setAttribute("font-weight", "700");
  t.setAttribute("style", "font-family: system-ui, -apple-system, sans-serif; letter-spacing: 0.08em;");
  t.textContent = text;
  svg.appendChild(t);
}

// ---------------------------------------------------------------------------
// HUD chip — top-right floating parameter readout
// ---------------------------------------------------------------------------

function renderHUD(hud, shot, sv) {
  hud.style.cssText = `
    position: absolute; top: 12px; right: 12px;
    display: flex; flex-direction: column; gap: 6px;
    align-items: flex-end;
    pointer-events: none;
  `;

  const camera = shot?.camera || {};
  const focal = camera.focal_length_mm ?? sv?.focalMm;
  const aperture = camera.aperture ?? (sv?.aperture ? `f/${Number(sv.aperture).toFixed(1)}` : null);
  const shutter = camera.shutter || camera.shutter_speed;
  const iso = camera.iso;

  const cameraChip = makeChip([
    focal != null ? `${Math.round(focal)}mm` : null,
    aperture || null,
    shutter ? formatShutter(shutter) : null,
    iso != null ? `ISO ${iso}` : null,
  ].filter(Boolean).join("  ·  "), "primary");
  if (cameraChip) hud.appendChild(cameraChip);

  const angle = shot?.angle || {};
  const az = angle.azimuth_deg ?? sv?.cameraAzimuthDeg;
  const dist = angle.distance_m ?? sv?.cameraDistanceM;
  const pitch = angle.pitch_deg ?? sv?.cameraPitchDeg;
  const angleChip = makeChip([
    az != null ? `方位 ${Math.round(az)}°` : null,
    dist != null ? `距离 ${Number(dist).toFixed(1)}m` : null,
    pitch != null ? `俯仰 ${pitch >= 0 ? "+" : ""}${Math.round(pitch)}°` : null,
  ].filter(Boolean).join("  ·  "));
  if (angleChip) hud.appendChild(angleChip);

  if (shot?.composition?.primary) {
    const compChip = makeChip(`构图 · ${prettyCompName(shot.composition.primary)}`, "soft");
    if (compChip) hud.appendChild(compChip);
  }
}

function makeChip(text, kind = "neutral") {
  if (!text) return null;
  const el = document.createElement("div");
  el.className = `scene-3d-hud-chip scene-3d-hud-chip--${kind}`;
  el.textContent = text;
  el.style.cssText = `
    background: ${chipBg(kind)};
    color: ${chipFg(kind)};
    padding: 6px 12px;
    border-radius: 999px;
    font-size: 12.5px;
    font-weight: 600;
    letter-spacing: 0.02em;
    backdrop-filter: blur(10px);
    -webkit-backdrop-filter: blur(10px);
    border: 1px solid ${chipBorder(kind)};
    box-shadow: 0 4px 16px rgba(0,0,0,0.32);
    white-space: nowrap;
  `;
  return el;
}

function chipBg(kind) {
  switch (kind) {
    case "primary": return "rgba(91, 156, 255, 0.32)";
    case "soft":    return "rgba(255, 215, 120, 0.18)";
    default:        return "rgba(20, 22, 28, 0.55)";
  }
}
function chipFg(kind) {
  switch (kind) {
    case "primary": return "#ffffff";
    case "soft":    return "#ffd778";
    default:        return "#dde2ec";
  }
}
function chipBorder(kind) {
  switch (kind) {
    case "primary": return "rgba(91, 156, 255, 0.55)";
    case "soft":    return "rgba(255, 215, 120, 0.40)";
    default:        return "rgba(255, 255, 255, 0.10)";
  }
}

function formatShutter(s) {
  if (typeof s === "string") return s;
  if (typeof s !== "number" || !Number.isFinite(s) || s <= 0) return "";
  if (s >= 1) return `${s.toFixed(1)}s`;
  return `1/${Math.round(1 / s)}`;
}

function prettyCompName(rule) {
  const map = {
    rule_of_thirds: "三分法",
    thirds: "三分法",
    golden_ratio: "黄金分割",
    golden: "黄金分割",
    leading_lines: "引导线",
    diagonal: "对角线",
    centered: "中心",
    symmetry: "对称",
    frame_within_frame: "框架",
    frame: "框架",
  };
  return map[String(rule).toLowerCase()] || rule;
}
