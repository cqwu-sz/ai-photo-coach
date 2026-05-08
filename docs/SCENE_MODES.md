# 6 档出片场景

`SceneMode` 枚举决定了分析流程几乎每一层的偏置：prompt 分支、相机参数甜区、姿势引擎是否提供姿势、UI 是否显示 Avatar 选择 + 入框检测，以及是否请求位置。

## 速查表

| Mode | id | 焦段 | 光圈 | 构图偏好 | poses | UI 行为 |
| --- | --- | --- | --- | --- | --- | --- |
| 人像 | `portrait` | 35-85 mm（默认 50） | f/1.4-f/2.0 | rule_of_thirds / frame_within_frame | 必填，详细 stance + gaze + expression | 默认模式，向后兼容 |
| 特写 | `closeup` | 70-135 mm（默认 85） | ≤ f/2.0 | centered / negative_space | 必填，重点 hands / gaze / expression | 距离自动缩短到 ~1 m |
| 全身 | `full_body` | 35-50 mm | f/2.0-f/2.8 | rule_of_thirds / leading_line | 必填，重点 stance | 距离 2-4 m |
| 人文 | `documentary` | 24-50 mm（默认 28） | 中等 | leading_line / frame_within_frame | 必填，walking / leaning / sitting | layout 偏向 cluster / line / diagonal |
| 风景 | `scenery` | 14-35 mm（默认 24） | f/8 | leading_line / negative_space / symmetry | **可空 `[]`** | 隐藏 Avatar 选择，AR 不要求入框 |
| 光影 | `light_shadow` | 50-135 mm（特写偏 85+） | f/4-f/8 | negative_space / leading_line / frame_within_frame | 可不出多人，强调轮廓 | 请求位置 → 计算太阳数据；结果页显示太阳罗盘 + 黄金时刻倒计时；按时间敏感度重排 shots |

## person_count 的特殊规则

* 默认所有模式 `person_count ∈ [1, 4]`。
* 仅 `scenery` 模式允许 `person_count = 0`：后端 `CaptureMeta` 里有 `model_validator` 校验，其它模式传 0 会 400。
* `scenery + person_count > 0` 是合法组合：人物作为画面点缀（背影、远景剪影），AI 仍会给一些粗略 pose 描述但不强制 hands / expression。

## 后端实现指引

* `backend/app/services/prompts.py::_scene_mode_branch(mode, person_count)` —— 5 段中文 prompt 文本，作为 user prompt 的一部分注入。
* `backend/app/services/camera_params.py::_apply_scene_mode(preset, scene_mode)` —— 在原始 lighting × person_count 预设之上做焦段 / 光圈微调。`repair_camera_settings(... scene_mode=)` 用 `_focal_range_for(scene_mode)` 决定合法焦段范围。
* `backend/app/services/pose_engine.py::fallback_pose(person_count, scene_mode=)` —— `scene_mode == "scenery" && person_count == 0` 时直接返回空 `persons`，layout 留 `single` 占位。
* `backend/app/services/analyze_service.py` 在 scenery 模式下跳过 `pose_engine.map_to_library`（不会去 pose 知识库匹配），只剔除空 PoseSuggestion。
* `backend/app/api/dev.py::/dev/sample-manifest?scene_mode=...` 提供 5 套 demo manifest；每套自带推荐的 `person_count_default` 与 `style_keywords_default`。

## 客户端实现指引

### Web

* 首页 `<section class="scene-section">` 5 个 chip。选中 `scenery` 时 `data-scenery="1"` 让人数行半透明，并自动激活 `0 人` chip + 隐藏 Avatar 区。
* `web/js/store.js::saveSceneMode/loadSceneMode` 用 `localStorage` 持久化。
* `web/js/render.js::isSceneryShot(shot) = shot.poses.length === 0`：风景 shot 收起 3D toggle、姿势卡，改显 `renderSceneryTips(shot)`（构图 + 站位 + 相机 4 行要点）。
* `web/js/guide.js` 风景模式隐藏"入框"HUD 卡，stepPose 文案改成"构图：xxx"。
* `web/js/avatar_gallery.js::refreshSlots` 在 `personCount() <= 0` 时清空两个 host，跳过缩略图渲染。

### iOS

* `Schemas.swift::SceneMode` 枚举（`portrait/closeup/full_body/documentary/scenery`），实现 `CaseIterable + Hashable + Codable`，提供 `displayName / blurb / allowsZeroPeople`。
* `RootView.swift` 顶部新增水平滚动的场景 picker，`@AppStorage("aphc.sceneMode")` 持久化。`scenery` 选中时人数 picker 加 `0`、avatar 区 `if sceneMode != .scenery` 折叠。
* `AppRouter.AppDestination.capture(... sceneMode: ...)` 多带一个参数。
* `EnvCaptureViewModel.stopAndAnalyze` 把 `sceneMode` 拼进 `CaptureMeta`。
* `RecommendationView.ShotCard` 风景 shot（`shot.poses.isEmpty`）渲染"风景出片要点"区块，不再渲染姿势卡。
* `ARGuideView` 风景 shot 调用 `alignment.disable(dimension: .person)` 跳过入框检测；`statusCards` 隐藏入框卡；不再调 `placeAvatar`。

## 测试落点

* `backend/tests/test_scene_modes.py` —— 5 种模式 prompt 分支字符串断言、camera 焦段验证、scenery + 0 人 fallback、`CaptureMeta` 拒收非法 `person_count=0`。
* `backend/tests/test_analyze_endpoint.py::test_analyze_accepts_scenery_mode_with_zero_people` —— /analyze 端到端确保返回 `poses == []`。
* `web/js/__tests__/sceneMode.test.mjs` —— `isSceneryShot` 行为契约。
