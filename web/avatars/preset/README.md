# Preset Avatar Pack — v7

This directory holds the 8 preset ReadyPlayerMe avatars used by both
the web 3D shot preview and (transcoded to USDZ) the iOS AR guide.

## Asset list

| id | gender | age | style | file size (approx) |
|---|---|---|---|---|
| `male_casual_25` | male | 25 | casual / street | 3.2 MB |
| `male_business_35` | male | 35 | business / office | 3.4 MB |
| `male_athletic_28` | male | 28 | athletic / outdoor | 3.1 MB |
| `female_casual_22` | female | 22 | casual / youth | 3.2 MB |
| `female_elegant_30` | female | 30 | elegant / fashion | 3.3 MB |
| `female_artsy_25` | female | 25 | artsy / bohemian | 3.2 MB |
| `child_boy_8` | male | 8 | kids / family | 2.8 MB |
| `child_girl_8` | female | 8 | kids / family | 2.8 MB |

Each preset ships as **two files**:

- `<id>.glb` — the rigged 3D model (Mixamo-compatible skeleton)
- `<id>.png` — a 256x256 face thumbnail used in the avatar gallery

Total pack size: ~25 MB on web, ~30 MB on iOS (USDZ).

## Generating the assets

The `.glb` files are generated once via the [ReadyPlayerMe Avatar Creator](https://readyplayer.me/avatar):

1. Visit `https://readyplayer.me/avatar?body=fullbody&gender=<male|female>&age=<adult|child>`
2. Customise via the in-page editor (face / hair / outfit)
3. Hit **Done** → choose **Download `.glb`** with the following options:
   - Pose: `T` (default)
   - Texture atlas: `1024x1024`
   - Optimise: `Yes` (the slimmer DRACO-compressed export)
4. Rename to `<id>.glb` per the table above and drop into this directory.

For the thumbnails, ReadyPlayerMe serves a head-only render at:
```
https://api.readyplayer.me/v1/avatars/<avatar-id>.png?size=256&type=head
```

Save it as `<id>.png` next to the glb.

## iOS conversion

After the glb files are in place, run from repo root:

```bash
bash scripts/glb_to_usdz.sh
```

This converts each `.glb` into `<id>.usdz` and drops the result into
`ios/AIPhotoCoach/Resources/Avatars/`.

## Mixamo animations

Animations live one directory up at `web/avatars/animations/`. See
`web/avatars/animations/README.md` for the asset list and the import
pipeline.

## Fallback behavior

If a `.glb` file is missing at runtime, [`web/js/avatar_loader.js`](../../js/avatar_loader.js)
falls back to the legacy procedural mesh built by `avatar_builder.js`,
so the 3D preview never errors out — it just looks lo-fi until the
preset is dropped in.
