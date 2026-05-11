/**
 * Home-page avatar gallery: lets the user pick which procedural avatar
 * goes into each "person slot" of the upcoming shoot.
 *
 * Layout:
 *   [Slot 1] [Slot 2] [Slot 3] [Slot 4]   ← N slots based on personCount
 *      ↑ active slot highlighted
 *   ┌───────────────────────────────────────────────────────┐
 *   │  7 thumbnails in a horizontal grid; click → assign to │
 *   │  the active slot, advance to next slot.               │
 *   └───────────────────────────────────────────────────────┘
 *
 * The thumbnails are rendered once on first paint with an offscreen
 * Three.js renderer (powered by avatar_builder.buildAvatar) and cached
 * as data URLs in sessionStorage so subsequent page loads are instant.
 *
 * Persistence: selection is saved as `apc.avatarPicks` in sessionStorage
 * (see store.js). Result page + scene_3d.js read it to render avatars.
 */
import {
  AVATAR_PRESETS,
  resolveAvatarPicks,
} from "./avatar_styles.js";
import { mountAvatarCardPreview } from "./avatar_card_preview.js";
import { loadAvatarPicks, saveAvatarPicks } from "./store.js";
import { loadAvatarManifest } from "./avatar_loader.js";
const RPM_DEFAULT_PICKS = [
  "female_youth_18",
  "male_casual_25",
  "female_casual_22",
  "female_elegant_30",
];

function resolveRpmPicks(stored, n, presetIds) {
  const valid = new Set(presetIds);
  const defaults = RPM_DEFAULT_PICKS.filter((id) => valid.has(id));
  for (const id of presetIds) {
    if (!defaults.includes(id)) defaults.push(id);
  }
  const fallback = defaults[0] || presetIds[0] || "";
  return Array.from({ length: n }, (_, i) => {
    const fromStored = Array.isArray(stored) ? stored[i] : null;
    if (fromStored && valid.has(fromStored)) return fromStored;
    return defaults[i % defaults.length] || fallback;
  });
}

/**
 * v7 — when the backend manifest is reachable AND every preset has a
 * real PNG thumbnail bundled, prefer the RPM avatars over the
 * procedural ones. Otherwise we fall back to the v6 procedural pack
 * so the gallery is never empty.
 *
 * Returns one of:
 *   - { kind: "rpm", presets, thumbs }   (real images, no Three.js render)
 *   - { kind: "legacy" }                 (use AVATAR_PRESETS + offscreen render)
 */
async function chooseAvatarSource() {
  try {
    const manifest = await loadAvatarManifest();
    if (!manifest || !manifest.presets || manifest.presets.length === 0) {
      return { kind: "legacy" };
    }
    // Probe a single thumbnail; if it 404s we treat the whole pack as
    // "not yet shipped" and fall back. Avoids 8 network errors on a
    // dev box that hasn't run the asset import script.
    const probe = manifest.presets[0].thumbnail;
    const ok = probe ? await imageHeadOk(probe) : false;
    if (!ok) return { kind: "legacy" };
    return { kind: "rpm", presets: manifest.presets };
  } catch {
    return { kind: "legacy" };
  }
}

function imageHeadOk(url) {
  return new Promise((resolve) => {
    const im = new Image();
    im.onload = () => resolve(true);
    im.onerror = () => resolve(false);
    im.src = url;
    setTimeout(() => resolve(false), 2500);
  });
}

/**
 * Initialise the gallery. Returns an interface to read/write the
 * current selection and react to person-count changes.
 *
 * @param {{
 *   slotsHost: HTMLElement,
 *   gridHost: HTMLElement,
 *   personCount: () => number,
 * }} hosts
 */
export function initAvatarGallery({ slotsHost, gridHost, personCount }) {
  let activeSlot = 0;
  let picks = loadAvatarPicks();
  // Per-slot bookkeeping so we never tear down a slot's WebGL context
  // unless its avatar id actually changed. Re-creating a renderer on
  // every click was the visible jank.
  const slotEntries = [];   // [{ button, disposer, avatarId }]
  let gridPreviewDisposers = [];
  let gridBuildSeq = 0;
  let slotRenderSeq = 0;
  let gridBuildPromise = null;
  let activeFilter = "female"; // 女生 / 男生 / 小孩
  // Tabs live in the page; bind once, drive the grid via CSS only so
  // switching a tab is O(N) class toggles, not a 3D rebuild.
  const tabsHost = document.getElementById("avatar-tabs");
  if (tabsHost) {
    tabsHost.addEventListener("click", (ev) => {
      const btn = ev.target.closest(".avatar-tab");
      if (!btn) return;
      activeFilter = btn.dataset.filter || "female";
      tabsHost.querySelectorAll(".avatar-tab").forEach((b) => {
        b.classList.toggle("is-active", b === btn);
      });
      applyGridFilter();
    });
  }

  // v7 — race the RPM-manifest probe against the procedural fallback.
  // When RPM is available we use real preset pngs; otherwise we
  // render the legacy procedural set offscreen.
  const sourcePromise = chooseAvatarSource();
  // The catalog used by the grid: RPM presets when available, legacy
  // procedural otherwise.
  const catalogPromise = sourcePromise.then((src) => {
    if (src.kind === "rpm") {
      return orderCatalog(src.presets.map((p) => ({
        id: p.id,
        name: p.nameZh || p.name_zh || p.id,
        gender: p.gender || (p.id?.startsWith("female") ? "female" : p.id?.startsWith("male") ? "male" : ""),
        age: typeof p.age === "number" ? p.age : 99,
      })));
    }
    return AVATAR_PRESETS.map((s) => ({
      id: s.id,
      name: s.name,
      gender: s.gender || "",
      age: 99,
    }));
  });

  function setActiveSlot(i) {
    activeSlot = i;
    // Highlight only — do NOT rebuild slot WebGL contexts. The previous
    // implementation called refreshSlots() here, which disposed all
    // 4 renderers and re-cloned every glb just to move the ring color.
    slotEntries.forEach((e, idx) => {
      e?.button?.classList?.toggle("active", idx === i);
    });
    refreshGridSelection();
  }

  function setPick(i, avatarId) {
    if (picks[i] === avatarId) {
      // Same avatar: nothing to rebuild, but still advance focus.
      const n = personCount();
      if (i < n - 1) setActiveSlot(i + 1);
      return;
    }
    picks[i] = avatarId;
    saveAvatarPicks(picks);
    updateSlotAvatar(i, avatarId);
    refreshGridSelection();
  }

  function updateSlotAvatar(i, avatarId) {
    const entry = slotEntries[i];
    if (!entry) return;
    if (entry.avatarId === avatarId && entry.disposer) return;
    // Rebuild only this one slot's preview.
    entry.disposer?.dispose?.();
    const previewEl = entry.button.querySelector(".avatar-slot-preview");
    if (!previewEl) return;
    entry.avatarId = avatarId;
    entry.disposer = mountAvatarCardPreview(previewEl, {
      avatarId,
      compact: true,
      interactive: true,
    });
  }

  function refreshSlots() {
    const renderSeq = ++slotRenderSeq;
    const n = personCount();
    if (n <= 0) {
      disposeSlotPreviews();
      disposeGridPreviews();
      slotsHost.innerHTML = "";
      gridHost.innerHTML = "";
      gridBuildSeq += 1;
      gridBuildPromise = null;
      return;
    }
    if (activeSlot >= n) activeSlot = n - 1;
    if (activeSlot < 0) activeSlot = 0;
    ensureGridBuilt();
    Promise.all([catalogPromise, sourcePromise]).then(([catalog, src]) => {
      if (renderSeq !== slotRenderSeq) return;
      const normalized = src.kind === "rpm"
        ? resolveRpmPicks(picks, n, catalog.map((x) => x.id))
        : resolveAvatarPicks(picks, n);
      if (JSON.stringify(normalized) !== JSON.stringify(picks)) {
        picks = normalized;
        saveAvatarPicks(picks);
      } else {
        picks = normalized;
      }
      // Trim slots that no longer fit.
      while (slotEntries.length > n) {
        const e = slotEntries.pop();
        e?.disposer?.dispose?.();
        e?.button?.remove();
      }
      // Add or update the rest.
      for (let i = 0; i < n; i++) {
        let entry = slotEntries[i];
        if (!entry) {
          const slot = document.createElement("button");
          slot.type = "button";
          slot.className = "avatar-slot" + (i === activeSlot ? " active" : "");
          slot.dataset.slot = String(i);
          // Labels removed per design — the 3D thumb itself communicates
          // who's in this slot. Keeps the row compact and noise-free.
          slot.innerHTML = `
            <div class="avatar-slot-preview" aria-hidden="true"></div>
            <span class="avatar-slot-num">${i + 1}</span>
          `;
          slot.addEventListener("click", () => setActiveSlot(i));
          slotsHost.appendChild(slot);
          entry = { button: slot, disposer: null, avatarId: null };
          slotEntries[i] = entry;
        } else {
          entry.button.classList.toggle("active", i === activeSlot);
        }
        if (entry.avatarId !== picks[i]) {
          updateSlotAvatar(i, picks[i]);
        }
      }
    });
  }

  function refreshGridSelection() {
    const cells = gridHost.querySelectorAll("[data-avatar-id]");
    cells.forEach((c) => {
      c.classList.toggle("active", c.dataset.avatarId === picks[activeSlot]);
    });
  }

  function buildGrid() {
    const buildSeq = ++gridBuildSeq;
    disposeGridPreviews();
    gridHost.innerHTML = "";
    return Promise.all([catalogPromise]).then(([catalog]) => {
      if (buildSeq !== gridBuildSeq) return;
      for (const style of catalog) {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "avatar-cell";
        cell.dataset.avatarId = style.id;
        cell.dataset.gender = style.gender || "";
        cell.dataset.age = String(style.age ?? 99);
        // Title attribute keeps the (optional) Chinese name accessible
        // on hover without crowding the visual grid.
        if (style.name) cell.title = style.name;
        cell.innerHTML = `<div class="avatar-cell-preview" aria-hidden="true"></div>`;
        cell.addEventListener("click", () => {
          setPick(activeSlot, style.id);
          const n = personCount();
          if (activeSlot < n - 1) setActiveSlot(activeSlot + 1);
        });
        gridHost.appendChild(cell);
        gridPreviewDisposers.push(
          mountAvatarCardPreview(cell.querySelector(".avatar-cell-preview"), {
            avatarId: style.id,
            compact: false,
            interactive: true,
          }),
        );
      }
      applyGridFilter();
      refreshGridSelection();
    });
  }

  function applyGridFilter() {
    const cells = gridHost.querySelectorAll(".avatar-cell");
    cells.forEach((c) => {
      const age = Number(c.dataset.age || 99);
      const gender = c.dataset.gender || "";
      let show;
      if (activeFilter === "child") show = age < 14;
      else show = gender === activeFilter && age >= 14;
      c.classList.toggle("is-hidden", !show);
    });
  }

  function ensureGridBuilt() {
    if (!gridBuildPromise) {
      gridBuildPromise = buildGrid().finally(() => {
        gridBuildPromise = null;
      });
    }
  }

  function disposeSlotPreviews() {
    slotEntries.forEach((e) => {
      e?.disposer?.dispose?.();
      e?.button?.remove();
    });
    slotEntries.length = 0;
  }

  function disposeGridPreviews() {
    gridPreviewDisposers.forEach((view) => view?.dispose?.());
    gridPreviewDisposers = [];
  }

  ensureGridBuilt();
  refreshSlots();

  return {
    onPersonCountChanged: refreshSlots,
    getPicks: () => [...picks],
    dispose() {
      disposeSlotPreviews();
      disposeGridPreviews();
    },
  };
}

function orderCatalog(catalog) {
  const rank = new Map(RPM_DEFAULT_PICKS.map((id, i) => [id, i]));
  return [...catalog].sort((a, b) => {
    const ar = rank.has(a.id) ? rank.get(a.id) : 999;
    const br = rank.has(b.id) ? rank.get(b.id) : 999;
    if (ar !== br) return ar - br;
    return String(a.name).localeCompare(String(b.name), "zh-Hans-CN");
  });
}
