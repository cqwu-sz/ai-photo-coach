#!/usr/bin/env python3
"""Avatar bundle integrity check.

The iOS Stage-2 ghost-avatar guide loads thumbnails as
`UIImage(named: "Avatars/<presetId>")` and USDZ models as
`Entity(named: "Avatars/<presetId>")`. If a preset is listed in the
`AvatarManifest` fallback table but the corresponding bundled asset
is missing, the production AR guide will silently degrade to a
neutral SF Symbol / disc-only marker — breaking the
"so-what-you-see-is-what-you-get" promise without anyone noticing
until a real user complains.

This script enforces parity at CI time:

  1. Parse the `bundledPresetFallbacks()` literal in
     `ios/AIPhotoCoach/Features/Avatar/AvatarLoader.swift`.
  2. For each preset id, verify that
     `ios/AIPhotoCoach/Resources/Avatars/<id>.png` AND
     `ios/AIPhotoCoach/Resources/Avatars/<id>.usdz` both exist.

Exit non-zero with a list of offenders so CI fails fast.

Run:

    python3 scripts/check_avatar_bundle_assets.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOADER = REPO_ROOT / "ios/AIPhotoCoach/Features/Avatar/AvatarLoader.swift"
ASSETS_DIRS = [
    REPO_ROOT / "ios/AIPhotoCoach/Resources/Avatars",
    REPO_ROOT / "ios/AIPhotoCoach/Avatars",
]


def parse_preset_ids(swift_source: str) -> list[str]:
    """Return every preset id literal under bundledPresetFallbacks()."""
    in_fallbacks = False
    ids: list[str] = []
    for line in swift_source.splitlines():
        if "bundledPresetFallbacks" in line:
            in_fallbacks = True
            continue
        if not in_fallbacks:
            continue
        if line.strip().startswith("}") and ".init(" not in line:
            break
        m = re.search(r'AvatarPresetEntry\s*\(\s*id:\s*"([^"]+)"', line)
        if m:
            ids.append(m.group(1))
    return ids


def find_asset(preset_id: str, ext: str) -> Path | None:
    for base in ASSETS_DIRS:
        candidate = base / f"{preset_id}.{ext}"
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    if not LOADER.exists():
        print(f"[avatar-check] AvatarLoader.swift not found at {LOADER}",
              file=sys.stderr)
        return 1
    ids = parse_preset_ids(LOADER.read_text(encoding="utf-8"))
    if not ids:
        print("[avatar-check] no preset ids parsed — did the loader format change?",
              file=sys.stderr)
        return 1

    missing: list[tuple[str, str]] = []
    for pid in ids:
        for ext in ("png", "usdz"):
            if find_asset(pid, ext) is None:
                missing.append((pid, ext))

    if missing:
        print("[avatar-check] FAIL — bundled preset fallbacks reference missing assets:")
        for pid, ext in missing:
            print(f"   - {pid}.{ext}")
        print()
        print("Drop the files into one of:")
        for d in ASSETS_DIRS:
            print(f"   - {d.relative_to(REPO_ROOT)}")
        return 1

    print(f"[avatar-check] OK — all {len(ids)} preset(s) have png + usdz bundled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
