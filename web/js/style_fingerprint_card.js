// style_fingerprint_card.js (W6.3)
export function renderStyleFingerprintCard(container, fp) {
  if (!container || !fp) return null;
  const el = document.createElement("div");
  el.className = "style-fp-card";
  el.innerHTML = `
    <div class="style-fp-card__head">
      <strong>借鉴自参考 #${fp.index + 1}</strong>
      <small>${(fp.mood_keywords || []).join(" · ")}</small>
    </div>
    <div class="style-fp-card__swatches">
      ${(fp.palette || []).slice(0, 5).map(hex =>
        `<span class="swatch" style="background:${hex}"></span>`
      ).join("")}
    </div>
  `;
  container.appendChild(el);
  return { destroy() { el.remove(); } };
}

// time_recommendation_card.js (W7.3) — bundled in same file for now to
// keep file count manageable. Both modules export named functions.
export function renderTimeRecommendationCard(container, rec) {
  if (!container || !rec) return null;
  const el = document.createElement("div");
  el.className = "time-rec-card";
  const hh = String(rec.best_hour_local).padStart(2, "0");
  const blurb = rec.blurb_zh ||
    `附近 ${rec.sample_n} 张照片在 ${hh}:00 前后评分最高（${rec.score.toFixed(1)} / 5）。`;
  const runner = rec.runner_up_hour_local != null
    ? `次优时段：${String(rec.runner_up_hour_local).padStart(2, "0")}:00`
    : "";
  const eta = rec.minutes_until_best != null
    ? (rec.minutes_until_best > 0
       ? `距最佳时段 ${Math.round(rec.minutes_until_best)} 分钟`
       : "现在就是最佳时段")
    : "";
  el.innerHTML = `
    <div class="time-rec-card__title">⏱ 今晚几点拍更好</div>
    <div class="time-rec-card__body">${blurb}</div>
    <div class="time-rec-card__sub">${runner}${runner && eta ? "  ·  " : ""}${eta}</div>
  `;
  container.appendChild(el);
  return { destroy() { el.remove(); } };
}
