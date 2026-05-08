"""Sun position computation — pure local math, no external API.

We use a simplified subset of NOAA's Solar Position Algorithm (the
spreadsheet variant, 1900-2100 valid range, ±0.5° accuracy at temperate
latitudes — perfectly fine for "photographer's golden-hour timing").

References:
  - https://gml.noaa.gov/grad/solcalc/calcdetails.html (NOAA)
  - https://en.wikipedia.org/wiki/Position_of_the_Sun

Why we keep it local:
  - Photo planning happens at sub-second cadence (camera adjustments,
    rim-light planning). A network round trip would feel sluggish.
  - Privacy: raw lat/lng never leaves the device for this feature.
  - Cost: zero. We can call this on every analyze request.

Public API:
  - ``compute(lat, lon, ts_utc) -> SunInfo``
  - ``infer_phase(altitude, kind) -> str``  (golden / blue / day / night)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


# Phases used in the prompt + UI. "kind" = "rising" / "setting" derived from
# whether altitude is increasing.
PHASE_NIGHT = "night"
PHASE_BLUE_DAWN = "blue_hour_dawn"
PHASE_GOLDEN_DAWN = "golden_hour_dawn"
PHASE_DAY = "day"
PHASE_GOLDEN_DUSK = "golden_hour_dusk"
PHASE_BLUE_DUSK = "blue_hour_dusk"


@dataclass(frozen=True, slots=True)
class SunInfo:
    """Result of a sun-position query for a single (lat, lon, t) tuple."""
    azimuth_deg: float           # 0 = north, 90 = east, 180 = south, 270 = west
    altitude_deg: float          # below horizon -> negative
    phase: str                   # one of the PHASE_* constants above
    color_temp_k_estimate: int   # rough Kelvin (warm 2700 → 5500 → 6500)
    minutes_to_golden_end: float | None      # only meaningful in golden phase
    minutes_to_blue_end: float | None        # only meaningful in blue phase
    minutes_to_sunset: float | None          # positive while sun is up
    minutes_to_sunrise: float | None         # positive while sun is down
    declination_deg: float       # for the 3D viewer / dev-only
    hour_angle_deg: float        # for the 3D viewer / dev-only

    def to_dict(self) -> dict:
        return {
            "azimuth_deg": round(self.azimuth_deg, 2),
            "altitude_deg": round(self.altitude_deg, 2),
            "phase": self.phase,
            "color_temp_k_estimate": self.color_temp_k_estimate,
            "minutes_to_golden_end": _round_or_none(self.minutes_to_golden_end),
            "minutes_to_blue_end": _round_or_none(self.minutes_to_blue_end),
            "minutes_to_sunset": _round_or_none(self.minutes_to_sunset),
            "minutes_to_sunrise": _round_or_none(self.minutes_to_sunrise),
            "declination_deg": round(self.declination_deg, 3),
            "hour_angle_deg": round(self.hour_angle_deg, 3),
        }


def _round_or_none(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


# ---------------------------------------------------------------------------
# Core algorithm (NOAA spreadsheet variant)
# ---------------------------------------------------------------------------


def _julian_day(t: datetime) -> float:
    """UTC datetime -> Julian Day. Astronomical convention (JD 2451545.0 = 2000-01-01 12:00 UT)."""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    t = t.astimezone(timezone.utc)
    # Fractional day part
    frac = (t.hour + t.minute / 60 + t.second / 3600) / 24
    y, m, d = t.year, t.month, t.day
    if m <= 2:
        y -= 1
        m += 12
    A = y // 100
    B = 2 - A + A // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + B - 1524.5 + frac


def _solar_position(lat_deg: float, lon_deg: float, t_utc: datetime) -> tuple[float, float, float, float]:
    """Compute (azimuth_deg, altitude_deg, declination_deg, hour_angle_deg).

    Implementation follows NOAA's "Solar Calculation Details" spreadsheet,
    accurate to roughly ±0.5° between 1900 and 2100.
    """
    jd = _julian_day(t_utc)
    century = (jd - 2451545.0) / 36525.0  # Julian centuries from 2000-01-01 12:00 UT

    # Geometric mean longitude (deg)
    geom_mean_long = (280.46646 + century * (36000.76983 + century * 0.0003032)) % 360
    # Geometric mean anomaly (deg)
    geom_mean_anom = 357.52911 + century * (35999.05029 - 0.0001537 * century)
    # Earth eccentricity (unitless)
    eccentricity = 0.016708634 - century * (0.000042037 + 0.0000001267 * century)

    # Sun equation of center
    sin1 = math.sin(math.radians(geom_mean_anom))
    sin2 = math.sin(math.radians(2 * geom_mean_anom))
    sin3 = math.sin(math.radians(3 * geom_mean_anom))
    eqn_center = (
        sin1 * (1.914602 - century * (0.004817 + 0.000014 * century))
        + sin2 * (0.019993 - 0.000101 * century)
        + sin3 * 0.000289
    )

    sun_true_long = geom_mean_long + eqn_center
    omega = 125.04 - 1934.136 * century
    sun_app_long = sun_true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))

    mean_obliq_ecliptic = 23 + (26 + (21.448 - century * (46.815 + century * (0.00059 - 0.001813 * century))) / 60) / 60
    obliq_corr = mean_obliq_ecliptic + 0.00256 * math.cos(math.radians(omega))

    sun_decl_deg = math.degrees(
        math.asin(
            math.sin(math.radians(obliq_corr)) * math.sin(math.radians(sun_app_long))
        )
    )

    var_y = math.tan(math.radians(obliq_corr / 2)) ** 2

    eqn_of_time_min = 4 * math.degrees(
        var_y * math.sin(2 * math.radians(geom_mean_long))
        - 2 * eccentricity * math.sin(math.radians(geom_mean_anom))
        + 4 * eccentricity * var_y * math.sin(math.radians(geom_mean_anom)) * math.cos(2 * math.radians(geom_mean_long))
        - 0.5 * var_y * var_y * math.sin(4 * math.radians(geom_mean_long))
        - 1.25 * eccentricity * eccentricity * math.sin(2 * math.radians(geom_mean_anom))
    )

    # True solar time (minutes)
    minutes_utc = t_utc.hour * 60 + t_utc.minute + t_utc.second / 60
    true_solar_time_min = (minutes_utc + eqn_of_time_min + 4 * lon_deg) % 1440

    hour_angle_deg = (true_solar_time_min / 4) - 180
    if hour_angle_deg < -180:
        hour_angle_deg += 360

    lat_rad = math.radians(lat_deg)
    decl_rad = math.radians(sun_decl_deg)
    ha_rad = math.radians(hour_angle_deg)

    cos_zenith = math.sin(lat_rad) * math.sin(decl_rad) + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(ha_rad)
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    zenith_rad = math.acos(cos_zenith)
    altitude_deg = 90 - math.degrees(zenith_rad)

    cos_az_num = (math.sin(decl_rad) - cos_zenith * math.sin(lat_rad))
    cos_az_den = math.sin(zenith_rad) * math.cos(lat_rad)
    if abs(cos_az_den) < 1e-9:
        # Sun directly overhead — azimuth is undefined; fall back to 180 (south).
        az_deg = 180.0
    else:
        cos_az = max(-1.0, min(1.0, cos_az_num / cos_az_den))
        az_deg = math.degrees(math.acos(cos_az))
        if hour_angle_deg > 0:
            az_deg = (az_deg + 180) % 360
        else:
            az_deg = (540 - az_deg) % 360

    return az_deg, altitude_deg, sun_decl_deg, hour_angle_deg


# ---------------------------------------------------------------------------
# Higher-level helpers (phase, color temp, countdowns)
# ---------------------------------------------------------------------------


def _classify_phase(altitude_deg: float, rising: bool) -> str:
    """Map altitude to a photographic phase. Thresholds match common
    photographer rules of thumb (golden hour is altitude < 6°, blue hour
    is below the horizon to about -6°).
    """
    if altitude_deg < -6:
        return PHASE_NIGHT
    if altitude_deg < 0:
        return PHASE_BLUE_DAWN if rising else PHASE_BLUE_DUSK
    if altitude_deg < 6:
        return PHASE_GOLDEN_DAWN if rising else PHASE_GOLDEN_DUSK
    return PHASE_DAY


def _color_temp(altitude_deg: float, phase: str) -> int:
    """Very rough Kelvin estimate. Real value varies with weather, smog,
    altitude — but for prompt purposes this is enough."""
    if phase == PHASE_NIGHT:
        return 4000  # ambient / streetlight territory
    if phase in (PHASE_BLUE_DAWN, PHASE_BLUE_DUSK):
        return 9000  # blue hour really is blue
    if phase in (PHASE_GOLDEN_DAWN, PHASE_GOLDEN_DUSK):
        # Sun close to horizon = warm. Lower altitude = warmer.
        # Linear blend: alt=0 → 2800K, alt=6 → 4500K
        t = max(0.0, min(altitude_deg, 6.0)) / 6.0
        return int(2800 + (4500 - 2800) * t)
    # Day
    return 5500


def _is_rising(lat: float, lon: float, t: datetime) -> bool:
    """Determine sun rising vs setting by sampling 5 min later."""
    later = t + timedelta(minutes=5)
    _, alt_now, *_ = _solar_position(lat, lon, t)
    _, alt_later, *_ = _solar_position(lat, lon, later)
    return alt_later > alt_now


def _countdown_to_altitude(
    lat: float, lon: float, t: datetime, target_alt_deg: float, *,
    direction: int, max_minutes: float = 240,
) -> float | None:
    """How many minutes until the sun's altitude crosses ``target_alt_deg``?

    direction = +1 means we're waiting for altitude to *fall* through it
    (used for sunset / golden-hour-ends countdowns).
    direction = -1 means we're waiting for altitude to *rise* through it.
    Returns None if not reached within ``max_minutes`` (e.g. polar regions
    or wrong phase).
    """
    step = 1.0  # minute
    last_alt = None
    for k in range(int(max_minutes / step)):
        sample_t = t + timedelta(minutes=k * step)
        _, alt, *_ = _solar_position(lat, lon, sample_t)
        if last_alt is not None:
            if direction > 0 and last_alt > target_alt_deg >= alt:
                return k * step
            if direction < 0 and last_alt < target_alt_deg <= alt:
                return k * step
        last_alt = alt
    return None


def compute(lat: float, lon: float, t_utc: datetime | None = None) -> SunInfo:
    """Compute everything we need about the sun at this location and time."""
    t_utc = t_utc or datetime.now(timezone.utc)
    az, alt, decl, ha = _solar_position(lat, lon, t_utc)
    rising = _is_rising(lat, lon, t_utc)
    phase = _classify_phase(alt, rising)
    color = _color_temp(alt, phase)

    # Countdowns
    minutes_to_sunset = _countdown_to_altitude(lat, lon, t_utc, 0, direction=+1) if alt > 0 else None
    minutes_to_sunrise = _countdown_to_altitude(lat, lon, t_utc, 0, direction=-1) if alt < 0 else None

    minutes_to_golden_end: float | None = None
    if phase == PHASE_GOLDEN_DUSK:
        # Golden hour ends when the sun drops below the horizon.
        minutes_to_golden_end = minutes_to_sunset
    elif phase == PHASE_GOLDEN_DAWN:
        # Golden hour ends when altitude rises above 6°.
        minutes_to_golden_end = _countdown_to_altitude(lat, lon, t_utc, 6, direction=-1)

    minutes_to_blue_end: float | None = None
    if phase == PHASE_BLUE_DUSK:
        minutes_to_blue_end = _countdown_to_altitude(lat, lon, t_utc, -6, direction=+1)
    elif phase == PHASE_BLUE_DAWN:
        # Blue hour ends when altitude rises above 0° (sunrise).
        minutes_to_blue_end = minutes_to_sunrise

    return SunInfo(
        azimuth_deg=az,
        altitude_deg=alt,
        phase=phase,
        color_temp_k_estimate=color,
        minutes_to_golden_end=minutes_to_golden_end,
        minutes_to_blue_end=minutes_to_blue_end,
        minutes_to_sunset=minutes_to_sunset,
        minutes_to_sunrise=minutes_to_sunrise,
        declination_deg=decl,
        hour_angle_deg=ha,
    )


# ---------------------------------------------------------------------------
# Pretty-print for prompt injection
# ---------------------------------------------------------------------------


def to_prompt_block(info: SunInfo, lat: float, lon: float) -> str:
    """Format SunInfo as a human-readable block we can paste into the LLM
    prompt under "ENVIRONMENT FACTS"."""
    lines = [
        f"  · 当前位置：lat {lat:.4f}, lon {lon:.4f}",
        f"  · 太阳方位角 (azimuth)：{info.azimuth_deg:.0f}°  (0=北 / 90=东 / 180=南 / 270=西)",
        f"  · 太阳高度角 (altitude)：{info.altitude_deg:.0f}°",
        f"  · 当前时段：{_phase_zh(info.phase)}",
        f"  · 估算色温：约 {info.color_temp_k_estimate}K",
    ]
    if info.minutes_to_golden_end:
        lines.append(f"  · 距「黄金时刻」结束还有：{info.minutes_to_golden_end:.0f} 分钟（光线会快速衰减，时间敏感）")
    if info.minutes_to_blue_end:
        lines.append(f"  · 距「蓝调时刻」结束还有：{info.minutes_to_blue_end:.0f} 分钟")
    if info.minutes_to_sunset and info.altitude_deg > 6:
        lines.append(f"  · 距日落还有：{info.minutes_to_sunset:.0f} 分钟")
    return "\n".join(lines)


_PHASE_ZH = {
    PHASE_NIGHT: "夜间（无自然光）",
    PHASE_BLUE_DAWN: "蓝调时刻（清晨）",
    PHASE_GOLDEN_DAWN: "黄金时刻（清晨）",
    PHASE_DAY: "白天",
    PHASE_GOLDEN_DUSK: "黄金时刻（傍晚）",
    PHASE_BLUE_DUSK: "蓝调时刻（傍晚）",
}


def _phase_zh(phase: str) -> str:
    return _PHASE_ZH.get(phase, phase)
