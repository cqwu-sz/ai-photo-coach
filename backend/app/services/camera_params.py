"""Rule-based fallback / sanity-check for camera settings.

The LLM produces camera settings as part of its JSON output, but we run a
deterministic post-pass to:
  1. Fill missing fields if the LLM omitted them.
  2. Clamp obviously-wrong values into safe ranges.
  3. Provide a deterministic fallback when running in mock mode.

The rule table is intentionally small and explicit so it can be code-reviewed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..models import (
    CameraSettings,
    DeviceHints,
    IphoneLens,
    Lighting,
)


@dataclass(frozen=True)
class _ParamPreset:
    focal_length_mm: float
    aperture: str
    shutter: str
    iso: int
    wb_k: int
    ev: float
    iphone_lens: IphoneLens


# (lighting, person_count_bucket) -> preset.
# person_count_bucket: 1 -> single, 2 -> couple, 3+ -> group
_PRESETS: dict[tuple[Lighting, int], _ParamPreset] = {
    (Lighting.golden_hour, 1): _ParamPreset(50, "f/1.8", "1/320", 200, 5500, -0.3, IphoneLens.tele_2x),
    (Lighting.golden_hour, 2): _ParamPreset(50, "f/2.0", "1/320", 200, 5500, -0.3, IphoneLens.tele_2x),
    (Lighting.golden_hour, 3): _ParamPreset(35, "f/2.8", "1/250", 200, 5500, -0.3, IphoneLens.wide_1x),

    (Lighting.blue_hour, 1): _ParamPreset(35, "f/1.8", "1/125", 800, 4500, +0.3, IphoneLens.wide_1x),
    (Lighting.blue_hour, 2): _ParamPreset(35, "f/2.0", "1/125", 800, 4500, +0.3, IphoneLens.wide_1x),
    (Lighting.blue_hour, 3): _ParamPreset(28, "f/2.8", "1/125", 1600, 4500, +0.3, IphoneLens.wide_1x),

    (Lighting.harsh_noon, 1): _ParamPreset(50, "f/2.8", "1/1000", 100, 5500, -0.7, IphoneLens.tele_2x),
    (Lighting.harsh_noon, 2): _ParamPreset(35, "f/2.8", "1/1000", 100, 5500, -0.7, IphoneLens.wide_1x),
    (Lighting.harsh_noon, 3): _ParamPreset(28, "f/4.0", "1/800", 100, 5500, -0.7, IphoneLens.wide_1x),

    (Lighting.overcast, 1): _ParamPreset(50, "f/2.0", "1/250", 400, 6500, 0.0, IphoneLens.tele_2x),
    (Lighting.overcast, 2): _ParamPreset(35, "f/2.0", "1/250", 400, 6500, 0.0, IphoneLens.wide_1x),
    (Lighting.overcast, 3): _ParamPreset(28, "f/2.8", "1/250", 400, 6500, 0.0, IphoneLens.wide_1x),

    (Lighting.shade, 1): _ParamPreset(50, "f/1.8", "1/200", 400, 5500, +0.3, IphoneLens.tele_2x),
    (Lighting.shade, 2): _ParamPreset(35, "f/2.0", "1/200", 400, 5500, +0.3, IphoneLens.wide_1x),
    (Lighting.shade, 3): _ParamPreset(28, "f/2.8", "1/200", 800, 5500, +0.3, IphoneLens.wide_1x),

    (Lighting.indoor_warm, 1): _ParamPreset(35, "f/1.8", "1/125", 800, 3200, 0.0, IphoneLens.wide_1x),
    (Lighting.indoor_warm, 2): _ParamPreset(35, "f/2.0", "1/125", 1600, 3200, 0.0, IphoneLens.wide_1x),
    (Lighting.indoor_warm, 3): _ParamPreset(28, "f/2.8", "1/125", 1600, 3200, 0.0, IphoneLens.wide_1x),

    (Lighting.indoor_cool, 1): _ParamPreset(35, "f/1.8", "1/125", 800, 4500, 0.0, IphoneLens.wide_1x),
    (Lighting.indoor_cool, 2): _ParamPreset(35, "f/2.0", "1/125", 1600, 4500, 0.0, IphoneLens.wide_1x),
    (Lighting.indoor_cool, 3): _ParamPreset(28, "f/2.8", "1/125", 1600, 4500, 0.0, IphoneLens.wide_1x),

    (Lighting.low_light, 1): _ParamPreset(28, "f/1.8", "1/60", 3200, 3200, +0.3, IphoneLens.wide_1x),
    (Lighting.low_light, 2): _ParamPreset(28, "f/1.8", "1/60", 3200, 3200, +0.3, IphoneLens.wide_1x),
    (Lighting.low_light, 3): _ParamPreset(24, "f/2.0", "1/60", 6400, 3200, +0.3, IphoneLens.ultrawide_0_5x),

    (Lighting.backlight, 1): _ParamPreset(50, "f/2.0", "1/500", 200, 5500, +0.7, IphoneLens.tele_2x),
    (Lighting.backlight, 2): _ParamPreset(50, "f/2.2", "1/500", 200, 5500, +0.7, IphoneLens.tele_2x),
    (Lighting.backlight, 3): _ParamPreset(35, "f/2.8", "1/500", 200, 5500, +0.7, IphoneLens.wide_1x),

    (Lighting.mixed, 1): _ParamPreset(35, "f/2.0", "1/250", 400, 5000, 0.0, IphoneLens.wide_1x),
    (Lighting.mixed, 2): _ParamPreset(35, "f/2.0", "1/250", 400, 5000, 0.0, IphoneLens.wide_1x),
    (Lighting.mixed, 3): _ParamPreset(28, "f/2.8", "1/250", 800, 5000, 0.0, IphoneLens.wide_1x),
}


def _bucket(person_count: int) -> int:
    if person_count <= 1:
        return 1
    if person_count == 2:
        return 2
    return 3


def preset_for(lighting: Lighting, person_count: int) -> _ParamPreset:
    return _PRESETS.get(
        (lighting, _bucket(person_count)),
        _PRESETS[(Lighting.overcast, _bucket(person_count))],
    )


def synthesize_camera_settings(
    lighting: Lighting, person_count: int, rationale: Optional[str] = None
) -> CameraSettings:
    p = preset_for(lighting, person_count)
    return CameraSettings(
        focal_length_mm=p.focal_length_mm,
        aperture=p.aperture,
        shutter=p.shutter,
        iso=p.iso,
        white_balance_k=p.wb_k,
        ev_compensation=p.ev,
        rationale=rationale or _default_rationale(lighting, person_count),
        device_hints=DeviceHints(iphone_lens=p.iphone_lens),
    )


def repair_camera_settings(
    cam: CameraSettings, lighting: Lighting, person_count: int
) -> CameraSettings:
    """Clamp obviously-wrong LLM output back into a safe range, filling holes
    with the deterministic preset."""
    fallback = synthesize_camera_settings(lighting, person_count)
    data = cam.model_dump()
    for k, default_val in fallback.model_dump().items():
        if data.get(k) in (None, ""):
            data[k] = default_val

    if not (14 <= data["focal_length_mm"] <= 200):
        data["focal_length_mm"] = fallback.focal_length_mm
    if not (50 <= data["iso"] <= 12800):
        data["iso"] = fallback.iso

    return CameraSettings.model_validate(data)


def _default_rationale(lighting: Lighting, person_count: int) -> str:
    base = {
        Lighting.golden_hour: "黄金时段侧逆光，长焦压缩 + 浅景深突出主体",
        Lighting.blue_hour: "蓝调时段光线弱，开大光圈 + 提 ISO 保留氛围",
        Lighting.harsh_noon: "正午顶光对比强，欠曝半档保高光，建议找阴影",
        Lighting.overcast: "阴天光线柔和均匀，标准曝光即可",
        Lighting.shade: "阴影中色温偏冷，正补偿 + 抬色温",
        Lighting.indoor_warm: "暖色室内灯，色温压低 + 大光圈拉氛围",
        Lighting.indoor_cool: "冷色室内灯，色温拉中性",
        Lighting.low_light: "弱光场景，使用最大光圈和较高 ISO，保持安全快门",
        Lighting.backlight: "逆光下使用正补偿避免人物过暗",
        Lighting.mixed: "混合光线，选最干净光源做主光",
    }.get(lighting, "标准室外曝光")
    suffix = "" if person_count <= 1 else f"，{person_count} 人合影需保证景深覆盖所有人"
    return base + suffix
