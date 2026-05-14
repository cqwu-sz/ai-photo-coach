"""Shared utilities for the works_crawler toolkit.

Path conventions:
    scripts/works_crawler/raw/<platform>/<id>.jpg
    scripts/works_crawler/raw/<platform>/<id>.json    (source metadata)
    scripts/works_crawler/drafts/<id>.json            (after auto_annotate)
    backend/app/knowledge/works/<id>.json             (after review approval)
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
DRAFT_DIR = ROOT / "drafts"
APPROVED_DIR = (ROOT / ".." / ".." / "backend" / "app" / "knowledge" / "works").resolve()


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    APPROVED_DIR.mkdir(parents=True, exist_ok=True)


def safe_id(platform: str, raw_id: str) -> str:
    """Stable, filesystem-safe id with platform prefix."""
    clean = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id)
    return f"work_{platform}_{clean[:48]}"


def sha8(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_json(path: Path) -> Optional[dict | list]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


SCHEMA_VERSION = "works-v1"


def empty_draft(*, work_id: str, source_platform: str, source_url: str,
                 author: Optional[str], license: Optional[str],
                 image_uri: str) -> dict:
    """Return a draft dict that conforms to ``knowledge/works/`` schema
    but with every analytic field empty — ready for ``auto_annotate``."""
    return {
        "id": work_id,
        "schema_version": SCHEMA_VERSION,
        "source": {
            "platform": source_platform,
            "url":      source_url,
            "author":   author,
            "license":  license,
        },
        "image_uri":     image_uri,
        "thumbnail_uri": image_uri,
        "scene_tags":      [],
        "light_tags":      [],
        "composition_tags": [],
        "person_count":     None,
        "why_good":        [],
        "reusable_recipe": {
            "subject_pose":   "",
            "camera_position": "",
            "framing":        "",
            "focal_length":   "",
            "aperture":       "",
            "post_style":     "",
            "applicable_to": {
                "scene_modes": ["portrait"],
            },
        },
        "embedding":  None,
        "added_at":   None,
        "reviewed_by": None,
    }
