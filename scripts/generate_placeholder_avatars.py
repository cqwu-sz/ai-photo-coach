"""v7 — generate placeholder glb avatars + PNG thumbnails + Mixamo
animation glbs.

Why this exists
---------------
The "real" v7 plan ships 8 ReadyPlayerMe characters and 30 Mixamo
animations. Generating those requires a free RPM/Adobe account + manual
clicks on their websites, which we can't do from inside the agent.

This script bridges the gap: it programmatically generates a *passable*
3-tone humanoid mesh per preset (different skin / hair / outfit colours,
slightly different proportions) so the new RealityKit / Three.js loader
pipeline has assets to load *today*. The visual quality is a step above
the v6 procedural builder because:

  - meshes are real glb (Three.js + RealityKit can both consume directly)
  - per-preset material differences are visible at a glance
  - the file shapes match what RPM exports, so dropping in the real
    assets later is a pure file-replace — no code changes

How to upgrade to real RPM avatars
----------------------------------
Run ``scripts/fetch_rpm_assets.py`` with a RPM API key (or download the
glb from readyplayer.me's Avatar Creator manually) and overwrite the
files in ``web/avatars/preset/``. The loader logic is identical.

Usage:
    python scripts/generate_placeholder_avatars.py
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pygltflib as gl


REPO_ROOT = Path(__file__).resolve().parent.parent
PRESET_DIR = REPO_ROOT / "web" / "avatars" / "preset"
ANIM_DIR = REPO_ROOT / "web" / "avatars" / "animations"
PRESET_DIR.mkdir(parents=True, exist_ok=True)
ANIM_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────
# Preset catalogue — keep in sync with backend/app/api/avatars.py.
# Each entry drives BOTH the glb mesh AND the matching PNG thumbnail.
# ─────────────────────────────────────────────────────────────────────
PRESETS: list[dict] = [
    {
        "id": "male_casual_25", "name_zh": "休闲男 · 25",
        "skin": (235, 198, 168), "hair": (40, 28, 22),
        "top": (60, 110, 200), "bottom": (40, 50, 80),
        "shoes": (28, 28, 32),
        "height": 1.78, "build": 1.0,
    },
    {
        "id": "male_business_35", "name_zh": "商务男 · 35",
        "skin": (224, 188, 158), "hair": (24, 18, 14),
        "top": (32, 36, 52), "bottom": (28, 32, 42),
        "shoes": (16, 16, 18),
        "height": 1.82, "build": 1.05,
    },
    {
        "id": "male_athletic_28", "name_zh": "运动男 · 28",
        "skin": (210, 168, 134), "hair": (52, 32, 22),
        "top": (220, 70, 50), "bottom": (28, 30, 36),
        "shoes": (240, 240, 244),
        "height": 1.80, "build": 1.10,
    },
    {
        "id": "female_casual_22", "name_zh": "休闲女 · 22",
        "skin": (245, 218, 192), "hair": (88, 50, 30),
        "top": (250, 190, 200), "bottom": (90, 110, 160),
        "shoes": (240, 240, 244),
        "height": 1.66, "build": 0.92,
    },
    {
        "id": "female_elegant_30", "name_zh": "优雅女 · 30",
        "skin": (240, 210, 180), "hair": (38, 22, 18),
        "top": (180, 50, 80), "bottom": (40, 32, 38),
        "shoes": (40, 28, 30),
        "height": 1.70, "build": 0.94,
    },
    {
        "id": "female_artsy_25", "name_zh": "文艺女 · 25",
        "skin": (250, 220, 196), "hair": (160, 80, 110),
        "top": (210, 200, 175), "bottom": (90, 80, 70),
        "shoes": (180, 150, 110),
        "height": 1.68, "build": 0.90,
    },
    {
        "id": "child_boy_8", "name_zh": "男孩 · 8",
        "skin": (250, 215, 185), "hair": (30, 22, 18),
        "top": (90, 200, 230), "bottom": (50, 70, 110),
        "shoes": (240, 240, 244),
        "height": 1.30, "build": 0.78,
    },
    {
        "id": "child_girl_8", "name_zh": "女孩 · 8",
        "skin": (252, 222, 196), "hair": (140, 80, 60),
        "top": (255, 180, 200), "bottom": (240, 220, 230),
        "shoes": (240, 240, 244),
        "height": 1.28, "build": 0.74,
    },
]


# ─────────────────────────────────────────────────────────────────────
# Mesh helpers — UV sphere + capsule + cylinder + box
# ─────────────────────────────────────────────────────────────────────

def uv_sphere(radius: float, lat: int = 12, lon: int = 18,
              cx: float = 0, cy: float = 0, cz: float = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return (positions, indices) for a UV sphere centred at (cx, cy, cz)."""
    verts = []
    for i in range(lat + 1):
        v = i / lat
        phi = math.pi * v
        for j in range(lon):
            u = j / lon
            theta = 2 * math.pi * u
            x = radius * math.sin(phi) * math.cos(theta)
            y = radius * math.cos(phi)
            z = radius * math.sin(phi) * math.sin(theta)
            verts.append((x + cx, y + cy, z + cz))
    idx = []
    for i in range(lat):
        for j in range(lon):
            a = i * lon + j
            b = i * lon + ((j + 1) % lon)
            c = (i + 1) * lon + j
            d = (i + 1) * lon + ((j + 1) % lon)
            idx.extend([a, c, b, b, c, d])
    return np.array(verts, dtype=np.float32), np.array(idx, dtype=np.uint32)


def capsule(radius: float, height: float, lat: int = 8, lon: int = 14,
            cx: float = 0, cy: float = 0, cz: float = 0) -> tuple[np.ndarray, np.ndarray]:
    """Vertical capsule: cylindrical middle + 2 hemisphere caps."""
    verts = []
    half = height / 2
    # Top hemisphere
    for i in range(lat + 1):
        v = i / lat
        phi = (math.pi / 2) * v
        for j in range(lon):
            u = j / lon
            theta = 2 * math.pi * u
            r = radius * math.cos(phi)
            y = half + radius * math.sin(phi)
            verts.append((cx + r * math.cos(theta), cy + y, cz + r * math.sin(theta)))
    # Bottom hemisphere (mirrored)
    for i in range(lat + 1):
        v = i / lat
        phi = (math.pi / 2) * v
        for j in range(lon):
            u = j / lon
            theta = 2 * math.pi * u
            r = radius * math.cos(phi)
            y = -half - radius * math.sin(phi)
            verts.append((cx + r * math.cos(theta), cy + y, cz + r * math.sin(theta)))
    idx = []
    n_top = (lat + 1) * lon
    # connect top hemisphere
    for i in range(lat):
        for j in range(lon):
            a = i * lon + j
            b = i * lon + ((j + 1) % lon)
            c = (i + 1) * lon + j
            d = (i + 1) * lon + ((j + 1) % lon)
            idx.extend([a, b, c, b, d, c])
    # connect bottom hemisphere (reversed winding)
    for i in range(lat):
        for j in range(lon):
            a = n_top + i * lon + j
            b = n_top + i * lon + ((j + 1) % lon)
            c = n_top + (i + 1) * lon + j
            d = n_top + (i + 1) * lon + ((j + 1) % lon)
            idx.extend([a, c, b, b, c, d])
    # connect cylindrical waist (top hem ring 0 ↔ bottom hem ring 0)
    for j in range(lon):
        a = 0 * lon + j
        b = 0 * lon + ((j + 1) % lon)
        c = n_top + 0 * lon + j
        d = n_top + 0 * lon + ((j + 1) % lon)
        idx.extend([a, c, b, b, c, d])
    return np.array(verts, dtype=np.float32), np.array(idx, dtype=np.uint32)


def estimate_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Per-vertex normal = average of incident face normals."""
    normals = np.zeros_like(positions)
    tri = indices.reshape(-1, 3)
    v0 = positions[tri[:, 0]]
    v1 = positions[tri[:, 1]]
    v2 = positions[tri[:, 2]]
    fn = np.cross(v1 - v0, v2 - v0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9)
    for i in range(3):
        np.add.at(normals, tri[:, i], fn)
    norms = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.where(norms > 1e-9, normals / norms, np.array([0, 1, 0], dtype=np.float32))
    return normals.astype(np.float32)


# ─────────────────────────────────────────────────────────────────────
# Build a humanoid as a list of (positions, indices, color, name) parts
# ─────────────────────────────────────────────────────────────────────

def build_humanoid(p: dict) -> list[tuple[np.ndarray, np.ndarray, tuple[int, int, int], str]]:
    """Return body parts: (positions, indices, rgb_int, name) tuples.

    Anatomy (proportions of total height):
      - feet at y=0
      - lower legs:   0.00 → 0.45  (capsule, r=0.06)
      - hips:         0.45 → 0.55  (sphere)
      - torso:        0.55 → 0.78  (capsule)
      - shoulders:    0.78
      - upper arms:   shoulder ± 0.15 → elbow at 0.55
      - head + neck:  0.85 → 1.00
    """
    h = p["height"]
    b = p["build"]
    parts = []

    # Hips (skin tone darker = pants colour) — represented by lower torso
    # We blend pants colour into hips for visual continuity.
    pants_color = p["bottom"]
    skin = p["skin"]
    top_color = p["top"]
    hair_color = p["hair"]
    shoes_color = p["shoes"]

    # Legs — two capsules; top of capsule at hip level (y=0.45h)
    leg_r = 0.07 * b
    leg_h = 0.40 * h  # half-height passed to capsule is height/2 below
    for sign, name in ((-1, "leg_l"), (1, "leg_r")):
        cy = 0.25 * h
        pos, idx = capsule(leg_r, 0.50 * h, cx=sign * 0.10 * b, cy=cy, cz=0)
        parts.append((pos, idx, pants_color, name))

    # Shoes — flat boxes at y=0
    shoe_r_w = 0.10 * b
    for sign, name in ((-1, "shoe_l"), (1, "shoe_r")):
        pos, idx = uv_sphere(0.07 * b, lat=6, lon=10,
                              cx=sign * 0.10 * b, cy=0.04, cz=0.04)
        # Flatten Y for shoe
        pos[:, 1] *= 0.45
        pos[:, 1] += 0.045
        parts.append((pos, idx, shoes_color, name))

    # Hips/pelvis sphere (pants colour)
    pos, idx = uv_sphere(0.13 * b, cy=0.50 * h)
    pos[:, 1] *= 0.85  # squish vertically
    pos[:, 1] += 0.50 * h * 0.15
    parts.append((pos, idx, pants_color, "hips"))

    # Torso (top colour)
    torso_r = 0.16 * b
    torso_cy = 0.66 * h
    pos, idx = capsule(torso_r, 0.18 * h, cy=torso_cy)
    parts.append((pos, idx, top_color, "torso"))

    # Arms — short capsules slung at the side. Top of capsule near shoulder.
    arm_r = 0.055 * b
    arm_cy = 0.66 * h
    arm_offset_x = 0.20 * b
    for sign, name in ((-1, "arm_l"), (1, "arm_r")):
        pos, idx = capsule(arm_r, 0.36 * h, cx=sign * arm_offset_x, cy=arm_cy)
        parts.append((pos, idx, top_color, name))
    # Hands at end of arms
    for sign, name in ((-1, "hand_l"), (1, "hand_r")):
        pos, idx = uv_sphere(arm_r * 1.3,
                              cx=sign * arm_offset_x, cy=arm_cy - 0.21 * h, cz=0)
        parts.append((pos, idx, skin, name))

    # Neck (skin)
    neck_cy = 0.85 * h
    pos, idx = uv_sphere(0.05 * b, cy=neck_cy)
    parts.append((pos, idx, skin, "neck"))

    # Head (skin)
    head_r = 0.11 * b
    head_cy = 0.93 * h
    pos, idx = uv_sphere(head_r, lat=14, lon=20, cy=head_cy)
    parts.append((pos, idx, skin, "head"))

    # Hair (helmet — half-sphere on top of head)
    pos, idx = uv_sphere(head_r * 1.05, lat=14, lon=20, cy=head_cy + 0.005)
    # Cull bottom half (below mid-head) to make a half-sphere
    mask = pos[:, 1] >= head_cy - 0.02
    keep_idx = np.where(mask)[0]
    remap = -np.ones(len(pos), dtype=np.int64)
    remap[keep_idx] = np.arange(len(keep_idx))
    new_pos = pos[keep_idx]
    tri = idx.reshape(-1, 3)
    keep_tri_mask = np.all(remap[tri] >= 0, axis=1)
    new_idx = remap[tri[keep_tri_mask]].flatten().astype(np.uint32)
    parts.append((new_pos, new_idx, hair_color, "hair"))

    return parts


# ─────────────────────────────────────────────────────────────────────
# glb writer — pure pygltflib
# ─────────────────────────────────────────────────────────────────────

def write_glb(parts, out_path: Path):
    g = gl.GLTF2()
    g.scenes = [gl.Scene(nodes=[])]
    g.scene = 0

    # Single buffer with all binary data appended
    binary_blob = bytearray()
    buffer_views = []
    accessors = []
    materials = []
    meshes = []
    nodes = []

    for (positions, indices, color, name) in parts:
        normals = estimate_normals(positions, indices)
        # 1) positions
        pos_bytes = positions.astype(np.float32).tobytes()
        bv_pos = gl.BufferView(
            buffer=0, byteOffset=len(binary_blob),
            byteLength=len(pos_bytes), target=gl.ARRAY_BUFFER,
        )
        binary_blob += pos_bytes
        # padding to 4 bytes
        while len(binary_blob) % 4: binary_blob.append(0)
        buffer_views.append(bv_pos)
        bv_pos_idx = len(buffer_views) - 1

        # 2) normals
        n_bytes = normals.astype(np.float32).tobytes()
        bv_n = gl.BufferView(
            buffer=0, byteOffset=len(binary_blob),
            byteLength=len(n_bytes), target=gl.ARRAY_BUFFER,
        )
        binary_blob += n_bytes
        while len(binary_blob) % 4: binary_blob.append(0)
        buffer_views.append(bv_n)
        bv_n_idx = len(buffer_views) - 1

        # 3) indices
        idx_bytes = indices.astype(np.uint32).tobytes()
        bv_idx = gl.BufferView(
            buffer=0, byteOffset=len(binary_blob),
            byteLength=len(idx_bytes), target=gl.ELEMENT_ARRAY_BUFFER,
        )
        binary_blob += idx_bytes
        while len(binary_blob) % 4: binary_blob.append(0)
        buffer_views.append(bv_idx)
        bv_idx_idx = len(buffer_views) - 1

        # accessors
        a_pos = gl.Accessor(
            bufferView=bv_pos_idx, componentType=gl.FLOAT, count=len(positions),
            type=gl.VEC3, max=positions.max(axis=0).tolist(),
            min=positions.min(axis=0).tolist(),
        )
        a_n = gl.Accessor(
            bufferView=bv_n_idx, componentType=gl.FLOAT, count=len(normals),
            type=gl.VEC3,
        )
        a_idx = gl.Accessor(
            bufferView=bv_idx_idx, componentType=gl.UNSIGNED_INT,
            count=len(indices), type=gl.SCALAR,
        )
        accessors.extend([a_pos, a_n, a_idx])
        a_pos_i = len(accessors) - 3
        a_n_i = len(accessors) - 2
        a_idx_i = len(accessors) - 1

        # material
        r, gg, bb = color
        mat = gl.Material(
            name=f"mat_{name}",
            pbrMetallicRoughness=gl.PbrMetallicRoughness(
                baseColorFactor=[r / 255, gg / 255, bb / 255, 1.0],
                metallicFactor=0.0,
                roughnessFactor=0.85,
            ),
            doubleSided=True,
        )
        materials.append(mat)
        mat_i = len(materials) - 1

        prim = gl.Primitive(
            attributes=gl.Attributes(POSITION=a_pos_i, NORMAL=a_n_i),
            indices=a_idx_i, material=mat_i,
        )
        mesh = gl.Mesh(name=name, primitives=[prim])
        meshes.append(mesh)
        m_i = len(meshes) - 1

        node = gl.Node(name=name, mesh=m_i)
        nodes.append(node)

    # Single root node holding all the parts so the importer sees one
    # scene root that can be transformed/animated as a whole.
    root = gl.Node(name="root", children=list(range(len(nodes))))
    nodes.append(root)
    g.scenes[0].nodes = [len(nodes) - 1]

    # Stitch glTF together
    g.buffers = [gl.Buffer(byteLength=len(binary_blob))]
    g.bufferViews = buffer_views
    g.accessors = accessors
    g.materials = materials
    g.meshes = meshes
    g.nodes = nodes
    g.set_binary_blob(bytes(binary_blob))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.save_binary(str(out_path))
    print(f"  [glb] {out_path.relative_to(REPO_ROOT)} ({out_path.stat().st_size / 1024:.0f} KB)")


# ─────────────────────────────────────────────────────────────────────
# Thumbnail PNG renderer — silhouette-style, NOT a 3D render.
# Good enough for the gallery cell preview at 96-256px.
# ─────────────────────────────────────────────────────────────────────

def render_thumbnail(p: dict, out_path: Path, size: int = 256):
    img = Image.new("RGBA", (size, size), (24, 24, 30, 255))
    d = ImageDraw.Draw(img)

    # Soft radial-ish background using two filled circles
    bg_centre = (size // 2, size // 2)
    for r, alpha in [(int(size * 0.55), 18), (int(size * 0.40), 28)]:
        d.ellipse(
            [bg_centre[0] - r, bg_centre[1] - r,
             bg_centre[0] + r, bg_centre[1] + r],
            fill=(80, 100, 140, alpha),
        )

    skin = p["skin"]
    hair = p["hair"]
    top = p["top"]
    bottom = p["bottom"]

    cx = size // 2
    body_y = int(size * 0.62)
    body_w = int(size * 0.42)
    body_h = int(size * 0.42)

    # Torso (top colour)
    d.rounded_rectangle(
        [cx - body_w // 2, body_y - body_h // 2,
         cx + body_w // 2, body_y + body_h // 2],
        radius=int(size * 0.10), fill=top + (255,),
    )
    # Bottom skirt-like shape
    d.rounded_rectangle(
        [cx - body_w // 2 - 4, body_y + body_h // 4,
         cx + body_w // 2 + 4, body_y + body_h // 2 + int(size * 0.04)],
        radius=int(size * 0.05), fill=bottom + (255,),
    )

    # Neck
    neck_h = int(size * 0.05)
    d.rounded_rectangle(
        [cx - 12, body_y - body_h // 2 - neck_h,
         cx + 12, body_y - body_h // 2 + 4],
        radius=4, fill=skin + (255,),
    )

    # Face circle
    head_r = int(size * 0.18)
    head_cy = int(size * 0.32)
    d.ellipse(
        [cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r],
        fill=skin + (255,),
    )

    # Hair: top arc
    d.pieslice(
        [cx - head_r - 4, head_cy - head_r - 6,
         cx + head_r + 4, head_cy + head_r - int(size * 0.06)],
        start=180, end=360, fill=hair + (255,),
    )

    # Eyes (subtle dots so it doesn't feel like a faceless mannequin)
    eye_y = head_cy + int(size * 0.02)
    eye_dx = int(size * 0.045)
    eye_r = max(2, int(size * 0.012))
    for sign in (-1, 1):
        ex = cx + sign * eye_dx
        d.ellipse([ex - eye_r, eye_y - eye_r, ex + eye_r, eye_y + eye_r],
                  fill=(40, 30, 30, 255))

    # Caption strip at bottom
    strip_h = int(size * 0.14)
    d.rectangle(
        [0, size - strip_h, size, size],
        fill=(0, 0, 0, 180),
    )
    try:
        font = ImageFont.truetype("msyh.ttc", int(size * 0.07))
    except Exception:
        try:
            font = ImageFont.truetype("simhei.ttf", int(size * 0.07))
        except Exception:
            font = ImageFont.load_default()
    text = p["name_zh"]
    try:
        bbox = d.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except Exception:
        tw = th = int(size * 0.07)
    d.text(
        (cx - tw // 2, size - strip_h // 2 - th // 2),
        text, fill=(240, 240, 250, 255), font=font,
    )

    img.save(out_path)
    print(f"  [png] {out_path.relative_to(REPO_ROOT)} ({out_path.stat().st_size / 1024:.0f} KB)")


# ─────────────────────────────────────────────────────────────────────
# Animation glb — minimal track that the AnimationMixer can bind to.
# We emit a tiny "breathing" or "rotation" animation on a single bone-
# less node. Real Mixamo retargeting requires a skeleton, but the
# AnimationMixer can still drive node-level translation/rotation, which
# is enough for the placeholder pipeline to play *some* motion.
# ─────────────────────────────────────────────────────────────────────

ANIMATION_IDS = [
    # single (9)
    "idle_relaxed", "idle_lean_wall", "idle_sit_low_wall",
    "pose_hand_in_hair", "pose_back_view", "pose_jumping",
    "pose_lying_grass", "pose_holding_object", "walk_natural",
    # two-person (11)
    "couple_high_low", "couple_forehead_touch", "couple_side_by_side",
    "couple_back_to_back", "couple_walk_handhold", "couple_running",
    "couple_dancing", "couple_seated_steps", "couple_embrace",
    "couple_piggyback", "family_lift_child",
    # three-person (6)
    "group_triangle_pose", "group_circle_jump", "group_diagonal_walk",
    "group_walking_line", "group_huddle", "family_three_seated",
    # four-person (2)
    "group_diamond_pose", "group_four_couch",
    # extras for robustness
    "idle_arms_crossed", "pose_thinking",
]


def animation_descriptor(anim_id: str) -> dict:
    """Return motion params for the placeholder animation. We pick a
    primary axis + amplitude per anim_id so each plays a slightly
    distinct motion (idle = gentle Y-rotation; jumping = Y-translation;
    walking = X-translation, etc.). The AnimationMixer cares about
    track names matching the bound Object3D — we'll target ``root``."""
    if "jump" in anim_id:
        return {"type": "translate_y", "amp": 0.25, "period": 0.7}
    if "walk" in anim_id or "running" in anim_id:
        return {"type": "translate_x", "amp": 0.10, "period": 1.1}
    if "dancing" in anim_id:
        return {"type": "rotate_y", "amp": 0.5, "period": 1.4}
    if "lying" in anim_id or "seated" in anim_id or "couch" in anim_id:
        return {"type": "rotate_y", "amp": 0.05, "period": 4.0}
    return {"type": "rotate_y", "amp": 0.10, "period": 2.5}


def write_animation_glb(anim_id: str, out_path: Path):
    """Write a tiny glb with a 4-frame animation on a single 'root' node.

    Three.js AnimationMixer + glTF importer will pick up the animation
    automatically when the clip's target node name matches one of the
    avatar's node names — we name the target ``root`` to match the
    humanoid root we wrote above.
    """
    desc = animation_descriptor(anim_id)
    g = gl.GLTF2()
    g.scenes = [gl.Scene(nodes=[0])]
    g.scene = 0

    # single empty root node
    g.nodes = [gl.Node(name="root", translation=[0, 0, 0],
                        rotation=[0, 0, 0, 1])]

    binary_blob = bytearray()
    buffer_views = []
    accessors = []

    # Time samples (4 keyframes over `period` seconds)
    period = float(desc["period"])
    times = np.array([0.0, period * 0.25, period * 0.5, period * 0.75,
                      period], dtype=np.float32)

    # Output values depend on track type
    if desc["type"] == "translate_y":
        amp = float(desc["amp"])
        values = np.array([
            [0, 0, 0],
            [0, amp, 0],
            [0, 0, 0],
            [0, -amp * 0.2, 0],
            [0, 0, 0],
        ], dtype=np.float32)
        path = "translation"
        n_components = 3
    elif desc["type"] == "translate_x":
        amp = float(desc["amp"])
        values = np.array([
            [0, 0, 0],
            [amp * 0.5, 0.02, 0],
            [0, 0, 0],
            [-amp * 0.5, 0.02, 0],
            [0, 0, 0],
        ], dtype=np.float32)
        path = "translation"
        n_components = 3
    else:  # rotate_y — output quaternion
        amp = float(desc["amp"])
        # quaternion for rotation around Y by ±amp radians
        def q_y(angle):
            return np.array([0.0, math.sin(angle / 2), 0.0,
                             math.cos(angle / 2)], dtype=np.float32)
        values = np.stack([
            q_y(0), q_y(amp), q_y(0), q_y(-amp), q_y(0),
        ])
        path = "rotation"
        n_components = 4

    # Pack times
    t_bytes = times.tobytes()
    bv_t = gl.BufferView(buffer=0, byteOffset=len(binary_blob),
                         byteLength=len(t_bytes))
    binary_blob += t_bytes
    while len(binary_blob) % 4: binary_blob.append(0)
    buffer_views.append(bv_t)

    # Pack values
    v_bytes = values.astype(np.float32).tobytes()
    bv_v = gl.BufferView(buffer=0, byteOffset=len(binary_blob),
                         byteLength=len(v_bytes))
    binary_blob += v_bytes
    while len(binary_blob) % 4: binary_blob.append(0)
    buffer_views.append(bv_v)

    a_t = gl.Accessor(
        bufferView=0, componentType=gl.FLOAT, count=len(times),
        type=gl.SCALAR, min=[float(times.min())],
        max=[float(times.max())],
    )
    a_v = gl.Accessor(
        bufferView=1, componentType=gl.FLOAT, count=len(values),
        type=gl.VEC3 if n_components == 3 else gl.VEC4,
    )
    accessors.extend([a_t, a_v])

    sampler = gl.AnimationSampler(input=0, output=1, interpolation="LINEAR")
    channel = gl.AnimationChannel(
        sampler=0,
        target=gl.AnimationChannelTarget(node=0, path=path),
    )
    anim = gl.Animation(name=anim_id, samplers=[sampler], channels=[channel])

    g.buffers = [gl.Buffer(byteLength=len(binary_blob))]
    g.bufferViews = buffer_views
    g.accessors = accessors
    g.animations = [anim]
    g.set_binary_blob(bytes(binary_blob))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.save_binary(str(out_path))


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"[generate] writing presets to {PRESET_DIR}")
    for p in PRESETS:
        parts = build_humanoid(p)
        write_glb(parts, PRESET_DIR / f"{p['id']}.glb")
        render_thumbnail(p, PRESET_DIR / f"{p['id']}.png")

    print(f"[generate] writing animations to {ANIM_DIR}")
    for anim_id in ANIMATION_IDS:
        out = ANIM_DIR / f"{anim_id}.glb"
        write_animation_glb(anim_id, out)
    print(f"  [glb] wrote {len(ANIMATION_IDS)} animation glbs")

    print("[generate] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
