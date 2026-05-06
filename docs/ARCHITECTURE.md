# AI Photo Coach - Architecture

## Components

```
+------------------------+         +-----------------------+
|    iOS App (SwiftUI)   |  HTTPS  |   Backend (FastAPI)   |
|                        +-------->+                       |
|  - EnvCapture          | multipart  - /analyze           |
|  - KeyframeExtractor   |         |  - /pose-library      |
|  - APIClient           |<--------+  - /healthz           |
|  - RecommendationView  |   JSON  |                       |
|  - ReferenceImageStore |         |  + Gemini 2.5 client  |
|  - CLIPEmbedder        |         |  + pose engine        |
+------------------------+         |  + camera_params      |
                                   +-----------+-----------+
                                               |
                                               v
                                  +------------+-----------+
                                  | Gemini 2.5 (Flash/Pro) |
                                  +------------------------+
```

## Request lifecycle

1. iOS records 10-20s of environment scan, sampling each frame plus heading.
2. `KeyframeExtractor` selects 8-12 azimuth-spaced keyframes.
3. `EnvCaptureViewModel` builds a `CaptureMeta` and calls
   `POST /analyze` with the JPEGs and (optional) reference thumbnails.
4. Backend dispatches to `AnalyzeService`:
   - In `MOCK_MODE=true` -> returns canned `AnalyzeResponse` (mock_provider).
   - Otherwise calls `GeminiClient.analyze` with a structured prompt.
5. The Pydantic `AnalyzeResponse` is deterministically post-processed:
   - `camera_params.repair_camera_settings` clamps invalid LLM output.
   - `pose_engine.map_to_library` resolves `reference_thumbnail_id`.
6. iOS decodes and renders shot cards.

## Data contract

`shared/schema/analyze.openapi.yaml` is the single source of truth.
Both `backend/app/models/schemas.py` (Pydantic) and
`ios/AIPhotoCoach/Models/Schemas.swift` (Codable) implement the same shape.

## Knowledge bases

- `backend/app/knowledge/poses/` - one JSON per pose template + matching PNG
  thumbnail. Served via `/pose-library` so iOS can lazy-load thumbnails.
- `backend/app/knowledge/camera_settings/` - per-lighting baseline params.
- `backend/app/knowledge/composition/` - composition principles.

To regenerate placeholder thumbnails:
```
python scripts/generate_pose_thumbnails.py
```

## Personalization (Phase 2)

`ReferenceImageStore` keeps user-imported style references on-device only.
Each image is CLIP-embedded by `CLIPEmbedder` (CoreML model, see
`scripts/convert_clip_to_coreml.py`). The active subset is uploaded as
small JPEG thumbnails alongside the analysis request and used by the LLM
as inspiration. No raw user data leaves the device beyond those tiny
thumbnails the user explicitly opts into.
