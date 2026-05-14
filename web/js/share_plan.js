// Share / save the active shot plan as a portable image card.
//
// v9 UX polish #6 — the result page's primary CTA is "带去现场". Web
// users can't auto-apply parameters like iOS can, so the deliverable
// is a single image card they can pin to their phone or send to
// friends. This module renders that card on a hidden canvas and either
// (a) opens the native Share Sheet (iOS Safari / Android Chrome) or
// (b) downloads a PNG (everyone else).
//
// Zero deps: pure Canvas 2D. Keeps the bundle small and avoids the
// html2canvas tax (~150KB minified) which we'd pay even if the user
// never taps the button.

const CARD_W = 1080;
const CARD_H = 1920;
const PAD = 72;

const LIGHTING_LABEL = {
  golden_hour: "黄金时段",
  blue_hour: "蓝调时段",
  harsh_noon: "正午顶光",
  overcast: "阴天",
  shade: "阴影",
  indoor_warm: "室内暖光",
  indoor_cool: "室内冷光",
  low_light: "弱光",
  backlight: "逆光",
  mixed: "混合光",
};

const COMPOSITION_LABEL = {
  rule_of_thirds: "三分线",
  leading_line: "引导线",
  symmetry: "对称",
  frame_within_frame: "框中框",
  negative_space: "负空间",
  centered: "居中",
  diagonal: "对角线",
  golden_ratio: "黄金比例",
};

export async function shareOrDownloadPlan(shot, idx) {
  const canvas = document.createElement("canvas");
  canvas.width = CARD_W;
  canvas.height = CARD_H;
  const ctx = canvas.getContext("2d");
  drawCard(ctx, shot, idx);

  const blob = await new Promise((resolve) =>
    canvas.toBlob(resolve, "image/png", 0.92),
  );
  if (!blob) throw new Error("toBlob returned null");

  const fileName = `aphc_shot_${idx + 1}.png`;
  const file = new File([blob], fileName, { type: "image/png" });

  // Prefer the native Share Sheet on iOS / Android — it lets the user
  // save to Photos, AirDrop, or send to chat. Falls back to a download
  // on desktop or browsers without Web Share Level 2.
  if (
    typeof navigator !== "undefined" &&
    navigator.canShare &&
    navigator.canShare({ files: [file] })
  ) {
    try {
      await navigator.share({
        files: [file],
        title: `拾光 · 方案 #${idx + 1}`,
        text: "AI 取景者替你框好的方案，带去现场拍照参考",
      });
      return;
    } catch (e) {
      // User cancelled or share threw — fall through to download.
      if (e && e.name !== "AbortError") {
        console.warn("[share_plan] navigator.share failed, falling back", e);
      } else {
        return; // explicit cancel, don't double-trigger download
      }
    }
  }

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

function drawCard(ctx, shot, idx) {
  // Background — same warm dark palette as the app shell so the card
  // feels like a screenshot from the product, not a generic export.
  const grad = ctx.createLinearGradient(0, 0, 0, CARD_H);
  grad.addColorStop(0, "#0a0c18");
  grad.addColorStop(0.55, "#0e1024");
  grad.addColorStop(1, "#04050b");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, CARD_W, CARD_H);

  // Aurora accent — single warm glow at top-left so the card has a
  // visual anchor without needing imported imagery.
  const halo = ctx.createRadialGradient(280, 220, 0, 280, 220, 540);
  halo.addColorStop(0, "rgba(244, 184, 96, 0.32)");
  halo.addColorStop(1, "rgba(244, 184, 96, 0)");
  ctx.fillStyle = halo;
  ctx.fillRect(0, 0, CARD_W, CARD_H);

  let y = PAD;

  // Brand strip
  ctx.fillStyle = "rgba(245, 244, 238, 0.6)";
  ctx.font = "500 28px 'PingFang SC', -apple-system, system-ui, sans-serif";
  ctx.textBaseline = "top";
  ctx.fillText("拾光 · AI 取景者", PAD, y);
  y += 50;

  ctx.fillStyle = "#f5f4ee";
  ctx.font = "800 88px 'PingFang SC', -apple-system, system-ui, sans-serif";
  ctx.fillText(`方案 #${idx + 1}`, PAD, y);
  y += 110;

  if (shot.title) {
    ctx.fillStyle = "rgba(245, 244, 238, 0.82)";
    ctx.font = "600 42px 'PingFang SC', -apple-system, system-ui, sans-serif";
    y = wrapText(ctx, shot.title, PAD, y, CARD_W - PAD * 2, 54);
    y += 12;
  }

  // Coach brief
  if (shot.coach_brief) {
    ctx.fillStyle = "#f4b860";
    ctx.font = "600 36px 'PingFang SC', -apple-system, system-ui, sans-serif";
    y = wrapText(ctx, `"${shot.coach_brief}"`, PAD, y, CARD_W - PAD * 2, 50);
    y += 24;
  }

  // Camera dial — large 2×2 grid of the four numbers everyone wants.
  const cam = shot.camera || {};
  const focal = `${Math.round(cam.focal_length_mm || 0)}mm`;
  const ap = cam.aperture || "—";
  const sh = cam.shutter || "—";
  const iso = `ISO ${cam.iso || "—"}`;

  y += 16;
  drawDial2x2(ctx, PAD, y, CARD_W - PAD * 2, 360, [
    ["焦段", focal],
    ["光圈", ap],
    ["快门", sh],
    ["感光度", iso],
  ]);
  y += 380;

  // Angle / composition / lighting row
  ctx.fillStyle = "rgba(245, 244, 238, 0.7)";
  ctx.font = "500 32px 'PingFang SC', -apple-system, system-ui, sans-serif";
  const meta = [];
  if (shot.angle) {
    meta.push(`方向 ${Math.round(shot.angle.azimuth_deg)}°`);
    meta.push(`距 ${(shot.angle.distance_m || 0).toFixed(1)}m`);
  }
  if (shot.composition && shot.composition.primary) {
    meta.push(COMPOSITION_LABEL[shot.composition.primary] || shot.composition.primary);
  }
  ctx.fillText(meta.join(" · "), PAD, y);
  y += 56;

  // Pose summary
  const pose = (shot.poses || [])[0];
  if (pose) {
    ctx.fillStyle = "rgba(245, 244, 238, 0.88)";
    ctx.font = "600 34px 'PingFang SC', -apple-system, system-ui, sans-serif";
    ctx.fillText("姿势", PAD, y);
    y += 50;
    ctx.fillStyle = "rgba(245, 244, 238, 0.72)";
    ctx.font = "400 30px 'PingFang SC', -apple-system, system-ui, sans-serif";
    const lines = [];
    (pose.persons || []).slice(0, 3).forEach((p, i) => {
      const bits = [];
      if (p.stance) bits.push(`站姿：${p.stance}`);
      if (p.gaze) bits.push(`视线：${p.gaze}`);
      if (p.hands) bits.push(`手部：${p.hands}`);
      if (bits.length) lines.push(`#${i + 1} ${bits.join(" · ")}`);
    });
    if (!lines.length && pose.interaction) lines.push(pose.interaction);
    for (const line of lines) {
      y = wrapText(ctx, line, PAD, y, CARD_W - PAD * 2, 42);
      y += 8;
    }
  }

  // Footer
  const footerY = CARD_H - PAD - 40;
  ctx.fillStyle = "rgba(245, 244, 238, 0.45)";
  ctx.font = "500 24px 'PingFang SC', -apple-system, system-ui, sans-serif";
  ctx.fillText("拾光 · 环视一圈，AI 给你出片方案", PAD, footerY);
}

function drawDial2x2(ctx, x, y, w, h, cells) {
  const cellW = (w - 24) / 2;
  const cellH = (h - 24) / 2;
  cells.forEach((cell, i) => {
    const col = i % 2;
    const row = Math.floor(i / 2);
    const cx = x + col * (cellW + 24);
    const cy = y + row * (cellH + 24);
    // Card background
    ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
    roundRect(ctx, cx, cy, cellW, cellH, 28);
    ctx.fill();
    ctx.strokeStyle = "rgba(244, 184, 96, 0.18)";
    ctx.lineWidth = 1;
    roundRect(ctx, cx, cy, cellW, cellH, 28);
    ctx.stroke();

    ctx.fillStyle = "rgba(245, 244, 238, 0.55)";
    ctx.font = "600 26px 'PingFang SC', -apple-system, system-ui, sans-serif";
    ctx.fillText(cell[0], cx + 28, cy + 26);

    ctx.fillStyle = "#f4b860";
    ctx.font = "800 84px 'SF Pro Display', -apple-system, system-ui, sans-serif";
    ctx.fillText(cell[1], cx + 28, cy + 70);
  });
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function wrapText(ctx, text, x, y, maxW, lineH) {
  if (!text) return y;
  const chars = String(text).split("");
  let line = "";
  for (const ch of chars) {
    const tryLine = line + ch;
    if (ctx.measureText(tryLine).width > maxW && line) {
      ctx.fillText(line, x, y);
      y += lineH;
      line = ch;
    } else {
      line = tryLine;
    }
  }
  if (line) {
    ctx.fillText(line, x, y);
    y += lineH;
  }
  return y;
}
