"""Style compliance check + auto-clamp for LLM-returned shots.

After the LLM returns its shot list, we verify each shot's camera
settings actually fall inside the recommended ranges of the user's
selected style(s). The motivation:

  - We tell the LLM (in prompts.py STYLE PRESETS block) "white_balance_k
    must be in [3500, 4500] for 氛围感". But there's no hard penalty if
    it ignores us, so it sometimes outputs WB=5500 anyway.
  - Re-prompting Gemini for "you violated the style range" is slow
    (extra 3-6 s per analyze) and unreliable (it might violate other
    constraints in the retry).
  - Solution: a deterministic Python pass that **(a)** clamps each
    out-of-range value back to the nearest range edge, **(b)** appends
    a one-line "(已按所选风格 X 调整)" suffix to the shot's rationale
    so the user knows we adjusted it, **(c)** logs a per-request
    `style_compliance_rate` metric for observability.

Why deterministic clamp instead of LLM repair:
  - The recommended ranges in STYLE_PRESETS are the source of truth
    we WANT the camera at. If the LLM disagreed, it's almost always
    because it was reasoning from a generic photography prior, not
    because the range was wrong for this style.
  - Clamping is O(N_shots), no extra LLM token spend, no failure mode.

The compliance rate reported is "fraction of (shot, knob) pairs that
were already in-range before clamping" — so 1.0 means LLM nailed it,
0.0 means we had to fix everything.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from ..models import ShotStyleMatch
from . import calibration as calibration_service
from . import color_science
from . import style_feasibility as sf

log = logging.getLogger(__name__)


# Knobs we actively clamp. Other STYLE_PRESETS fields (lighting,
# composition.primary, height_hint) are categorical preferences — we
# log violations but don't override the LLM since changing them would
# break the rationale's narrative.
_NUMERIC_KNOBS = ("white_balance_k", "focal_length_mm", "ev_compensation")


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    total_checks: int            # number of (shot, knob) pairs evaluated
    in_range_count: int          # of the above, how many were already OK
    clamped_count: int           # of the above, how many we adjusted
    rate: float                  # in_range_count / max(total_checks, 1)
    per_shot: list[dict]         # one dict per shot: {shot_id, style_id, fixes:[{knob,from,to}]}
    # v11 color-palette check (scene-level, not per-shot)
    palette_drift: list[dict] = None  # type: ignore[assignment]

    def to_log_dict(self) -> dict:
        return {
            "style_compliance_rate": round(self.rate, 3),
            "style_compliance_total": self.total_checks,
            "style_compliance_clamped": self.clamped_count,
            "style_compliance_per_shot": self.per_shot,
        }


def validate_and_clamp(
    shots: list,
    style_keywords: list[str],
    *,
    scene_cct_k: Optional[int] = None,
    scene_saturation: Optional[float] = None,
    scene_contrast: Optional[float] = None,
) -> ComplianceReport:
    """Walk each shot, pick the matching style, clamp out-of-range knobs.

    Mutates `shots` in place (each shot's `camera` fields and `rationale`
    are updated when something was clamped).

    `style_keywords` is the raw `meta.style_keywords` list from the user
    (e.g. ``["cinematic","moody","film","warm"]``). Same English-keyword
    → style-id mapping as in prompts.py is applied here.
    """
    style_ids = _resolve_style_ids(style_keywords)
    if not style_ids or not shots:
        return ComplianceReport(0, 0, 0, 1.0, [])

    total = 0
    in_range = 0
    clamped = 0
    per_shot: list[dict] = []

    for idx, shot in enumerate(shots):
        # When the user picked multiple styles, the prompt asks the LLM
        # to lean N-1 shots toward the first style and the rest toward
        # the second. Mirror that: shot 0..N-2 → style[0], last → style[-1].
        # For 2 picks + 3 shots: shots 0,1 → style[0], shot 2 → style[1].
        if len(style_ids) == 1 or idx < len(shots) - 1:
            style_id = style_ids[0]
        else:
            style_id = style_ids[-1]

        preset = sf.STYLE_PRESETS.get(style_id)
        if preset is None:
            continue

        camera = getattr(shot, "camera", None)
        if camera is None:
            continue

        fixes: list[dict] = []
        shot_total = 0
        shot_in_range = 0
        # v12 — apply hot-reloaded WB calibration: if recalibration
        # found that real users at this style routinely shoot at e.g.
        # 5800K, recentre the range there (keeping the original width).
        wb_overrides = calibration_service.current().style_wb_centres
        wb_override_centre = wb_overrides.get(style_id)

        for knob in _NUMERIC_KNOBS:
            range_key = f"{knob}_range"
            rng = preset.get(range_key)
            if rng is None:
                continue
            lo, hi = rng
            if knob == "white_balance_k" and wb_override_centre is not None:
                half = (hi - lo) / 2
                lo = int(wb_override_centre - half)
                hi = int(wb_override_centre + half)
            cur = getattr(camera, knob, None)
            if cur is None:
                # LLM left it blank; fill with the midpoint so downstream
                # device-hint computation has something to work with.
                mid = _mid(lo, hi, knob)
                setattr(camera, knob, mid)
                fixes.append({"knob": knob, "from": None, "to": mid})
                clamped += 1
                total += 1
                shot_total += 1
                continue
            total += 1
            shot_total += 1
            if lo <= cur <= hi:
                in_range += 1
                shot_in_range += 1
                continue
            new_val = _clamp(cur, lo, hi, knob)
            setattr(camera, knob, new_val)
            fixes.append({"knob": knob, "from": cur, "to": new_val})
            clamped += 1

        # Always attach style_match so the result UI can render the
        # "风格 X · 推荐 Y · 实际 Z" badge — even when in_range is True.
        shot.style_match = ShotStyleMatch(
            style_id=style_id,
            label_zh=sf.STYLE_LABELS_ZH.get(style_id, style_id),
            white_balance_k_range=preset["white_balance_k_range"],
            focal_length_mm_range=preset["focal_length_mm_range"],
            ev_range=preset["ev_range"],
            in_range=(shot_total > 0 and shot_in_range == shot_total),
            fixes=fixes,
        )

        if fixes:
            _annotate_rationale(shot, style_id, fixes)
            per_shot.append({
                "shot_id": getattr(shot, "id", f"shot_{idx}"),
                "style_id": style_id,
                "fixes": fixes,
            })

    rate = in_range / total if total else 1.0

    # v11: scene-level palette drift check. Compare measured cct/sat/
    # contrast against each style's STYLE_PALETTE bands. Result is only
    # used for surfacing warnings to the user (we don't auto-clamp
    # color since the LLM doesn't directly control it — it lives in
    # post-processing recommendations).
    palette_drift: list[dict] = []
    for style_id in style_ids:
        # Map the resolved style_id into a color_science palette key.
        palette_key = _style_id_to_palette_key(style_id)
        if not palette_key:
            continue
        diffs = color_science.check_style_palette(
            palette_key, scene_cct_k, scene_saturation, scene_contrast,
        )
        for axis, msg in diffs:
            palette_drift.append({
                "style_id": style_id,
                "axis": axis,
                "message": msg,
            })

    report = ComplianceReport(
        total_checks=total,
        in_range_count=in_range,
        clamped_count=clamped,
        rate=rate,
        per_shot=per_shot,
        palette_drift=palette_drift,
    )
    if clamped:
        log.info(
            "style compliance: %d/%d in-range (rate=%.2f), clamped %d",
            in_range, total, rate, clamped,
        )
    return report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Map STYLE_PRESETS id (used by feasibility) → STYLE_PALETTE key
# (used by color_science). Both vocabularies live independently so
# compliance can mix-and-match without coupling either module.
_STYLE_ID_TO_PALETTE: dict[str, str] = {
    "japanese":   "japanese_clean",
    "clean":      "japanese_clean",
    "ambient":    "ambient_mood",
    "moody":      "ambient_mood",
    "neon":       "hk_neon",
    "cyberpunk":  "hk_neon",
    "film":       "film_grain",
    "highkey":    "high_key",
    "high_key":   "high_key",
    "golden":     "golden_hour",
    "magic_hour": "golden_hour",
}


def _style_id_to_palette_key(style_id: str) -> Optional[str]:
    return _STYLE_ID_TO_PALETTE.get(style_id)


def _clamp(value, lo, hi, knob):
    if knob == "white_balance_k":
        return int(max(lo, min(hi, round(value))))
    if knob == "focal_length_mm":
        # Phones can't do arbitrary focal length — snap to common
        # iPhone equivalents inside the allowed range.
        snapped = max(lo, min(hi, value))
        common = (14, 24, 28, 35, 50, 65, 85, 105, 135, 200)
        return min(common, key=lambda c: (abs(c - snapped), c < lo or c > hi))
    if knob == "ev_compensation":
        # Round to 0.3-step (matches iPhone EV slider granularity).
        snapped = max(lo, min(hi, value))
        return round(snapped * 3) / 3
    return value


def _mid(lo, hi, knob):
    if knob == "focal_length_mm":
        return _clamp((lo + hi) / 2, lo, hi, knob)
    if knob == "white_balance_k":
        return int((lo + hi) / 2)
    return round((lo + hi) / 2, 1)


def _resolve_style_ids(keywords: list[str]) -> list[str]:
    # Inline copy of prompts._STYLE_KEYWORD_MAP to avoid a circular import
    # (prompts.py already depends on style_feasibility, which would
    # trigger if we imported it back here).
    mapping = {
        "cinematic": "cinematic_moody", "moody": "cinematic_moody",
        "clean": "clean_bright",        "bright": "clean_bright",
        "film": "film_warm",            "warm": "film_warm",
        "street": "street_candid",      "candid": "street_candid",
        "editorial": "editorial_fashion", "fashion": "editorial_fashion",
    }
    seen: list[str] = []
    for kw in keywords or []:
        sid = mapping.get(kw.strip().lower())
        if sid and sid not in seen:
            seen.append(sid)
    return seen


_ANNOTATION_RE = re.compile(r"（已按.+风格自动校准[^）]*）")


def _annotate_rationale(shot, style_id: str, fixes: list[dict]) -> None:
    """Append a one-line "we tweaked X for style Y" note to rationale.

    Idempotent: if a previous annotation is already present (e.g. tests
    that re-run the validator), strip it before re-appending so we don't
    chain "（...）（...）（...）".
    """
    label = sf.STYLE_LABELS_ZH.get(style_id, style_id)
    parts = []
    for f in fixes:
        knob_zh = {
            "white_balance_k": "白平衡",
            "focal_length_mm": "焦段",
            "ev_compensation": "曝光补偿",
        }.get(f["knob"], f["knob"])
        if f["from"] is None:
            parts.append(f"{knob_zh}→{f['to']}")
        else:
            parts.append(f"{knob_zh} {f['from']}→{f['to']}")
    note = f"（已按{label}风格自动校准：{ '、'.join(parts) }）"

    cur = getattr(shot, "rationale", "") or ""
    cur = _ANNOTATION_RE.sub("", cur).rstrip()
    shot.rationale = (cur + note) if cur else note
