"""Style × Environment feasibility scoring.

Given a user's location-derived sun + weather snapshot, compute a 0..1
feasibility score for each of the 5 wizard styles plus a Chinese reason
sentence the UI can show. The thresholds and weights here are the
canonical implementation of the design doc:

    docs/STYLE_FEASIBILITY.md

If you're touching the constants below, update the doc too — the doc is
both the user-facing rationale and the App-Store-style "we're being
honest with the user about what's achievable" record.

Why this lives in the backend (not the frontend):
  - Same logic feeds (a) the picker UI badge ("⚠ 当前环境不太适合") and
    (b) the LLM prompt suggestion ("user picked X, env is Y, please
    push parameters toward Z"). One source of truth, two consumers.
  - Sun + weather data already lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import sun as sun_service
from . import weather as weather_service


# All 5 styles match `web/img/style/manifest.json`. Keep the IDs aligned.
STYLE_IDS = (
    "cinematic_moody",
    "clean_bright",
    "film_warm",
    "street_candid",
    "editorial_fashion",
)

STYLE_LABELS_ZH = {
    "cinematic_moody":   "氛围感",
    "clean_bright":      "清爽日系",
    "film_warm":         "温柔暖光",
    "street_candid":     "自然随手",
    "editorial_fashion": "大片感",
}


# ---------------------------------------------------------------------------
# Recommended camera knobs per style (used in the prompt block — LLM should
# bias toward these unless reality forces otherwise).
# ---------------------------------------------------------------------------
STYLE_PRESETS: dict[str, dict] = {
    "cinematic_moody": {
        "white_balance_k_range": (3500, 4500),
        "focal_length_mm_range": (35, 85),
        "ev_range":             (-0.7, -0.3),
        "aperture_range":       ("f/1.4", "f/2.8"),
        "lighting_prefer":      ["golden_hour", "blue_hour", "low_light", "backlight"],
        "composition_prefer":   ["leading_line", "negative_space", "diagonal"],
        "height_hint_prefer":   ["low", "eye_level"],
    },
    "clean_bright": {
        "white_balance_k_range": (5500, 6500),
        "focal_length_mm_range": (24, 50),
        "ev_range":             (0.0, 0.7),
        "aperture_range":       ("f/2.8", "f/5.6"),
        "lighting_prefer":      ["overcast", "shade", "golden_hour"],
        "composition_prefer":   ["rule_of_thirds", "centered", "symmetry"],
        "height_hint_prefer":   ["eye_level", "high"],
    },
    "film_warm": {
        "white_balance_k_range": (3200, 4500),
        "focal_length_mm_range": (35, 85),
        "ev_range":             (-0.3, 0.3),
        "aperture_range":       ("f/1.8", "f/4"),
        "lighting_prefer":      ["golden_hour"],
        "composition_prefer":   ["rule_of_thirds", "golden_ratio"],
        "height_hint_prefer":   ["eye_level"],
    },
    "street_candid": {
        "white_balance_k_range": (5000, 5800),
        "focal_length_mm_range": (35, 50),
        "ev_range":             (-0.3, 0.3),
        "aperture_range":       ("f/2.8", "f/5.6"),
        "lighting_prefer":      ["mixed", "overcast", "golden_hour", "shade"],
        "composition_prefer":   ["rule_of_thirds", "leading_line"],
        "height_hint_prefer":   ["eye_level"],
    },
    "editorial_fashion": {
        "white_balance_k_range": (4500, 7500),
        "focal_length_mm_range": (50, 135),
        "ev_range":             (-0.3, 0.0),
        "aperture_range":       ("f/2.8", "f/8"),
        "lighting_prefer":      ["golden_hour", "harsh_noon", "backlight"],
        "composition_prefer":   ["centered", "negative_space", "symmetry"],
        "height_hint_prefer":   ["low", "high"],
    },
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class StyleScore:
    style_id: str
    label_zh: str
    score: float                 # 0..1
    tier: str                    # "recommended" | "marginal" | "discouraged"
    reason_zh: str               # one-line user-facing explanation


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def score_styles(
    sun: Optional[sun_service.SunInfo],
    weather: Optional[weather_service.WeatherSnapshot],
) -> list[StyleScore]:
    """Score all 5 styles for the current environment.

    When `sun` is None (no GPS) we return uniform 'unknown' scores so the
    UI doesn't paint warnings without evidence.
    """
    if sun is None:
        return [
            StyleScore(
                style_id=sid,
                label_zh=STYLE_LABELS_ZH[sid],
                score=0.65,
                tier="unknown",
                reason_zh="未授权位置，无法判断环境是否适合",
            )
            for sid in STYLE_IDS
        ]

    altitude = sun.altitude_deg
    phase = sun.phase
    kelvin = sun.color_temp_k_estimate
    softness = weather.softness if weather else "unknown"
    cloud_pct = (weather.cloud_cover_pct if weather else None) or 50
    to_sunset = sun.minutes_to_sunset

    out: list[StyleScore] = []
    for sid in STYLE_IDS:
        score, reason = _score_one(
            sid, altitude, softness, kelvin, phase, to_sunset, cloud_pct,
        )
        # Clamp — bonuses can push above 1.0 in edge cases (golden hour
        # film warm). UI / prompt logic both expect [0, 1].
        score = max(0.0, min(1.0, score))
        tier = _tier_for(score)
        out.append(StyleScore(
            style_id=sid,
            label_zh=STYLE_LABELS_ZH[sid],
            score=round(score, 2),
            tier=tier,
            reason_zh=reason,
        ))
    return out


def to_prompt_block(
    selected_styles: list[str],
    scores: list[StyleScore] | None = None,
) -> str:
    """Build the STYLE_PRESETS block that goes into the LLM prompt.

    Always includes the recommended-knob table for the styles the user
    selected. If `scores` is provided (we have GPS), also annotates each
    with the current feasibility verdict so the LLM can deviate
    intelligently when the env doesn't match.
    """
    if not selected_styles:
        return ""

    score_by_id = {s.style_id: s for s in (scores or [])}
    lines = ["── STYLE PRESETS（用户选定的风格 → 推荐参数倾向）──"]

    for sid in selected_styles:
        preset = STYLE_PRESETS.get(sid)
        if not preset:
            continue
        label = STYLE_LABELS_ZH.get(sid, sid)
        verdict = score_by_id.get(sid)
        verdict_str = ""
        if verdict and verdict.tier != "unknown":
            tier_zh = {
                "recommended": "推荐",
                "marginal": "勉强可拍",
                "discouraged": "不推荐",
            }[verdict.tier]
            verdict_str = (
                f"  · 当前环境可行性：**{tier_zh}**（{verdict.score:.2f}）— "
                f"{verdict.reason_zh}\n"
            )
        wb_lo, wb_hi = preset["white_balance_k_range"]
        fl_lo, fl_hi = preset["focal_length_mm_range"]
        ev_lo, ev_hi = preset["ev_range"]
        ap_lo, ap_hi = preset["aperture_range"]
        lines.append(
            f"\n• 风格：{label}（{sid}）\n"
            f"{verdict_str}"
            f"  · white_balance_k: {wb_lo}–{wb_hi}K\n"
            f"  · focal_length_mm: {fl_lo}–{fl_hi}mm\n"
            f"  · ev_compensation: {ev_lo:+.1f} ~ {ev_hi:+.1f}\n"
            f"  · aperture: {ap_lo} ~ {ap_hi}\n"
            f"  · scene.lighting 倾向: {', '.join(preset['lighting_prefer'])}\n"
            f"  · composition.primary 倾向: {', '.join(preset['composition_prefer'])}\n"
            f"  · angle.height_hint 倾向: {', '.join(preset['height_hint_prefer'])}"
        )

    lines.append(
        "\n\n规则：\n"
        "  1. 在不违反 ENVIRONMENT FACTS 与场景模式硬约束的前提下，"
        "**所有 shots 的相机参数必须落在该风格的推荐区间内**。\n"
        "  2. 若可行性为「不推荐」，仍按风格做但要在 rationale 里说明"
        "「当前环境不太理想，已尽力靠拢」并给一个折中方案。\n"
        "  3. 若用户选了多个风格，前 N-1 个 shots 倾向第一个风格，"
        "其余倾向第二个，让用户能比较。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# "Better time" suggestion — when current env doesn't match any picked
# style, scan the rest of today (in 30-min steps) to find the best slot.
# Weather is held constant (we'd need an hourly forecast to do better,
# and that endpoint exists on Open-Meteo but we'd need a separate cache);
# good enough for a UX hint that says "wait until 18:30, conditions
# improve a lot". Returns None when no slot beats `current_score` by a
# meaningful margin.
# ---------------------------------------------------------------------------
def suggest_better_time(
    selected_style_ids: list[str],
    lat: float,
    lon: float,
    now_utc,                          # datetime
    weather: Optional[weather_service.WeatherSnapshot] = None,
    *,
    min_improvement: float = 0.25,     # absolute score delta to bother surfacing
    step_minutes: int = 30,
    horizon_hours: int = 24,
) -> Optional[dict]:
    """Find the best future timestamp today/tomorrow for the user's picks.

    Returns ``{"timestamp", "phase", "best_score", "current_score",
    "delta", "reason_zh"}`` or None when no future slot is materially
    better than now.
    """
    if not selected_style_ids:
        return None

    from datetime import timedelta   # local to avoid heavy top-level dep

    def _max_score(at_utc) -> tuple[float, str]:
        info = sun_service.compute(lat, lon, at_utc)
        scores = score_styles(info, weather)
        chosen = [s for s in scores if s.style_id in selected_style_ids]
        if not chosen:
            return 0.0, info.phase
        top = max(chosen, key=lambda s: s.score)
        return top.score, info.phase

    current_score, _ = _max_score(now_utc)

    best_t = None
    best_score = current_score
    best_phase = None
    steps = (horizon_hours * 60) // step_minutes
    for i in range(1, steps + 1):
        t = now_utc + timedelta(minutes=step_minutes * i)
        s, phase = _max_score(t)
        if s > best_score:
            best_score = s
            best_t = t
            best_phase = phase

    if best_t is None:
        return None
    delta = best_score - current_score
    if delta < min_improvement:
        return None

    minutes_until = round((best_t - now_utc).total_seconds() / 60)
    if minutes_until < 60:
        when = f"{minutes_until} 分钟"
    elif minutes_until < 24 * 60:
        hh = minutes_until // 60
        mm = minutes_until % 60
        when = f"{hh} 小时" + (f" {mm} 分钟" if mm else "")
    else:
        when = "约 1 天"
    reason = (
        f"再等 {when}（{_phase_zh(best_phase or '')}），你选的风格可行性能从 "
        f"{current_score:.2f} 提升到 {best_score:.2f}"
    )
    return {
        "timestamp":     best_t.isoformat(),
        "phase":         best_phase,
        "best_score":    round(best_score, 2),
        "current_score": round(current_score, 2),
        "delta":         round(delta, 2),
        "reason_zh":     reason,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tier_for(score: float) -> str:
    if score >= 0.7:
        return "recommended"
    if score >= 0.45:
        return "marginal"
    return "discouraged"


def _geom_mean(*xs: float) -> float:
    """Geometric mean — penalises any one near-zero factor harder than
    arithmetic mean. We want "missing one critical condition" to drag
    the overall score down, not get averaged out by good ones."""
    p = 1.0
    for x in xs:
        p *= max(x, 0.01)
    return p ** (1.0 / len(xs))


def _band(value: float, ideal: float, half_width: float) -> float:
    """Triangular falloff around `ideal`. 1.0 at ideal, 0 at +/- half_width."""
    if half_width <= 0:
        return 1.0 if value == ideal else 0.0
    return max(0.0, 1.0 - abs(value - ideal) / half_width)


def _ramp_up(value: float, low: float, high: float) -> float:
    """0 below low, 1 above high, linear in between."""
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def _ramp_down(value: float, low: float, high: float) -> float:
    """1 below low, 0 above high, linear in between (penalty above high)."""
    if value <= low:
        return 1.0
    if value >= high:
        return 0.0
    return 1.0 - (value - low) / (high - low)


def _phase_zh(phase: str) -> str:
    return {
        "night":             "夜",
        "blue_hour_dawn":    "黎明蓝调",
        "golden_hour_dawn":  "清晨金光",
        "day":               "白天",
        "golden_hour_dusk":  "黄昏金光",
        "blue_hour_dusk":    "傍晚蓝调",
    }.get(phase, phase)


def _softness_zh(s: str) -> str:
    return {"soft": "柔光", "hard": "硬光", "mixed": "半云半晴", "unknown": "光质未知"}.get(s, s)


# ---------------------------------------------------------------------------
# Per-style scoring functions
#
# Each returns (0..1 score before bonus, str reason). The shared dispatcher
# `_score_one` adds the phase/time bonus and clamps to [0, 1].
# ---------------------------------------------------------------------------
def _score_one(
    sid: str,
    altitude: float,
    softness: str,
    kelvin: int,
    phase: str,
    to_sunset: Optional[float],
    cloud_pct: int,
) -> tuple[float, str]:
    if sid == "cinematic_moody":
        return _score_cinematic_moody(altitude, softness, kelvin, phase)
    if sid == "clean_bright":
        return _score_clean_bright(altitude, softness, kelvin, phase, cloud_pct)
    if sid == "film_warm":
        return _score_film_warm(altitude, softness, kelvin, phase, to_sunset)
    if sid == "street_candid":
        return _score_street_candid(altitude, phase)
    if sid == "editorial_fashion":
        return _score_editorial_fashion(altitude, softness, phase)
    return 0.5, "未知风格"


def _score_cinematic_moody(altitude, softness, kelvin, phase) -> tuple[float, str]:
    # Need: low altitude (directional shadows), hard or mixed light,
    # warm-ish kelvin (3000-5500). Killers: high noon, full overcast.
    sub_alt = _ramp_down(altitude, 35, 60)        # ideal under 35°, dead by 60°
    sub_soft = {"hard": 1.0, "mixed": 0.7, "soft": 0.2, "unknown": 0.6}[softness]
    sub_k = _ramp_down(kelvin, 5500, 6800)        # cooler than 5500 starts hurting
    bonus = 0.15 if phase in (
        sun_service.PHASE_GOLDEN_DUSK,
        sun_service.PHASE_BLUE_DUSK,
        sun_service.PHASE_BLUE_DAWN,
        sun_service.PHASE_NIGHT,
    ) else 0
    score = _geom_mean(sub_alt, sub_soft, sub_k) + bonus

    if score >= 0.7:
        # Dark phases (night / blue hour) — softness derived from cloud
        # cover is meaningless after sunset, so don't surface it in copy.
        if phase in (sun_service.PHASE_NIGHT, sun_service.PHASE_BLUE_DUSK,
                     sun_service.PHASE_BLUE_DAWN):
            reason = f"当前{_phase_zh(phase)}，正适合氛围感"
        else:
            reason = f"当前{_phase_zh(phase)}{_softness_zh(softness)}，正适合氛围感"
    elif altitude > 50:
        reason = "太阳偏顶光，氛围感很难拍出明显阴影"
    elif softness == "soft":
        reason = "全阴天散射光过软，氛围感缺少主光方向"
    else:
        reason = "环境勉强能做氛围感，效果会打折"
    return score, reason


def _score_clean_bright(altitude, softness, kelvin, phase, cloud_pct) -> tuple[float, str]:
    # Need: bright (altitude > 20°), neutral/cool kelvin (>=5000K),
    # soft to mixed light (薄云最佳). Killers: dusk/dawn warm tones, low light.
    sub_alt = _ramp_up(altitude, 10, 30)          # need altitude > 10° to start
    sub_soft = {"soft": 1.0, "mixed": 0.85, "hard": 0.55, "unknown": 0.7}[softness]
    sub_k = _ramp_up(kelvin, 4500, 5500)          # need at least ~5000K
    bonus = 0.10 if phase == sun_service.PHASE_DAY else 0
    if 30 <= cloud_pct <= 70:
        bonus += 0.05                              # 薄云加成
    score = _geom_mean(sub_alt, sub_soft, sub_k) + bonus

    if score >= 0.7:
        reason = "当前光线明亮、色温合适，能拍出清爽日系"
    elif kelvin < 4500:
        reason = "色温偏暖，清爽日系会泛黄不通透"
    elif altitude < 10:
        reason = "光线太低/太暗，清爽日系出不来明亮感"
    elif softness == "hard":
        reason = "光线偏硬，清爽日系会有明显阴影；建议找树荫或白墙反射"
    else:
        reason = "环境勉强能做清爽日系，建议找浅色背景"
    return score, reason


def _score_film_warm(altitude, softness, kelvin, phase, to_sunset) -> tuple[float, str]:
    # Need: low altitude (under 20°), warm kelvin (under 5000K),
    # mixed or hard light. Killers: midday, overcast.
    sub_alt = _ramp_down(altitude, 20, 50)        # 黄金时段最佳
    sub_soft = {"mixed": 1.0, "hard": 0.85, "soft": 0.4, "unknown": 0.6}[softness]
    sub_k = _ramp_down(kelvin, 5000, 6500)
    bonus = 0
    if phase in (sun_service.PHASE_GOLDEN_DUSK, sun_service.PHASE_GOLDEN_DAWN):
        bonus = 0.20
    elif to_sunset is not None and to_sunset < 60:
        bonus = 0.15
    score = _geom_mean(sub_alt, sub_soft, sub_k) + bonus

    if score >= 0.7:
        reason = "正好赶上暖光时段，温柔暖光手到擒来"
    elif altitude > 30:
        reason = "太阳还高，温柔暖光要等日落前 1 小时左右"
    elif softness == "soft":
        reason = "阴天没有定向暖光，温柔暖光出不来胶片味"
    else:
        reason = "环境勉强能做温柔暖光，建议靠近窗光或暖色墙面"
    return score, reason


def _score_street_candid(altitude, phase) -> tuple[float, str]:
    # Almost always feasible — only kill in deep night with no fill light.
    if altitude is None or altitude > 5:
        return 0.85, "街头风格不挑环境，随时能拍"
    if phase == sun_service.PHASE_NIGHT:
        return 0.45, "天太黑，街头抓拍需要街灯/招牌等人造光源"
    return 0.7, "光线偏弱，街头抓拍要靠近灯光主体"


def _score_editorial_fashion(altitude, softness, phase) -> tuple[float, str]:
    # Needs directional light for shape; almost always works during day.
    sub_alt = 1.0 if altitude > 5 else 0.3
    sub_soft = {"hard": 1.0, "mixed": 0.8, "soft": 0.55, "unknown": 0.7}[softness]
    sub_k = 0.85
    score = _geom_mean(sub_alt, sub_soft, sub_k)

    if score >= 0.7:
        reason = "光线条件不错，可以做大片感造型"
    elif altitude < 5:
        reason = "天黑了，大片感需要街灯或人造主光"
    elif softness == "soft":
        reason = "全阴天的大平光会让大片感少了戏剧度"
    else:
        reason = "环境勉强能做大片感，建议加强姿态对比"
    return score, reason
