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
xcodegen generate
open AIPhotoCoach.xcodeproj
```

## Backend dev URL

Point `APIConfig.baseURL` at your local backend (default `http://localhost:8000`).
On a real device, start the backend on your dev box and use its LAN IP.

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
