// shot_rating.js (W2.3)
//
// Renders a 1-5 star rating widget for a chosen ShotPosition and POSTs
// /feedback when the user submits. Designed to be invoked from the
// result page after the user takes a photo.

const STARS = [1, 2, 3, 4, 5];

export function mountRating(container, opts) {
  const { chosenPosition, analyzeRequestId, sceneKind, apiBase = "" } = opts || {};
  if (!container) return null;
  const el = document.createElement("div");
  el.className = "shot-rating";
  el.innerHTML = `
    <div class="shot-rating__title">这张机位拍得怎么样？</div>
    <div class="shot-rating__stars" role="radiogroup" aria-label="评分">
      ${STARS.map(n =>
        `<button type="button" class="shot-rating__star" data-n="${n}" aria-label="${n} 星">☆</button>`
      ).join("")}
    </div>
    <div class="shot-rating__msg" hidden></div>
  `;
  container.appendChild(el);

  let chosen = 0;
  const stars = Array.from(el.querySelectorAll(".shot-rating__star"));
  const msg = el.querySelector(".shot-rating__msg");

  function paint(n) {
    stars.forEach((b, i) => { b.textContent = i < n ? "★" : "☆"; });
  }
  stars.forEach((b) => {
    b.addEventListener("mouseenter", () => paint(Number(b.dataset.n)));
    b.addEventListener("mouseleave", () => paint(chosen));
    b.addEventListener("click", async () => {
      chosen = Number(b.dataset.n);
      paint(chosen);
      msg.hidden = false;
      msg.textContent = "正在记录…";
      try {
        const resp = await fetch((apiBase || "") + "/feedback/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            analyze_request_id: analyzeRequestId || null,
            chosen_position: chosenPosition || null,
            rating: chosen,
            scene_kind: sceneKind || null,
            geo_lat: chosenPosition?.lat ?? null,
            geo_lon: chosenPosition?.lon ?? null,
          }),
        });
        if (!resp.ok) throw new Error("HTTP " + resp.status);
        const out = await resp.json();
        if (out.ugc_action === "insert" || out.ugc_action === "merge") {
          msg.textContent = "感谢！这个机位会被加入用户社区推荐";
        } else {
          msg.textContent = "感谢评分！";
        }
      } catch (e) {
        msg.textContent = "记录失败：" + (e?.message || e);
      }
    });
  });
  return { destroy() { el.remove(); } };
}
