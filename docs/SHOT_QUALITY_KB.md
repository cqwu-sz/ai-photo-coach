# 拾光 · 专业摄影评判知识库（v6 KB）

这份文档是 `[backend/app/knowledge/composition/](../backend/app/knowledge/composition/)` 下约 **210 条** JSON 的总纲——它们就是 LLM 给出 `criteria_score`/`criteria_notes` 时**必须引用的规则字典**。

## 1. 设计目标

把"什么是好看"从"LLM 凭印象主观打分"升级到"按可引用规则评判"：

- 每条 KB 是一条可执行的摄影判定规则（不是纯口号）
- LLM 在每个轴的 `notes` 里**必须**以 `[rule_id] ...` 引用一条本字典里的规则；找不到合适规则才允许写 `[freeform] ...`
- 注入时按 `scene_mode` + `axes` + `priority` **动态挑 top-30**（约 800-900 token）灌进 prompt，避免 token 爆炸
- 全部规则**自撰中文化**，仅引用书目章节级（不复制原文），合规可控

## 2. KB 条目 schema

`[backend/app/knowledge/composition/<id>.json](../backend/app/knowledge/composition/)` 每条规则一文件，UTF-8。

```json
{
  "id": "comp_rule_of_thirds",
  "category": "composition",
  "name_zh": "三分法",
  "summary": "把画面横竖各 1/3 切成 9 格，主体放交叉点或沿三分线",
  "axes": ["composition", "subject_fit"],
  "scene_modes": ["portrait", "full_body", "documentary", "scenery"],
  "person_count_range": [0, 4],
  "priority": 5,
  "when_to_use": ["人像半身/全身", "风光带主体", "环境人像"],
  "evidence": "把视觉重心从画面中心移开，能让画面产生流动感与呼吸空间；眼睛偏离正中也更接近自然观看。",
  "watch_out": "背景已左右对称（建筑正立面、对称湖景）时强行三分会破坏稳定感",
  "counter_example": "拍证件照、国宝建筑正面、节日合影 — 这些主题就是要居中",
  "tags": ["对称感弱", "动态感强", "适配 16:9"],
  "citations": [
    { "source": "Michael Freeman, The Photographer's Eye (Definitive Ed)", "chapter": "Ch.3 Dividing the Frame" },
    { "source": "宁思潇潇《新摄影笔记》", "chapter": "Ch.4 构图基础" }
  ],
  "embedding": null
}
```

字段含义：

| 字段 | 必填 | 类型 | 含义 |
|---|---|---|---|
| `id` | 是 | str | 全局唯一，蛇形命名，前缀决定 category（`comp_` / `sub_` / `bg_` / `theme_`） |
| `category` | 是 | enum | 4 选 1：`composition` / `subject` / `background` / `theme` |
| `name_zh` | 是 | str | 中文规则名（≤ 12 字），用于 LLM `criteria_notes` 引用 |
| `summary` | 是 | str | ≤ 40 字一句话总结 |
| `axes` | 是 | list[str] | 这条规则用来评判的 7 维评分轴（取自 `composition`/`light`/`color`/`depth`/`subject_fit`/`background`/`theme`），可多选 |
| `scene_modes` | 是 | list[str] | 哪几个场景模式适用（取自 `portrait`/`closeup`/`full_body`/`documentary`/`scenery`/`light_shadow`） |
| `person_count_range` | 否 | [int, int] | 人数适用区间，0 表示风景/纯环境也行；缺省视为 [0, 4] |
| `priority` | 是 | int 1-5 | 5 = 必修核心规则 / 3 = 常用 / 1 = 冷门补强；注入器先选高 priority |
| `when_to_use` | 是 | list[str] | 触发场景，3-5 条短语 |
| `evidence` | 是 | str | **原创中文**说明为什么这条是好看的依据，≤ 80 字。**禁止**复制原书原文 |
| `watch_out` | 是 | str | 反向告诫：什么时候这条会失效甚至添乱 |
| `counter_example` | 否 | str | 一句话举一个不该用这条规则的反例 |
| `tags` | 否 | list[str] | 用于 RAG 后期向量查询的标签 |
| `citations` | 否 | list[obj] | 来源书目，**只到章节级别**，不放页码段落 |
| `embedding` | 否 | list[float] / null | Phase 3+ 才会批量算；MVP 留 null |

## 3. 4 大 category 目录

### 3.1 构图 / `composition`（约 56 条）

来源主力：Michael Freeman《Photographer's Eye》、宁思潇潇《新摄影笔记》、顾荣军《非常摄影手记》、顾锡《看佳作学构图》、Scott Kelby《Crush the Composition》、B 站《零基础构图》

子主题：

- 经典分割：三分法、黄金比、对角线、对称、中心、L/S/Z 形动线
- 引导线与几何：引导线、消失点、汇聚线、平行斜线、几何重复、节奏感
- 框架：框中框、自然边框（树枝/拱门）、屏息空间
- 留白与节奏：负空间、呼吸空间、视觉重心、视觉锚点
- 层次：前景压花、近-中-远三层、空气透视
- 视角：低位仰拍、高位俯拍、环绕弧线、平视、与主体同高
- 视觉力学：图形对比、明暗对比、色块对比、形状重复
- 开放/封闭：开放式（视线指向画外）vs 封闭式（视线在画内）

### 3.2 主体 / `subject`（约 36 条）

来源主力：David duChemin《Photographically Speaking》、巴内克《人像摄影经典教程》、Scott Kelby《Crush the Composition》

子主题：

- 比例：人物占画面高度 1/3 - 1/2 / 半身 60% / 特写 80%
- 位置：视线方向 60% 留白、距画面边缘≥1 个头距、不要让主体顶到画面顶端
- 姿态：破对称、高低错位、肢体不闭合、手不要遮脸但可托腮
- 视线：与镜头交流 vs 看向画外讲故事，按主题选
- 多人：主-辅人物大小差、紧凑度、相互交互（不要并排站桩）
- 边缘呼吸：四肢/手/头别被画面边缘截到关节
- 主体识别：背景里别有抢眼物体（红伞/广告牌）拉走视线

### 3.3 背景 / `background`（约 32 条）

来源主力：Scott Kelby《Crush the Composition》、宁思潇潇、顾锡

子主题：

- 复杂度：背景元素 ≤ 3、删繁就简的取景策略
- 主体-背景分离：色彩对比 / 亮度对比 / 焦点分离 / 距离分离
- 穿头穿杆：避免主体长在树/灯柱/电线杆上
- 色块干扰：背景同色系拉低识别度的修正方法
- 透视：背景灭点位置、地平线对齐
- 背景情绪：背景作为环境信息（环境人像）vs 背景作为虚化（糖水）
- 噪点元素：路人、广告牌、垃圾桶、施工的处理

### 3.4 主题 / `theme`（约 36 条 + 36 条理论补强）

来源主力：David duChemin《Within the Frame》、Cartier-Bresson《Decisive Moment》、Roland Barthes《Camera Lucida》、Susan Sontag《On Photography》、John Suler《Photographic Psychology》（合规免费在线书）、Bryan Peterson《Learning to See Creatively》

子主题：

- 主题表达：旅拍 / 纪实 / 糖水 / 人文 / 写真 / 风光 / 街头 各自的"该长什么样"
- 决定性瞬间：等待 vs 抓拍、动作峰值、表情峰值
- 情绪：温度（冷峻 vs 温暖）、节奏（静 vs 动）、孤独 vs 喧嚣
- 故事性：单图叙事的"开端-冲突-余韵"三层
- 视觉心理（Suler）：punctum / studium 二元、视觉吸引点的心理机制
- 伦理（Sontag）：拍人前的尊重、可疑场景的克制
- 看见的训练（Peterson）：换视角、走近、等候、留意意外

## 4. 各书贡献预算

| 来源 | 类别主投放 | 计划条数 | 现状 |
|---|---|---|---|
| Michael Freeman《Photographer's Eye》 | composition | 25 | 待写 |
| 宁思潇潇《新摄影笔记》 | composition + subject | 25 | 待写 |
| David duChemin《Photographically Speaking》 | subject + theme | 15 | 待写 |
| 顾荣军《非常摄影手记》 | composition | 15 | 待写 |
| Suler《Photographic Psychology》 | theme | 15 | 待写 |
| 顾锡《看佳作学构图》 | composition + 反例 | 12 | 待写 |
| Scott Kelby《Crush the Composition》 | subject + background | 12 | 待写 |
| David duChemin《Within the Frame》 | theme | 12 | 待写 |
| 巴内克《人像摄影经典教程》 | subject (人像专项) | 12 | 待写 |
| Bryan Peterson《Understanding Exposure》 | light + depth | 12 | 待写 |
| Bryan Peterson《Learning to See Creatively》 | theme | 10 | 待写 |
| B 站汤辉《从零学用光》 | light | 10 | 待写 |
| Roland Barthes《Camera Lucida》 | theme（理论） | 8 | 待写 |
| Cartier-Bresson《Decisive Moment》 | theme（纪实） | 6 | 待写 |
| Susan Sontag《On Photography》 | theme（伦理） | 5 | 待写 |
| B 站《走近摄影》 | composition + light | 8 | 待写 |
| B 站《零基础构图》 | composition | 8 | 待写 |
| **合计** | | **≈ 210** | |

## 5. 撰写工作流（合规底线）

1. **私人学习副本** — 只用我有合法访问权的纸质书 + 公开课视频 + Suler 等开源在线书
2. **人工读出大纲** — 章节标题 + 关键词 + 每章 2-3 个核心点（不抄原文）
3. **AI 按大纲产出 JSON 初稿** — 每条强制自撰中文，AI 不得直接翻译/复述原文
4. **人工审校** — 检查 evidence/watch_out/counter_example 是否流畅、有据可依
5. **ngram 比对** — 跟所有可下载的原文（Suler 在线版等）做 5-gram 重合检测，> 30% 立刻人工改写
6. **入库** — `[backend/app/knowledge/composition/<id>.json](../backend/app/knowledge/composition/)`

红线：

- ❌ KB 任何字段不得包含原书超过 30 字的连续短语
- ❌ `citations` 只放"书名 + 章节号"，不放页码段落
- ✅ 规则名（"三分法"、"对角构图"）这类**事实词汇**沿用——这是公共领域的术语
- ✅ 解释、触发条件、修复建议都是原创中文

## 6. 三波分批推进

**第一波（80 条 · MVP 必修）**：覆盖 portrait / closeup / full_body 三大主流模式 + 构图必修 14 条 + 主体核心 10 条 + 背景核心 8 条 + 通用主题 8 条 + 通用光线 / 景深 各 5 条

**第二波（70 条 · 模式补全）**：覆盖 documentary / scenery / light_shadow + 主题表达深度 18 条 + 人像专项 12 条 + 光线 10 条 + 景深 10 条 + 反例 12 条

**第三波（50 条 · 理论补强）**：Barthes 8 条 + Suler 15 条 + Cartier-Bresson 6 条 + Sontag 5 条 + 各路反例 16 条

## 7. 注入机制

`backend/app/services/knowledge.py` 的 `summarize_composition_kb(scene_mode, person_count, axes_focus=None)`：

1. 用 `scene_modes` 过滤掉无关条目
2. 按 `axes` 重叠度 + `priority` 排序
3. 取 top-30 输出紧凑中文清单：每行 `[id] name_zh — summary（when_to_use 简写）`
4. 至少夹带 2 条 `counter_example`

`backend/app/services/prompts.py` rule 12 收紧：每条 `criteria_notes` 必须以 `[rule_id]` 开头，**只能从字典取**；找不到合适规则才允许 `[freeform]`，禁止虚构 id。

## 8. 后续 RAG 平滑

`embedding` 字段已预留。后期接 RAG：

- 用 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 离线为每条算向量
- `summarize_composition_kb` 改用 cosine 召回；prompt 段保持不变
- KB 数量上 1000+ 时再做这一步
