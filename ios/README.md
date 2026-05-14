# AIPhotoCoach iOS

SwiftUI app that records a 10-20s environment scan, sends keyframes to the
backend, and shows a structured shot plan (angle + composition + camera
settings + pose).

## Generate the Xcode project

The repo ships sources only. Generate the `.xcodeproj` with
[XcodeGen](https://github.com/yonaskolb/XcodeGen) on a Mac:

```bash
brew install xcodegen
cd ios
# Provide a Production.xcconfig before generating — see the next section.
cp Config/Production.xcconfig.template Config/Production.xcconfig
# Edit Config/Production.xcconfig and set the real backend URL.
xcodegen generate
open AIPhotoCoach.xcodeproj
```

## Build variants (商业化双产物)

This project intentionally produces **two binaries** to keep the
"point at any server" debug capability out of App Store builds:

| Scheme | Bundle ID | Display | INTERNAL_BUILD flag | Contains endpoint override UI |
| --- | --- | --- | --- | --- |
| `AIPhotoCoach` | `com.aiphotocoach.app` | 拾光 | ❌ | ❌ (compiled out at build time) |
| `AIPhotoCoach-Internal` | `com.aiphotocoach.app.internal` | 拾光 Dev | ✅ | ✅ |

- **Production** (`AIPhotoCoach`) bakes the backend URL into the binary
  at build time via `Config/Production.xcconfig`. CI writes this from
  `secrets.PROD_API_BASE_URL`; for local archives, you must
  `cp Config/Production.xcconfig.template Config/Production.xcconfig`
  and replace the `REPLACE-ME.invalid` sentinel with the real URL.
  Building with the sentinel **fails the postCompileScripts gate**,
  so you can never accidentally ship a placeholder.

- **Internal** (`AIPhotoCoach-Internal`) ships with an empty
  `API_BASE_URL` and exposes a *Connection Settings* sheet (from the
  login screen) that lets the user point the app at any reachable URL
  — typed manually or scanned from a QR code. Use this build for LAN
  development against `http://<your-mac-lan-ip>:8000`.

## Backend dev URL (Internal build)

1. Boot the backend on your dev box, bound to `0.0.0.0`:
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
2. Find your LAN IP (`ipconfig` on Windows, `ifconfig` on macOS).
3. Install the Internal IPA on your iPhone (see `docs/IOS_SIDELOAD.md`).
4. Open *拾光 Dev* → tap **连接设置 · Internal Build** at the bottom of
   the login screen → enter `http://192.168.x.y:8000` → **测试连接**
   (probes `/healthz`) → **应用并保存**.

> Local HTTP to the LAN host is allowed by `NSAllowsLocalNetworking`.
> Public hosts must use `https://`.

## Layout

- `App/` - root SwiftUI scene + navigation router
- `Features/EnvCapture/` - environment scan UI + video recording
- `Features/Recommendation/` - shot plan results UI
- `Features/ReferenceLib/` - user reference image library
- `Features/ARGuide/` - reserved for Phase 3 AR overlay
- `Core/KeyframeExtractor/` - extract 8-12 keyframes from the scan
- `Core/CLIPEmbedder/` - CoreML CLIP wrapper (Phase 2)
- `Core/APIClient/` - typed HTTP client matching `shared/schema/analyze.openapi.yaml`
- `Core/Storage/` - on-device SQLite for reference images
- `Models/` - Codable types mirroring the backend Pydantic models
- `Resources/PoseLibrary/` - bundled pose thumbnails (cached from backend)

## CLIP model

Phase 2 needs a CoreML CLIP image encoder. See
`scripts/convert_clip_to_coreml.py` (in repo root) for one-time conversion.
