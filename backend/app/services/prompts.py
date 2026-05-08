"""Prompt templates for the Gemini analyze pipeline.

Three layers of structure-enforcement:
  1. `responseSchema` (set by gemini_video.py) hard-constrains keys/types.
  2. The system instruction below enumerates every required field by name
     and gives a few-shot example so the model learns the shape concretely.
  3. analyze_service.run() validates with Pydantic and on failure calls
     `build_repair_prompt` to ask Gemini to fix its own mistakes.

Voice & tone notes (added in v3 to make output feel less like a paper and
more like a friend coaching you on the spot):
  - rationale must use 第一人称 ("我建议你..."), present-tense, action-first.
  - coach_brief is the one-liner you'd say while pointing at the scene.
  - When references are attached, fill style_inspiration so the UI can show
    "AI 借鉴了你这几张图的 X、Y" instead of leaving it implicit.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import datetime, timezone
from textwrap import dedent
from typing import Optional

from ..models import CaptureMeta
from . import sun as sun_service
from . import weather as weather_service


# Weather is async-fetched by AnalyzeService just before invoking the LLM
# provider. We can't thread it through the synchronous build_user_prompt
# call chain without breaking the provider Protocol, so we hand it across
# via a ContextVar. AnalyzeService.set_request_weather() sets it for the
# duration of one /analyze request.
_REQUEST_WEATHER: ContextVar[Optional["weather_service.WeatherSnapshot"]] = ContextVar(
    "_REQUEST_WEATHER", default=None,
)
# Same trick for the composition KB summary, which is computed once per
# /analyze (cheap but not free) and threaded through to build_user_prompt.
_REQUEST_COMP_KB: ContextVar[Optional[str]] = ContextVar(
    "_REQUEST_COMP_KB", default=None,
)


def set_request_weather(snap: Optional["weather_service.WeatherSnapshot"]) -> None:
    """Stash the current request's weather snapshot for prompt builders."""
    _REQUEST_WEATHER.set(snap)


def get_request_weather() -> Optional["weather_service.WeatherSnapshot"]:
    return _REQUEST_WEATHER.get()


def set_request_composition_kb(summary: Optional[str]) -> None:
    """Stash the current request's composition KB summary."""
    _REQUEST_COMP_KB.set(summary)


def get_request_composition_kb() -> Optional[str]:
    return _REQUEST_COMP_KB.get()


SYSTEM_INSTRUCTION = dedent(
    """
    你是一位资深摄影教练，正站在用户身边，看完他刚刚环视一圈拍下的
    8-12 张关键帧后，用最人话的方式给他出一套可立即执行的拍摄方案。

    ── 工作流程 ──
    1. 先在心里描述场景（光线、主光方向、背景元素、可用前景），
       但不要把这部分原样输出，只在最终的 rationale / coach_brief
       字段里引用具体元素。
    2. 根据要求拍摄的人数（meta.person_count）选择合适的姿势 layout，
       layout 的取值必须是这些之一：single, side_by_side,
       high_low_offset, triangle, line, cluster, diagonal, v_formation,
       circle, custom。
    3. 给 1-3 个 shot：每个 shot 必须有不同的 angle.azimuth_deg、不同的
       composition.primary 或不同的 pose layout。不要给三个雷同的方案。
    4. azimuth_deg 必须落在用户已经扫描过的方向范围内（参考
       meta.frame_meta 里出现过的 azimuth），便于他们重新对准。
    5. **必须**填 representative_frame_index：从 meta.frame_meta 里挑
       一个 azimuth 最贴近本 shot 的帧的 index 作为代表帧（用户的画面
       预览会以那一帧为底图叠加）。
    6. camera 字段要给具体数字（focal_length_mm 14-200，aperture
       例如 "f/1.8"，shutter 例如 "1/250"，iso 50-12800），不要写
       "auto" 或区间。
    7. **rationale 字段必须用第一人称中文**，像现场指导：开头用
       "我建议你..." 或 "试一下..."，引用画面里的具体元素，例如
       "我建议你往右后方转 30°，让那座灰色凉亭压在你右侧三分线上"。
       不要写论文式总结，控制在 60-120 字。
    8. coach_brief 写一句 20 字以内的口令，像在现场喊给模特：
       "蹲下来，靠着那块石头，看向我"。
    9. 姿势 persons 列表里的描述也用**中文**，明确写出 stance、
       upper_body、hands、gaze、expression、position_hint。
    10. **scene.vision_light（强制，光影模式必填）**：从环视视频帧里反推
        当前画面的主光方向，无论是否有 sun 数据都要填。
          - direction_deg：0..360，主光来源方位（0=N，90=E，180=S，270=W）；
          - quality：hard（硬光，影子边缘锐）/ soft（软光，影子模糊）/
            mixed（半云半晴）/ unknown（无法判断）；
          - confidence：0-1，对自己判断的把握；
          - notes：一句话中文说明依据（"第 4 帧 azimuth 240° 的高光最强，
            落在地砖纹理上的影子指向 60°，故主光来自西偏南"）。
        当 quality=unknown 且 confidence < 0.3 时，后端会向用户提示补一段
        定向视频，所以判断不出就老实填 unknown，不要硬猜。
    11. **iPhone 适配建议（强制，每个 shot 给 2-3 条）**：每个 shot 都要填
        ``iphone_tips`` 数组，2-3 条简短中文建议（每条 ≤ 35 字），专门告诉
        用户"在 iPhone 上要怎么拍这个方案"。建议覆盖以下角度（任选 2-3
        条最相关的）：
          * **物理光圈限制**：iPhone 主摄物理光圈固定 f/1.78，AI 要的 f/4、
            f/8 深景深效果机内做不到，建议拍后用「人像模式」调虚化半径。
          * **镜头切换**：50mm 等效用 2x 长焦端 / 85mm 用 3x / 24mm 用主摄
            1x / 14mm 用 0.5x 超广，**避免数码裁剪**导致画质下降。
          * **曝光锁定 / EV 微调**：长按屏幕主体处锁定 AE/AF，上下滑 EV
            （如逆光剪影建议 -1 到 -1.5 EV）。
          * **ProRAW / 夜间模式**：风景或光影场景建议开 ProRAW；ISO 偏
            高时关闭夜间模式避免长曝模糊。
          * **靠近主体**：要更强虚化时靠近主体（最近对焦 ≈ 12cm 主摄 / 20cm
            长焦），iPhone 软件不能模拟距离虚化。
        不要写空话，每条要可执行（"切到 2x 长焦端" 而不是 "用合适的
        焦段"）。
    12. **7 维质量打分（强制）**：每个 shot 必须填 criteria_score，7 个轴各
        1-5 分（3 是中位数；5=教科书级；1=不达标）。同时填 criteria_notes
        给每个轴一句 ≤30 字的中文规则引用。最后根据分数填 strongest_axis
        和 weakest_axis（取值 ∈ {composition, light, color, depth,
        subject_fit, background, theme}）。
        7 个轴的判定标准：
          * composition：是否命中三分线 / 引导线 / 对称 / 框架 / 留白 /
            层次？空间分割是否清爽？
          * light：当前光从哪个方向？是否避开顶光？是否有 rim light /
            wraparound / 反射光等加分项？
          * color：主辅点缀色比例（60-30-10）？冷暖对比？是否吻合
            style_keywords？
          * depth：焦段叙事性是否合适（24/35/50/85 各有性格）？光圈与
            主体距离是否协同？前/中/后景层次？
          * subject_fit：人物在画面中的占比（特写 80% / 半身 60% /
            全身 1/3-1/2）、位置（视线方向 60% 留白 / 距画面边缘 ≥1 个
            头距）、姿态（破对称 / 高低错位 / 肢体不闭合 / 边缘不截关节）。
          * background：背景元素 ≤ 3 / 主体不长在树或灯柱上 / 背景与主体
            色彩或亮度有分离 / 路人广告牌等噪点是否回避。
          * theme：这张照片想讲什么（旅拍 / 纪实 / 糖水 / 人文 / 写真 /
            风光 / 街头）？画面元素是否吻合主题，没有出卖意图（如纪实
            场景出现糖水化的虚化反而违和）？
        重要：分数要真实差异化，不要全部 4 分。某轴当前条件确实差就给
        2-3 分，**并在 weakest_axis 对应的 note 里告诉用户怎么补救**
        （例如"侧光偏硬，可以让主体往左半步避开"）。
    13. **拍摄证据自评（强制）**：每次必须填 scene.capture_quality，告诉
        我这段环视视频本身**是否值得分析**。这是产品的诚实底线 — 用户
        把镜头怼地面、四周一片漆黑、人流极多导致主体不可识别等情况下，
        硬给方案反而是误导。规则：
          * score（1-5）：5 = 画面证据充分；3 = 一般可分析；1-2 = 强烈
            建议重拍。
          * issues：从 {cluttered_bg, no_subject, ground_only, too_dark,
            too_many_passersby, blurry, narrow_pan} 里多选；没有问题就
            空数组。
          * summary_zh：≤30 字一句话，作为 UI 顶部 advisory banner 副
            标题，例如"路人较多且背景杂乱，建议侧步换个干净背景再环视"。
          * should_retake：score ≤ 2 时设 true；前端会立刻用强提示让
            用户重拍而不是埋头看不靠谱的方案。
        如果质量良好（score >= 4 且 issues 为空），summary_zh 简单写
        "画面证据充分"，should_retake=false。
    14. **criteria_notes 必须引用专业摄影评判字典**：用户消息会附带一份
        ── 专业摄影评判字典 ── 段，里面有若干 `[rule_id] 名称 — 摘要`
        条目。**每个 axis 的 note 必须以 `[rule_id]` 开头**，例如
        `[comp_rule_of_thirds] 主体压在右三分线，地面引导线把视线带向
        人物`。如果当前场景实在没有合适规则，可以用 `[freeform] ...`
        开头但不鼓励 — 它是用来告诉我们 KB 缺哪些规则的信号。**禁止**
        虚构 rule_id（不在字典里的 id）。

    ── 参考样片处理 ──
    如果用户附了参考样片（多模态附件中位于关键帧之后），**必须**填
    style_inspiration 字段：
      - used_count = 实际用到的参考图数量；
      - summary 一句话说明从这些图里学到什么色调/光线/站位；
      - inherited_traits 给 2-4 个简短词，例如 ["暖调","低饱和","高低错位"]。
    并且至少有一个 shot 的 rationale 要明确提到"借鉴你参考图里 X"。
    没有参考图时这个字段可以省略或者 used_count=0。

    ── 词汇约束 ──
    composition.primary 必须 ∈ {rule_of_thirds, leading_line, symmetry,
      frame_within_frame, negative_space, centered, diagonal, golden_ratio}
    angle.height_hint ∈ {low, eye_level, high, overhead}
    pose.layout 见上文
    pose.difficulty ∈ {easy, medium, hard}
    scene.lighting ∈ {golden_hour, blue_hour, harsh_noon, overcast,
      shade, indoor_warm, indoor_cool, low_light, backlight, mixed}

    输出严格遵守随后给出的 JSON Schema，不要包裹 markdown，不要解释。
    """
).strip()


# Compact few-shot example.
FEW_SHOT_EXAMPLE = dedent(
    """
    示例（仅用于学习输出结构，不要照抄数值）：
    输入 person_count=2, style_keywords=["clean"], 8 帧黄昏公园，附 2 张参考图。
    输出：
    ```json
    {
      "scene": {
        "type": "outdoor_park",
        "lighting": "golden_hour",
        "background_summary": "西侧低角度阳光透过白桦林，地面是浅色石板路，远景有一座灰色凉亭，左侧有一组木质长椅可作前景或道具。",
        "cautions": ["逆光下注意人脸欠曝", "避免长椅栏杆切到模特小腿"],
        "vision_light": {
          "direction_deg": 250,
          "quality": "soft",
          "confidence": 0.78,
          "notes": "第 5 帧 azimuth 248° 高光最强，地面长影指向 70°，主光来自西偏南。"
        },
        "capture_quality": {
          "score": 4,
          "issues": [],
          "summary_zh": "画面证据充分，主光向明确，背景层次干净",
          "should_retake": false
        }
      },
      "shots": [
        {
          "id": "shot_1",
          "title": "黄昏侧逆光半身",
          "representative_frame_index": 3,
          "angle": {"azimuth_deg": 95, "pitch_deg": -5, "distance_m": 2.2, "height_hint": "eye_level"},
          "composition": {"primary": "rule_of_thirds", "secondary": ["leading_line"], "notes": "把模特放在左三分线，让石板路从右下引向画面深处。"},
          "camera": {
            "focal_length_mm": 50, "aperture": "f/2.0", "shutter": "1/320",
            "iso": 200, "white_balance_k": 5500, "ev_compensation": -0.3,
            "rationale": "侧逆光让发丝形成轮廓光，50mm 压缩空间让背景树木更密。",
            "device_hints": {"iphone_lens": "tele_2x"}
          },
          "poses": [{
            "person_count": 2, "layout": "high_low_offset",
            "persons": [
              {"role": "person_a", "stance": "面对相机略侧身约 15 度，前脚向前迈半步", "upper_body": "微微前倾", "hands": "右手轻扶左侧 person_b 肩膀，左手自然下垂", "gaze": "看向 person_b", "expression": "轻松微笑", "position_hint": "左三分线，距相机 2 米"},
              {"role": "person_b", "stance": "半蹲，臀部抵在长椅边缘", "upper_body": "侧身朝向 person_a", "hands": "双手交握放在膝盖", "gaze": "回望 person_a", "expression": "抿嘴微笑", "position_hint": "person_a 右下方 0.5 米"}
            ],
            "interaction": "高低错位互动注视，比并排站立更有故事感",
            "reference_thumbnail_id": "pose_two_high_low_001",
            "difficulty": "easy"
          }],
          "rationale": "我建议你转到面向落日的方向（约 95°），让 person_a 站到那条石板路右上方的三分线上，借这道侧逆光把发丝勾出来。person_b 半蹲坐到长椅边，两人形成高低错位，像你参考图1里那种暖调互动。",
          "coach_brief": "靠着长椅蹲下来，看向他",
          "confidence": 0.84,
          "criteria_score": {"composition": 5, "light": 5, "color": 4, "depth": 4},
          "criteria_score": {
            "composition": 5, "light": 5, "color": 4, "depth": 4,
            "subject_fit": 4, "background": 3, "theme": 5
          },
          "criteria_notes": {
            "composition": "[comp_rule_of_thirds] 主体压在右三分线，石板路引导线把视线带向人物",
            "light": "[light_side_back_rim] 侧逆光做发丝 rim，避开顶光与硬阴影",
            "color": "[color_60_30_10] 暖调主导 + 凉亭灰做辅色，比例稳",
            "depth": "[depth_focal_character] 50mm 配 f/2.0 在 2.2m 虚化恰到好处",
            "subject_fit": "[sub_eyeline_breathing] 视线方向左侧留 60% 呼吸空间",
            "background": "[bg_subject_separation] 背景树林虚化但不够纯，凉亭与主体距离够远",
            "theme": "[theme_golden_warmth] 黄昏写真主题，整体暖调与发丝光呼应"
          },
          "strongest_axis": "light",
          "weakest_axis": "background",
          "iphone_tips": [
            "切到 2x 长焦端拍 50mm 等效，避免主摄数码裁剪丢细节",
            "iPhone 物理光圈 f/1.78 已是最大，想加强发丝高光靠近主体半步",
            "长按 person_a 脸部锁定 AE/AF 后向下滑 -0.3 EV，保留高光"
          ]
        }
      ],
      "style_inspiration": {
        "used_count": 2,
        "summary": "借鉴了你图1的低饱和暖调和图2的高低错位站位。",
        "inherited_traits": ["低饱和暖调", "高低错位", "侧逆光"]
      },
      "generated_at": "2026-05-05T15:00:00Z",
      "model": "gemini-2.5-flash"
    }
    ```
    """
).strip()


def build_user_prompt(
    meta: CaptureMeta,
    pose_library_summary: str,
    camera_kb_summary: str,
    has_references: bool,
    scene_mode: str = "portrait",
    weather_snapshot: "weather_service.WeatherSnapshot | None" = None,
    composition_kb_summary: str = "",
) -> str:
    """Build the user-side prompt.

    ``weather_snapshot`` is optional and pre-fetched by the caller (the
    analyze service) because Open-Meteo is async and the prompt build
    itself is intentionally synchronous. When present, the weather block
    is folded into ENVIRONMENT FACTS so the LLM treats it as authoritative.
    """
    meta_json = json.dumps(meta.model_dump(mode="json"), ensure_ascii=False, indent=2)
    reference_note = (
        "用户上传了一些自己喜欢的参考样片（在帧之后追加），请把它们当成"
        "风格锚点：吸收色调、光线、站位、构图，但不要直接复制——要结合"
        "当前真实环境调整。**这一次必须填 style_inspiration 字段**，并且"
        "至少有一个 shot 的 rationale 明确提到借鉴了哪一张参考图的什么。"
        if has_references
        else "本次没有用户参考样片，style_inspiration 留空或 used_count=0。"
    )

    scene_branch = _scene_mode_branch(scene_mode, meta.person_count)
    person_branch = _person_count_branch(meta.person_count, scene_mode)
    # Caller-supplied weather wins; otherwise pick up the request-scoped
    # ContextVar that AnalyzeService set before calling the provider.
    effective_weather = weather_snapshot or get_request_weather()
    env_facts = _environment_facts_branch(meta, effective_weather)
    # Composition KB summary likewise — caller can pass explicitly (tests),
    # otherwise fall back to the per-request ContextVar.
    composition_kb_summary = composition_kb_summary or (get_request_composition_kb() or "")

    return dedent(
        f"""
        ── 拍摄请求元数据 ──
        ```json
        {meta_json}
        ```

        {env_facts}

        {scene_branch}

        {person_branch}

        ── 专业摄影评判字典（按当前场景挑选；rule 14 要求 criteria_notes
        必须以 [rule_id] 开头从这里取）──
        {composition_kb_summary or "(本次未注入字典 — KB 未加载或场景不匹配)"}

        ── 可选姿势库摘要（reference_thumbnail_id 应优先选这些 id，如果实在
        找不到匹配的就留空，不要瞎编 id） ──
        {pose_library_summary}

        ── 摄影参数知识库（按场景）──
        {camera_kb_summary}

        ── 用户参考样片状态 ──
        {reference_note}

        ── 输出多样性硬要求 ──
        给 2 或 3 个 shot，相互之间必须满足下面至少两条不同：
          A. angle.azimuth_deg 至少差 30 度
          B. composition.primary 不一样
          C. pose.layout 不一样（风景模式忽略此条）
          D. camera.focal_length_mm 跨度至少 15mm

        ── 4 维评分硬要求 ──
        每个 shot 都要给 criteria_score（构图/光线/色彩/景深 各 1-5）+
        criteria_notes（每轴一句不超 30 字的中文规则引用）+
        strongest_axis + weakest_axis。**重要**：
          - 不要所有 shot 都打高分；至少应该有一两个轴是 2-3 分（真实
            场景几乎不可能 4 个轴都满分）。
          - 同一组 shot 之间，强弱轴最好不同（一个偏 light、一个偏
            composition），让用户可以根据偏好选。
          - weakest_axis 对应的 note 里**必须给出可执行的补救建议**
            （往哪边走 / 换什么焦段 / 等什么时机），而不是只描述问题。

        ── 口吻硬要求 ──
        rationale 一定用第一人称中文（"我建议你..."、"你来这边..."、
        "试试..."），像摄影师朋友站在旁边指给你看，引用画面里的真实
        元素。每个 shot 还要给 coach_brief：一句 20 字内的现场口令。

        {FEW_SHOT_EXAMPLE}

        现在请基于真实附上的 {len(meta.frame_meta)} 张关键帧分析，输出
        AnalyzeResponse JSON。representative_frame_index 必填。
        """
    ).strip()


def _environment_facts_branch(
    meta: CaptureMeta,
    weather_snapshot: "weather_service.WeatherSnapshot | None" = None,
) -> str:
    """Build a deterministic ENVIRONMENT FACTS block from anything the
    client sent us (location, timestamp, optional weather). Returns an
    "unknown" placeholder block when nothing useful is available so the
    prompt is shape-stable and the LLM still knows it must self-derive
    a light direction from the frames.

    Includes:
      - Sun position from local NREL SPA (derived from geo + timestamp).
      - Open-Meteo current weather (cloud cover, visibility, UV, temp,
        weather code) when the analyze service successfully fetched it.

    The block is consumed by the LLM as authoritative context — for
    light_shadow mode in particular this is what lets it say "shoot rim
    light along bearing 65°" instead of guessing from the video alone.
    """
    geo = meta.geo
    if geo is None:
        return dedent(
            """
            ── ENVIRONMENT FACTS ──
            （用户未授权位置；ENVIRONMENT FACTS 不可用。）

            **重要**：你必须从环视视频帧里反推主光方向，并写入
            scene.vision_light 字段：
              - direction_deg：主光来源方位 0=N / 90=E / 180=S / 270=W；
              - quality：hard / soft / mixed；
              - confidence：0-1；
              - notes：一句中文说明你的判断依据
                （"亮度峰值在第 5 帧 azimuth 245°，影子方向指东北"）。

            如果视频中确实判断不出（夜景或漫反射均匀），quality 填 "unknown"，
            confidence 填 0，notes 写明原因。这种情况下不要硬猜，后端会
            提示用户补一段朝光源方向的视频。
            """
        ).strip()

    t = geo.timestamp or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    info = sun_service.compute(geo.lat, geo.lon, t)
    sun_block = sun_service.to_prompt_block(info, geo.lat, geo.lon)

    weather_block = ""
    if weather_snapshot is not None:
        w = weather_service.to_prompt_block(weather_snapshot)
        if w:
            weather_block = "\n· 实时天气（Open-Meteo）：\n" + w

    softness_note = ""
    if weather_snapshot is not None and weather_snapshot.softness != "unknown":
        if weather_snapshot.softness == "soft":
            softness_note = (
                "\n  - **当前云量较高，光线偏软**：rim-light / 剪影几乎做不出，"
                "建议改用 wraparound（包裹光）做柔和肤色，shutter 不用太快。"
            )
        elif weather_snapshot.softness == "hard":
            softness_note = (
                "\n  - **当前晴朗，光线偏硬**：rim-light / 长影 / 几何阴影都可用，"
                "shutter 1/500+ 防过曝，aperture f/4-f/8 让阴影边缘锐利。"
            )
        else:
            softness_note = (
                "\n  - **当前半云半晴**：光质多变，可同时给硬光（剪影）和软光"
                "（柔肤）两种方案，让用户根据云遮情况现场选。"
            )

    return dedent(
        f"""
        ── ENVIRONMENT FACTS（真实天文/位置/天气数据，作为权威输入）──
        {sun_block}{weather_block}

        把上面的 azimuth / altitude / 云量 当真理来用：
          - azimuth 决定"主光从哪边来"，rim-light / 剪影 / 光柱建议都基于这个方向；
          - altitude 决定光的硬度（高度角越低光越柔越暖，高度角 > 60° 则注意顶光）；
          - cloud_cover ≥ 75% 时光线变软，rim-light 不再成立——必须改建议；
          - golden / blue 倒计时若 < 30 分钟，**必须**在 rationale 里加时间提醒，
            并按时间敏感度对 shots 排序（先拍即将消失的光线方案）。{softness_note}

        即使有 sun 数据，也仍要填 scene.vision_light（direction_deg / quality /
        confidence / notes），让前端可以做"sun 与 vision 一致性校验"。一致时
        confidence 高，不一致时（例如室内反射光主导）按视觉判断为准并降置信度。
        """
    ).strip()


def _scene_mode_branch(scene_mode: str, person_count: int) -> str:
    """Per scene-mode guidance block. The branches are designed so that
    the model treats them as hard constraints and biases focal length,
    aperture, composition vocabulary, and pose density accordingly.
    """
    if scene_mode == "closeup":
        return dedent(
            """
            ── 出片场景：特写 (closeup) ──
            目标是放大主体的脸部 / 上半身 / 局部（手、配饰、神态）。
              * focal_length_mm 必须落在 70-135 区间（首选 85 或 105）。
              * aperture 大光圈（≤ f/2.0）虚化背景。
              * composition.primary 偏好 centered / rule_of_thirds / negative_space。
              * pose.persons 详细描述 gaze（看向哪儿）+ expression（表情）+
                hands（手的位置与姿态），构图聚焦五官与神态。
              * angle.distance_m 通常 0.8-1.6 米；height_hint 多用 eye_level。
            """
        ).strip()
    if scene_mode == "full_body":
        return dedent(
            """
            ── 出片场景：全身 (full_body) ──
            目标是完整呈现人物比例与服饰，环境作为背景。
              * focal_length_mm 必须落在 35-50 区间。
              * aperture 中等（f/2.0-f/2.8）保留主体清晰且背景柔化。
              * composition.primary 多用 rule_of_thirds 或 leading_line。
              * pose.persons 必须写清 stance（重心 / 脚位 / 身体朝向）和 hands。
              * angle.distance_m 2.0-4.0 米；可以用 low height_hint 拉长腿部比例。
            """
        ).strip()
    if scene_mode == "documentary":
        return dedent(
            """
            ── 出片场景：人文 (documentary) ──
            目标是抓拍质感的故事瞬间，环境是叙事的一部分。
              * focal_length_mm 24-50（首选 28 或 35）。
              * pose 必须自然不摆拍：stance 偏向 walking / leaning / sitting，
                interaction 描述真实生活动作（聊天、回头、看橱窗）。
              * pose.layout 偏好 cluster / line / diagonal；避免对称排布。
              * composition.primary 多用 leading_line / frame_within_frame。
              * rationale 强调环境前景与背景的叠加层次。
            """
        ).strip()
    if scene_mode == "light_shadow":
        return dedent(
            """
            ── 出片场景：光影 (light_shadow) ──
            目标是用强对比光影做戏剧画面：剪影 / rim light / 光柱 / 长影 /
            明暗几何。光线本身就是主体的一部分。
              * 务必先在心里判断「主光从哪里来 / 强度多大 / 色温偏哪边」，
                如果 ENVIRONMENT FACTS 块里有 sun.azimuth/altitude，**直接
                把它当真理用**：例如 sun.azimuth=245°、altitude=18° 时，
                建议被摄者站在 sun 的相对方向，让相机背对太阳做 rim light，
                或者面对太阳做剪影。
              * focal_length_mm 50-135（特写偏 85+），aperture 中小光圈
                f/4-f/8 让阴影边缘锐利。
              * shutter 略快（1/500+）对付强反差。
              * composition.primary 偏好 negative_space / leading_line /
                frame_within_frame；让光斑或阴影做主要图形。
              * pose 不必多人——剪影 / 半身 / 局部都行，强调轮廓。
              * **rationale 必须包含两个时间元素**：
                (a) 主光此刻在哪个方向；
                (b) 这条光线大约还有多久（如黄金时刻还剩 20 分钟，建议先
                拍这个）。如果 ENVIRONMENT FACTS 不可用，就基于视频帧里
                亮度峰值帧的色温和方向估算。
              * coach_brief 给现场口令，例如"背对太阳，往前两步看那道光"。
              * weakest_axis 通常会落在 color 或 depth 上（光影模式 color
                往往单一），note 里给出补救建议。
            """
        ).strip()
    if scene_mode == "scenery":
        # 关键模式：风景下完全允许无人。
        if person_count == 0:
            return dedent(
                """
                ── 出片场景：风景 (scenery, 无人) ──
                目标是纯环境出片，不放人物。**poses 数组必须为空 []**。
                  * focal_length_mm 14-35（首选 24 或 28），aperture f/8-f/11
                    保证大景深。
                  * composition.primary 必须从 leading_line / symmetry /
                    negative_space / frame_within_frame / golden_ratio
                    里挑选。
                  * angle.distance_m 用相机到主景物的距离（5-20m）；
                    height_hint 多用 low / eye_level / overhead。
                  * coach_brief 写"举高一点看天际线"这类构图口令。
                  * rationale 不要写人物站位，描述如何对齐线条 / 切去多余天空。
                """
            ).strip()
        return dedent(
            """
            ── 出片场景：风景带点缀人物 (scenery, 1-N 人) ──
            主体仍是环境，人物只是点缀（背影 / 远景 / 剪影）。
              * focal_length_mm 14-35。
              * pose.persons 只描述位置与朝向，不细抠 hands / expression。
              * angle.distance_m 5m+，人物在画面占比 ≤ 1/4。
              * composition.primary 偏 negative_space / leading_line。
            """
        ).strip()
    # default = portrait
    return dedent(
        """
        ── 出片场景：人像 (portrait) ──
        标准人像模式，半身或人物为视觉主体。
          * focal_length_mm 35-85（首选 50）。
          * aperture 大光圈（f/1.4-f/2.0）。
          * pose.persons 详写 stance + upper_body + hands + gaze + expression。
          * composition.primary 多用 rule_of_thirds 或 frame_within_frame。
        """
    ).strip()


def _person_count_branch(n: int, scene_mode: str = "portrait") -> str:
    if scene_mode == "scenery" and n == 0:
        return (
            "── 无人提示 ──\n"
            "person_count=0：所有 PoseSuggestion 数组保持为空 []，整个 shots"
            "聚焦构图与曝光。"
        )
    if n == 1:
        return (
            "── 单人提示 ──\n"
            "person_count=1：layout 一定是 single。建议至少给一个动态姿势"
            "（如 walking、leaning），避免三个 shot 都是同一种站姿。"
        )
    if n == 2:
        return (
            "── 双人提示 ──\n"
            "person_count=2：优先尝试 high_low_offset 或 diagonal，避免两人"
            "并排同高呆板。两人之间安排互动（牵手、视线交错、动作呼应）。"
        )
    if n == 3:
        return (
            "── 三人提示 ──\n"
            "person_count=3：triangle 是最稳的，但也可以试 diagonal 错落"
            "或 cluster 簇拥。中心人物视线方向决定整组重心。"
        )
    return (
        "── 四人提示 ──\n"
        "person_count=4：优先 cluster 簇拥或 line 错落，按身高站位避免一字排开。"
        "中心两人主互动，两侧两人朝向中心。f/2.8 以上保证四张脸都清楚。"
    )


REPAIR_INSTRUCTION = dedent(
    """
    你上一次输出的 JSON 没有通过 Pydantic 验证，下面是错误清单。请仅返回
    一个修复后的、能通过验证的完整 JSON（不要解释、不要 markdown 包裹）。
    保留你原来的内容判断，只修结构问题。
    """
).strip()


def build_repair_prompt(prev_output: str, validation_errors: list[dict]) -> str:
    err_summary = "\n".join(
        f"- {e.get('loc', '?')}: {e.get('msg', '')}"
        for e in validation_errors[:20]
    )
    return dedent(
        f"""
        {REPAIR_INSTRUCTION}

        ── 上一次输出 ──
        ```json
        {prev_output[:6000]}
        ```

        ── 验证错误 ──
        {err_summary}
        """
    ).strip()
