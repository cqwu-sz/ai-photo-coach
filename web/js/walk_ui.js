// walk_ui.js (W5.3)
//
// Tiny in-page UI for the optional walk segment. Exposes a single
// ``mountWalkUi(containerEl, opts)`` that paints a "Start walk" button,
// a live trajectory mini-canvas, and a stop button. On stop the captured
// segment is stashed onto ``window.__pendingWalkSegment`` so capture.js
// picks it up at /analyze time.
//
// Pure DOM — no framework required. Designed to live alongside the
// existing capture.html flow without forking the page.

import { startWalk, isWalkAvailable } from "./walk_segment.js";

const TRACK_WIDTH = 220;
const TRACK_HEIGHT = 220;
const MARGIN_PX = 12;

export function mountWalkUi(container, opts = {}) {
  if (!container || !isWalkAvailable()) {
    return null;
  }

  const wrap = document.createElement("div");
  wrap.className = "walk-ui";
  wrap.innerHTML = `
    <div class="walk-ui__row">
      <button type="button" class="walk-ui__start">开始漫游 ＋ 解锁远机位</button>
      <button type="button" class="walk-ui__stop" hidden>停止漫游</button>
      <span class="walk-ui__status">未开始</span>
    </div>
    <canvas class="walk-ui__canvas" width="${TRACK_WIDTH}" height="${TRACK_HEIGHT}"></canvas>
    <p class="walk-ui__hint">沿建筑或景点走 10-20 米，回到原地附近停止。</p>
  `;
  container.appendChild(wrap);

  const startBtn = wrap.querySelector(".walk-ui__start");
  const stopBtn  = wrap.querySelector(".walk-ui__stop");
  const status   = wrap.querySelector(".walk-ui__status");
  const canvas   = wrap.querySelector(".walk-ui__canvas");
  const ctx      = canvas.getContext("2d");

  let controller = null;
  let raf = null;

  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    status.textContent = "申请相机/位置权限…";
    try {
      controller = await startWalk({
        initialHeadingDeg: opts.initialHeadingDeg ?? null,
        videoEl: opts.videoEl ?? null,
      });
      status.textContent = "漫游中，沿景点走一段然后停止";
      stopBtn.hidden = false;
      raf = requestAnimationFrame(drawLoop);
    } catch (e) {
      status.textContent = "漫游不可用：" + (e?.message || e);
      startBtn.disabled = false;
    }
  });

  stopBtn.addEventListener("click", async () => {
    stopBtn.disabled = true;
    if (raf) cancelAnimationFrame(raf);
    if (!controller) return;
    const seg = await controller.stop();
    if (seg) {
      window.__pendingWalkSegment = seg;
      status.textContent = `漫游完成：${seg.poses.length} 个采样点，已待发送`;
    } else {
      status.textContent = "漫游数据不足，请再试一次";
    }
    startBtn.disabled = false;
    stopBtn.hidden = true;
    stopBtn.disabled = false;
    drawTrack(controller, true);
  });

  function drawLoop() {
    drawTrack(controller, false);
    raf = requestAnimationFrame(drawLoop);
  }

  function drawTrack(ctrl, finalDraw) {
    if (!ctrl) return;
    const seg = ctrl;
    const poses = (seg && seg._currentPoses && seg._currentPoses()) ||
                  (window.__pendingWalkSegment && window.__pendingWalkSegment.poses) ||
                  [];
    // Fallback: pull positions from the last accessible array via stop()'s closure;
    // until we actually stop, sample using ctrl.coverageM() to at least show motion.
    const cov = typeof ctrl.coverageM === "function" ? ctrl.coverageM() : 0;
    if (status && !finalDraw) {
      status.textContent = `漫游中 · 半径 ≈ ${cov.toFixed(1)} m`;
    }
    ctx.clearRect(0, 0, TRACK_WIDTH, TRACK_HEIGHT);
    ctx.fillStyle = "rgba(0,0,0,0.04)";
    ctx.fillRect(0, 0, TRACK_WIDTH, TRACK_HEIGHT);
    ctx.strokeStyle = "#888";
    ctx.beginPath();
    ctx.arc(TRACK_WIDTH / 2, TRACK_HEIGHT / 2, 4, 0, Math.PI * 2);
    ctx.stroke();
    if (!poses.length) {
      ctx.fillStyle = "#888";
      ctx.font = "12px sans-serif";
      ctx.fillText("起点", TRACK_WIDTH / 2 + 8, TRACK_HEIGHT / 2 + 4);
      // Show a coverage ring to give live feedback.
      if (cov > 0) {
        const r = Math.min(TRACK_WIDTH, TRACK_HEIGHT) / 2 - MARGIN_PX;
        const px = (cov / 25) * r; // normalise to 25 m max walk
        ctx.strokeStyle = "rgba(0,128,255,0.6)";
        ctx.beginPath();
        ctx.arc(TRACK_WIDTH / 2, TRACK_HEIGHT / 2, Math.min(r, px), 0, Math.PI * 2);
        ctx.stroke();
      }
      return;
    }
    let maxR = 1;
    for (const p of poses) maxR = Math.max(maxR, Math.hypot(p.x, p.y));
    const scale = (Math.min(TRACK_WIDTH, TRACK_HEIGHT) / 2 - MARGIN_PX) / maxR;
    ctx.strokeStyle = "#0080ff";
    ctx.lineWidth = 2;
    ctx.beginPath();
    poses.forEach((p, i) => {
      const sx = TRACK_WIDTH / 2 + p.x * scale;
      const sy = TRACK_HEIGHT / 2 - p.y * scale; // y up
      if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    });
    ctx.stroke();
  }

  return {
    destroy() {
      if (raf) cancelAnimationFrame(raf);
      if (controller) controller.stop().catch(() => {});
      wrap.remove();
    },
  };
}
