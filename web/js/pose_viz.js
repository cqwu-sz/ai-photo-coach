/**
 * Render an SVG stick-figure visualization of a PoseSuggestion.
 *
 * Inputs:
 *   pose: {
 *     person_count, layout, persons: [{role, stance, upper_body, hands, gaze, ...}]
 *   }
 *
 * Outputs an inline SVG showing 1..4 figures in the correct layout, each
 * with a stance preset inferred from natural-language fields.
 *
 * Why we infer instead of asking the LLM for explicit pose codes:
 *   - The LLM already produces rich Chinese descriptions (stance="半蹲...").
 *   - Asking for an extra structured field would lower diversity and cost
 *     more tokens for marginal gain.
 *   - Inference is good enough for a 200x200 visual hint.
 */

const STAGE = { w: 280, h: 200 };
const FIGURE = { w: 32, h: 80 };

// ---------------------------------------------------------------------------
// Layout positions: world-space (xRatio, yRatio) per person, where x∈[0,1]
// is left-to-right and y∈[0,1] is back-to-front (1 = closer to viewer).
// ---------------------------------------------------------------------------
const LAYOUTS = {
  single: [
    [0.5, 0.6],
  ],
  side_by_side: [
    [0.36, 0.6], [0.64, 0.6],
  ],
  high_low_offset: [
    // person_a standing slightly back and left, person_b sitting/lower right.
    [0.4, 0.5], [0.6, 0.7],
  ],
  triangle: [
    [0.5, 0.75], [0.32, 0.55], [0.68, 0.55],
  ],
  line: [
    [0.2, 0.6], [0.4, 0.6], [0.6, 0.6], [0.8, 0.6],
  ],
  cluster: [
    [0.5, 0.7], [0.32, 0.55], [0.68, 0.55], [0.5, 0.45],
  ],
  diagonal: [
    [0.25, 0.4], [0.5, 0.55], [0.75, 0.7], [0.85, 0.85],
  ],
  v_formation: [
    [0.5, 0.5], [0.3, 0.65], [0.7, 0.65], [0.15, 0.8],
  ],
  circle: [
    [0.5, 0.45], [0.7, 0.6], [0.5, 0.78], [0.3, 0.6],
  ],
  custom: [
    [0.5, 0.6],
  ],
};

// ---------------------------------------------------------------------------
// Stance presets: each is a parametric description of arms/legs in the
// stick-figure space (offset relative to the figure's center). All stances
// share the same head and torso geometry; only limbs change.
// ---------------------------------------------------------------------------

function classifyStance(person) {
  const text = [person.stance, person.upper_body, person.position_hint]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  if (/sit|坐|半蹲|蹲|crouch|squat/.test(text)) return "sitting";
  if (/walk|跑|run|stride|漫步|走|jog/.test(text)) return "walking";
  if (/jump|leap|跳|mid-?air|跃起/.test(text)) return "jumping";
  if (/lying|lie|躺|recl/.test(text)) return "lying";
  if (/lean|靠|倚/.test(text)) return "leaning";
  return "standing";
}

function classifyHands(person) {
  const text = (person.hands || "").toLowerCase();
  if (/pocket|插裤兜|插兜/.test(text)) return "pocket";
  if (/cross|交叠|交握|抱胸|抱怀/.test(text)) return "crossed";
  if (/hold.*hand|牵手|holding hand/.test(text)) return "holding";
  if (/shoulder|搭肩|搭在|肩膀/.test(text)) return "on_shoulder";
  if (/raise|wave|raised|举/.test(text)) return "raised";
  if (/hair|抚发|捋发|入鬓/.test(text)) return "to_hair";
  return "natural";
}

// Build the limb geometry for one figure. Returns an array of <line> segments
// in figure-local coords (origin = figure center, +y down).
function limbsFor(stance, hands, role, isLeftSide) {
  const seg = (x1, y1, x2, y2) => ({ x1, y1, x2, y2 });
  const arms = [];
  const legs = [];

  // Default arms
  if (hands === "pocket") {
    arms.push(seg(0, -10, -8, 6));
    arms.push(seg(0, -10, 8, 6));
  } else if (hands === "crossed") {
    arms.push(seg(0, -10, -10, -2));
    arms.push(seg(0, -10, 10, -2));
    arms.push(seg(-10, -2, 8, 0));
    arms.push(seg(10, -2, -8, 0));
  } else if (hands === "raised") {
    arms.push(seg(0, -10, -14, -22));
    arms.push(seg(0, -10, 14, -22));
  } else if (hands === "on_shoulder") {
    arms.push(seg(0, -10, isLeftSide ? 16 : -16, -16));
    arms.push(seg(0, -10, isLeftSide ? -10 : 10, 8));
  } else if (hands === "holding") {
    arms.push(seg(0, -10, isLeftSide ? 14 : -14, -2));
    arms.push(seg(0, -10, isLeftSide ? -8 : 8, 8));
  } else if (hands === "to_hair") {
    arms.push(seg(0, -10, -6, -28));
    arms.push(seg(0, -10, 9, 6));
  } else {
    arms.push(seg(0, -10, -10, 8));
    arms.push(seg(0, -10, 10, 8));
  }

  // Legs
  if (stance === "sitting") {
    legs.push(seg(0, 8, -10, 16));
    legs.push(seg(-10, 16, -2, 28));
    legs.push(seg(0, 8, 10, 16));
    legs.push(seg(10, 16, 18, 28));
  } else if (stance === "walking") {
    legs.push(seg(0, 8, -8, 30));
    legs.push(seg(0, 8, 10, 28));
  } else if (stance === "jumping") {
    legs.push(seg(0, 6, -10, 18));
    legs.push(seg(0, 6, 10, 16));
  } else if (stance === "lying") {
    legs.push(seg(0, 6, 22, 6));
  } else if (stance === "leaning") {
    legs.push(seg(0, 8, -10, 30));
    legs.push(seg(0, 8, 4, 30));
  } else {
    legs.push(seg(0, 8, -7, 30));
    legs.push(seg(0, 8, 7, 30));
  }

  return [...arms, ...legs];
}

function torsoFor(stance) {
  if (stance === "sitting") return { x: 0, y: -10, w: 0, h: 18 };
  if (stance === "lying") return { x: 0, y: 0, w: 24, h: 0 };
  return { x: 0, y: -10, w: 0, h: 18 };
}

function headFor(stance) {
  if (stance === "lying") return { cx: -16, cy: 0, r: 6 };
  if (stance === "sitting") return { cx: 0, cy: -18, r: 6 };
  return { cx: 0, cy: -18, r: 6 };
}

const PALETTE = [
  "#5b9cff", // a - accent blue
  "#f55ac8", // b - pink
  "#45c89c", // c - mint
  "#f0a040", // d - amber
];

function svgEl(tag, attrs = {}) {
  const ns = "http://www.w3.org/2000/svg";
  const el = document.createElementNS(ns, tag);
  for (const [k, v] of Object.entries(attrs)) {
    el.setAttribute(k, String(v));
  }
  return el;
}

function renderFigure(person, idx, totalCount) {
  const stance = classifyStance(person);
  const hands = classifyHands(person);
  const isLeftSide = idx < totalCount / 2;
  const color = PALETTE[idx % PALETTE.length];

  const g = svgEl("g", {
    class: "stick-figure",
    "data-role": person.role || `person_${idx + 1}`,
    "data-stance": stance,
  });

  // Soft shadow blob on the ground
  g.appendChild(
    svgEl("ellipse", {
      cx: 0, cy: 32, rx: 12, ry: 3,
      fill: "rgba(0,0,0,0.35)", filter: "blur(0.5px)",
    }),
  );

  const head = headFor(stance);
  g.appendChild(
    svgEl("circle", {
      cx: head.cx, cy: head.cy, r: head.r,
      fill: color, "fill-opacity": 0.18,
      stroke: color, "stroke-width": 2,
    }),
  );

  // Torso
  const t = torsoFor(stance);
  g.appendChild(
    svgEl("line", {
      x1: t.x, y1: t.y, x2: t.x + t.w, y2: t.y + t.h,
      stroke: color, "stroke-width": 3, "stroke-linecap": "round",
    }),
  );

  for (const seg of limbsFor(stance, hands, person.role, isLeftSide)) {
    g.appendChild(
      svgEl("line", {
        x1: seg.x1, y1: seg.y1, x2: seg.x2, y2: seg.y2,
        stroke: color, "stroke-width": 2.5, "stroke-linecap": "round",
      }),
    );
  }

  // Role badge
  const roleLabel = (person.role || `P${idx + 1}`).replace("person_", "").toUpperCase();
  g.appendChild(
    svgEl("text", {
      x: 0, y: 44, "text-anchor": "middle",
      "font-size": 9, "font-weight": 700,
      fill: color, "font-family": "system-ui",
    }),
  ).textContent = roleLabel;

  return g;
}

/**
 * Build the visualization SVG. Returns an SVGElement that can be appended
 * directly to a card.
 */
export function renderPoseSVG(pose, options = {}) {
  const layoutKey = pose.layout in LAYOUTS ? pose.layout : "single";
  const positions = LAYOUTS[layoutKey];
  const persons = pose.persons || [];
  const total = Math.min(persons.length, positions.length);

  const svg = svgEl("svg", {
    viewBox: `0 0 ${STAGE.w} ${STAGE.h}`,
    class: "pose-svg",
    width: "100%",
    height: options.height || "auto",
    role: "img",
    "aria-label": `${pose.person_count} 人 · ${pose.layout}`,
  });

  // Soft floor / ground hint
  const grad = svgEl("defs");
  const lg = svgEl("linearGradient", {
    id: "floor", x1: "0", x2: "0", y1: "0", y2: "1",
  });
  lg.appendChild(svgEl("stop", { offset: "0%", "stop-color": "rgba(255,255,255,0.02)" }));
  lg.appendChild(svgEl("stop", { offset: "100%", "stop-color": "rgba(91,156,255,0.10)" }));
  grad.appendChild(lg);
  svg.appendChild(grad);
  svg.appendChild(
    svgEl("rect", { x: 0, y: STAGE.h * 0.55, width: STAGE.w, height: STAGE.h * 0.45, fill: "url(#floor)" }),
  );

  // Camera marker bottom-center, just so the user reads the frame correctly.
  const cam = svgEl("g", { transform: `translate(${STAGE.w / 2}, ${STAGE.h - 6})` });
  cam.appendChild(svgEl("rect", {
    x: -10, y: -8, width: 20, height: 12, rx: 3, fill: "#222a35", stroke: "#5b9cff", "stroke-width": 1.4,
  }));
  cam.appendChild(svgEl("circle", { cx: 0, cy: -2, r: 3.2, fill: "#5b9cff" }));
  cam.appendChild(svgEl("text", {
    x: 0, y: 12, "text-anchor": "middle", "font-size": 8, fill: "rgba(255,255,255,0.55)", "font-family": "system-ui",
  })).textContent = "CAMERA";
  svg.appendChild(cam);

  // Place figures
  for (let i = 0; i < total; i++) {
    const [xR, yR] = positions[i];
    const cx = xR * STAGE.w;
    // Use a band [0.18..0.82] so figures stay within the stage vertically.
    const cy = (0.18 + 0.62 * yR) * STAGE.h;
    const fig = renderFigure(persons[i], i, total);
    fig.setAttribute("transform", `translate(${cx}, ${cy})`);
    svg.appendChild(fig);
  }

  // Layout label, top-left
  const tag = svgEl("g", { transform: "translate(8,12)" });
  const tagBg = svgEl("rect", {
    x: 0, y: 0, width: 100, height: 18, rx: 9,
    fill: "rgba(91,156,255,0.16)", stroke: "rgba(91,156,255,0.4)",
  });
  tag.appendChild(tagBg);
  const tagText = svgEl("text", {
    x: 50, y: 12.5, "text-anchor": "middle",
    "font-size": 10, "font-weight": 700, fill: "#5b9cff", "font-family": "system-ui",
  });
  tagText.textContent = `${pose.person_count}P · ${pose.layout}`;
  tag.appendChild(tagText);
  svg.appendChild(tag);

  return svg;
}
