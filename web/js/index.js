// Home screen orchestrator. Drives the 4-step wizard, persists user
// preferences, exposes the demo flow, and hands off to /capture.html or
// /result.html when the user is ready.

import {
  loadAvatarPicks,
  loadSceneMode,
  saveSceneMode,
  savePanoramaUrl,
  saveFrames,
  saveRefInspiration,
  saveResult,
  saveSettings,
  saveLastPrefs,
  loadLastPrefs,
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
  getReferenceBlobs,
  listReferences,
  removeReference,
} from "./reference_db.js";
import { initAvatarGallery } from "./avatar_gallery.js";
import { initStylePicker } from "./style_picker.js";
import { readCache as readGeoCache } from "./geo.js";
import {
  getActiveModelConfig,
  openModelSettings,
  refreshModeBadge,
} from "./model_settings.js";
import { initWizard } from "./wizard.js";
import {
  getCapturedMeta,
  loadCapturedFrames,
  relativeTime,
} from "./frames_db.js";

// ---------------------------------------------------------------------------
// DOM lookups
// ---------------------------------------------------------------------------

const sceneRow = document.getElementById("scene-mode");
const personRow = document.getElementById("person-count");
const qualityRow = document.getElementById("quality-mode");
const personSection = document.querySelector(".person-section");
const avatarSection = document.querySelector(".avatar-section");
const styleInput = document.getElementById("style-input");
const styleSuggest = document.getElementById("style-suggest");
const styleGrid = document.getElementById("style-grid");
const styleDrawer = document.getElementById("style-drawer");
let stylePicker = null;

const modeBadge = document.getElementById("mode-badge");
const settingsBtn = document.getElementById("settings-btn");
const apiUrl = document.getElementById("api-url");
const poseCountEl = document.getElementById("pose-count");
const modelNameEl = document.getElementById("model-name");

const sumScene = document.getElementById("sum-scene");
const sumCast = document.getElementById("sum-cast");
const sumTone = document.getElementById("sum-tone");

const reuseChip = document.getElementById("reuse-chip");
const reuseTitle = document.getElementById("reuse-chip-title");
const reuseMeta = document.getElementById("reuse-chip-meta");

const refInput = document.getElementById("ref-input");
const refGrid = document.getElementById("ref-grid");
const refCount = document.getElementById("ref-count");
const refClear = document.getElementById("ref-clear");
const refError = document.getElementById("ref-error");
const refLearnCard = document.getElementById("ref-learn-card");
const refLearnCount = document.getElementById("ref-learn-count");

if (apiUrl) apiUrl.textContent = location.origin;

// ---------------------------------------------------------------------------
// Hoisted constants (declared early so the bootstrap-time
// `applySceneMode -> updateSummary` chain can read them).
// ---------------------------------------------------------------------------

const SCENE_LABELS = {
  portrait: "人像",
  closeup: "特写",
  full_body: "全身",
  documentary: "人文",
  scenery: "风景",
  light_shadow: "光影",
};

const QUALITY_LABELS = { fast: "快速出片", high: "精致出片" };

// ---------------------------------------------------------------------------
// Tiny chip-row helpers
// ---------------------------------------------------------------------------

const ACTIVE_CLASSES = ["is-active", "active"];

function activate(row, value) {
  if (!row) return;
  [...row.children].forEach((c) => {
    if (!c.dataset || c.dataset.value == null) return;
    const isMatch = c.dataset.value === String(value);
    ACTIVE_CLASSES.forEach((cls) => c.classList.toggle(cls, isMatch));
  });
}

function getValue(row) {
  if (!row) return undefined;
  return row.querySelector("[data-value].is-active, [data-value].active")
    ?.dataset.value;
}

function bindRowSelect(row, onChange) {
  if (!row) return;
  row.addEventListener("click", (e) => {
    const t = e.target.closest("[data-value]");
    if (!t || !row.contains(t)) return;
    const value = t.dataset.value;
    activate(row, value);
    onChange?.(value);
  });
}

// ---------------------------------------------------------------------------
// Avatar gallery (Step 2)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Scene-mode side effects
// ---------------------------------------------------------------------------

function applySceneMode(mode) {
  const isScenery = mode === "scenery";
  if (personSection) personSection.dataset.scenery = isScenery ? "1" : "";
  if (avatarSection) avatarSection.style.display = isScenery ? "none" : "";

  // Scenery permits 0 people; make the 0-pill visible. Other modes hide it.
  if (personRow) {
    [...personRow.children].forEach((c) => {
      if (c.dataset?.value === "0") {
        c.style.display = isScenery ? "" : "none";
      }
    });
  }

  if (isScenery) {
    activate(personRow, "0");
    avatarGallery?.onPersonCountChanged();
  } else {
    const cur = getValue(personRow);
    if (cur === "0") {
      activate(personRow, "1");
      avatarGallery?.onPersonCountChanged();
    }
  }
  saveSceneMode(mode);
  updateSummary();
  // Update the Step 2 subtitle to reflect scenery quirk.
  const sub = document.getElementById("step-2-sub");
  if (sub) {
    sub.textContent = isScenery
      ? "你选了风景模式，可以不出人。这一步会自动跳过。"
      : "先告诉我们这次有几个人入镜，然后为每位选一个角色 — 拍摄页会用这些角色为你示范站位。";
  }
}

// ---------------------------------------------------------------------------
// Bind chip rows
// ---------------------------------------------------------------------------

bindRowSelect(sceneRow, applySceneMode);
bindRowSelect(personRow, () => {
  avatarGallery?.onPersonCountChanged();
  updateSummary();
});
bindRowSelect(qualityRow, updateSummary);

if (styleInput) {
  styleInput.addEventListener("input", updateSummary);
}

if (styleSuggest) {
  styleSuggest.addEventListener("click", (e) => {
    const c = e.target.closest("button[data-suggest]");
    if (!c) return;
    styleInput.value = c.dataset.suggest || "";
    styleInput.focus();
    updateSummary();
  });
}

// ---------------------------------------------------------------------------
// Pre-fill from previous session (returning user) + first-run defaults
// ---------------------------------------------------------------------------

const lastPrefs = loadLastPrefs();
const initialScene = lastPrefs?.sceneMode || loadSceneMode() || "portrait";
const initialPerson = String(
  Number.isFinite(lastPrefs?.personCount) ? lastPrefs.personCount : 1,
);
const initialQuality = lastPrefs?.qualityMode || "fast";
const initialStyle = (lastPrefs?.styleKeywords || []).join(", ");

if (sceneRow) {
  activate(sceneRow, initialScene);
  applySceneMode(initialScene);
}
if (personRow) activate(personRow, initialPerson);
if (qualityRow) activate(qualityRow, initialQuality);
if (styleInput && initialStyle) styleInput.value = initialStyle;

// Style picker (Step 3) — must init AFTER `styleInput.value` is pre-filled
// so the picker can derive initial card selection from existing keywords.
// Pull a previously-cached geo fix (set by capture.js after a prior
// shoot, or by a previous Step-3 visit). We never trigger a fresh GPS
// prompt from the wizard — the prompt belongs to the capture flow,
// not the planning flow. Without a fix, the picker just shows cards
// without feasibility badges.
const cachedGeo = readGeoCache()?.fix || null;
initStylePicker({
  gridHost: styleGrid,
  drawerHost: styleDrawer,
  input: styleInput,
  onChange: () => updateSummary(),
  geoFix: cachedGeo,
}).then((p) => {
  stylePicker = p;
  updateSummary();
});

avatarGallery?.onPersonCountChanged();

// ---------------------------------------------------------------------------
// Step 4 review summary
// ---------------------------------------------------------------------------

function readState() {
  return {
    sceneMode: getValue(sceneRow) || "portrait",
    personCount: parseInt(getValue(personRow) || "1", 10),
    qualityMode: getValue(qualityRow) || "fast",
    styleKeywords: (styleInput?.value || "")
      .split(/[,，;；]/)
      .map((s) => s.trim())
      .filter(Boolean),
  };
}

function updateSummary() {
  if (!sumScene || !sumCast || !sumTone) return;
  const s = readState();
  sumScene.textContent = SCENE_LABELS[s.sceneMode] || s.sceneMode;

  if (s.sceneMode === "scenery") {
    sumCast.textContent = "纯风景，不出人";
  } else {
    const picks = loadAvatarPicks();
    const names = picks.slice(0, s.personCount).filter(Boolean);
    const namePart = names.length
      ? names.length <= 2
        ? ` · ${names.join(" / ")}`
        : ` · ${names[0]} 等 ${names.length} 位`
      : " · 角色待选";
    sumCast.textContent = `${s.personCount} 人${namePart}`;
  }

  // Prefer human-readable Chinese labels when the user picked one of the
  // 5 known style cards; fall back to the raw English keywords for users
  // who typed something custom (or partial overlap).
  const picked = stylePicker?.getSelected?.() || [];
  const pickedKeywords = new Set(
    picked.flatMap((p) => (p.keywords || []).map((k) => k.toLowerCase())),
  );
  const customExtras = s.styleKeywords.filter(
    (k) => !pickedKeywords.has(k.toLowerCase()),
  );
  const toneParts = [
    ...picked.map((p) => p.label_zh),
    ...customExtras,
  ];
  const tonePart = toneParts.length ? toneParts.join(" + ") : "无指定基调";
  sumTone.textContent = `${QUALITY_LABELS[s.qualityMode] || s.qualityMode} · ${tonePart}`;
}

updateSummary();

// ---------------------------------------------------------------------------
// Wizard bootstrapping
// ---------------------------------------------------------------------------

// React to step changes — refresh summary, focus inputs, etc.
// IMPORTANT: register listeners BEFORE initWizard() runs, because the
// wizard fires its first `wizard:step` synchronously inside init when
// it decides which step to land on (e.g. step 4 for returning users).
document.addEventListener("wizard:step", (e) => {
  const step = e.detail?.step;
  if (step === 4) {
    updateSummary();
    refreshReuseChip();
    refreshLightRecaptureHint();
  }
  if (step === 3) styleInput?.focus({ preventScroll: true });
});

const wizard = initWizard({
  getSceneMode: () => getValue(sceneRow) || "portrait",
  onValidate: (step) => {
    if (step === 2) {
      const sceneMode = getValue(sceneRow);
      if (sceneMode !== "scenery") {
        const n = parseInt(getValue(personRow) || "0", 10);
        if (!n || n < 1) throw new Error("请至少选 1 人");
      }
    }
  },
});

// Last step's "开始环视拍摄" CTA: persist prefs and jump to capture.
document.addEventListener("wizard:start-capture", () => {
  const s = readState();
  saveSettings(s);
  saveLastPrefs(s);
  wizard.markCompleted();
  // The recapture hint, if any, has now been actioned — let the next
  // result page render fresh advice without reusing a stale hint.
  try { sessionStorage.removeItem("lightRecaptureHint"); } catch {}
  location.href = "/web/capture.html";
});

// ---------------------------------------------------------------------------
// "Reuse last environment" chip — appears when IndexedDB has cached frames
// from a previous capture. Lets a returning user change the scene mode (or
// any tone setting) and re-run /analyze without re-shooting.
// (Defined as a function declaration so it's available to the wizard:step
//  listener registered earlier in this file.)
// ---------------------------------------------------------------------------

function refreshLightRecaptureHint() {
  const card = document.getElementById("light-recapture-card");
  if (!card) return;
  let payload = null;
  try {
    const raw = sessionStorage.getItem("lightRecaptureHint");
    if (raw) payload = JSON.parse(raw);
  } catch {}
  if (!payload) {
    card.hidden = true;
    return;
  }
  const meta = document.getElementById("light-recapture-meta");
  if (meta) {
    if (payload.azimuth_deg != null) {
      meta.textContent =
        `建议中心方位 ${Math.round(payload.azimuth_deg)}° · 对准最亮处慢转 10 秒`;
    } else {
      meta.textContent = "对着最亮的方向慢转 10 秒，AI 会更准";
    }
  }
  card.hidden = false;
}

async function refreshReuseChip() {
  if (!reuseChip) return;
  const meta = await getCapturedMeta().catch(() => null);
  if (!meta || !meta.freshEnough) {
    reuseChip.hidden = true;
    return;
  }
  const cur = readState();
  const sameScene = cur.sceneMode === meta.sceneMode;
  const title = sameScene
    ? "上次环境帧还在 · 直接出方案"
    : `换成「${SCENE_LABELS[cur.sceneMode] || cur.sceneMode}」用上次环境出方案`;
  if (reuseTitle) reuseTitle.textContent = title;
  if (reuseMeta) {
    const sceneTag = SCENE_LABELS[meta.sceneMode] || meta.sceneMode;
    reuseMeta.textContent =
      `${meta.count} 张 · ${relativeTime(meta.ageMs)} · 上次拍的「${sceneTag}」环境`;
  }
  reuseChip.hidden = false;
}

if (reuseChip) {
  reuseChip.addEventListener("click", () => {
    runReuseFlow().catch((err) => {
      console.error(err);
      const raw = err?.message || String(err);
      const friendly = /503|UNAVAILABLE|high demand/i.test(raw)
        ? "AI 当前繁忙（503），稍等几秒再点一次。"
        : /quota|RESOURCE_EXHAUSTED/i.test(raw)
        ? "免费额度今天用完了，明天再来。"
        : raw.slice(0, 220);
      showDemoError(`复用失败：${friendly}`);
      demoMsg.textContent = "失败 — 点击外侧关闭";
    });
  });
}

async function runReuseFlow() {
  showDemoError("");
  resetDemoStages();
  demoOverlay.style.display = "flex";
  demoMsg.textContent = "读取上次环境帧…";
  setDemoStage("fetch", "active");
  reuseChip.disabled = true;

  try {
    const cached = await loadCapturedFrames();
    if (!cached?.frames?.length) {
      throw new Error("缓存已被清空，请重新拍摄");
    }
    setDemoStage("fetch", "done");

    setDemoStage("refs", "active");
    demoMsg.textContent = "读取参考图…";
    let referenceBlobs = [];
    let refRecords = [];
    try {
      referenceBlobs = await getReferenceBlobs();
      refRecords = await listReferences();
    } catch (e) {
      console.warn("reference db read failed in reuse flow", e);
    }
    setDemoStage("refs", "done");

    setDemoStage("ai", "active");
    demoMsg.textContent = "AI 重新出方案中…（30~60秒）";

    const s = readState();
    const meta = {
      person_count: s.personCount,
      scene_mode: s.sceneMode,
      quality_mode: s.qualityMode,
      style_keywords: s.styleKeywords,
      frame_meta: cached.frames.map((f, i) => ({
        index: i,
        azimuth_deg: f.meta?.azimuth_deg ?? 0,
        pitch_deg: f.meta?.pitch_deg ?? 0,
        roll_deg: f.meta?.roll_deg ?? 0,
        timestamp_ms: f.meta?.timestamp_ms ?? i * 220,
      })),
    };
    const frameBlobs = cached.frames.map((f) => f.blob);

    const modelCfg = getActiveModelConfig();
    const response = await analyze({
      meta,
      frames: frameBlobs,
      references: referenceBlobs,
      modelId: modelCfg.model_id,
      modelApiKey: modelCfg.api_key,
      modelBaseUrl: modelCfg.base_url,
    });
    setDemoStage("ai", "done");

    setDemoStage("render", "active");
    demoMsg.textContent = "整理结果，跳转中…";
    saveSettings(s);
    saveLastPrefs(s);
    wizard.markCompleted();

    saveFrames(
      cached.frames.map((f, i) => ({
        index: i,
        azimuthDeg: f.meta?.azimuth_deg ?? 0,
        src: URL.createObjectURL(f.blob),
      })),
    );
    if (cached.panoramaUrl) savePanoramaUrl(cached.panoramaUrl);
    saveRefInspiration({
      count: refRecords.length,
      thumbs: refRecords.slice(0, 4).map((r) => r.thumbDataUrl),
      names: refRecords.slice(0, 4).map((r) => r.name),
    });
    saveResult(response);

    await new Promise((r) => setTimeout(r, 250));
    setDemoStage("render", "done");
    location.href = "/web/result.html";
  } finally {
    reuseChip.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Settings drawer + health badges
// ---------------------------------------------------------------------------

if (settingsBtn) {
  settingsBtn.addEventListener("click", () => {
    openModelSettings().catch((e) => console.error(e));
  });
}

(async () => {
  if (!modeBadge) return;
  // v9 UX polish #O4 — badge is hidden by default; only surface it
  // when there is something the user actually needs to know about
  // (mock mode or offline). Live mode keeps the header clean.
  try {
    const h = await getHealth();
    if (h.mock_mode) {
      modeBadge.style.display = "";
      modeBadge.textContent = "示范数据";
      modeBadge.classList.add("mock");
      if (modelNameEl) modelNameEl.textContent = "演示模式";
    } else {
      // Live mode — keep badge hidden. Still call refreshModeBadge
      // for callers that look at its data attrs.
      modeBadge.classList.add("live");
      refreshModeBadge(modeBadge).catch(() => {});
      if (modelNameEl) {
        const cfg = getActiveModelConfig();
        modelNameEl.textContent = cfg.model_id || "default";
      }
    }
  } catch (e) {
    modeBadge.style.display = "";
    modeBadge.textContent = "服务未连接";
    modeBadge.classList.add("mock");
    if (modelNameEl) modelNameEl.textContent = "离线";
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
  if (refCount) refCount.textContent = `${items.length}/${REF_LIMIT}`;
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
// "Run with sample data" — Step 4 secondary action
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
    demoMsg.textContent = "正在准备示范场景…";
    setDemoStage("fetch", "active");

    try {
      const s = readState();
      const manifest = await getDemoManifest(s.sceneMode);

      const frameBlobs = [];
      for (const f of manifest.frames) {
        const blob = await fetchAsBlob(f.url);
        frameBlobs.push(blob);
      }
      setDemoStage("fetch", "done");

      setDemoStage("refs", "active");
      demoMsg.textContent = "准备参考照片…";
      const referenceBlobs = [];
      for (const r of manifest.references || []) {
        const blob = await fetchAsBlob(r.url);
        referenceBlobs.push(blob);
      }
      setDemoStage("refs", "done");

      setDemoStage("ai", "active");
      demoMsg.textContent = "AI 正在为你设计出片方案…（30~60 秒）";

      const meta = {
        person_count: s.personCount,
        scene_mode: s.sceneMode,
        quality_mode: s.qualityMode,
        style_keywords: s.styleKeywords,
        frame_meta: manifest.frames.map((f) => ({
          index: f.index,
          azimuth_deg: f.azimuth_deg,
          pitch_deg: f.pitch_deg || 0,
          roll_deg: f.roll_deg || 0,
          timestamp_ms: f.timestamp_ms || 0,
        })),
      };

      const modelCfg = getActiveModelConfig();
      const response = await analyze({
        meta,
        frames: frameBlobs,
        references: referenceBlobs,
        modelId: modelCfg.model_id,
        modelApiKey: modelCfg.api_key,
        modelBaseUrl: modelCfg.base_url,
      });
      setDemoStage("ai", "done");

      setDemoStage("render", "active");
      demoMsg.textContent = "整理结果，跳转中…";
      saveSettings(s);
      saveLastPrefs(s);
      wizard.markCompleted();

      saveFrames(
        manifest.frames.map((f, i) => ({
          index: i,
          azimuthDeg: f.azimuth_deg,
          src: f.url,
        })),
      );
      if (manifest.panorama_url) savePanoramaUrl(manifest.panorama_url);
      saveRefInspiration({
        count: (manifest.references || []).length,
        thumbs: (manifest.references || []).map((r) => r.url),
        names: ["参考照 1", "参考照 2", "参考照 3"].slice(
          0,
          (manifest.references || []).length,
        ),
      });
      saveResult(response);

      await new Promise((r) => setTimeout(r, 250));
      setDemoStage("render", "done");
      location.href = "/web/result.html";
    } catch (err) {
      console.error(err);
      // v9 UX polish #4 — use the shared error normaliser so demo /
      // capture / future pages give the user identical, calm copy.
      const { normaliseError } = await import("./error_messages.js");
      const norm = normaliseError(err);
      showDemoError(`运行失败：${norm.message}`);
      demoMsg.textContent = "失败 — 点击外侧关闭，再试一次";
    }
  });

  demoOverlay.addEventListener("click", (e) => {
    if (e.target === demoOverlay && demoError.style.display === "block") {
      demoOverlay.style.display = "none";
    }
  });
}
