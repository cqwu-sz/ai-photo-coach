# LUT 资源目录

`FilterEngine` 在运行时按 `Bundle.main.url(forResource:"<id>", withExtension:"cube", subdirectory:"LUTs")` 加载文件。文件名必须与 `PostProcessRecipe.filter_preset` 的 8 个 key 一一对应：

| id              | 用途          | 当前状态        |
|-----------------|---------------|-----------------|
| `natural`        | 通透干净     | baseline 占位  |
| `film_warm`      | 暖胶片       | baseline 占位  |
| `film_cool`      | 冷胶片       | baseline 占位  |
| `mono`           | 黑白         | baseline 占位  |
| `hk_neon`        | 港风霓虹     | baseline 占位  |
| `japanese_clean` | 日系小清新   | baseline 占位  |
| `golden_glow`    | 金色暖光     | baseline 占位  |
| `moody_fade`     | 复古褪色     | baseline 占位  |

## 重新生成 baseline

```bash
python scripts/luts/gen_baseline_luts.py
```

baseline 是程序化生成的 17³ `.cube`（每个 ~134 KB），仅用于跑通链路。**正式上线前要替换成手工调色或商业 LUT**（保持文件名不变），调色逻辑也可以升 33³ 提升过渡平滑度。

## 添加 Xcode bundle

工程使用 PBXFileSystemSynchronizedRootGroup 自动包含 `Resources/`，新增 `.cube` 文件无需手动 add。如果 Xcode 没自动识别：右键 `Resources` → Add Files → 勾选 "Create folder references"（**不是** "Create groups"），确保 `LUTs/` 作为 subdirectory 进 bundle。

## 自检

在 `PostProcessView` 把 `model.lutId = "hk_neon"` 写死，效果与不带 LUT 应肉眼可见差异。也可以在 iOS console 看 `FilterEngine.makeLUTFilter(lutId:)` 是否返回 nil（nil 表示 .cube 没进 bundle）。
