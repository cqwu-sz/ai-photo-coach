# AI Photo Coach

iOS app + PWA web demo + Python backend that turns a 10-20s environment scan
video into a structured shot plan: best angle, composition, camera settings
(focal length, aperture, shutter, ISO, EV) and pose suggestions for 1-N
people. Reference images on-device personalize the recommendations to your
style.

See `docs/ARCHITECTURE.md` for a top-down view, `docs/WEB_DEMO.md` for the
fastest way to see the product in action without a Mac, and the plan file in
`.cursor/plans/` for the phased roadmap.

## Repo layout

```
ai-ios-photo/
├─ backend/                 FastAPI + Gemini + knowledge bases
├─ ios/                     SwiftUI app (XcodeGen project.yml, needs a Mac)
├─ web/                     PWA demo - works in any browser, mounted under /web
├─ shared/schema/           OpenAPI source of truth
├─ scripts/                 Pose thumbnail generator + CLIP -> CoreML
└─ docs/                    ARCHITECTURE.md, WEB_DEMO.md, GEMINI_SETUP.md, POSE_SCHEMA.md
```

## Quickstart (no Mac, no iPhone needed)

```bash
cd backend
python -m venv .venv
. .venv/Scripts/activate            # Windows
# or: source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
cp .env.example .env                # MOCK_MODE=true by default
uvicorn app.main:app --reload --port 8000
```

Then open **http://localhost:8000/web/** in Chrome / Edge / Firefox. You get
the full UX (camera + heading ring + recording + AI recommendation) right
there. If your laptop has no gyroscope, mouse movement simulates heading.

Real Gemini takes 2 more minutes - see [docs/GEMINI_SETUP.md](docs/GEMINI_SETUP.md).
Want to test on your iPhone over Wi-Fi? See [docs/WEB_DEMO.md](docs/WEB_DEMO.md)
for the mkcert HTTPS recipe.

### Tests

```bash
cd backend
. .venv/Scripts/activate
pytest -q
```

## iOS quickstart

The repo ships sources only. **No Mac?** Push to GitHub, the workflow at
`.github/workflows/ios-build.yml` builds an unsigned `.ipa` on a free
macOS runner; install it onto your iPhone from Windows with Sideloadly +
your free Apple ID — full step-by-step in
[docs/IOS_SIDELOAD.md](docs/IOS_SIDELOAD.md).

On a Mac you can build natively:

```bash
brew install xcodegen
cd ios
xcodegen generate
open AIPhotoCoach.xcodeproj
```

Edit `APIConfig.baseURL` (or set `API_BASE_URL` in Info.plist) to point at
your local backend. On a real device, use the LAN IP of your dev machine.

For the optional CLIP image encoder used in Phase 2 personalization:

```bash
pip install coremltools open_clip_torch torch torchvision pillow
python scripts/convert_clip_to_coreml.py
```

Then drag `ios/AIPhotoCoach/Resources/CLIPImageEncoder.mlpackage` into the
Xcode project. The app gracefully degrades if the model is missing.

## Status

Phase 0/1 (mock + real-Gemini end-to-end) and Phase 2 (reference library
+ CLIP plumbing) are implemented. Phase 3 (live AR overlay) is a placeholder.
See the plan file in `.cursor/plans/` for the full roadmap.
