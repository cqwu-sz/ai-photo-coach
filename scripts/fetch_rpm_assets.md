# 替换占位资产为真实 ReadyPlayerMe + Mixamo 资产

`scripts/generate_placeholder_avatars.py` 生成的 8 个 glb / 8 张 PNG / 30 个动作 glb
是 **占位资产**，让加载管线立刻能跑通。视觉上是几何体拼接的人形，离 "游戏级" 还有距离。

下面是切到真 RPM + Mixamo 的完整步骤。**所有代码层不需要改一行**，只是 file-replace。

---

## 1) 准备 ReadyPlayerMe 角色（8 个）

### 选项 A：网页手动生成（推荐，免 API key）

去 [https://readyplayer.me/avatar](https://readyplayer.me/avatar) 用 Avatar Creator 在线
生成 8 个角色，对应 plan 的清单：

| preset id | 性别 | 年龄段 | 风格关键词 |
|---|---|---|---|
| `male_casual_25` | 男 | 青年 | 街头 / T 恤 + 牛仔 |
| `male_business_35` | 男 | 中年 | 西装 / 衬衫 |
| `male_athletic_28` | 男 | 青年 | 运动 / 紧身 T |
| `female_casual_22` | 女 | 青年 | 街头 / 卫衣 |
| `female_elegant_30` | 女 | 中青 | 礼服 / 高跟 |
| `female_artsy_25` | 女 | 青年 | 文艺 / 长裙 |
| `child_boy_8` | 男 | 儿童 | T 恤 / 短裤 |
| `child_girl_8` | 女 | 儿童 | 连衣裙 |

每个角色生成完毕后：

1. 点 Done → Download `.glb`
   - Body: **Full Body**
   - Pose: **T-Pose**
   - Texture atlas: **1024×1024**
   - Optimise: **Yes**（文件 ~3.5 MB）
2. 重命名为 `<preset id>.glb`
3. 替换 `web/avatars/preset/<id>.glb`
4. 同时去 `https://api.readyplayer.me/v1/avatars/<rpm-avatar-uuid>.png?size=256&type=head`
   下载头像 PNG，命名为 `<id>.png` 替换 `web/avatars/preset/<id>.png`

### 选项 B：API key 自动生成

申请 RPM Studio API key：[https://docs.readyplayer.me/ready-player-me/api-reference](https://docs.readyplayer.me/ready-player-me/api-reference)

```bash
export RPM_API_KEY="<your key>"
export RPM_APP_ID="<your app id>"
# Then run: python scripts/fetch_rpm_via_api.py  (not yet shipped)
```

---

## 2) 准备 Mixamo 动画（30 个）

去 [https://www.mixamo.com](https://www.mixamo.com)（Adobe 免费账号）。

### 资产清单

`web/avatars/animations/` 应保留这 30 个动作 id（一一对应 backend 的 pose_to_mixamo.json）：

```
idle_relaxed, idle_lean_wall, idle_sit_low_wall, idle_arms_crossed,
pose_hand_in_hair, pose_back_view, pose_jumping, pose_lying_grass,
pose_holding_object, pose_thinking, walk_natural,
couple_high_low, couple_forehead_touch, couple_side_by_side,
couple_back_to_back, couple_walk_handhold, couple_running,
couple_dancing, couple_seated_steps, couple_embrace, couple_piggyback,
family_lift_child,
group_triangle_pose, group_circle_jump, group_diagonal_walk,
group_walking_line, group_huddle, family_three_seated,
group_diamond_pose, group_four_couch
```

### 下载流程

1. Mixamo 网站搜索动作名（90% 一搜即中；couple_* 这类双人动作需要叠两个单人动作或自己绑定）
2. 上传你的 RPM glb 作为骨架（Mixamo 会 auto-rig），勾 **Use Source Model Skeleton**
3. 每个动作配置：
   - Frames per second: **30**
   - In Place: **Yes**（除 `walk_natural` 等行进类）
   - Skin: **Without Skin**
   - Format: **FBX**
4. 下载 `.fbx`

### 转 glb（一次性批量）

```bash
# 安装 fbx2gltf：https://github.com/godotengine/fbx2gltf/releases
brew install fbx2gltf  # 或解压 binary 到 PATH

# 批量转换（修改 INPUT_DIR）
INPUT_DIR=~/Downloads/mixamo_fbx
OUT_DIR=web/avatars/animations
for f in "$INPUT_DIR"/*.fbx; do
  name=$(basename "$f" .fbx)
  fbx2gltf -i "$f" -o "$OUT_DIR/$name.glb" --keep-attribute=auto --no-flip-v
done
```

文件名必须严格匹配上面 30 个 id（`<id>.glb`），否则 `pose_to_mixamo.json` 找不到。

---

## 3) 生成 iOS USDZ

替换 web glb 后，运行：

```bash
bash scripts/glb_to_usdz.sh
```

需要先安装 `usdzconvert`（macOS Xcode 自带，命令行：`xcrun usdzconvert ...`）或 `gltf2usd`（pip 安装跨平台）。

会把 web 端 glb → ios/AIPhotoCoach/Resources/Avatars/*.usdz 和 ios/AIPhotoCoach/Resources/Animations/*.usdz。

---

## 4) 验证

```bash
# 后端测试 — 检查 pose 映射完整性
cd backend && python -m pytest tests/test_pose_to_mixamo_mapping.py -v

# 截图 — 应该看到真实 RPM 角色站在 3D 预览中央
node scripts/snap_wizard_v7_only.mjs
open docs/preview/wizard_20_shot_preview_3d.png
```

如果替换正确：

- Web 3D 预览中的人形从 "几何体拼贴" 变成 RPM 风格的卡通真实角色
- `/avatars/manifest` 仍返回相同 JSON，但 `/web/avatars/preset/<id>.png` 会是真头像

iOS 端等 USDZ 落地后，`AvatarLoader.shared.load(presetId:)` 会从 fallback nil
变成返回真实 Entity，`ARGuideView` 自动从 SCN 路径切到 RealityKit 路径。

---

## 资产法务

- ReadyPlayerMe：用户在 readyplayer.me 生成的 avatar 归用户所有，可商用
- Mixamo：Adobe 提供，royalty-free for any project（包含商业）
- 占位资产（本仓库当前 glb）：MIT，由 `generate_placeholder_avatars.py` 生成

记得在 App 的 Settings → Credits 加 RPM + Mixamo 鸣谢条目（已在 plan 验收清单里）。
