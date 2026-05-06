---
name: ai photo coach ios
overview: 基于环视视频 + 多模态 VLM + 端侧 CLIP 的 iOS 摄影助手 App。核心闭环：用户拍 10-20s 环视视频 → 后端 Gemini 视频理解 → 输出机位/姿势/相机参数三位一体的拍摄方案；个性化通过用户上传/收藏的参考图做端侧风格匹配。
todos:
  - id: schema_first
    content: 定义 /analyze API 的 OpenAPI/JSON schema（iOS 和后端共享），包含 shots/composition/camera/poses 结构
    status: completed
  - id: backend_skeleton
    content: 创建 backend/ 骨架（FastAPI + Pydantic + requirements.txt + Dockerfile + mock 模式）
    status: completed
  - id: gemini_integration
    content: 接入 Gemini 2.5 Flash 多帧推理 + prompt 模板 + JSON 输出校验
    status: completed
  - id: ios_skeleton
    content: 创建 ios/AIPhotoCoach/ Xcode 工程 + SwiftUI 骨架 + 路由
    status: completed
  - id: ios_video_capture
    content: 实现环视视频采集 + 陀螺仪角度引导 UI
    status: completed
  - id: keyframe_extractor
    content: 客户端关键帧抽取器（按角度均匀采样 8-12 帧）
    status: completed
  - id: api_client_e2e
    content: iOS APIClient + 关键帧上传 + 端到端连贪 Gemini 返回真实 JSON
    status: completed
  - id: result_ui_basic
    content: 结果展示 UI v0：机位卡片 + 姿势描述 + 参数面板（纯文字版）
    status: completed
  - id: pose_library_v0
    content: Phase 1 姿势库 v0：打多个样本姿势 JSON + 缩略图（1/2/3+人）
    status: completed
  - id: camera_params_engine
    content: 后端摄影参数推理模块（焦段/光圈/快门/ISO 决策规则 + LLM prompt）
    status: completed
  - id: ref_image_lib
    content: iOS 用户参考图库：导入 + SQLite 存储 + 缩略图管理
    status: completed
  - id: clip_embed_phase2
    content: Phase 2：CoreML CLIP 集成 + 端侧 embedding + 相似参考图检索
    status: completed
  - id: personalization_rag
    content: Phase 2：上传参考图缩略图与提示词一起发给 Gemini 做 RAG 增强
    status: completed
isProject: false
---

# AI 摄影教练 iOS App - 实施方案

## 1. 产品定位与差异化

不同于 PoseGPT/SnapPose 等只用单帧分析的竞品，本产品的护城河是**「环视视频整体理解 + 多人编排 + 摄影参数建议」三合一**，并通过用户自有参考图库做风格个性化（合规、零冷启动数据风险）。

## 2. 技术栈

- **iOS**: Swift 5.9 / SwiftUI + AVFoundation（视频采集）+ ARKit（陀螺仪辅助、可选 AR 叠加）+ CoreML（端侧 CLIP）
- **后端**: Python 3.11 + FastAPI + Pydantic v2，部署 Cloud Run / Fly.io
- **AI 模型**:
  - 视频理解主链路：**Gemini 2.5 Flash**（成本敏感）/ **Gemini 2.5 Pro**（高质量档）
  - 端侧风格嵌入：**CLIP ViT-B/32 (CoreML)** 做用户参考图相似度
- **向量库**: 端侧用 SQLite + 自建余弦相似度（参考图量级 < 1000，无需 Qdrant）
- **姿势库**: JSON schema + 缩略图打包随版本下发（Phase 1: 100-200 条精标）

## 3. 系统架构

```mermaid
flowchart LR
    subgraph IOS[iOS App]
        Cap[环视视频采集<br>+陀螺仪角度引导]
        KF[关键帧抽取器<br>按角度+时间均匀采样]
        Up[多帧上传]
        UI[结果展示<br>姿势卡片+参数面板+导航箭头]
        Ref[参考图库<br>本地存储]
        CL[CoreML CLIP<br>端侧风格向量]
    end

    subgraph BE[后端 FastAPI]
        Ana[/analyze 端点]
        VLM[Gemini 视频/多帧理解]
        PE[姿势编排引擎<br>规则+模板]
        CP[相机参数推理<br>VLM+知识库]
        KB[(姿势库+构图库<br>+摄影参数知识库)]
    end

    Cap --> KF --> Up --> Ana
    Ref --> CL
    CL -->|"风格向量(可选)"| Ana
    Ana --> VLM --> PE --> CP --> Ana
    KB --> PE
    KB --> CP
    Ana --> UI
```



## 4. 输出数据结构（核心 API 契约）

`POST /analyze` 返回示例：

```json
{
  "scene": { "type": "outdoor_park", "lighting": "golden_hour", "background": "..." },
  "shots": [
    {
      "angle": { "azimuth_deg": 45, "pitch_deg": -10, "distance_m": 2.5 },
      "composition": "rule_of_thirds + leading_line",
      "camera": {
        "focal_length_mm": 35,
        "aperture": "f/2.0",
        "shutter": "1/250",
        "iso": 200,
        "wb": "5500K",
        "rationale": "黄昏侧逆光，35mm 兼顾环境与人物..."
      },
      "poses": [
        {
          "person_count": 2,
          "layout": "high_low_offset",
          "person_a": { "stance": "...", "hands": "...", "gaze": "..." },
          "person_b": { ... },
          "interaction": "牵手对视",
          "reference_thumbnail_id": "pose_xxx"
        }
      ]
    }
  ]
}
```

这份 schema 是后续 iOS UI、姿势库、参数引擎共同的合同，**先定 schema 再写代码**。

## 5. 分阶段实施

### Phase 0: 闭环原型（2 周）

- iOS：基础 SwiftUI 工程 + 环视视频采集 + 陀螺仪角度提示（"再向右转 30 度"）
- iOS：关键帧抽取器（按陀螺仪角度均匀取 8-12 帧）
- 后端：FastAPI 骨架 + `/analyze` 接 Gemini 2.5 Flash
- 输出：1 套机位 + 1 个姿势 + 基础参数（纯文字卡片）

### Phase 1: MVP（4-6 周）

- 后端：姿势编排引擎 + 摄影参数知识库（焦段/光圈/快门/ISO 决策规则）
- 后端：精标姿势库 100-200 条（覆盖 1/2/3+人 × 室内/室外/特殊场景）
- iOS：完整结果 UI（机位卡片、姿势缩略图、参数面板）
- iOS：用户参考图导入（相册/拖入）+ 本地 SQLite 存储
- 后端：从用户上传的"风格关键词"做 prompt 增强（暂不接 CLIP）

### Phase 2: 个性化（3-4 周）

- iOS：CoreML CLIP 模型集成，参考图入库时计算 embedding 存本地
- iOS：分析请求时计算环境帧 embedding，做相似参考图检索（Top-5）
- 后端：接受参考图 thumbnail / embedding 做 RAG 提示
- 体验：推荐结果中标注"匹配你收藏的 X 风格"

### Phase 3 (可选): AR 实时引导（4-6 周）

- ARKit `ARBodyTrackingConfiguration` + 自定义 SCNNode
- 在相机预览叠加：机位箭头、构图网格、人物站位占位
- 拍摄完照片对比目标姿势，给打分

## 6. 关键工程决策

- **视频处理放后端**：Gemini 视频 API 直传成本高，**iOS 端先采 8-12 张关键帧**（按陀螺仪角度均匀采样）再上传，单次推理压到 ≈ $0.01-0.03
- **参考图全部端侧**：隐私友好、合规零风险、避开自媒体抓取问题
- **姿势库随版本下发**：JSON + 缩略图，避免每次推荐都拉缩略图
- **API schema 先于实现**：iOS 和后端用 OpenAPI 生成的类型代码同步

## 7. 主要风险与对策

- **Gemini 视频成本**：默认 Flash，Pro 仅在用户开启"高质量模式"时启用
- **iPhone 原生相机参数可控有限**：MVP 把参数当"建议"展示（适配单反/微单用户和 ProCamera 等三方 App），不做相机控制；v2 再考虑集成 AVCaptureDevice 手动模式
- **多人姿势不自然**：MVP 重模板（每种人数组合 20-30 个高质量模板），不让 LLM 凭空编排，VLM 只负责"挑选+微调"
- **姿势库冷启动**：内部标注或采购 200 条精品起步；可用 FLUX/SDXL 生成参考插图
- **App Store 审核**：在 Info.plist 声明 AI 用途，避免出现 "镜头建议拍摄他人" 类隐私敏感措辞

## 8. 项目骨架（建议目录）

```
ai-ios-photo/
├─ ios/
│  └─ AIPhotoCoach/
│     ├─ App/
│     ├─ Features/
│     │  ├─ EnvCapture/      # 环视视频采集
│     │  ├─ Recommendation/  # 结果展示
│     │  ├─ ReferenceLib/    # 参考图管理 + CLIP 嵌入
│     │  └─ ARGuide/         # AR 叠加 (Phase 3)
│     ├─ Core/
│     │  ├─ KeyframeExtractor/
│     │  ├─ CLIPEmbedder/
│     │  └─ APIClient/
│     └─ Resources/
│        └─ PoseLibrary/     # 随版本下发的姿势库
├─ backend/
│  ├─ app/
│  │  ├─ api/                # /analyze 等端点
│  │  ├─ services/
│  │  │  ├─ gemini_video.py
│  │  │  ├─ pose_engine.py
│  │  │  └─ camera_params.py
│  │  └─ knowledge/
│  │     ├─ poses/           # 姿势库 JSON + 缩略图
│  │     ├─ composition/     # 构图原理库
│  │     └─ camera_settings/ # 摄影参数知识库
│  ├─ tests/
│  └─ requirements.txt
├─ shared/
│  └─ schema/                # OpenAPI / Pydantic 共享 schema
└─ docs/
   ├─ ARCHITECTURE.md
   └─ POSE_SCHEMA.md
```

## 9. Phase 0 落地的具体动作

只要 Plan 通过，Phase 0 我会按以下顺序开工：

1. 写 `docs/ARCHITECTURE.md` 和 `shared/schema/analyze.json`（API 契约）
2. 创建 `backend/` 骨架 + `/analyze` 端点 + Gemini 接入（含 mock 模式）
3. 创建 `ios/AIPhotoCoach/` Xcode 工程 + 视频采集 + 关键帧抽取
4. 端到端打通：iOS 上传关键帧 → 后端返回固定 JSON → iOS 渲染卡片
5. 接入真实 Gemini，跑通第一个真实 demo

