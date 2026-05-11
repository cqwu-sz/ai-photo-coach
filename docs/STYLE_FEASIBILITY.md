# 风格可行性矩阵 (Style × Environment Feasibility)

> 目的：让用户在第 3 步选风格时，**结合当前环境**判断这个风格能不能拍出来；
> 让 LLM 在出方案时按"风格 → 该环境下推荐参数"硬性靠拢，而不是泛泛而谈。
>
> 数据来源：`backend/app/services/sun.py` (NREL SPA 算的太阳方位/高度/色温/相位)
> + `backend/app/services/weather.py` (Open-Meteo 云量/能见度/UV/光软硬)
>
> 这份文档既是**用户/审核**可读的设计依据，也是
> [`backend/app/services/style_feasibility.py`](../backend/app/services/style_feasibility.py)
> 代码里阈值表的真理之源。改阈值时**两边一起改**。

---

## 1. 5 个风格各自的"摄影硬条件"

下面是成熟摄影经验转化的硬性需求。列出每个风格**必须的环境特征**（缺了就出不来），
**加分**特征（有了更好），**杀手**特征（有了直接黄牌）。

### 氛围感 (cinematic_moody)

| 维度 | 必需 | 加分 | 杀手 |
|---|---|---|---|
| 光高度角 | altitude < 35° | < 10° (黄昏/夜) | > 60° (顶光出不来阴影) |
| 光软硬 | hard 或 mixed | 硬光 + 雾/烟 | 全阴天 (没主光方向) |
| 色温 | < 5500K | 3000-4500K (暖低光) | > 6500K (高色温显冷淡) |
| 时段 | 黄金/蓝调/夜 | golden_hour_dusk / blue_hour | 正午前后 ±2h |

**人话理由**：氛围感需要"光不平均"——某个方向亮、某个方向暗。中午顶光光线均匀，
全阴天散射光也均匀，都出不来戏剧感。

### 清爽日系 (clean_bright)

| 维度 | 必需 | 加分 | 杀手 |
|---|---|---|---|
| 光高度角 | altitude > 20° | 30-60° (白天柔和) | < 10° (黄昏太暗，色温偏暖) |
| 光软硬 | soft 或 mixed | 薄云 (cloud 30-70%) | 强逆光、低光 |
| 色温 | ≥ 5000K | 5500-7000K (清冷自然) | < 4500K (黄到不像日系) |
| 时段 | day / golden_hour_dawn | 上午-下午 | 夜、blue_hour |

**人话理由**：日系核心是"明亮+干净"。色温低于 5000K 自动偏黄不再清爽；altitude 过低
（黄昏）色温会快速下落到 3500K 以下；薄云比晴天好（光更柔不刺眼）。

### 温柔暖光 (film_warm)

| 维度 | 必需 | 加分 | 杀手 |
|---|---|---|---|
| 光高度角 | altitude < 20° | < 10° (黄金时段) | > 50° (色温偏冷) |
| 光软硬 | mixed 或 hard | 晴天黄昏 (硬+暖) | 全阴天 (软但灰、不暖) |
| 色温 | < 5000K | 2800-4000K (Kodak Gold 风) | > 5500K (太冷不复古) |
| 时段 | golden_hour_dawn / golden_hour_dusk | 距日落 < 60 min | 正午、夜 |

**人话理由**：胶片暖调 = 黄金时段独有的暖色低角度光。距日落 > 2 小时、altitude > 20°
就基本不可能；阴天没有定向暖光也无效。

### 自然随手 (street_candid)

| 维度 | 必需 | 加分 | 杀手 |
|---|---|---|---|
| 光高度角 | 任意 > 5° | 10-50° (方便扫街) | altitude < 0 (太黑没法抓拍) |
| 光软硬 | 任意 | 任何条件都能玩 | 强极端天气 (大雨大雾视野差) |
| 色温 | 任意 | 任何 | — |
| 时段 | 几乎任意 (除深夜) | day / golden / blue | 凌晨 0-5 点 |

**人话理由**：街头风格本来就讲究"将就环境"，硬条件最少。只要有点光、能看清主体，
都能拍。这一项基本永远可行。

### 大片感 (editorial_fashion)

| 维度 | 必需 | 加分 | 杀手 |
|---|---|---|---|
| 光高度角 | altitude > 0° (有定向光) | 10-50° (能造型) | altitude < 0 + 无人造光 |
| 光软硬 | hard 或 mixed | 硬光 (轮廓清晰) | 软光大平光 (失去戏剧度) |
| 色温 | 任意 | 任意 (后期能调) | — |
| 时段 | 几乎任意 | golden / 正午硬光 / blue | 完全黑夜 |

**人话理由**：杂志大片靠"姿态 + 干净背景 + 强造型光"。软光可以拍但会平淡；
完全黑夜没人造光做不出。

---

## 2. 6 个旋钮 × 5 个风格的推荐倾向

LLM 输出的每张 shot 包含这些可控字段。下表是"理想环境下"该给的值范围。
环境不理想时由可行性分数加权降级。

| 旋钮 | 氛围感 | 清爽日系 | 温柔暖光 | 自然随手 | 大片感 |
|---|---|---|---|---|---|
| `camera.white_balance_k` | 3500-4500 | 5500-6500 | 3200-4500 | 5000-5800 | 自由 (4500-7500) |
| `camera.focal_length_mm` | 35-85 | 24-50 | 35-85 | 35-50 | 50-135 |
| `camera.ev_compensation` | -0.7 ~ -0.3 | 0 ~ +0.7 | -0.3 ~ +0.3 | -0.3 ~ +0.3 | -0.3 ~ 0 |
| `camera.aperture` | f/1.4-f/2.8 | f/2.8-f/5.6 | f/1.8-f/4 | f/2.8-f/5.6 | f/2.8-f/8 |
| `scene.lighting` | golden/blue/low_light/backlight | overcast/shade/golden_dawn | golden_dawn/golden_dusk | 任意 | golden/harsh_noon/backlight |
| `composition.primary` | leading_line / negative_space / diagonal | rule_of_thirds / centered / symmetry | rule_of_thirds / golden_ratio | rule_of_thirds / leading_line | centered / negative_space / symmetry |
| `angle.height_hint` | low / eye_level | eye_level / high | eye_level | eye_level | low / high (戏剧角度) |

> 注：白平衡是最直接的视觉差异（一眼就看出冷暖），所以阈值最严。
> 焦段差距 35mm vs 85mm 用户也能感知。其他几项对最终图影响相对软。

---

## 3. 环境 → 5 风格可行性打分逻辑

输入：`SunInfo`（来自 `sun_service.compute(lat, lon, t)`）+
`WeatherSnapshot`（来自 `weather_service`，可能为 None）

输出：每个风格一个 0.0-1.0 的可行分 + 一句说人话的中文理由。

### 通用关键变量

```
altitude   = sun.altitude_deg            # 太阳高度角，决定光的硬度/偏向
phase      = sun.phase                    # day / golden_hour_dusk / blue_hour_dawn / night ...
kelvin     = sun.color_temp_k_estimate   # 估算色温
softness   = weather.softness if weather else 'unknown'  # soft / hard / mixed / unknown
cloud_pct  = weather.cloud_cover_pct or 50
to_sunset  = sun.minutes_to_sunset         # 到日落分钟数（白天有效）
```

### 评分算法（每个风格独立，0-1）

每个风格按下面打 3 个子分（高度角、软硬、色温），各 0-1，最后取**加权几何平均**
（任意一项接近 0 就直接拉低总分）。然后按特殊加成项加 0-0.2。

```
score = (sub_altitude * sub_softness * sub_kelvin) ** (1/3) + bonus
```

**子分函数（伪代码，真实见 `style_feasibility.py`）**：

```python
def score_cinematic_moody(altitude, softness, kelvin, phase, ...):
    sub_alt = sigmoid_band(altitude, ideal=5, kill_above=60)   # 越低越好
    sub_soft = {'hard': 1.0, 'mixed': 0.7, 'soft': 0.2, 'unknown': 0.6}[softness]
    sub_k = sigmoid_band_decay(kelvin, ideal=4000, falloff_above=6000)
    bonus = 0.15 if phase in (golden_dusk, blue_dusk, night) else 0
    return geom_mean(sub_alt, sub_soft, sub_k) + bonus

def score_clean_bright(altitude, softness, kelvin, phase, ...):
    sub_alt = sigmoid_band(altitude, ideal=40, low_kill=10)    # 要够亮
    sub_soft = {'soft': 1.0, 'mixed': 0.85, 'hard': 0.55, 'unknown': 0.7}[softness]
    sub_k = sigmoid_band(kelvin, ideal=6000, low_kill=4500)    # 要够冷
    bonus = 0.10 if phase == 'day' else 0
    return geom_mean(sub_alt, sub_soft, sub_k) + bonus

def score_film_warm(altitude, softness, kelvin, phase, to_sunset, ...):
    sub_alt = sigmoid_band(altitude, ideal=8, kill_above=30)
    sub_soft = {'mixed': 1.0, 'hard': 0.85, 'soft': 0.4, 'unknown': 0.6}[softness]
    sub_k = sigmoid_band_decay(kelvin, ideal=3500, falloff_above=5500)
    bonus = 0.20 if (phase in (golden_dusk, golden_dawn)) or
                    (to_sunset and to_sunset < 60) else 0
    return geom_mean(sub_alt, sub_soft, sub_k) + bonus

def score_street_candid(altitude, ...):
    # 几乎永远可行，除非纯黑夜
    if altitude is None or altitude > 5: return 0.85
    if phase == 'night': return 0.4
    return 0.7

def score_editorial_fashion(altitude, softness, ...):
    sub_alt = 1.0 if altitude > 5 else 0.3
    sub_soft = {'hard': 1.0, 'mixed': 0.8, 'soft': 0.55, 'unknown': 0.7}[softness]
    sub_k = 0.85   # 色温对大片影响最弱
    return geom_mean(sub_alt, sub_soft, sub_k)
```

### 阈值定义：可行性等级

```
score >= 0.7    : "推荐"      (绿色 ✓)
0.45 <= score < 0.7 : "勉强可"   (黄色 △)  - 卡片正常显示，但加 ⚠ 标
score < 0.45    : "不推荐"     (灰色 ⚠)  - 卡片半透明，hover 显示原因
```

### 文案模板（给前端 UI 用）

每个风格在算分时同时输出一句中文理由：

```
推荐: "当前 {phase_zh} {softness_zh}光线，正适合{label_zh}"
勉强: "当前{condition}，{label_zh}能拍但效果会打折"
不推荐: "当前{condition}，{label_zh}基本拍不出来；建议改{suggestion}"
```

举例：
- altitude=2°, softness=hard, kelvin=3200, phase=golden_dusk
  - 氛围感: 0.85 → "当前黄昏硬光，正适合氛围感"
  - 清爽日系: 0.18 → "当前黄昏色温偏暖，清爽日系基本拍不出来；建议改温柔暖光"
  - 温柔暖光: 0.92 → "当前黄昏硬光，正适合温柔暖光"
  - 自然随手: 0.85 → "几乎任何环境都能拍"
  - 大片感: 0.82 → "当前光线不错，能拍出大片感"

---

## 4. 没有 GPS 时怎么办

**短答**：不打分，所有风格都正常显示，但 prompt 里告诉 LLM "用户未授权位置，
风格选择属于用户偏好，请尽力按 STYLE_PRESETS 表给出该风格的典型参数倾向"。

**长答**：可行性检查只在有 GPS 时启用。没 GPS 时：
- UI 不加任何 ⚠ 标记
- 后端 prompt 里仍然加 `STYLE_PRESETS` 推荐倾向块（旋钮表）
- LLM 自由发挥但有锚点

---

## 5. 维护规则

- 阈值改动：先在本文档第 1/2 节改阈值/范围，再同步到
  [`backend/app/services/style_feasibility.py`](../backend/app/services/style_feasibility.py) 的常量
- 新增风格：先在 [`web/img/style/manifest.json`](../web/img/style/manifest.json) 加风格 → 在本文档加它的硬条件 + 推荐旋钮 → 在 `style_feasibility.py` 加 score 函数
- 删除风格：反向操作

---

## 6. 验收

- 上海 5/9 16:00（晴）：
  - 氛围感 0.4-0.6（中午刚过没多久，光太高）
  - 清爽日系 0.7+（5500K + altitude 32° + 推断 mixed）
  - 温柔暖光 0.3 左右（还没到黄金时段）
  - 自然随手 0.85
  - 大片感 0.8

- 上海 5/9 18:30（黄昏，golden_hour_dusk）：
  - 氛围感 0.85+
  - 清爽日系 < 0.4
  - 温柔暖光 0.9+
  - 自然随手 0.85
  - 大片感 0.85
