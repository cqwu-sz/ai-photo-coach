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
import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js";

import { buildAvatar } from "./avatar_builder.js";
import {
  AVATAR_PRESETS,
  DEFAULT_AVATAR_PICK,
  resolveAvatarPicks,
} from "./avatar_styles.js";
import { applyPosePreset } from "./pose_presets.js";
import { loadAvatarPicks, saveAvatarPicks } from "./store.js";

const THUMB_CACHE_KEY = "apc.avatarThumbs.v1";

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
  let picks = resolveAvatarPicks(loadAvatarPicks(), personCount());

  // Render thumbnails (cached). Returns a {[id]: dataUrl} map.
  const thumbsPromise = renderAllThumbnails();

  function setActiveSlot(i) {
    activeSlot = i;
    refreshSlots();
    refreshGridSelection();
  }

  function setPick(i, avatarId) {
    picks[i] = avatarId;
    saveAvatarPicks(picks);
    refreshSlots();
    refreshGridSelection();
  }

  function refreshSlots() {
    const n = personCount();
    if (picks.length !== n) {
      picks = resolveAvatarPicks(picks, n);
      saveAvatarPicks(picks);
    }
    if (activeSlot >= n) activeSlot = n - 1;
    if (activeSlot < 0) activeSlot = 0;
    slotsHost.innerHTML = "";
    thumbsPromise.then((thumbs) => {
      for (let i = 0; i < n; i++) {
        const slot = document.createElement("button");
        slot.type = "button";
        slot.className = "avatar-slot" + (i === activeSlot ? " active" : "");
        slot.dataset.slot = String(i);
        const style = AVATAR_PRESETS.find((p) => p.id === picks[i]) || AVATAR_PRESETS[0];
        const tn = thumbs[picks[i]] || thumbs[AVATAR_PRESETS[0].id];
        slot.innerHTML = `
          <img src="${tn || ""}" alt="${style.name}" />
          <span class="avatar-slot-num">${i + 1}</span>
          <span class="avatar-slot-name">${style.name}</span>
        `;
        slot.addEventListener("click", () => setActiveSlot(i));
        slotsHost.appendChild(slot);
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
    gridHost.innerHTML = "";
    thumbsPromise.then((thumbs) => {
      for (const style of AVATAR_PRESETS) {
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "avatar-cell";
        cell.dataset.avatarId = style.id;
        cell.innerHTML = `
          <img src="${thumbs[style.id] || ""}" alt="${style.name}" />
          <div class="avatar-cell-meta">
            <b>${style.name}</b>
            <span>${style.summary}</span>
          </div>
        `;
        cell.addEventListener("click", () => {
          setPick(activeSlot, style.id);
          // Auto-advance to next slot for fast filling
          const n = personCount();
          if (activeSlot < n - 1) setActiveSlot(activeSlot + 1);
        });
        gridHost.appendChild(cell);
      }
      refreshGridSelection();
    });
  }

  buildGrid();
  refreshSlots();

  return {
    onPersonCountChanged: refreshSlots,
    getPicks: () => [...picks],
  };
}

// ---------------------------------------------------------------------------
// Thumbnail renderer (Three.js, offscreen)
// ---------------------------------------------------------------------------

async function renderAllThumbnails() {
  // Try cache first
  try {
    const cached = JSON.parse(sessionStorage.getItem(THUMB_CACHE_KEY) || "null");
    if (cached && AVATAR_PRESETS.every((p) => cached[p.id])) return cached;
  } catch {}

  const out = {};
  // Single hidden renderer reused for all 7 — avoids 7 WebGL contexts
  const W = 256;
  const H = 320;
  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: true,
    preserveDrawingBuffer: true,
  });
  renderer.setSize(W, H, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  for (const style of AVATAR_PRESETS) {
    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(28, W / H, 0.05, 50);
    cam.position.set(0, 1.05, 3.6);
    cam.lookAt(0, 0.95, 0);

    scene.add(new THREE.HemisphereLight(0xddeeff, 0xffd9b8, 0.85));
    const key = new THREE.DirectionalLight(0xfff1d6, 1.0);
    key.position.set(2, 4, 3);
    scene.add(key);

    const ground = new THREE.Mesh(
      new THREE.CircleGeometry(0.8, 24),
      new THREE.MeshStandardMaterial({
        color: 0x000000,
        roughness: 0.9,
        transparent: true,
        opacity: 0.4,
      }),
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);

    const av = buildAvatar(style);
    av.setExpression("joy");
    // Apply a friendly default pose
    applyPosePreset("hands_clasped", av.joints);
    scene.add(av.root);

    renderer.render(scene, cam);
    out[style.id] = renderer.domElement.toDataURL("image/webp", 0.85);

    av.dispose();
    scene.clear();
  }
  renderer.dispose();

  try {
    sessionStorage.setItem(THUMB_CACHE_KEY, JSON.stringify(out));
  } catch {
    // Quota exceeded → ignore, we'll re-render next page load.
  }
  return out;
}
