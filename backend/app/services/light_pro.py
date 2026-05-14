"""Professional lighting characterisation — promotes the current
亮/暗/顺/逆 four-bucket light reading into the words a working
photographer actually uses: main-light elevation, hardness, key/fill
ratio.

This module **extends** ``scene_aggregate``'s already-decent lighting
output rather than replacing it. ``scene_aggregate`` gives us:
  - color temperature (cct_k) — already pro-grade
  - dynamic range bucket — coarse but useful
  - light_direction (front / side / back) — too coarse
  - lighting_notes — free-form Chinese cues

What we add here:
  - main_light_elevation_label — top / 45° / side / low / front-flat
  - hardness — soft / medium / hard
  - key_fill_ratio — float (1.0 = no fill, 8.0 = deep chiaroscuro)
  - one-line zh summary that the LLM can quote in rationale

Approach (deterministic, no LLM):
  Drive estimates from
    sun.altitude_deg  →  elevation label
    sun.phase + cct_k →  hardness bias
    highlight_clip + shadow_clip + luma p05/p95 spread → final hardness
    p95/(p05+ε)       →  key:fill ratio surrogate
  Each axis falls back gracefully when its input is missing.

Inputs are plain values (kept generic on purpose) so this module is
trivially unit-testable without needing to spin up a full SceneAggregate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

from ..models import FrameMeta


ElevationLabel = Literal[
    "overhead",      # sun > 60° altitude — top light, harsh on face
    "high",          # 30..60° — typical mid-morning / mid-afternoon
    "golden",        # 6..30° — flattering low angle, golden hour
    "horizon",       # 0..6° — sunrise/sunset, long shadows
    "below_horizon", # sun is set / hasn't risen — civil twilight or night
    "indoor",        # no sun angle (or unreliable)
]


HardnessLabel = Literal["soft", "medium", "hard"]


@dataclass(frozen=True, slots=True)
class LightingProAggregate:
    elevation: ElevationLabel
    elevation_deg: Optional[float]
    hardness: HardnessLabel
    hardness_score: float                # 0..1 — higher = harder
    key_fill_ratio: Optional[float]      # surrogate from p95 / max(p05, 1)
    chiaroscuro_level: Literal["flat", "shaped", "dramatic", "extreme"]
    summary_zh: str

    def to_dict(self) -> dict:
        return {
            "elevation":          self.elevation,
            "elevation_deg":      self.elevation_deg,
            "hardness":           self.hardness,
            "hardness_score":     self.hardness_score,
            "key_fill_ratio":     self.key_fill_ratio,
            "chiaroscuro_level":  self.chiaroscuro_level,
            "summary_zh":         self.summary_zh,
        }


def _elevation_label(altitude_deg: Optional[float]) -> tuple[ElevationLabel, Optional[float]]:
    if altitude_deg is None:
        return ("indoor", None)
    a = float(altitude_deg)
    if a > 60:    return ("overhead", a)
    if a > 30:    return ("high", a)
    if a > 6:     return ("golden", a)
    if a >= 0:    return ("horizon", a)
    return ("below_horizon", a)


def _hardness(
    elevation: ElevationLabel,
    cct_k: Optional[int],
    highlight_clip: Optional[float],
    shadow_clip: Optional[float],
    p05: Optional[float],
    p95: Optional[float],
) -> tuple[HardnessLabel, float]:
    """Hardness = how sharp shadow edges are. We don't have edge data,
    so we proxy via:
        - direct sun + low altitude → harder
        - simultaneous highlight clip + shadow clip → harder
        - large p95-p05 spread → harder
        - overcast (low contrast + neutral cct) → softer
    """
    score = 0.5  # neutral baseline
    if elevation == "overhead":      score += 0.25
    elif elevation == "high":        score += 0.15
    elif elevation == "golden":      score += 0.05
    elif elevation == "horizon":     score -= 0.05  # long shadows but softer due to atmosphere
    elif elevation == "indoor":      score -= 0.10
    elif elevation == "below_horizon": score -= 0.20

    if (highlight_clip is not None and shadow_clip is not None
            and highlight_clip > 0.02 and shadow_clip > 0.02):
        score += 0.20
    if p05 is not None and p95 is not None:
        spread = max(0.0, (p95 - p05) / 255.0)
        score += (spread - 0.5) * 0.30

    # Daylight CCT (5500-6500K) with little clipping == overcast — softer.
    if cct_k is not None and 5200 <= cct_k <= 6800:
        if (highlight_clip or 0) < 0.005 and (shadow_clip or 0) < 0.005:
            score -= 0.20

    score = max(0.0, min(1.0, score))
    if score < 0.35:   return ("soft", round(score, 2))
    if score < 0.65:   return ("medium", round(score, 2))
    return ("hard", round(score, 2))


def _key_fill_ratio(p05: Optional[float], p95: Optional[float]) -> Optional[float]:
    """Surrogate key:fill ratio derived from luma percentiles.

    A real ratio requires knowing the meter reading on the lit face vs
    shadow face of the subject. We don't have segmentation here, so we
    use p95/(p05+ε) — captures the global "how bright is the brightest
    vs the darkest area" which is the same intuition photographers use
    when they eyeball a lighting setup.

    Clamped to [1.0, 16.0] — anything beyond 16:1 is extreme chiaroscuro
    that the LLM should be warning the user about, not exact-quantifying.
    """
    if p05 is None or p95 is None:
        return None
    ratio = (p95 + 1.0) / max(p05 + 1.0, 1.0)
    return round(max(1.0, min(16.0, ratio)), 2)


def _chiaroscuro_level(ratio: Optional[float]) -> Literal["flat", "shaped", "dramatic", "extreme"]:
    if ratio is None:    return "shaped"
    if ratio < 2.0:      return "flat"
    if ratio < 5.0:      return "shaped"
    if ratio < 10.0:     return "dramatic"
    return "extreme"


def _summary_zh(
    elevation: ElevationLabel,
    hardness: HardnessLabel,
    ratio: Optional[float],
    chiaro: str,
    direction_zh: Optional[str],
) -> str:
    elev_zh = {
        "overhead":      "正顶光",
        "high":          "高位光",
        "golden":        "黄金时段低位光",
        "horizon":       "贴地长影光",
        "below_horizon": "无直射阳光（暮光/夜间）",
        "indoor":        "室内/无明显方向光",
    }[elevation]
    hard_zh = {"soft": "柔光", "medium": "中等硬度", "hard": "硬光"}[hardness]
    chiaro_zh = {
        "flat":     "明暗近乎平铺",
        "shaped":   "有立体感",
        "dramatic": "强戏剧感",
        "extreme":  "极端反差（接近剪影）",
    }[chiaro]
    ratio_txt = f"，明暗比约 {ratio}:1" if ratio else ""
    direction_txt = f"，{direction_zh}" if direction_zh else ""
    return f"{elev_zh} + {hard_zh}{direction_txt}{ratio_txt}（{chiaro_zh}）"


_DIRECTION_ZH = {
    "front": "顺光",
    "side":  "侧光",
    "back":  "逆光",
}


def aggregate(
    frames: Iterable[FrameMeta],
    sun_altitude_deg: Optional[float] = None,
    cct_k: Optional[int] = None,
    highlight_clip_pct: Optional[float] = None,
    shadow_clip_pct: Optional[float] = None,
    light_direction: Optional[str] = None,
) -> Optional[LightingProAggregate]:
    """Build a ``LightingProAggregate``. Returns ``None`` when no frames
    or absolutely no usable signal is present — callers treat that as
    "skip the LIGHTING PRO block".
    """
    frames = list(frames)
    if not frames:
        return None

    p05s = [f.luma_p05 for f in frames if f.luma_p05 is not None]
    p95s = [f.luma_p95 for f in frames if f.luma_p95 is not None]
    p05_mean = sum(p05s) / len(p05s) if p05s else None
    p95_mean = sum(p95s) / len(p95s) if p95s else None

    elevation, elev_deg = _elevation_label(sun_altitude_deg)
    hardness, hardness_score = _hardness(
        elevation, cct_k, highlight_clip_pct, shadow_clip_pct,
        p05_mean, p95_mean,
    )
    ratio = _key_fill_ratio(p05_mean, p95_mean)
    chiaro = _chiaroscuro_level(ratio)
    direction_zh = _DIRECTION_ZH.get(light_direction or "")

    # If we have absolutely nothing, bail out so we don't pollute the
    # prompt with three lines of "未知 / 未知 / 未知".
    if (elevation == "indoor"
            and hardness_score == 0.5
            and ratio is None
            and direction_zh is None):
        return None

    return LightingProAggregate(
        elevation=elevation,
        elevation_deg=elev_deg,
        hardness=hardness,
        hardness_score=hardness_score,
        key_fill_ratio=ratio,
        chiaroscuro_level=chiaro,
        summary_zh=_summary_zh(elevation, hardness, ratio, chiaro, direction_zh),
    )


def to_prompt_block(agg: Optional[LightingProAggregate]) -> str:
    """Render the LIGHTING PRO block. Empty when ``agg`` is None."""
    if agg is None:
        return ""
    parts = [
        "── LIGHTING PRO（专业摄影词汇的光线刻画）──",
        f"  · 主光位：**{agg.elevation}**"
        + (f"（约 {agg.elevation_deg:.0f}°）" if agg.elevation_deg is not None else "")
        + f"  ·  光质：**{agg.hardness}**（hardness_score={agg.hardness_score:.2f}）",
    ]
    if agg.key_fill_ratio is not None:
        parts.append(
            f"  · 明暗比：约 **{agg.key_fill_ratio}:1**（{agg.chiaroscuro_level}）"
        )
    parts.append(f"  · 一句话总结：{agg.summary_zh}")
    parts.append(
        "  LIGHTING PRO DOCTRINE：rationale 必须用这一行术语来谈光线"
        "（『正顶光 + 硬光，会让眼窝发黑，往遮阴下半步』），"
        "不要回退到「光线好/光线不好」这种业余说法。"
    )
    return "\n".join(parts)
