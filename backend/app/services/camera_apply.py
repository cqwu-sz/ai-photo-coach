"""Translate the LLM's photographer-facing camera advice into machine-
applicable iPhone parameters.

The LLM thinks in 摄影师 vocabulary: ``aperture: "f/2.0"``,
``shutter: "1/250"``, ``focal_length_mm: 85``. AVFoundation thinks in
``videoZoomFactor: 3.27``, ``duration: CMTime(1, 250)``,
``iso: 200``, ``targetBias: -0.3``. This module is the bridge.

Why on the backend instead of in iOS:
  - Mock mode and Web preview need the same numbers.
  - Easier to unit-test than Swift wiring.
  - Single source of truth — the iOS shoot screen never re-parses
    ``"f/2.0"`` strings, so a typo can't break the camera.

iPhone reality check:
  - Physical aperture is fixed (main lens f/1.78 on most modern bodies).
    We can't actually stop down to f/4 / f/8. We approximate the
    *exposure* with ISO/shutter and tell the user honestly that the
    *depth-of-field* effect is only achievable in post (Portrait Mode
    or Lightroom blur). The honest note lives in ``aperture_note``.
  - 26mm-equivalent main lens. Telephoto modules vary (2x = 52mm,
    3x = 77mm, 5x = 120mm). We compute zoom_factor relative to the
    main lens since AVFoundation's ``builtInTripleCamera`` does that
    automatically.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from ..models import CameraSettings, IphoneApplyPlan, IphoneLens

log = logging.getLogger(__name__)


# Main-lens (1x) equivalent focal length on modern iPhones — used as the
# zoom-factor anchor. ``builtInTripleCamera``'s zoom 1.0 corresponds to
# this lens, so zoom = focal_mm / MAIN_FOCAL_MM.
MAIN_FOCAL_MM = 26.0

# iPhone main lens physical aperture. Used to write the honest aperture
# note when the requested aperture differs.
IPHONE_PHYSICAL_APERTURE = 1.78


_SHUTTER_FRACTION_RE = re.compile(r"^\s*1\s*/\s*(\d+(?:\.\d+)?)\s*s?\s*$", re.IGNORECASE)
_SHUTTER_DECIMAL_RE  = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*s?\s*$", re.IGNORECASE)


def _parse_shutter_seconds(s: str) -> float:
    """Parse '1/250', '1/250s', '0.004', '2"' (deliberately not
    supported — long exposures aren't a realistic mobile photo case).
    Returns seconds. Falls back to 1/125 on parse failure to keep iOS
    capture functional even when the LLM emits something weird.
    """
    if not s:
        return 1.0 / 125.0
    s = s.strip()
    m = _SHUTTER_FRACTION_RE.match(s)
    if m:
        denom = float(m.group(1))
        if denom <= 0:
            return 1.0 / 125.0
        return 1.0 / denom
    m = _SHUTTER_DECIMAL_RE.match(s)
    if m:
        try:
            v = float(m.group(1))
            return max(min(v, 1.0), 1.0 / 8000.0)
        except ValueError:
            pass
    log.info("camera_apply: unrecognised shutter %r, defaulting to 1/125", s)
    return 1.0 / 125.0


_F_RE = re.compile(r"f\s*[/\\]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def _parse_aperture(s: str) -> Optional[float]:
    """Pull the f-number out of strings like 'f/2.0', 'F2.8', 'f1.4'.
    Returns ``None`` on failure."""
    if not s:
        return None
    m = _F_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _zoom_for_focal(focal_mm: float, hint: Optional[IphoneLens]) -> float:
    """Map equivalent focal length -> ``builtInTripleCamera`` zoomFactor.

    A small bias for telephoto lens hints: when the AI says "use the
    2x telephoto" but the focal_mm rounds to 1.7x, we honor the hint
    and snap to 2.0 so the system actually switches to the telephoto
    module instead of digital-cropping the main sensor.
    """
    raw = focal_mm / MAIN_FOCAL_MM
    if hint == IphoneLens.ultrawide_0_5x:
        return 0.5
    if hint == IphoneLens.tele_2x and raw < 2.0:
        return 2.0
    if hint == IphoneLens.tele_3x and raw < 3.0:
        return 3.0
    if hint == IphoneLens.tele_5x and raw < 5.0:
        return 5.0
    # Clamp into a realistic range for current iPhones (0.5x..15x via
    # max digital zoom on the longest module).
    return max(0.5, min(raw, 15.0))


def _aperture_note(requested_f: Optional[float]) -> str:
    """Honest note about the gap between requested aperture and what an
    iPhone main lens can physically deliver. The note is rendered
    *under* the aperture chip — never silently rewrites the AI's
    advice, just contextualizes it."""
    if requested_f is None:
        return ""
    if abs(requested_f - IPHONE_PHYSICAL_APERTURE) < 0.25:
        # Effectively the same as the lens — nothing to disclaim.
        return ""
    if requested_f < IPHONE_PHYSICAL_APERTURE:
        return (
            f"AI 建议 f/{requested_f:.1f} 比 iPhone 主摄物理光圈 "
            f"f/{IPHONE_PHYSICAL_APERTURE} 更大，机身已是最大开口；"
            "想加强虚化建议靠近主体或换 2x/3x 长焦端"
        )
    if requested_f >= 4.0:
        return (
            f"iPhone 主摄物理光圈固定 f/{IPHONE_PHYSICAL_APERTURE}，"
            f"AI 要的 f/{requested_f:.1f} 深景深效果机内做不到；"
            "可拍后用「人像模式」或 Lightroom 调虚化半径"
        )
    return (
        f"iPhone 物理光圈固定 f/{IPHONE_PHYSICAL_APERTURE}；"
        f"AI 用 ISO/快门组合实现 f/{requested_f:.1f} 的曝光等效"
    )


def build_plan(camera: CameraSettings) -> IphoneApplyPlan:
    """Translate a CameraSettings into an iPhone-applicable plan.

    Robust to LLM output quirks: malformed shutter/aperture strings
    fall back to safe defaults. The plan always validates against the
    Pydantic schema so iOS Codable can rely on it being well-formed.
    """
    hint = camera.device_hints.iphone_lens if camera.device_hints else None
    zoom_factor = round(_zoom_for_focal(camera.focal_length_mm, hint), 2)

    shutter_seconds = _parse_shutter_seconds(camera.shutter)
    requested_f = _parse_aperture(camera.aperture)
    aperture_note = _aperture_note(requested_f)

    iso = max(25, min(int(camera.iso), 12800))
    ev = float(camera.ev_compensation if camera.ev_compensation is not None else 0.0)
    ev = max(-3.0, min(ev, 3.0))

    wb = camera.white_balance_k if camera.white_balance_k is not None else 5500
    wb = max(2000, min(int(wb), 10000))

    can_apply = True
    # Outlandish requests we definitely can't execute on an iPhone.
    if camera.focal_length_mm > 200 or camera.focal_length_mm < 13:
        can_apply = False
    if shutter_seconds > 1.0 or shutter_seconds < 1.0 / 8000.0:
        can_apply = False

    return IphoneApplyPlan(
        zoom_factor=zoom_factor,
        iso=iso,
        shutter_seconds=shutter_seconds,
        ev_compensation=ev,
        white_balance_k=wb,
        aperture_note=aperture_note,
        can_apply=can_apply,
    )
