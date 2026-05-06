import {
  loadAvatarPicks,
  savePanoramaUrl,
  saveFrames,
  saveRefInspiration,
  saveResult,
  saveSettings,
} from "./store.js";
import {
  analyze,
  fetchAsBlob,
  getDemoManifest,
  getHealth,
  getPoseManifest,
} from "./api.js";
import {
  REF_LIMIT,
  addReference,
  clearReferences,
  countReferences,
  listReferences,
  removeReference,
} from "./reference_db.js";
import { initAvatarGallery } from "./avatar_gallery.js";

const personRow = document.getElementById("person-count");
const qualityRow = document.getElementById("quality-mode");
const styleInput = document.getElementById("style-input");
const startBtn = document.getElementById("start-btn");
const modeBadge = document.getElementById("mode-badge");
const apiUrl = document.getElementById("api-url");
const poseCountEl = document.getElementById("pose-count");
const modelNameEl = document.getElementById("model-name");
const suggestRow = document.querySelector(".suggest-row");

const refInput = document.getElementById("ref-input");
const refGrid = document.getElementById("ref-grid");
const refCount = document.getElementById("ref-count");
const refClear = document.getElementById("ref-clear");
const refError = document.getElementById("ref-error");
const refLearnCard = document.getElementById("ref-learn-card");
const refLearnCount = document.getElementById("ref-learn-count");

apiUrl.textContent = location.origin;

function singleSelect(row, onChange) {
  row.addEventListener("click", (e) => {
    const t = e.target.closest(".chip");
    if (!t) return;
    [...row.children].forEach((c) => c.classList.remove("active"));
    t.classList.add("active");
    onChange?.(t.dataset.value);
  });
}
singleSelect(personRow, () => avatarGallery?.onPersonCountChanged());
singleSelect(qualityRow);

let avatarGallery = null;
const avatarSlotsEl = document.getElementById("avatar-slots");
const avatarGridEl = document.getElementById("avatar-grid");
if (avatarSlotsEl && avatarGridEl) {
  avatarGallery = initAvatarGallery({
    slotsHost: avatarSlotsEl,
    gridHost: avatarGridEl,
    personCount: () => parseInt(getValue(personRow) || "1", 10),
  });
}

if (suggestRow) {
  suggestRow.addEventListener("click", (e) => {
    const c = e.target.closest(".suggest-chip");
    if (!c) return;
    styleInput.value = c.dataset.suggest || "";
    styleInput.focus();
  });
}

function getValue(row) {
  return row.querySelector(".chip.active")?.dataset.value;
}

startBtn.addEventListener("click", () => {
  const personCount = parseInt(getValue(personRow) || "1", 10);
  const qualityMode = getValue(qualityRow) || "fast";
  const styleKeywords = (styleInput.value || "")
    .split(/[,，;；]/)
    .map((s) => s.trim())
    .filter(Boolean);
  saveSettings({ personCount, qualityMode, styleKeywords });
  location.href = "/web/capture.html";
});

(async () => {
  try {
    const h = await getHealth();
    if (h.mock_mode) {
      modeBadge.textContent = "MOCK 模式";
      modeBadge.classList.add("mock");
      if (modelNameEl) modelNameEl.textContent = "mock";
    } else {
      modeBadge.textContent = "Gemini 已连接";
      modeBadge.classList.add("live");
      if (modelNameEl) modelNameEl.textContent = "gemini-2.5-flash";
    }
  } catch (e) {
    modeBadge.textContent = "后端未连接";
    modeBadge.classList.add("mock");
    if (modelNameEl) modelNameEl.textContent = "offline";
  }

  if (poseCountEl) {
    try {
      const m = await getPoseManifest();
      poseCountEl.textContent = String(m.poses?.length ?? "?");
    } catch (e) {
      poseCountEl.textContent = "?";
    }
  }
})();

// ---------------------------------------------------------------------------
// Reference library
// ---------------------------------------------------------------------------

function showRefError(msg) {
  if (!refError) return;
  refError.textContent = msg;
  refError.style.display = msg ? "block" : "none";
  if (msg) {
    setTimeout(() => {
      refError.style.display = "none";
    }, 3500);
  }
}

async function refreshReferences() {
  if (!refGrid) return;
  let items = [];
  try {
    items = await listReferences();
  } catch (e) {
    showRefError(`读取参考图库失败：${e.message || e}`);
    return;
  }
  refGrid.innerHTML = "";
  for (const it of items) {
    const cell = document.createElement("div");
    cell.className = "ref-cell";
    const img = document.createElement("img");
    img.src = it.thumbDataUrl;
    img.alt = it.name || "reference";
    cell.appendChild(img);
    const del = document.createElement("button");
    del.className = "ref-del";
    del.type = "button";
    del.textContent = "×";
    del.title = "删除";
    del.addEventListener("click", async () => {
      await removeReference(it.id);
      await refreshReferences();
    });
    cell.appendChild(del);
    refGrid.appendChild(cell);
  }
  if (refCount) {
    refCount.textContent = `${items.length}/${REF_LIMIT}`;
  }
  if (refLearnCard) {
    if (items.length > 0) {
      refLearnCard.style.display = "flex";
      if (refLearnCount) refLearnCount.textContent = String(items.length);
    } else {
      refLearnCard.style.display = "none";
    }
  }
}

if (refInput) {
  refInput.addEventListener("change", async (e) => {
    const files = [...(e.target.files || [])];
    e.target.value = "";
    let added = 0;
    for (const f of files) {
      try {
        await addReference(f);
        added++;
      } catch (err) {
        showRefError(err.message || String(err));
        break;
      }
    }
    if (added > 0) showRefError("");
    await refreshReferences();
  });
}

if (refClear) {
  refClear.addEventListener("click", async () => {
    const have = await countReferences();
    if (!have) return;
    if (!confirm(`确认清空 ${have} 张参考图？`)) return;
    await clearReferences();
    await refreshReferences();
  });
}

refreshReferences();

// ---------------------------------------------------------------------------
// "Run with sample data" – lets users without a webcam still hit the real
// /analyze pipeline using a curated set of synthetic environment frames
// served by the backend (/dev/sample-frame/*.jpg).
// ---------------------------------------------------------------------------

const demoBtn = document.getElementById("demo-btn");
const demoOverlay = document.getElementById("demo-overlay");
const demoStages = document.getElementById("demo-stages");
const demoMsg = document.getElementById("demo-msg");
const demoError = document.getElementById("demo-error");

function setDemoStage(name, status) {
  if (!demoStages) return;
  const el = demoStages.querySelector(`.stage[data-stage="${name}"]`);
  if (!el) return;
  el.classList.remove("active", "done");
  if (status) el.classList.add(status);
}

function resetDemoStages() {
  if (!demoStages) return;
  demoStages
    .querySelectorAll(".stage")
    .forEach((el) => el.classList.remove("active", "done"));
}

function showDemoError(msg) {
  if (!demoError) return;
  demoError.textContent = msg;
  demoError.style.display = msg ? "block" : "none";
}

if (demoBtn) {
  demoBtn.addEventListener("click", async () => {
    showDemoError("");
    resetDemoStages();
    demoOverlay.style.display = "flex";
    demoMsg.textContent = "下载示例环视帧…";
    setDemoStage("fetch", "active");

    try {
      const personCount = parseInt(getValue(personRow) || "1", 10);
      const qualityMode = getValue(qualityRow) || "fast";
      const styleKeywords = (styleInput.value || "")
        .split(/[,，;；]/)
        .map((s) => s.trim())
        .filter(Boolean);

      const manifest = await getDemoManifest();

      const frameBlobs = [];
      for (const f of manifest.frames) {
        const blob = await fetchAsBlob(f.url);
        frameBlobs.push(blob);
      }
      setDemoStage("fetch", "done");

      setDemoStage("refs", "active");
      demoMsg.textContent = "下载示例参考图…";
      const referenceBlobs = [];
      for (const r of manifest.references || []) {
        const blob = await fetchAsBlob(r.url);
        referenceBlobs.push(blob);
      }
      setDemoStage("refs", "done");

      setDemoStage("ai", "active");
      demoMsg.textContent = "Gemini 正在真分析这组示例环境…（30~60秒）";

      const meta = {
        person_count: personCount,
        quality_mode: qualityMode,
        style_keywords: styleKeywords,
        frame_meta: manifest.frames.map((f) => ({
          index: f.index,
          azimuth_deg: f.azimuth_deg,
          pitch_deg: f.pitch_deg || 0,
          roll_deg: f.roll_deg || 0,
          timestamp_ms: f.timestamp_ms || 0,
        })),
      };

      const response = await analyze({
        meta,
        frames: frameBlobs,
        references: referenceBlobs,
      });
      setDemoStage("ai", "done");

      setDemoStage("render", "active");
      demoMsg.textContent = "整理结果，跳转中…";
      saveSettings({ personCount, qualityMode, styleKeywords });

      // Persist frame URLs + reference URLs so the result page can use
      // them as backdrops + ref-inspiration thumbs.
      saveFrames(
        manifest.frames.map((f, i) => ({
          index: i,
          azimuthDeg: f.azimuth_deg,
          src: f.url,
        })),
      );
      // Cache the panorama URL too — result page's 3D mode reads this.
      if (manifest.panorama_url) savePanoramaUrl(manifest.panorama_url);
      saveRefInspiration({
        count: (manifest.references || []).length,
        thumbs: (manifest.references || []).map((r) => r.url),
        names: ["示例参考图 #1", "示例参考图 #2", "示例参考图 #3"].slice(
          0,
          (manifest.references || []).length,
        ),
      });
      saveResult(response);

      // Keep the overlay up briefly so users see the green checkmarks.
      await new Promise((r) => setTimeout(r, 250));
      setDemoStage("render", "done");
      location.href = "/web/result.html";
    } catch (err) {
      console.error(err);
      const raw = err && err.message ? err.message : String(err);
      const friendly = /503|UNAVAILABLE|high demand/i.test(raw)
        ? "Gemini 当前繁忙（503），稍等几秒再点一次。"
        : /quota|RESOURCE_EXHAUSTED/i.test(raw)
        ? "免费 Gemini 额度今天用完了，明天再来。"
        : raw.slice(0, 220);
      showDemoError(`运行失败：${friendly}`);
      demoMsg.textContent = "失败 — 点击外侧关闭，再试一次";
    }
  });

  // Click outside the spinner box to dismiss after error
  demoOverlay.addEventListener("click", (e) => {
    if (e.target === demoOverlay && demoError.style.display === "block") {
      demoOverlay.style.display = "none";
    }
  });
}
