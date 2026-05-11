# Web avatars (3D glb assets)

> **Files NOT in git**: `web/avatars/{preset,animations,base}/*.glb`
> are gitignored. Total ~200 MB across 40+ files; bloats clone time
> and burns LFS quota for no benefit.

## What lives here

| Subdir | Source | Used by |
|---|---|---|
| `preset/` | `scripts/gen_avatars_tripo.py` (Tripo3D + Mixamo rigging) | `web/js/avatar_loader.js` and the iOS app's `AvatarManifest` |
| `animations/` | Mixamo official (downloaded once) | `web/js/scene_3d.js` for posture preview |
| `base/xbot.glb` | Mixamo "X Bot" T-pose | Skeleton retargeting source |

## How to populate after clone

The fastest path for development is to copy from a teammate's machine
or your CDN bucket. There's no on-the-fly regeneration script — Tripo3D
costs real money per generation, so we treat the existing assets as
the canonical set.

```bash
# Option A — pull from your CDN mirror (example)
aws s3 sync s3://your-bucket/aphc/web/avatars/ web/avatars/

# Option B — re-run Tripo for one preset (paid, ~$0.15 each)
python scripts/gen_avatars_tripo.py --preset female_youth_18

# Option C — fetch only base + a couple presets to unblock dev
mkdir -p web/avatars/base web/avatars/preset
# ... grab xbot.glb + the 2-3 presets you actually need ...
```

## What's tracked in git

- This README
- `web/avatars/preset/manifest.json` (if present) — describes which
  preset id maps to which display name / age / gender
- Asset attribution / CREDITS files

The actual `.glb` payload is left to your local cache or CDN.

## Production deploy

Bake the glbs into your container image (recommended; they're
immutable) or serve them from a CDN with a long Cache-Control. The
frontend respects `Cache-Control: max-age=31536000, immutable`.
