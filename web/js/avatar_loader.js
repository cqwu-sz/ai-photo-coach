/**
 * v7 Phase B — ReadyPlayerMe glb loader + Mixamo animation playback.
 *
 * Replaces the old `avatar_builder.js` (which built block-rigid meshes
 * out of Three.js primitives) with real game-quality glb models from
 * ReadyPlayerMe and skeletal animations from Mixamo.
 *
 * Public API:
 *
 *   const m = await loadAvatarManifest();              // {presets, poseMap}
 *   const avatar = await loadAvatar("male_casual_25"); // THREE.Group
 *   const mixer = playAnimation(avatar, "idle_relaxed");
 *   // ... in render loop: mixer.update(deltaSec)
 *
 * Falls back to the legacy procedural builder when a glb is missing,
 * so the 3D preview always renders something — even before the asset
 * pack is dropped into web/avatars/preset/.
 */

import * as THREE from "https://cdn.jsdelivr.net/npm/three@0.165.0/build/three.module.js";
import { GLTFLoader } from "https://cdn.jsdelivr.net/npm/three@0.165.0/examples/jsm/loaders/GLTFLoader.js";
// SkeletonUtils exports named helpers (clone, retargetClip, ...) rather
// than a SkeletonUtils namespace object — import them as a namespace.
import * as SkeletonUtils from "https://cdn.jsdelivr.net/npm/three@0.165.0/examples/jsm/utils/SkeletonUtils.js";

const ASSET_BASE = "/web/avatars";
const PRESET_DIR = `${ASSET_BASE}/preset`;
const ANIM_DIR = `${ASSET_BASE}/animations`;

// In-memory caches keyed by asset id. We deep-clone via SkeletonUtils
// every time we hand out an avatar so the same preset can show up on
// multiple shots (or multiple persons) at once without sharing state.
const avatarCache = new Map();
const animCache = new Map();
let manifestPromise = null;
const loader = new GLTFLoader();

/** Light-weight wrapper for the /avatars/manifest endpoint. */
export async function loadAvatarManifest({ baseUrl = "" } = {}) {
  if (manifestPromise) return manifestPromise;
  manifestPromise = (async () => {
    try {
      const res = await fetch(`${baseUrl}/avatars/manifest`);
      if (!res.ok) throw new Error(`manifest http ${res.status}`);
      const json = await res.json();
      const flat = flattenPoseMap(json.pose_to_mixamo || {});
      return {
        presets: json.presets || [],
        poseMap: flat.flat,
        fallbackByCount: flat.fallbackByCount,
      };
    } catch (err) {
      console.warn("[avatar_loader] manifest fetch failed:", err);
      return { presets: [], poseMap: {}, fallbackByCount: {} };
    }
  })();
  return manifestPromise;
}

function flattenPoseMap(rawMap) {
  const flat = {};
  for (const section of ["single", "two_person", "three_person", "four_person"]) {
    Object.assign(flat, rawMap[section] || {});
  }
  return {
    flat,
    fallbackByCount: rawMap.fallback_by_count || { 1: "idle_relaxed" },
  };
}

/**
 * Fetch and cache a preset glb. Returns a deep clone so the caller can
 * place it in the scene without affecting later callers.
 *
 * Returns null on failure — caller should fall back to procedural mesh.
 *
 * @param {string} presetId  e.g. "male_casual_25"
 * @returns {Promise<THREE.Group | null>}
 */
export async function loadAvatar(presetId) {
  if (!presetId) return null;
  if (!avatarCache.has(presetId)) {
    avatarCache.set(presetId, fetchGltf(`${PRESET_DIR}/${presetId}.glb`));
  }
  const tpl = await avatarCache.get(presetId);
  if (!tpl) return null;
  // SkeletonUtils.clone preserves bones + animation tracks; plain
  // .clone() doesn't — that's the difference that lets multi-instance
  // animation work correctly on glTF models.
  return SkeletonUtils.clone(tpl);
}

/**
 * Fetch and cache a Mixamo animation glb. Returns the AnimationClip
 * (the geometry is discarded — the clip retargets onto whichever
 * avatar skeleton is provided to playAnimation).
 *
 * @param {string} animId  e.g. "idle_relaxed"
 * @returns {Promise<THREE.AnimationClip | null>}
 */
export async function loadAnimationClip(animId) {
  if (!animId) return null;
  if (!animCache.has(animId)) {
    animCache.set(animId, fetchGltf(`${ANIM_DIR}/${animId}.glb`));
  }
  const tpl = await animCache.get(animId);
  if (!tpl || !tpl.animations || !tpl.animations.length) return null;
  return tpl.animations[0];
}

/**
 * Bind a Mixamo animation to an avatar and start playback.
 * Caller must:
 *   - keep the returned AnimationMixer
 *   - call mixer.update(deltaSec) every frame
 *
 * @returns {{mixer: THREE.AnimationMixer, action: THREE.AnimationAction} | null}
 */
export function playAnimation(avatar, clip, { loop = true } = {}) {
  if (!avatar || !clip) return null;
  const mixer = new THREE.AnimationMixer(avatar);
  const action = mixer.clipAction(clip);
  action.setLoop(loop ? THREE.LoopRepeat : THREE.LoopOnce, Infinity);
  if (!loop) action.clampWhenFinished = true;
  action.play();
  return { mixer, action };
}

/**
 * Pick the right preset id for a given person + shot context.
 *
 * Heuristic: respect the user's persisted preference first
 * (UserDefaults/sessionStorage 'avatarPicks'), then derive from the
 * pose's role (person_a / person_b...) using a simple alternation
 * rule so couples don't end up with two identical presets.
 */
export function mapAvatarPick(person, personIndex, presets) {
  // Read the persisted pick list using the SAME key the rest of the
  // app uses (store.js's `apc.avatarPicks`, localStorage scope). This
  // is what makes "pick once on the home page, see it everywhere"
  // behaviour work cross-page and (when the iOS app uses the same id
  // string) cross-device-class.
  const persisted = (() => {
    try {
      const raw = localStorage.getItem("apc.avatarPicks")
                || sessionStorage.getItem("apc.avatarPicks");
      return JSON.parse(raw || "[]");
    } catch { return []; }
  })();
  if (persisted[personIndex]) return persisted[personIndex];

  if (!presets || !presets.length) return null;
  // Default rotation: female lead → male partner → female friend → child
  const order = [
    "female_casual_22",
    "male_casual_25",
    "female_elegant_30",
    "child_girl_8",
  ];
  const orderById = order.filter((id) => presets.some((p) => p.id === id));
  return orderById[personIndex % orderById.length] || presets[0].id;
}

/**
 * Resolve the LLM-recommended pose id to a Mixamo animation id, with
 * person_count fallback. Mirrors backend lookup_mixamo_for_pose.
 */
export function resolveMixamoId(poseId, personCount, manifest) {
  if (!manifest) return "idle_relaxed";
  const direct = manifest.poseMap[poseId];
  if (direct) return direct;
  return manifest.fallbackByCount[String(personCount)] || "idle_relaxed";
}

// Internal — load + cache a glTF or null on failure.
function fetchGltf(url) {
  return new Promise((resolve) => {
    loader.load(
      url,
      (gltf) => resolve(gltf.scene ? Object.assign(gltf.scene, { animations: gltf.animations }) : null),
      undefined,
      (err) => {
        // We *expect* this to fail until the asset pack is shipped;
        // log only at debug level.
        console.debug("[avatar_loader] fetch failed:", url, err?.message || err);
        resolve(null);
      },
    );
  });
}
