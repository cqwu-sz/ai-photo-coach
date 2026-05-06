/**
 * Procedural face / expression renderer.
 *
 * Draws onto a 256x256 canvas that's used as the diffuse texture of the
 * face plate sitting on the front of the avatar's head. The avatar
 * builder calls renderExpressionTexture() once on creation (neutral),
 * then again whenever pose_presets switches expression.
 *
 * 5 supported expressions, mapped from AI rationale keywords by
 * pose_presets.classifyExpression():
 *
 *   neutral   — calm, default
 *   joy       — open smile, eyes slightly closed
 *   smirk     — closed-mouth smile (for "抿嘴微笑")
 *   surprised — wide eyes, small "o" mouth
 *   pensive   — slight frown, lowered brow (for "认真"/"沉思")
 */

/**
 * @param {HTMLCanvasElement} canvas
 * @param {string} expr
 * @param {object} style                avatar style (skin tone, hair color etc.)
 */
export function renderExpressionTexture(canvas, expr, style) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  // Anchors in normalized 0..1 face-space; convert to pixels at the end.
  const cx = W * 0.5;
  const eyeY = H * 0.42;
  const mouthY = H * 0.72;
  const eyeDX = W * 0.18;

  const irisColor = pickIrisColor(style);
  const browColor = darken(style.hairColor || "#222", 0.6);

  // Eyes
  drawEyes(ctx, cx, eyeY, eyeDX, expr, irisColor);

  // Brows
  drawBrows(ctx, cx, eyeY - H * 0.11, eyeDX, expr, browColor);

  // Mouth
  drawMouth(ctx, cx, mouthY, expr);

  // Light blush (always on — anime styling)
  drawBlush(ctx, cx, eyeY + H * 0.06, eyeDX);

  // Tiny nose hint
  drawNose(ctx, cx, eyeY + H * 0.13);
}

// ---------------------------------------------------------------------------

function drawEyes(ctx, cx, y, dx, expr, iris) {
  const sclera = "#ffffff";
  const lash = "#1c1c24";
  const pupil = "#0d0d12";

  for (const sgn of [-1, 1]) {
    const x = cx + sgn * dx;
    if (expr === "joy") {
      // Closed happy arcs ‿
      ctx.strokeStyle = lash;
      ctx.lineWidth = 4.5;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.arc(x, y + 6, 16, Math.PI * 1.05, Math.PI * 1.95, true);
      ctx.stroke();
      continue;
    }
    if (expr === "pensive") {
      // Half-lidded
      drawEyeBall(ctx, x, y + 4, 16, 9, sclera, lash, iris, pupil);
      // Heavy upper lid
      ctx.fillStyle = lash;
      ctx.beginPath();
      ctx.ellipse(x, y - 2, 18, 7, 0, 0, Math.PI, false);
      ctx.fill();
      continue;
    }
    if (expr === "surprised") {
      drawEyeBall(ctx, x, y, 18, 18, sclera, lash, iris, pupil, 0.8);
      continue;
    }
    if (expr === "smirk") {
      // Slightly squinted, asymmetric (right side ↑)
      const sq = sgn === -1 ? 12 : 10;
      drawEyeBall(ctx, x, y, 16, sq, sclera, lash, iris, pupil);
      continue;
    }
    // neutral
    drawEyeBall(ctx, x, y, 16, 14, sclera, lash, iris, pupil);
  }
}

function drawEyeBall(ctx, x, y, w, h, sclera, lash, iris, pupil, irisFill = 0.65) {
  // White
  ctx.fillStyle = sclera;
  ctx.beginPath();
  ctx.ellipse(x, y, w, h, 0, 0, Math.PI * 2);
  ctx.fill();
  // Iris (large, anime-stylised)
  ctx.fillStyle = iris;
  ctx.beginPath();
  ctx.ellipse(x, y, w * irisFill, h * 0.85, 0, 0, Math.PI * 2);
  ctx.fill();
  // Pupil
  ctx.fillStyle = pupil;
  ctx.beginPath();
  ctx.ellipse(x, y, w * 0.28, h * 0.35, 0, 0, Math.PI * 2);
  ctx.fill();
  // Catchlight
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.ellipse(x - w * 0.2, y - h * 0.3, w * 0.13, h * 0.13, 0, 0, Math.PI * 2);
  ctx.fill();
  // Lash line on top
  ctx.strokeStyle = lash;
  ctx.lineWidth = 3.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.ellipse(x, y, w + 1, h + 1, 0, Math.PI, Math.PI * 2);
  ctx.stroke();
}

function drawBrows(ctx, cx, y, dx, expr, color) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 5;
  ctx.lineCap = "round";

  for (const sgn of [-1, 1]) {
    const x = cx + sgn * dx;
    const len = 18;
    let start, end;
    if (expr === "pensive") {
      // Lowered, inner edge tilted down  \  /
      start = { x: x - sgn * len, y: y };
      end = { x: x + sgn * len, y: y - 6 };
    } else if (expr === "surprised") {
      // Raised arches
      start = { x: x - len, y: y };
      end = { x: x + len, y: y };
      ctx.beginPath();
      ctx.moveTo(start.x, start.y + 4);
      ctx.quadraticCurveTo(x, y - 8, end.x, end.y + 4);
      ctx.stroke();
      continue;
    } else if (expr === "joy") {
      // Slight upward arc
      ctx.beginPath();
      ctx.moveTo(x - len, y + 2);
      ctx.quadraticCurveTo(x, y - 4, x + len, y + 2);
      ctx.stroke();
      continue;
    } else {
      // neutral / smirk
      start = { x: x - len, y: y + 2 };
      end = { x: x + len, y: y + 2 };
    }
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
  }
}

function drawMouth(ctx, cx, y, expr) {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  if (expr === "joy") {
    // Open smile with teeth highlight
    ctx.fillStyle = "#3a1a25";
    ctx.beginPath();
    ctx.moveTo(cx - 22, y);
    ctx.quadraticCurveTo(cx, y + 22, cx + 22, y);
    ctx.quadraticCurveTo(cx, y + 4, cx - 22, y);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(cx - 16, y + 2, 32, 4);
    return;
  }
  if (expr === "smirk") {
    // Closed asymmetric smile: right side higher
    ctx.strokeStyle = "#a04050";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(cx - 18, y + 2);
    ctx.quadraticCurveTo(cx + 3, y + 8, cx + 18, y - 6);
    ctx.stroke();
    return;
  }
  if (expr === "surprised") {
    // Small "O" mouth
    ctx.fillStyle = "#3a1a25";
    ctx.beginPath();
    ctx.ellipse(cx, y + 2, 7, 9, 0, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  if (expr === "pensive") {
    // Slight frown
    ctx.strokeStyle = "#a04050";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(cx - 16, y);
    ctx.quadraticCurveTo(cx, y - 6, cx + 16, y);
    ctx.stroke();
    return;
  }
  // neutral
  ctx.strokeStyle = "#a04050";
  ctx.lineWidth = 4;
  ctx.beginPath();
  ctx.moveTo(cx - 14, y);
  ctx.lineTo(cx + 14, y);
  ctx.stroke();
}

function drawBlush(ctx, cx, y, dx) {
  ctx.fillStyle = "rgba(255, 130, 150, 0.45)";
  for (const sgn of [-1, 1]) {
    ctx.beginPath();
    ctx.ellipse(cx + sgn * dx * 1.05, y, 16, 8, 0, 0, Math.PI * 2);
    ctx.fill();
  }
}

function drawNose(ctx, cx, y) {
  ctx.strokeStyle = "rgba(120, 60, 70, 0.45)";
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(cx, y - 4);
  ctx.lineTo(cx + 2, y + 4);
  ctx.stroke();
}

// ---------------------------------------------------------------------------

function pickIrisColor(style) {
  // Match iris loosely to hair color but a touch lighter.
  if (!style.hairColor) return "#3a5a7a";
  return darken(style.hairColor, -0.1);
}

function darken(hex, amount) {
  // amount > 0 darkens, < 0 lightens
  const c = hex.replace("#", "");
  const r = parseInt(c.substring(0, 2), 16);
  const g = parseInt(c.substring(2, 4), 16);
  const b = parseInt(c.substring(4, 6), 16);
  const f = (v) => Math.max(0, Math.min(255, Math.round(v - v * amount)));
  return `rgb(${f(r)}, ${f(g)}, ${f(b)})`;
}
