"""v7 — avatars + animation manifest endpoints.

The web 3D preview and iOS AR loader both need to know:

  1. Which preset avatars exist (id, gender, age, glb path, thumbnail).
  2. Which Mixamo animation should play for a given LLM-recommended
     pose KB id (so the avatar moves into the right stance).

Rather than hardcoding these on the client, we serve them as a manifest
so adding a new avatar / animation is a server-side change only.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..services.knowledge import load_pose_to_mixamo_raw

router = APIRouter(prefix="/avatars", tags=["avatars"])


# Hard-coded preset list — these are the 8 ReadyPlayerMe avatars we
# ship with the app. The .glb assets live in ``web/avatars/preset/``
# and ``ios/AIPhotoCoach/Resources/Avatars/`` respectively.
#
# When adding a new preset:
#   1. Drop the .glb into web/avatars/preset/
#   2. Run scripts/glb_to_usdz.sh to make the iOS counterpart
#   3. Append a new entry below
PRESET_AVATARS: list[dict[str, object]] = [
    {
        "id": "male_casual_25",
        "name_zh": "休闲男 · 25",
        "gender": "male", "age": 25,
        "style": "casual", "tags": ["street", "everyday"],
        "glb": "/web/avatars/preset/male_casual_25.glb",
        "usdz": "Avatars/male_casual_25.usdz",
        "thumbnail": "/web/avatars/preset/male_casual_25.png",
    },
    {
        "id": "male_business_35",
        "name_zh": "商务男 · 35",
        "gender": "male", "age": 35,
        "style": "business", "tags": ["formal", "office"],
        "glb": "/web/avatars/preset/male_business_35.glb",
        "usdz": "Avatars/male_business_35.usdz",
        "thumbnail": "/web/avatars/preset/male_business_35.png",
    },
    {
        "id": "male_athletic_28",
        "name_zh": "运动男 · 28",
        "gender": "male", "age": 28,
        "style": "athletic", "tags": ["outdoor", "fit"],
        "glb": "/web/avatars/preset/male_athletic_28.glb",
        "usdz": "Avatars/male_athletic_28.usdz",
        "thumbnail": "/web/avatars/preset/male_athletic_28.png",
    },
    {
        "id": "female_casual_22",
        "name_zh": "休闲女 · 22",
        "gender": "female", "age": 22,
        "style": "casual", "tags": ["youth", "street"],
        "glb": "/web/avatars/preset/female_casual_22.glb",
        "usdz": "Avatars/female_casual_22.usdz",
        "thumbnail": "/web/avatars/preset/female_casual_22.png",
    },
    {
        "id": "female_elegant_30",
        "name_zh": "优雅女 · 30",
        "gender": "female", "age": 30,
        "style": "elegant", "tags": ["formal", "fashion"],
        "glb": "/web/avatars/preset/female_elegant_30.glb",
        "usdz": "Avatars/female_elegant_30.usdz",
        "thumbnail": "/web/avatars/preset/female_elegant_30.png",
    },
    {
        "id": "female_artsy_25",
        "name_zh": "文艺女 · 25",
        "gender": "female", "age": 25,
        "style": "artsy", "tags": ["bohemian", "softlight"],
        "glb": "/web/avatars/preset/female_artsy_25.glb",
        "usdz": "Avatars/female_artsy_25.usdz",
        "thumbnail": "/web/avatars/preset/female_artsy_25.png",
    },
    {
        "id": "child_boy_8",
        "name_zh": "男孩 · 8",
        "gender": "male", "age": 8,
        "style": "child", "tags": ["family", "kids"],
        "glb": "/web/avatars/preset/child_boy_8.glb",
        "usdz": "Avatars/child_boy_8.usdz",
        "thumbnail": "/web/avatars/preset/child_boy_8.png",
    },
    {
        "id": "child_girl_8",
        "name_zh": "女孩 · 8",
        "gender": "female", "age": 8,
        "style": "child", "tags": ["family", "kids"],
        "glb": "/web/avatars/preset/child_girl_8.glb",
        "usdz": "Avatars/child_girl_8.usdz",
        "thumbnail": "/web/avatars/preset/child_girl_8.png",
    },
]


@router.get("/manifest")
def manifest() -> JSONResponse:
    """Return everything the client needs to render the avatar gallery.

    Includes both the preset avatar list and the Mixamo animation
    mapping so a single fetch on app boot is enough.
    """
    settings = get_settings()
    pose_map = load_pose_to_mixamo_raw(str(settings.kb_animations_path))
    return JSONResponse({
        "version": "v7",
        "presets": PRESET_AVATARS,
        "pose_to_mixamo": pose_map,
    })


@router.get("/animations")
def animations_only() -> JSONResponse:
    """Lightweight endpoint for clients that already cached the preset
    list and just need to refresh the pose-to-mixamo mapping."""
    settings = get_settings()
    pose_map = load_pose_to_mixamo_raw(str(settings.kb_animations_path))
    return JSONResponse(pose_map)
