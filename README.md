# 拾光 · AI 摄影教练 (AI Photo Coach)

iOS app + PWA web demo + Python backend that turns a 10-20s environment scan
video into a structured shot plan: best angle, composition, camera settings
(focal length, aperture, shutter, ISO, EV) and pose suggestions for 0-4
people. Reference images on-device personalize the recommendations to your
style.

**4 维质量评分**：每个机位都按 *构图 / 光线 / 色彩 / 景深* 4 个维度打分（1-5），
配上一行规则引用，结果页直接画成进度条 + "亮点 / 可改" 标签。让 AI 的建议从抽象
评论变成可视化指南。

**光影模式 + 太阳罗盘**：新增 `light_shadow` 出片场景。授权位置后后端用 NREL SPA
算法计算太阳方位 / 高度 / 黄金时刻倒计时（纯本地计算，无需外部 API），结果页
显示一个圆形罗盘 + 倒计时 chip，AI 还会**按时间敏感度重排方案**——先拍即将
消失的光线方向。

**多模型 BYOK**：原生 Gemini + 9 个 OpenAI 兼容预设（智谱 GLM-4.6V / GPT-4o /
Qwen-VL / DeepSeek-VL2 / Kimi Vision），用户可以在 PWA / iOS 设置里选模型 +
填自己的 API key（密钥仅存本地）。详见 [docs/MULTI_MODEL.md](docs/MULTI_MODEL.md)。

**6 档出片场景**：人像 / 特写 / 全身 / 人文 / 风景 / 光影 —— 风景模式可不出
人，光影模式按位置实时计算太阳数据。详见 [docs/SCENE_MODES.md](docs/SCENE_MODES.md)。

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

**Recommended path — TestFlight (when available)**
The maintained iOS build is distributed via TestFlight. Once invites are
open the link will appear in the project's GitHub Releases page; this is
the path Apple expects users to take and the only one we can guarantee
keeps working as iOS updates land.

**Web demo first**
Don't have a TestFlight invite? The web PWA at
`http://localhost:8000/web/` (Quickstart above) covers the full
"环视 → 出方案 → 看 7 维评分" experience without any Apple account.

**Geek path — sideload from sources (no Mac, no invite)**
Push to GitHub, the workflow at `.github/workflows/ios-build.yml` builds
an unsigned `.ipa` on a free macOS runner; install it onto your iPhone
from Windows with Sideloadly + your free Apple ID — full step-by-step in
[docs/IOS_SIDELOAD.md](docs/IOS_SIDELOAD.md). Note this expires every 7
days with a free Apple ID; it's a development workflow, not a daily
driver.

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
