# 虚拟角色系统

> 用户当初的反馈：**"VRoid 角色我不会弄"**。所以我们改了路线 — 不依赖任何外部 .vrm 文件，**用代码程序化构造类动漫角色**，0 外部依赖、0 license 风险。

## 内置 7 个角色（2 男 5 女，发型/服装明显差异）

| ID | 性别 | 名字 | 特点 |
|---|---|---|---|
| `akira` | 男 | 彻 Akira | 黑短发 · 蓝衬衫 |
| `jun` | 男 | 纯 Jun | 棕寸头 · 黑夹克 · 眼镜 |
| `yuki` | 女 | 雪 Yuki | 黑长直发 · 白连衣裙 |
| `sakura` | 女 | 樱 Sakura | 粉色双马尾 · 粉色短裙 |
| `rena` | 女 | 玲奈 Rena | 棕色波波头 · 黄毛衣 |
| `luna` | 女 | 露娜 Luna | 银色长卷 · 黑外套 |
| `haruko` | 女 | 春子 Haruko | 红狼尾 · 牛仔风 |

每个角色：
- 程序化骨骼（嵌套 Group/Node 转动控制姿势）
- 参数化外观：肤色、发色、发型几何、服装、配饰
- 5 种表情切换（中性、笑、抿嘴、惊讶、沉思），写到 canvas 纹理上贴脸
- 12 种 pose 预设（standing / hands_clasped / walking / half_sit / crouch / looking_back / holding_hands / hand_on_hip / v_sign / arms_crossed / facing_partner / leaning），AI rationale 关键词自动匹配

## 跨端一致

| 客户端 | 文件 |
|---|---|
| Web | `web/js/avatar_builder.js` + `web/js/avatar_styles.js` + `web/js/pose_presets.js` + `web/js/expression_system.js` |
| iOS | `ios/AIPhotoCoach/Features/ARGuide/AvatarBuilderSCN.swift` + `AvatarStyles.swift` + `PosePresets.swift` + `ExpressionRenderer.swift` |

骨骼 joint 名（leftShoulder, rightElbow, head, ...）两端完全对齐，所以 PosePresets 的 12 种姿势在 Web 和 iOS 上是同一份逻辑。AI 给出的 rationale → 关键词分类器 → preset 名也是镜像的（regex 完全一样）。

## 用户怎么选

**Web**：首页"选你的虚拟角色"面板。按当前人数显示 1–4 个 slot；点 slot 激活，点下面的 7 个缩略图分配。选择保存在 `localStorage.apc.avatarPicks`，跨页面复用。

**iOS**：`RootView` 同样有"选你的虚拟角色"面板，每个 slot 是 NavigationLink，点开 `AvatarChooserView`（7 个 LazyVGrid 卡片）选完返回。选择保存在 `@AppStorage("aphc.avatarPicks")`，逗号分隔。

## 渲染管线

**Web (Three.js + WebGL)**:
- `scene_3d.js`：result 页 hero 区，3D 全景球 + 角色按 shot 方位/距离站位 + 拖屏 360° 看
- `avatar_preview.js`：guide 页姿势示意，自动转动的小 3D 视图

**iOS (SceneKit + ARKit)**:
- `ARGuideView.swift`：试拍页，ARSCNView + LiDAR 测距 + AlignmentMachine 绿光放行
- `AvatarThumbView` (RootView)：首页 slot 缩略图，纯 SCNView 离屏渲染

## 进一步定制（可选）

如果将来想加新角色，只要在 `avatar_styles.js` 和 `AvatarStyles.swift` 各加一条 preset。如果想加新发型，给 `buildHair()` 加一个 `case`。如果想加新姿势，给 `pose_presets.js` 和 `PosePresets.swift` 各加一组关节角度。
