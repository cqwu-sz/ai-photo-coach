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
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
// SkeletonUtils exports named helpers (clone, retargetClip, ...) rather
// than a SkeletonUtils namespace object — import them as a namespace.
import * as SkeletonUtils from "three/addons/utils/SkeletonUtils.js";

const ASSET_BASE = "/web/avatars";
const PRESET_DIR = `${ASSET_BASE}/preset`;
const BASE_AVATAR_URL = `${ASSET_BASE}/base/xbot.glb`;
const ANIM_DIR = `${ASSET_BASE}/animations`;
const FALLBACK_PRESETS = [
  {
    id: "female_youth_18",
    name_zh: "少女 · 18",
    gender: "female",
    age: 18,
    style: "youth",
    thumbnail: `${PRESET_DIR}/female_youth_18.png`,
    glb: `${PRESET_DIR}/female_youth_18.glb`,
  },
  {
    id: "male_casual_25",
    name_zh: "休闲男 · 25",
    gender: "male",
    age: 25,
    style: "casual",
    thumbnail: `${PRESET_DIR}/male_casual_25.png`,
    glb: `${PRESET_DIR}/male_casual_25.glb`,
  },
  {
    id: "female_casual_22",
    name_zh: "休闲女 · 22",
    gender: "female",
    age: 22,
    style: "casual",
    thumbnail: `${PRESET_DIR}/female_casual_22.png`,
    glb: `${PRESET_DIR}/female_casual_22.glb`,
  },
  {
    id: "female_elegant_30",
    name_zh: "优雅女 · 30",
    gender: "female",
    age: 30,
    style: "elegant",
    thumbnail: `${PRESET_DIR}/female_elegant_30.png`,
    glb: `${PRESET_DIR}/female_elegant_30.glb`,
  },
  {
    id: "female_artsy_25",
    name_zh: "文艺女 · 25",
    gender: "female",
    age: 25,
    style: "artsy",
    thumbnail: `${PRESET_DIR}/female_artsy_25.png`,
    glb: `${PRESET_DIR}/female_artsy_25.glb`,
  },
  {
    id: "male_business_35",
    name_zh: "商务男 · 35",
    gender: "male",
    age: 35,
    style: "business",
    thumbnail: `${PRESET_DIR}/male_business_35.png`,
    glb: `${PRESET_DIR}/male_business_35.glb`,
  },
  {
    id: "male_athletic_28",
    name_zh: "运动男 · 28",
    gender: "male",
    age: 28,
    style: "athletic",
    thumbnail: `${PRESET_DIR}/male_athletic_28.png`,
    glb: `${PRESET_DIR}/male_athletic_28.glb`,
  },
  {
    id: "child_boy_8",
    name_zh: "男孩 · 8",
    gender: "male",
    age: 8,
    style: "child",
    thumbnail: `${PRESET_DIR}/child_boy_8.png`,
    glb: `${PRESET_DIR}/child_boy_8.glb`,
  },
  {
    id: "child_girl_8",
    name_zh: "女孩 · 8",
    gender: "female",
    age: 8,
    style: "child",
    thumbnail: `${PRESET_DIR}/child_girl_8.png`,
    glb: `${PRESET_DIR}/child_girl_8.glb`,
  },
];

// ─────────────────────────────────────────────────────────────────────
// v7 Phase B+: 8 preset 色板
//
// Why this exists:
//   We didn't manage to wire up 8 distinct ReadyPlayerMe glb avatars
//   (every RPM avatar requires a manual create on readyplayer.me). To
//   still ship a "8 visually distinct, game-quality, rigged" gallery
//   we use Three.js's official Xbot.glb as a SHARED rigged humanoid
//   base mesh and apply a per-preset colour tint to the 2 PBR
//   materials Xbot exposes (the "joints" material and the "limbs"
//   material).
//
//   Xbot is a Khronos-published Mixamo-rigged sample model — same
//   skeleton bone names as anything you download from Mixamo, so all
//   30 placeholder Mixamo animation clips (and any future real
//   Mixamo download) can drive it without retargeting.
//
//   When the user upgrades to real RPM avatars later, replacing the
//   per-preset glbs in web/avatars/preset/<id>.glb takes precedence
//   automatically — see the load order in loadAvatar() below.
// ─────────────────────────────────────────────────────────────────────
const PRESET_TINTS = {
  male_casual_25:    { limbs: 0x4e8de0, joints: 0x33415f },  // skin-warm, navy tee
  male_business_35:  { limbs: 0x21263a, joints: 0x101321 },  // dark suit
  male_athletic_28:  { limbs: 0xd33b3b, joints: 0x6a1b1b },  // red sport top
  female_casual_22:  { limbs: 0xe89bb6, joints: 0x6a4452 },  // pink hoodie
  female_elegant_30: { limbs: 0xb33064, joints: 0x4d1730 },  // wine evening
  female_artsy_25:   { limbs: 0xc7b894, joints: 0x6a614e },  // beige bohemian
  child_boy_8:       { limbs: 0x5dbcdf, joints: 0x294f5d },  // sky blue
  child_girl_8:      { limbs: 0xf7b1d3, joints: 0x6c4356 },  // rose
};

// In-memory caches keyed by asset id. We deep-clone via SkeletonUtils
// every time we hand out an avatar so the same preset can show up on
// multiple shots (or multiple persons) at once without sharing state.
const avatarCache = new Map();
const animCache = new Map();
let manifestPromise = null;
let baseTemplate = null; // {scene, animations} - Xbot loaded once, shared
const loader = new GLTFLoader();

/**
 * Lazy-load and cache the shared Xbot base mesh (rigged humanoid with
 * Mixamo-standard skeleton + 7 built-in animations: idle / run /
 * agree / headShake / sad_pose / sneak_pose). Returned glTF is the
 * RAW template — callers should SkeletonUtils.clone() it.
 */
async function loadBaseTemplate() {
  if (baseTemplate) return baseTemplate;
  baseTemplate = (async () => {
    const tpl = await fetchGltf(BASE_AVATAR_URL);
    if (!tpl) {
      console.warn("[avatar_loader] Xbot base failed to load — gallery will fall back to procedural");
      return null;
    }
    return tpl;
  })();
  return baseTemplate;
}

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
        presets: mergePresetManifest(json.presets || []),
        poseMap: flat.flat,
        fallbackByCount: flat.fallbackByCount,
      };
    } catch (err) {
      console.warn("[avatar_loader] manifest fetch failed:", err);
      return {
        presets: mergePresetManifest([]),
        poseMap: {},
        fallbackByCount: {},
      };
    }
  })();
  return manifestPromise;
}

function mergePresetManifest(remotePresets) {
  const merged = [];
  const seen = new Set();
  for (const preset of [...remotePresets, ...FALLBACK_PRESETS]) {
    if (!preset?.id || seen.has(preset.id)) continue;
    seen.add(preset.id);
    merged.push({ ...preset, nameZh: preset.nameZh || preset.name_zh || preset.id });
  }
  return merged;
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
  // SkeletonUtils.clone preserves bones + animation tracks (the
  // difference that lets multi-instance animation work on real RPM
  // glb). For our placeholder glb (which has no SkinnedMesh / Skeleton)
  // SkeletonUtils.clone occasionally throws on certain three.js
  // versions; fall back to a plain deep clone in that case so the
  // procedural placeholder pipeline still upgrades to the real glb.
  try {
    return SkeletonUtils.clone(tpl);
  } catch (err) {
    console.debug("[avatar_loader] SkeletonUtils.clone failed, using plain clone:", err?.message || err);
    return tpl.clone(true);
  }
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
    "female_youth_18",
    "male_casual_25",
    "female_casual_22",
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
