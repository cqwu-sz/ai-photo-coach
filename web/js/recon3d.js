// recon3d.js (W9.3)
//
// Tiny client for /recon3d/start + /recon3d/{job_id}. Renders a button +
// progress bar. Caller supplies the base64 images.

export function mountRecon3D(container, opts) {
  const { apiBase = "", imagesB64 = [], originLat, originLon } = opts || {};
  if (!container) return null;
  const el = document.createElement("div");
  el.className = "recon3d";
  el.innerHTML = `
    <h3>3D 重建（高级）</h3>
    <button type="button" class="recon3d__start">开始 3D 重建</button>
    <progress class="recon3d__progress" max="1" value="0" hidden></progress>
    <div class="recon3d__msg"></div>
  `;
  container.appendChild(el);
  const btn = el.querySelector(".recon3d__start");
  const bar = el.querySelector(".recon3d__progress");
  const msg = el.querySelector(".recon3d__msg");

  btn.addEventListener("click", async () => {
    btn.disabled = true;
    bar.hidden = false;
    msg.textContent = "提交…";
    try {
      const start = await fetch(apiBase + "/recon3d/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          images_b64: imagesB64,
          origin_lat: originLat ?? null,
          origin_lon: originLon ?? null,
        }),
      });
      if (!start.ok) throw new Error("HTTP " + start.status);
      const job = await start.json();
      msg.textContent = "排队中…";
      for (let i = 0; i < 60; i++) {
        await new Promise(r => setTimeout(r, 1500));
        const s = await fetch(apiBase + "/recon3d/" + job.job_id);
        if (!s.ok) throw new Error("HTTP " + s.status);
        const cur = await s.json();
        bar.value = cur.progress || 0;
        msg.textContent = "状态：" + cur.status;
        if (cur.status === "done") {
          msg.textContent = `完成：${cur.model.points_count} 个稀疏点 · ${cur.model.cameras_count} 帧`;
          return;
        }
        if (cur.status === "error") {
          msg.textContent = "失败：" + (cur.error || "unknown");
          return;
        }
      }
      msg.textContent = "超时（60s）";
    } catch (e) {
      msg.textContent = "失败：" + (e?.message || e);
    } finally {
      btn.disabled = false;
    }
  });
  return { destroy() { el.remove(); } };
}
