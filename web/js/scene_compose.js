/**
 * Scene mock-up composer — turns "azimuth 90° distance 2.5m 50mm
 * f/2.0, person high_low_offset" from text into a real picture by:
 *
 *   1. Painting the user's chosen environment frame as the backdrop
 *      (the keyframe whose azimuth is closest to the recommended one).
 *   2. Overlaying the composition's primary guide (rule-of-thirds grid,
 *      leading line, diagonal, golden-ratio spiral, etc.) so people
 *      *see* "left third" instead of having to imagine it.
 *   3. Stamping a ghost silhouette of the recommended pose on the
 *      backdrop, sized & positioned by distance + height_hint.
 *   4. Pinning corner chips with focal length / aperture / pose layout
 *      so the photo metadata sits on the photo itself.
 *
 * The output is a `<div class="scene-compose">` containing an absolutely
 * positioned <img> + <svg> overlay. Drop it into any card.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

function svg(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

function el(tag, attrs = {}, children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v);
  }
  if (Array.isArray(children)) {
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
  } else if (typeof children === "string") {
    node.textContent = children;
  } else if (children) {
    node.appendChild(children);
  }
  return node;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * @param {string|null} backdropSrc - URL or data URL of the environment frame.
 * @param {object} shot - ShotRecommendation
 * @param {object} [opt]
 * @param {number} [opt.idx]
 * @returns {HTMLElement}
 */
export function renderSceneCompose(backdropSrc, shot, opt = {}) {
  const wrap = el("div", { class: "scene-compose" });

  // Backdrop
  if (backdropSrc) {
    const img = el("img", {
      class: "scene-bg",
      src: backdropSrc,
      alt: shot.title || "scene",
    });
    img.onerror = () => {
      img.style.background =
        "linear-gradient(135deg, #1a2238 0%, #2a1a38 100%)";
      img.removeAttribute("src");
    };
    wrap.appendChild(img);
  } else {
    const ph = el("div", { class: "scene-bg scene-bg-fallback" });
    ph.appendChild(el("span", {}, "环境帧不可用 — 显示纯色背景"));
    wrap.appendChild(ph);
  }

  const overlay = svg("svg", {
    class: "scene-overlay",
    viewBox: "0 0 100 100",
    preserveAspectRatio: "none",
  });
  drawCompositionGuide(overlay, shot.composition);
  drawSilhouette(overlay, shot);
  wrap.appendChild(overlay);

  // Corner chips: focal/aperture/shutter/iso, pose layout
  const chips = el("div", { class: "scene-chips" });
  const cam = shot.camera || {};
  [
    `${Math.round(cam.focal_length_mm || 0)}mm`,
    cam.aperture,
    cam.shutter,
    `ISO ${cam.iso}`,
  ]
    .filter(Boolean)
    .forEach((t) => chips.appendChild(el("span", { class: "scene-chip" }, t)));
  wrap.appendChild(chips);

  // Bottom-left azimuth + distance hint
  const aim = el("div", { class: "scene-aim" });
  aim.appendChild(
    el(
      "span",
      { class: "scene-aim-az" },
      `${Math.round(shot.angle.azimuth_deg)}°`,
    ),
  );
  aim.appendChild(
    el(
      "span",
      { class: "scene-aim-dist" },
      `${shot.angle.distance_m.toFixed(1)} m`,
    ),
  );
  wrap.appendChild(aim);

  // Top-left shot index ribbon
  if (opt.idx != null) {
    wrap.appendChild(
      el("div", { class: "scene-ribbon" }, `SHOT ${opt.idx + 1}`),
    );
  }
  return wrap;
}

// Pick the keyframe whose azimuth is closest to the shot direction. Honor
// shot.representative_frame_index if the LLM gave one.
export function pickBackdrop(frames, shot) {
  if (!frames || !frames.length) return null;
  const idx = shot.representative_frame_index;
  if (Number.isInteger(idx) && idx >= 0 && idx < frames.length) {
    return frames[idx];
  }
  const target = shot.angle.azimuth_deg;
  let best = frames[0];
  let bestDelta = Infinity;
  for (const f of frames) {
    if (f.azimuthDeg == null) continue;
    const d = circDelta(f.azimuthDeg, target);
    if (d < bestDelta) {
      bestDelta = d;
      best = f;
    }
  }
  return best;
}

function circDelta(a, b) {
  return Math.abs(((a - b + 540) % 360) - 180);
}

// ---------------------------------------------------------------------------
// Composition guides
// ---------------------------------------------------------------------------

function drawCompositionGuide(svgEl, composition) {
  if (!composition) return;
  const primary = composition.primary;
  const colour = "rgba(255,255,255,0.55)";
  const accent = "rgba(91,156,255,0.85)";

  if (primary === "rule_of_thirds" || primary === "golden_ratio") {
    const stops = primary === "golden_ratio" ? [38.2, 61.8] : [33.33, 66.67];
    for (const x of stops) line(svgEl, x, 0, x, 100, colour, 0.35);
    for (const y of stops) line(svgEl, 0, y, 100, y, colour, 0.35);
    // Pinpoint the recommended intersection (left-third by default)
    circle(svgEl, stops[0], stops[0], 1.6, accent);
  } else if (primary === "centered" || primary === "symmetry") {
    line(svgEl, 50, 0, 50, 100, accent, 0.6);
    line(svgEl, 0, 50, 100, 50, accent, 0.4);
  } else if (primary === "leading_line") {
    line(svgEl, 5, 95, 60, 35, accent, 0.9);
    line(svgEl, 95, 95, 65, 38, accent, 0.6);
  } else if (primary === "diagonal") {
    line(svgEl, 0, 100, 100, 0, accent, 0.9);
    line(svgEl, 0, 0, 100, 100, colour, 0.3);
  } else if (primary === "frame_within_frame") {
    rect(svgEl, 12, 12, 76, 76, accent, 0.6);
    rect(svgEl, 22, 22, 56, 56, colour, 0.3);
  } else if (primary === "negative_space") {
    rect(svgEl, 65, 25, 30, 50, accent, 0.5);
  } else {
    // Generic thirds grid as a safe fallback
    for (const x of [33.33, 66.67]) line(svgEl, x, 0, x, 100, colour, 0.3);
    for (const y of [33.33, 66.67]) line(svgEl, 0, y, 100, y, colour, 0.3);
  }
}

function line(target, x1, y1, x2, y2, stroke, opacity = 0.45) {
  const ln = svg("line", {
    x1, y1, x2, y2,
    stroke,
    "stroke-width": 0.35,
    "stroke-dasharray": "1.2 1.4",
    "stroke-linecap": "round",
    opacity,
  });
  target.appendChild(ln);
}

function circle(target, cx, cy, r, fill) {
  target.appendChild(
    svg("circle", { cx, cy, r, fill, "fill-opacity": 0.85 }),
  );
}

function rect(target, x, y, w, h, stroke, opacity = 0.5) {
  target.appendChild(
    svg("rect", {
      x, y, width: w, height: h,
      fill: "none",
      stroke,
      "stroke-width": 0.4,
      "stroke-dasharray": "1.2 1.4",
      opacity,
    }),
  );
}

// ---------------------------------------------------------------------------
// Silhouette: where the model should stand inside the picture.
// The mapping is intentionally simple:
//   - For rule-of-thirds: anchor on the chosen vertical third stop.
//   - Centered/symmetry: middle of the frame.
//   - For other comps: bias to left-third (looks natural for reading order).
//   - Distance scales the figure size.
// ---------------------------------------------------------------------------

function drawSilhouette(svgEl, shot) {
  const angle = shot.angle || {};
  const composition = shot.composition || {};
  const pose = (shot.poses && shot.poses[0]) || {};
  const persons = pose.persons || [];

  // Vertical anchor based on height hint
  const yByHint = {
    low: 75,
    eye_level: 60,
    high: 50,
    overhead: 35,
  };
  const yCenter = yByHint[angle.height_hint] || 60;

  // Horizontal anchor by composition
  let xCenter = 33.3;
  if (composition.primary === "centered" || composition.primary === "symmetry")
    xCenter = 50;
  else if (composition.primary === "negative_space") xCenter = 80;
  else if (composition.primary === "diagonal") xCenter = 28;
  else if (composition.primary === "leading_line") xCenter = 38;
  else if (composition.primary === "golden_ratio") xCenter = 38.2;
  else if (composition.primary === "rule_of_thirds") xCenter = 33.3;

  const distance = clamp(angle.distance_m || 2.0, 0.8, 5.0);
  const figureSize = clamp(28 / distance, 7, 18);

  const layout = pose.layout || "single";
  const positions = layoutOffsets(layout, persons.length || 1, figureSize);

  positions.forEach((p, i) => {
    drawFigure(
      svgEl,
      xCenter + p.dx,
      yCenter + p.dy,
      figureSize,
      figureColor(i),
      personStanceFor(persons[i]),
    );
  });
}

function figureColor(i) {
  return ["#5b9cff", "#f55ac8", "#45c89c", "#f0a040"][i % 4];
}

function layoutOffsets(layout, count, fSize) {
  // Returns offsets in viewBox units (each unit ~ 1% of stage width).
  const s = fSize * 0.4;
  const map = {
    single: [{ dx: 0, dy: 0 }],
    side_by_side: [
      { dx: -s * 1.2, dy: 0 },
      { dx: s * 1.2, dy: 0 },
    ],
    high_low_offset: [
      { dx: -s * 0.8, dy: -s * 0.2 },
      { dx: s * 0.9, dy: s * 0.55 },
    ],
    triangle: [
      { dx: 0, dy: s * 0.4 },
      { dx: -s * 1.5, dy: -s * 0.2 },
      { dx: s * 1.5, dy: -s * 0.2 },
    ],
    line: [
      { dx: -s * 2.4, dy: 0 },
      { dx: -s * 0.8, dy: 0 },
      { dx: s * 0.8, dy: 0 },
      { dx: s * 2.4, dy: 0 },
    ],
    cluster: [
      { dx: 0, dy: 0 },
      { dx: -s * 1.4, dy: -s * 0.2 },
      { dx: s * 1.4, dy: s * 0.3 },
      { dx: 0, dy: -s * 0.45 },
    ],
    diagonal: [
      { dx: -s * 1.8, dy: -s * 0.4 },
      { dx: 0, dy: s * 0.0 },
      { dx: s * 1.8, dy: s * 0.4 },
    ],
    v_formation: [
      { dx: 0, dy: -s * 0.3 },
      { dx: -s * 1.5, dy: s * 0.3 },
      { dx: s * 1.5, dy: s * 0.3 },
    ],
    circle: [
      { dx: 0, dy: -s * 0.7 },
      { dx: s * 1.0, dy: 0 },
      { dx: 0, dy: s * 0.7 },
      { dx: -s * 1.0, dy: 0 },
    ],
    custom: [{ dx: 0, dy: 0 }],
  };
  return (map[layout] || map.single).slice(0, count);
}

function personStanceFor(person) {
  const text = [(person && person.stance) || "", (person && person.upper_body) || ""]
    .join(" ")
    .toLowerCase();
  if (/sit|坐|半蹲|蹲|crouch|squat/.test(text)) return "sitting";
  if (/walk|跑|run|stride|漫步|走|jog/.test(text)) return "walking";
  if (/jump|leap|跳|mid-?air|跃起/.test(text)) return "jumping";
  if (/lying|lie|躺|recl/.test(text)) return "lying";
  return "standing";
}

function drawFigure(svgEl, cx, cy, size, color, stance) {
  const g = svg("g", {
    transform: `translate(${cx}, ${cy})`,
    class: "scene-figure",
    "data-stance": stance,
  });

  // Glow behind silhouette
  g.appendChild(
    svg("ellipse", {
      cx: 0, cy: 0,
      rx: size * 0.5, ry: size * 0.85,
      fill: color, "fill-opacity": 0.12,
    }),
  );

  const isSeated = stance === "sitting";
  const isLying = stance === "lying";

  if (isLying) {
    g.appendChild(svg("ellipse", {
      cx: -size * 0.5, cy: 0, rx: size * 0.18, ry: size * 0.18,
      fill: color, "fill-opacity": 0.55,
    }));
    g.appendChild(svg("rect", {
      x: -size * 0.3, y: -size * 0.06, width: size * 1.0, height: size * 0.12,
      rx: size * 0.06, fill: color, "fill-opacity": 0.55,
    }));
  } else {
    // Head
    g.appendChild(svg("circle", {
      cx: 0, cy: -size * 0.65,
      r: size * 0.18,
      fill: color, "fill-opacity": 0.6, stroke: color, "stroke-width": 0.3,
    }));
    // Torso
    const torsoH = isSeated ? size * 0.5 : size * 0.7;
    g.appendChild(svg("rect", {
      x: -size * 0.2, y: -size * 0.45,
      width: size * 0.4, height: torsoH,
      rx: size * 0.12,
      fill: color, "fill-opacity": 0.5,
    }));
    if (isSeated) {
      g.appendChild(svg("rect", {
        x: -size * 0.18, y: torsoH - size * 0.45,
        width: size * 0.65, height: size * 0.18,
        rx: size * 0.06,
        fill: color, "fill-opacity": 0.45,
      }));
    } else if (stance === "walking") {
      g.appendChild(svg("line", {
        x1: -size * 0.05, y1: torsoH - size * 0.45,
        x2: -size * 0.25, y2: torsoH - size * 0.05,
        stroke: color, "stroke-width": size * 0.07, "stroke-linecap": "round",
        "stroke-opacity": 0.6,
      }));
      g.appendChild(svg("line", {
        x1: size * 0.05, y1: torsoH - size * 0.45,
        x2: size * 0.30, y2: torsoH - size * 0.05,
        stroke: color, "stroke-width": size * 0.07, "stroke-linecap": "round",
        "stroke-opacity": 0.6,
      }));
    } else {
      g.appendChild(svg("line", {
        x1: -size * 0.10, y1: torsoH - size * 0.45,
        x2: -size * 0.20, y2: torsoH - size * 0.05,
        stroke: color, "stroke-width": size * 0.07, "stroke-linecap": "round",
        "stroke-opacity": 0.6,
      }));
      g.appendChild(svg("line", {
        x1: size * 0.10, y1: torsoH - size * 0.45,
        x2: size * 0.20, y2: torsoH - size * 0.05,
        stroke: color, "stroke-width": size * 0.07, "stroke-linecap": "round",
        "stroke-opacity": 0.6,
      }));
    }
  }

  // Footprint pad on the ground
  g.appendChild(svg("ellipse", {
    cx: 0, cy: isLying ? size * 0.15 : size * 0.45,
    rx: size * 0.35, ry: size * 0.05,
    fill: color, "fill-opacity": 0.6,
  }));

  svgEl.appendChild(g);
}

function clamp(v, lo, hi) {
  return Math.min(hi, Math.max(lo, v));
}
