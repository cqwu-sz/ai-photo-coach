"""Serves the pose library manifest + thumbnails to the iOS app."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from ..config import get_settings
from ..services.knowledge import load_poses

router = APIRouter(prefix="/pose-library", tags=["pose-library"])


@router.get("/manifest")
def manifest() -> JSONResponse:
    settings = get_settings()
    poses = load_poses(str(settings.kb_poses_path))
    return JSONResponse(
        {
            "version": 1,
            "count": len(poses),
            "poses": poses,
        }
    )


@router.get("/thumbnail/{pose_id}.png")
def thumbnail(pose_id: str) -> FileResponse:
    settings = get_settings()
    poses = load_poses(str(settings.kb_poses_path))
    pose = next((p for p in poses if p.get("id") == pose_id), None)
    if pose is None or not pose.get("thumbnail"):
        raise HTTPException(status_code=404, detail={"error": "pose not found"})
    path = settings.kb_poses_path / pose["thumbnail"]
    if not path.exists():
        raise HTTPException(status_code=404, detail={"error": "thumbnail missing on disk"})
    return FileResponse(path, media_type="image/png")
