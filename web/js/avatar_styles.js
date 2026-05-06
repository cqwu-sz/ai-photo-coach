/**
 * Catalog of preset anime-style avatars used by the gallery + 3D scene.
 *
 * Designed for **maximum visual differentiation at first glance** — each
 * has a unique hairstyle + dominant outfit color so users can identify
 * "the pink-haired one in the dress" at a glance instead of squinting at
 * 7 lookalikes.
 *
 * Genders: 2 male, 5 female (matches the user request).
 *
 * Each entry feeds straight into avatar_builder.buildAvatar().
 */

/** @typedef {import("./avatar_builder.js").AvatarStyle} AvatarStyle */

/** @type {AvatarStyle[]} */
export const AVATAR_PRESETS = [
  {
    id: "akira",
    gender: "male",
    name: "彻 Akira",
    summary: "黑短发 · 蓝衬衫",
    height: 1.78,
    skinHue: 24, skinLightness: 0.74,
    hairColor: "#1a1a22",
    hair: "short",
    top: "short_sleeve",
    topColor: "#3a6dbe",
    bottom: "jeans",
    bottomColor: "#2a3a5a",
    shoeColor: "#1c1c1c",
    accessory: "none",
  },
  {
    id: "jun",
    gender: "male",
    name: "纯 Jun",
    summary: "棕寸头 · 黑夹克 · 眼镜",
    height: 1.81,
    skinHue: 26, skinLightness: 0.72,
    hairColor: "#5a4030",
    hair: "buzz",
    top: "jacket",
    topColor: "#1a1a1f",
    bottom: "pants",
    bottomColor: "#3a3340",
    shoeColor: "#0d0d0d",
    accessory: "glasses",
    accessoryColor: "#222222",
  },
  {
    id: "yuki",
    gender: "female",
    name: "雪 Yuki",
    summary: "黑长直发 · 白连衣裙",
    height: 1.62,
    skinHue: 22, skinLightness: 0.82,
    hairColor: "#101018",
    hair: "long_straight",
    top: "dress",
    topColor: "#f8f5ee",
    bottom: "long_skirt",
    bottomColor: "#f8f5ee",
    shoeColor: "#a48b6a",
    accessory: "none",
  },
  {
    id: "sakura",
    gender: "female",
    name: "樱 Sakura",
    summary: "粉色双马尾 · 粉色短裙",
    height: 1.58,
    skinHue: 22, skinLightness: 0.84,
    hairColor: "#f59ac4",
    hair: "twin_tails",
    top: "short_sleeve",
    topColor: "#ffffff",
    bottom: "skirt",
    bottomColor: "#f590b5",
    shoeColor: "#f55090",
    accessory: "hairband",
    accessoryColor: "#ffffff",
  },
  {
    id: "rena",
    gender: "female",
    name: "玲奈 Rena",
    summary: "棕色波波头 · 黄毛衣",
    height: 1.63,
    skinHue: 24, skinLightness: 0.78,
    hairColor: "#7a4f2a",
    hair: "bob",
    top: "sweater",
    topColor: "#f5c64a",
    bottom: "jeans",
    bottomColor: "#5a6a8a",
    shoeColor: "#7a3a2a",
    accessory: "none",
  },
  {
    id: "luna",
    gender: "female",
    name: "露娜 Luna",
    summary: "银色长卷 · 黑外套",
    height: 1.66,
    skinHue: 22, skinLightness: 0.86,
    hairColor: "#c8c8d0",
    hair: "long_curly",
    top: "jacket",
    topColor: "#1a1a26",
    bottom: "pants",
    bottomColor: "#2a2a36",
    shoeColor: "#1a1a26",
    accessory: "none",
  },
  {
    id: "haruko",
    gender: "female",
    name: "春子 Haruko",
    summary: "红狼尾 · 牛仔风",
    height: 1.64,
    skinHue: 22, skinLightness: 0.78,
    hairColor: "#c83838",
    hair: "wolf_tail",
    top: "short_sleeve",
    topColor: "#ffffff",
    bottom: "shorts",
    bottomColor: "#3a527a",
    shoeColor: "#3a3a3a",
    accessory: "none",
  },
];

/**
 * Default per-slot pick (when user hasn't customized yet).
 * The 4 default slots are spread across genders + hair tones for instant
 * variety in a 4-person scene.
 */
export const DEFAULT_AVATAR_PICK = ["akira", "yuki", "sakura", "luna"];

/**
 * Look up a preset by id, falling back to the first one if the id is bad.
 */
export function getAvatarStyle(id) {
  return AVATAR_PRESETS.find((p) => p.id === id) || AVATAR_PRESETS[0];
}

/**
 * Pick avatar IDs for N persons, falling back to defaults if the user's
 * stored selection is shorter or missing.
 */
export function resolveAvatarPicks(stored, n) {
  const picks = [];
  for (let i = 0; i < n; i++) {
    const fromStored = Array.isArray(stored) ? stored[i] : null;
    picks.push(
      fromStored && AVATAR_PRESETS.find((p) => p.id === fromStored)
        ? fromStored
        : DEFAULT_AVATAR_PICK[i % DEFAULT_AVATAR_PICK.length],
    );
  }
  return picks;
}
