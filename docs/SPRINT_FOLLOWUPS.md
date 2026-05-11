# Sprint 1-4 状态快照（v12 收尾）

最后更新：2026-05-10

## 全部完成项

### Sprint 1 — Color & Lighting Science
- ✅ `color_science.py`：CCT (McCamy)、tint、动态范围、光比、风格调色板
- ✅ Web/iOS 客户端单 pass 采样 `rgb_mean / luma_p05/p95 / clip_pct / saturation_mean`
- ✅ `light_direction` 数值化（接 `_light_direction_from_sun(sun_azimuth)`）
- ✅ `STYLE_PALETTE` 内置（`japanese_clean / ambient_mood / hk_neon / film_grain / high_key / golden_hour`）
- ✅ `style_compliance` 加 `palette_drift` 检测；前端渲染色温 / 光向 / 裁剪 / 风格偏离 chip
- ✅ `LIGHTING DOCTRINE`（规则 18）+ `LIGHTING FACTS` prompt 块
- ✅ 7 个色彩场景 smoke 通过

### Sprint 2 — Precision & Robustness
- ✅ iOS：`AVCaptureDevice.activeFormat.videoFieldOfView` → `(focal_mm, focal_35eq, sensor_mm)` 写入 `FrameMeta`
- ✅ backend：`_solve_distance_with_fov` 真实 FOV 算距离（替代经验 K，向后兼容）
- ✅ `scripts/calibrate_distance.py`：CSV 拟合 K_face / K_body
- ✅ iOS：`VNDetectHorizonRequest`（姿态）+ 客户端 sky-mask（亮 + 偏蓝）双源
- ✅ `_vote_horizon`：image gradient ⊕ Vision；天空 < 5% 时整体抑制
- ✅ Pose finegrain：`shoulder_tilt / hip_offset / chin_forward / spine_curve`（双端）
- ✅ `POSE FACTS` prompt 块 + `POSE DOCTRINE`（规则 19）+ result chip
- ✅ 6 个焦段/水平/姿态 smoke 通过

### Sprint 3 — Feedback Loop & Weather
- ✅ iOS：`ARKitDepthSource` smoothed sceneDepth（A12+ LiDAR）；旧设备降级 AVDepth
- ✅ `DepthRingBuffer.Payload` 多态；`DepthFusion.histogramFromArkit / medianDepthFromArkit` 走 confidence mask
- ✅ Web：`window.MIDAS_MODEL_URL` 可切到 v3.1 small（运行时配置，无需改代码）
- ✅ iOS：`FeedbackUploader`（PHAsset 权限 + EXIF 抓取 + POST `/feedback`）
- ✅ backend：`POST /feedback` + sqlite `shot_results` 表 + 索引
- ✅ `scripts/recalibrate_from_feedback.py`：每日重算 K_face / 风格 WB 中位
- ✅ `weather.py`：`WeatherProvider` 协议 + `OpenMeteoProvider / MockProvider`
- ✅ `predict_cloud_in_30min` + `golden_hour_countdown` 推理；写入 `response.debug.light_forecast`
- ✅ 7 个 weather/feedback smoke 通过

### Sprint 4 — Composition & Knowledge Base
- ✅ `_build_composition`：`rule_of_thirds_dist + symmetry_score`
- ✅ `COMPOSITION FACTS` prompt 块 + `COMPOSITION DOCTRINE`（规则 20）
- ✅ `poi_kb.py`：sqlite POI + peer_shots + `nearest_poi / median_exif_for_poi`
- ✅ Prompts 注入 `PEER SHOTS` 块（geo 命中时）
- ✅ 4 个构图/知识库 smoke 通过

### 总验收
- ✅ backend pytest **203 通过 / 0 失败**
- ✅ 全 4 Sprint 已闭环；新增 7 个 LLM 规则（13-20）

## 已完成的 follow-ups（v12.1）

- ✅ iOS RecommendationView 渲染 fine pose facts + composition facts 列表 + 金光/云遮 chip
- ✅ ARKit gravity 接入 `_vote_horizon`：image + Vision + gravity 三路 2-of-3 majority
- ✅ `scripts/seed_poi.py`：OSM Overpass 灌入景点；支持 `--bbox` / `--city <name>`
- ✅ `scripts/build_poi_refs.py`：按 POI × 风格抓 Unsplash CC0 + manifest + CREDITS
- ✅ `app/services/calibration.py`：`data/calibration.json` mtime 热重载，覆盖 K_face/K_body 与风格 WB 中位
- ✅ iOS `WeatherSource` 协议 + `OpenMeteoSource` + `#if canImport(WeatherKit) WeatherKitSource`
- ✅ 6 个 follow-up smoke（三路投票 / 热重载 / Overpass 解析 / 校准 → compliance）

## 仍需手工/外部输入的项

- WeatherKit 真实运行需要付费 Apple Developer Program；当前只是编译占位。

## v12.2 自动完成

- ✅ 下载 MiDaS v3.1 LeViT-224 ONNX (136 MB) 和 v2.1 small (63 MB) 到 `web/models/`，附 README 切换说明
- ✅ `seed_poi.py` 跑了 12 个起步城市 → **`backend/data/poi_kb.db` 现有 1630 个 POI**（attraction/peak/monument/museum/temple/viewpoint/...）
- ✅ `build_poi_refs.py` 给 100 个英文 POI 抓 Unsplash CC0 → **2700 个参考条目**（87 个 POI 有 manifest，13 个搜不到结果）
  - Windows GBK 控制台兼容修复（`sys.stdout.reconfigure`、subprocess UTF-8 解码）
  - 新增 `--english-only` / `--kinds` 过滤
- ✅ `poi_kb.to_prompt_block` 现在自动注入 CC0 参考照片 URL + 作者署名到 PEER SHOTS prompt 块
- ✅ `nearby_reference_photos` 按 user 选的 style_keywords 优先匹配 manifest，否则降级到该 POI 任一可用 style

## 衍生工件

- `backend/data/shot_results.db`（自动创建，prod 持久化反馈）
- `backend/data/poi_kb.db`（自动创建，待 seed）
- `backend/data/calibration.json`（recalibrate 脚本写入，service 启动时读取）

## 测试通过细节

```
tests/test_color_science_smoke.py       7 passed
tests/test_lens_horizon_pose_smoke.py   6 passed
tests/test_sprint3_smoke.py             7 passed
tests/test_sprint4_smoke.py             4 passed
tests/test_followups_smoke.py           6 passed
其余既有测试                          179 passed
─────────────────────────────────────────────────
合计                                  209 passed
```
