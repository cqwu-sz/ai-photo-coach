# UX Polish Backlog (post-productization 体检)

> Source: 2026-05-11 第二次独立体检（区别于 `PRODUCTIZATION_BACKLOG.md` 关注的合规/可观测/订阅链路；这份只看「用户真到手里会减分 / 难受 / 不准」的事）。
>
> **Status legend**: ☐ todo · ◐ in progress · ✅ done · ⏸ blocked · ❌ skipped
>
> 每完成一个 sub-task：状态改 ✅ 并附 commit SHA。**不要删除已完成行**——retro 时要复盘。

## Priority lanes（按 ROI，不按修改难度）

| 批次 | 目标 | 范围 | 时间窗 |
|---|---|---|---|
| **Batch 1 · 结果页 & 漏斗** | 首屏价值传达 + 新手前 60 秒不流失 | #1 #2 #4 #6 #22 #21 | 第 1 周 |
| **Batch 2 · 专业感** | 看一眼觉得是正经产品，不是 demo | #8 #9 #10 #11 #12 #14 #24 + O3 O4 | 第 2 周 |
| **Batch 3 · 准确性 & 杂项** | 把"AI 说的不对"修掉 + 清理 dev 残留 | #3 #5 #7 #15 #16 #17 #18 #19 #23 + O1 O2 O5 | 第 3-4 周 |
| **Skipped（产品决策）** | 不在这轮做 | #13 #20 | — |

---

## Batch 1 · 结果页 & 漏斗（最高 ROI）

> 这批是「用户来了能不能留下」。改完之前不发任何对外推广。

### B1-1 / 首次权限 explainer（#1） ✅
- **问题**：摄像头 / 陀螺仪 / 定位三个授权分散在不同页弹出，iOS Safari 第一次拒掉几乎找不回；现在失败文案只有 `授权失败：xxx`。
- **实现**：
  - 新增 `web/permissions.html` + `web/js/permissions.js`，作为 welcome → wizard 之间的中转屏。
  - 3 张状态卡（摄像头 / 方向感应 / 位置），每张写清楚用途、是否必需、降级方式；卡片 `data-state` ∈ `idle/pending/granted/denied/skipped` 切换底色 + 状态徽章。
  - 「一键全部开启」按钮串行触发 `getUserMedia` → `DeviceOrientationEvent.requestPermission`（非 iOS 走 1.1s 嗅探）→ `geolocation.getCurrentPosition`；都在同一次用户手势下，iOS Safari 不会丢失 user-activation。
  - 拒绝时每张卡显示 iOS / Android 各自的恢复路径（系统设置 → Safari → 权限 / 长按地址栏锁图标 → 网站设置）。
  - 完成后写 `localStorage.aphc.permsExplainerSeen`，「继续」CTA 跳 wizard。「跳过」也写 `skipped` 防重弹。
  - 改造 welcome 的 CTA：首次去 explainer，已 seen 直接进 wizard。
- **验收**：iOS Safari 全新 session 跑一次 welcome → permissions → wizard → capture，三项授权一次拿到；任一拒绝有可恢复路径；返回访问不再弹 explainer。
- **Owner / SHA**：— (待 commit)

### B1-2 / 录制中实时 hint（#2） ✅
- **问题**：现在只有「覆盖 X%」一个进度，光线/速度/方向不对要等录完才告诉用户。
- **实现**：在 `capture.js` 内新建 `liveCoachLoop`，录制启动时 `startLiveCoach()` 开 500ms 定时器，停止时 `stopLiveCoach()`。
  - 信号融合：rolling mean luma（最近 12 个 sample）+ rolling median blur + 角速度（最近 1s 解 wrap-360）+ 最近 2s heading 总位移 + coverage 进度。
  - 严重度阶梯（一次只显一个 hint，避免文案抖动）：
    1. `meanLuma < 0.08` → "环境太暗 — 转向更亮的方向再试"
    2. `speed > 90°/s` → "转得有点快 — 慢一点，让 AI 看清楚"
    3. `medianBlur < 2.2 && speed > 40` → "画面有些糊 — 放慢手势 / 别让手抖"
    4. `last2sDelta < 4° && samples >= 12` → "继续顺时针转一点，把没覆盖的角度补上"
    5. coverage ≥ 0.9 → "覆盖完成 ✓"
    6. coverage ≥ 0.5 → "继续顺时针转，已覆盖 X%"
    7. fallback → "缓慢顺时针转动 · 覆盖 X%"
  - heading 回调被瘦身为只画 ring + needle，文案完全交给 coach loop。
- **验收**：故意瞎转（azimuth 不变）/ 怼地（无 luma 变化）/ 暗光环境分别能在 1s 内切到对应文案；停止录制后定时器清掉。
- **Owner / SHA**：— (待 commit)

### B1-4 / 错误文案重写（#4） ✅
- **问题**：`friendlyError` 只覆盖 503/quota/network 三类，其他原始消息 slice 220 字直接抛给用户。
- **实现**：
  - 新建 `web/js/error_messages.js`，覆盖 **11 类 case**：BUSY (503/overload) / QUOTA (rate-limit/429) / SAFETY (content filter / RECITATION) / BAD_KEY (401/invalid key) / TOO_BIG (413) / UPLOAD_BROKEN (multipart/stream_reset) / TIMEOUT (504/deadline) / NETWORK (fetch fail) / AUTH (jwt/token) / SERVER (500) / FRAMES_FEW (客户端关键帧不足)。
  - 正式环境兜底为「出了点意外，已经记下来了，稍后再试」+ 「复制错误码」按钮（用 `navigator.clipboard` 写入 `CODE: raw`，用户可发支持），`?debug=1` 时展开原文。
  - 摄像头授权失败按 `DOMException.name` 分流：`NotAllowedError` / `NotFoundError` / `NotReadableError` 各有具体文案 + 系统设置引导话术。
  - `capture.js` 和 `index.js` (demo flow) 都接入。`buildErrorView()` 渲染统一组件 `.err-view`。
- **验收**：枚举各类 raw 输入测过；正式环境无 JSON / HTTP status / stack 外漏。
- **Owner / SHA**：— (待 commit)

### B1-5 / loading 阶段链 + tips 降噪（#5） ✅（提前到 Batch 1 完成）
- **问题**：`capture.js` `FUNNY_TIPS` 4 条文案每 2.4s 循环，高质量模式 60s 会循环 25 次，搞笑变烦人。
- **实现**：删除 `setInterval` 轮播，改成"按真实阶段切文案"——`STAGE_COPY[stage]` 映射 extract/upload/ai/render 各自一句话；ai 阶段（停留最久）加一行 `sub` 安抚文案"高质量模式 ≈ 60 秒"。`setSpinnerCopy(stage)` 在 `setStage` 同步调用，零额外 timer。
- 新增 `.spinner-sub` CSS。
- **验收**：跑一次 fast / high 模式 loading，文案随阶段切换，不再循环；ai 阶段下方有静态副文案。
- **Owner / SHA**：— (待 commit)

### B1-6 / 结果页主次重排（#6） ✅
- **问题**：`render.js` 顶部依次塞 recapture banner / capture advisory / environment strip / scene / inspiration / 3 个 shot 卡，用户要滚 2-3 屏才看到「该怎么站怎么按快门」。
- **实现**：
  - **Web** (`web/js/render.js` `renderShot`)：调整为 hero → 相机 dial → coach bubble → **主 CTA（保存方案截图）** → 副 CTA（AR 演练）+ minimap → 姿势 → 默认折叠 `<details>`（7 维评分 / style match / iPhone tips / 原始 angle+composition+camera 行）。
  - **iOS** (`RecommendationView.swift` `ShotCard`)：调整为 hero → `CameraSettingsRow` → coach brief/rationale → 主 CTA「按此方案拍 · 自动调好参数」+ 副 CTA「AR 演练」→ 姿势 → `DisclosureGroup` 折叠 7 维评分 / ForegroundCard / IphoneTipsCard / AngleRow+CompositionRow。
  - 新增 `web/js/share_plan.js`：纯 Canvas 2D 渲染 1080×1920 卡片 → Web Share Sheet（iOS Safari / Android Chrome）或 PNG 下载，零依赖。
  - 新增 CSS：`.shot-primary-cta` / `.btn-primary-shot` / `.shot-longtail` / `.banner-inline-note` 等。
- **验收**：iPhone 15 Pro 视口下，方案 #1 的「机位 + 焦段/光圈/快门/ISO + coach brief + 主 CTA」全部在首屏，无需滚动。Mock 模式验证通过（`make_mock_response` 输出 shots[0] 包含全部字段）。
- **附带**：完成 O2（结果导出/分享），原计划放 Batch 3 的 `share_plan.js` 提前到 #6 一起做。
- **Owner / SHA**：— (待 commit)

### B1-21 / banner 合并（#21） ✅
- **问题**：`light_recapture_hint` 和 `capture_quality.score<=3` 两个红色 banner 可能同时挂在结果页顶部，用户看到两个负面提示直接放弃。
- **实现**：
  - **Web** (`web/js/render.js` `renderResult` 顶部 + `renderRecaptureBanner` / `renderCaptureAdvisory` 加 opts 参数)。
  - **iOS** (`RecommendationView.swift` 顶部 banner 选择逻辑 + `LightRecaptureBanner.degradedAdvisory` + `CaptureAdvisoryBanner.degradedHint`)。
  - 严重度阶梯：`capture_quality.should_retake`（score ≤ 2）> light_recapture > capture_quality score == 3。
  - 输家降级为赢家卡片内的 inline note（`.banner-inline-note` CSS / SwiftUI inline Text），信号不丢、视觉只剩一块。
- **验收**：构造 `should_retake=true` + `light_recapture_hint.enabled=true` 的 fixture，结果页只显示 advisory banner，光线 hint 作为 inline note 出现在 banner 底部。
- **Owner / SHA**：— (待 commit)

### B1-22 / 4 维评分加可执行解读（#22） ✅
- **问题**：LLM 自评的「构图 4.2 / 光线 3.8」对用户是空数字。
- **实现**：
  - **零新字段，零兼容代价**——发现 `criteria_notes` 字段已存在且前端 (`renderCriteriaPanel` / iOS `CriteriaPanel`) 已渲染。
  - 直接改 prompt (`backend/app/services/prompts.py` rule 12)：要求统一格式 `[rule_id] 现状一句 → 动作一句`，高分轴动作是"保持/锁住"，低分轴必须是第二人称可执行物理动作或参数微调，weakest_axis 尤其严格。新格式至少要覆盖 7 个轴里 4 个。
  - 更新 mock 数据 (`mock_provider.py`) 3 个 shot 全部 7 个轴改成新格式作为可见样例。
- **验收**：MOCK_MODE 下打开结果页 → 展开"更多分析" → 每个轴的 note 都有箭头 + 可执行动作；老格式（无箭头）渲染兼容不破。
- **Owner / SHA**：— (待 commit)

---

## Batch 2 · 专业感（不动核心流程，纯感知）

### B2-8 / 去版本号 dev 残留（#8） ✅
- `welcome.html` footer 移除 `v0.4`，换成「隐私政策」链接。
- 全 repo grep `v0\.` 没有其他遗留。
- **Owner / SHA**：— (待 commit)

### B2-9 / 品牌文案统一（#9） ✅
- 全部 page title 统一为「拾光 · {功能}」公式：
  - `welcome.html`: 「拾光 · 你的随身 AI 摄影导演」→ 「拾光 · AI 摄影教练」
  - `index.html`: 「拾光 · 环视一圈给你出片方案」→ 「拾光 · AI 摄影教练」
  - `privacy.html`: 「AI Photo Coach — 隐私政策」→ 「拾光 · AI 摄影教练 — 隐私政策」
  - `post_process.html`: 「修图 · AI Photo Coach」→ 「修图 · 拾光」
- 教练 > 导演（行为是"教你拍"不是"替你导演"），统一定调。
- **Owner / SHA**：— (待 commit)

### B2-10 / 场景卡 emoji 换 SVG（#10） ✅
- `web/index.html` 6 个场景卡：人像 / 特写 / 全身 / 人文 / 风景 / 光影，全部换成 inline SVG（viewBox 32×32, stroke 1.6px）。每个 SVG 用 currentColor，激活时切换到 accent color。
- 样式加在 `style.css` `.scene-card-glyph svg` + 激活态 selector。
- 其他 emoji（如 result 页的 📷、environment chip 的 ☀☁⛅）保留——它们要么在 details 内部、要么作为状态指示器，不是首要视觉。
- **Owner / SHA**：— (待 commit)

### B2-11 / 移除 preview.html（#11） ✅
- 加 `<meta name="robots" content="noindex,nofollow,noarchive">` 防止 Google 索引。
- `backend/app/main.py` 在 mount `/web` **之前**注册 explicit `GET /web/preview.html`，prod (mock_mode=false 且 app_env ∉ {local,dev,development}) 返回 404，dev/local 正常 serve。
- **Owner / SHA**：— (待 commit)

### B2-12 / welcome 加"为什么靠谱"卡（#12） ✅
- `welcome.html` 在 features 后加 `.welcome-trust` section，3 个 cell：
  1. **50+ 条专业摄影规则** — 标明评分维度，提到 `[rule_id]` 可见。
  2. **太阳算法本地计算** — 强调 NREL SPA + 不依赖外部 API。
  3. **7 维评分 + 可执行建议** — 引用 #22 改造完的"可执行 note"格式。
- 样式：`welcome.css` `.welcome-trust-grid`（≥ 720px 三列）+ 入场动画延迟 400ms。
- **Owner / SHA**：— (待 commit)

### B2-14 / welcome 加样片对比（#14） ❌（推迟）
- 现在没有真实「随手拍 vs 拾光方案」对比素材，硬塞 mock 图反而拉低可信度。
- 等有真实样片再做，转入下一季度产品 backlog。

### B2-24 / 隐私文案修正（#24） ✅
- `welcome.html` 「所有数据只保存在你这台设备」 → 「环视画面用于本次出片，关键数据脱敏后协助 AI 进步；可随时一键删除」。
- `privacy.html` 顶部一句话总结同步更新，并把主体名从 `AI Photo Coach` 改为 `拾光 · AI 摄影教练`。
- **Owner / SHA**：— (待 commit)

### B2-O3 / PWA manifest + icon ✅
- 新增 `web/manifest.webmanifest`（name / short_name / icons / theme color #0a0c18 / display standalone / start_url /web/welcome.html）。
- 新增 `web/img/icon.svg` 作为 maskable + any 的 icon 源（aperture 主题 + 暖冷渐变）。注：PWA 标准支持 SVG icon，避免发版前必须烤 PNG 的 blocker。
- `welcome.html` + `index.html` 都挂 `<link rel="manifest">` + `<link rel="apple-touch-icon" href="/web/img/icon.svg">` + `<meta name="theme-color">`。
- **后续改进**：发版前用 `rsvg-convert` 烤一组 PNG (192/512/maskable) 提高 iOS/Android 兼容性。
- **Owner / SHA**：— (待 commit)

### B2-O4 / 去掉 dev 文案（#O4） ✅
- `index.html:55` mode-badge 默认 `display:none`。
- `index.js` 在拿到 health 后：mock_mode=true 显示「示范数据」、网络失败显示「服务未连接」、live 模式**保持隐藏**（不再常驻"已连接"chip）。
- **Owner / SHA**：— (待 commit)

### B2-23 / README sideload 改 TestFlight 优先（#23） ✅（提前到 Batch 2）
- README 「iOS quickstart」段重写：先 TestFlight（推荐）→ Web demo（开箱即用）→ sideload（极客路径，明确 7 天过期）。
- **Owner / SHA**：— (待 commit)

---

## Batch 3 · 准确性 & 杂项

### B3-3 / Welcome 重看 + TTL 提示（#3） ✅
- welcome 首屏的 note 行重写为「免注册即可使用 · 关键数据脱敏 · 30 天不来匿名账号自动清空，也可随时一键删除 · 详情见 隐私政策」，跟 `privacy.html §5` 的 30 天 TTL 对齐。
- 「只看一次」逻辑早就在了（`index.html` inline redirect + `aphc.welcomeSeen`），本次只补 TTL 锚定。

### B3-5 / loading 阶段链 + tips 降噪（#5） ✅（Batch 1 已完成）

### B3-7 / BYOK 收进高级（#7） ✅
- `model_settings.js` 把 API Key + Base URL 两个输入框收进 `<details class="form-advanced">`，默认折叠；用户填过 key 的话保留 open（避免修改时找不到）。
- 每个 vendor 的 hint 从纯字符串改成可点 `<a target="_blank">`，6 家厂商一键跳申请页。
- 配套 CSS `.form-advanced` / `.form-advanced-summary` 自定义 disclosure marker。

### B3-15 / capture quality 阈值按 scene_mode 联动（#15） ✅
- `capture.js` 新增 `QUALITY_THRESHOLDS_DEFAULT` + `QUALITY_THRESHOLDS_BY_MODE` 表，定义 `light_shadow` / `scenery` / `closeup` 的 override。
- `light_shadow` luma block 0.06 → 0.02、warn 0.12 → 0.05；`scenery` pitch warn 35° → 50°、azWarn 90° → 120°；`closeup` azBlock 30° → 20°。
- `assessCaptureQuality` + `evaluateLiveHint` 都从 `qualityThresholds(settings.sceneMode)` 取，复用同一份表。

### B3-16 / fake heading 标 source（#16） ✅
- 前端 `capture.js` 用 `headingSource` 局部变量在 `heading.start()` 后落 `sensor` / `fake`，meta 里以 `heading_source` 字段上报。
- 后端 `models/schemas.py` `CaptureMeta` 新增 `heading_source: Literal["sensor","fake","unknown"]`（默认 `unknown` 兼容老客户端）。
- `services/prompts.py:_inputs_note` 在 `heading_source=="fake"` 时追加一条强约束：禁止说「光从 N°方向打来」、用「逆光侧/阴影侧」等画面内相对方向描述。
- **未做**：结果页"机位方向仅供参考"灰条——backlog 留给 v9.1（要在 render.js + RecommendationView.swift 双端做）。

### B3-17 / luma/blur 跨浏览器兜底（#17） ✅
- `keyframe.js` `computeFrameQuality` 失败 / 没 canvas 时返回 `{meanLuma: null, blurScore: null}` 而不是 0.5 中性值，避免 Safari 用户被静默判成"中等光照"。
- `assessCaptureQuality` 把 null filter 掉后再算均值/中位，empty 数组时 meanLuma/medianBlur 也是 null、跳过对应判定。

### B3-18 / geo rounding 分级（#18） ✅
- `config.py` 在原有 `geo_round_decimals=4` 之外加 `geo_round_decimals_log=3` / `geo_round_decimals_third_party=3` / `geo_round_decimals_poi=4`。
- 新增 `round_geo_by_use(value, use)` helper，use ∈ {`log`, `third_party`, `poi`, `default`}。
- `services/poi_lookup.py` `_fetch_amap` / `_fetch_osm` 都改用 `round_geo_by_use(..., "third_party")` 再发给第三方，避免高德/Overpass 看到原始 GPS。

### B3-19 / demo 模式常驻 banner（#19） ✅
- `result.js` 在 `params.get("demo")==="v7"` 或 `response.model` 匹配 `/^mock(-\d+)?$/i` 或 `debug.mode==="mock"` 时，在 content 上方插入 `.demo-banner`：「这是 **示范数据**，用来体验交互——回首页录一段真实环境就能拿到 AI 真实方案。」+「回首页录一段」CTA。
- 配套 CSS `.demo-banner` 用暖色 dot pulse 动画（reduced-motion 自动停）。

### B3-23 / README sideload 改 TestFlight 优先（#23） ✅（Batch 2 已完成）

### B3-O1 / 历史方案页入口 ✅
- `store.js` 新增 `appendResultHistory` / `listResultHistory` / `loadResultHistoryEntry` / `clearResultHistory`，滚动保留 10 条到 `localStorage.aphc.resultHistory.v1`，quota 超了自动降到 3 条。
- `saveResult` 在非 mock 响应时自动 append。
- 新增 `web/history.html` 列表页：时间 / 场景 / 方案数 / model；点 card 把 payload 回灌到 `saveResult` 再跳 `result.html`。
- 入口：`result.html` header 加「历史」badge、`welcome.html` footer 加「历史方案」链接。

### B3-O2 / 结果导出 / 分享 ✅（随 #6 一起做完）
- Web：`share_plan.js` 纯 Canvas 2D 渲染 1080×1920 卡（标题/机位/焦段-光圈-快门-ISO/姿势/footer），Web Share API 优先，fallback 下载 PNG。零外部依赖（没引 html2canvas 的 150KB tax）。
- iOS：主 CTA 已经是「按此方案拍」直接进 shoot 屏，本身就是更强的"带去现场"动作；图片分享走 iOS 系统截屏 + Share Sheet（用户行为完全自然，无需自建 UI）。
- **Owner / SHA**：— (待 commit，与 #6 同 commit)

### B3-O5 / BYOK XSS 警告 ✅
- `model_settings.js` 高级折叠段末尾加 `.form-warn` 黄色提示：「Key 只保存在你这台浏览器（localStorage），但浏览器扩展或他人共用设备时仍可能读到——共享设备请用完点"清除密钥"。建议在你的 key 提供商那里给这把 key 设额度上限。」
- 「清除本地保存」按钮原来就有，文案保持。

---

## Skipped（不在本轮做）

### ❌ #13 / 角色选择默认隐藏
- 产品决策：avatar 是 demo 站位的核心可视化，不隐藏。

### ❌ #20 / Web 端 CTA 引导到 iOS
- 产品决策：web 本身是独立 PWA 产品，不只是 iOS 的引流页。

---

## Tracking rules

1. 每完成 sub-task：`☐` → `✅` + commit SHA + 一行验收结论。
2. 一批做完在文件末尾加一个 batch retro section（实际用时 / 阻塞 / 漏的 case）。
3. 跳过的项目（❌）写明原因，下个季度复盘时重看。
