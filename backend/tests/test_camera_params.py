from app.models import CameraSettings, DeviceHints, IphoneLens, Lighting
from app.services.camera_params import (
    preset_for,
    repair_camera_settings,
    synthesize_camera_settings,
)


def test_preset_for_known_combo():
    p = preset_for(Lighting.golden_hour, 2)
    assert p.focal_length_mm == 50
    assert p.aperture == "f/2.0"


def test_synthesize_returns_valid_camera_settings():
    cs = synthesize_camera_settings(Lighting.golden_hour, 2)
    assert cs.focal_length_mm == 50
    assert cs.iso == 200
    assert cs.device_hints is not None
    assert cs.device_hints.iphone_lens == IphoneLens.tele_2x


def test_repair_fills_missing_fields():
    bad = CameraSettings(
        focal_length_mm=50,
        aperture="f/2.0",
        shutter="1/250",
        iso=100,
    )
    repaired = repair_camera_settings(bad, Lighting.harsh_noon, 1)
    assert repaired.white_balance_k is not None
    assert repaired.ev_compensation is not None


def test_repair_clamps_out_of_range_iso():
    bad = CameraSettings(
        focal_length_mm=50,
        aperture="f/2.0",
        shutter="1/250",
        iso=50,
    )
    bad_dict = bad.model_dump()
    bad_dict["iso"] = 100000  # invalid; repair should clamp to fallback
    bad2 = CameraSettings.model_construct(**bad_dict)
    fixed = repair_camera_settings(bad2, Lighting.golden_hour, 1)
    assert 50 <= fixed.iso <= 12800
