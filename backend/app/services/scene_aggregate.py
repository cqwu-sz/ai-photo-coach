"""Cross-frame scene aggregation.

The user's environment scan arrives as 4-16 keyframes, each tagged with
azimuth + per-frame signals (mean_luma, blur_score, person_box,
saliency_quadrant, horizon_tilt_deg). Individually these are weak; the
LLM essentially has to read 10 numbers and form a mental map. This
module does the cross-frame integration we want — turning per-frame
columns into "fact summaries the LLM can quote without inspecting the
JPEGs":

  * lighting_axis    — which azimuth is brightest (≈ main light source);
                       which is darkest (probable shadow side); how big
                       the contrast is.
  * geometry_axis    — which azimuth has the highest edge density (busy
                       background, leading lines, cityscape) vs lowest
                       (clean negative space).
  * person_axis      — which azimuth(s) had a person detected and the
                       largest box, so the LLM knows where the subject
                       was actually seen during the scan.
  * level_indicator  — average horizon tilt with a "tripod-needed?" hint
                       when median |tilt| > 5°.
  * salience_layout  — which quadrant of the frames most often holds
                       the visual centre of mass (helps decide rule-of-
                       thirds bias / where to place subject).

Output is a single Markdown-ish text block ready for the prompt builder
to drop under ENVIRONMENT FACTS. None of these facts override what the
LLM sees, but they replace "look closely at frame 4" with "azimuth 245°
is the brightest direction by 1.6× over the mean; aim rim-light shots
that way".
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

from ..models import FrameMeta


# Azimuth → compass label, matching the convention used elsewhere
# (0=N / 90=E / 180=S / 270=W). Used for human-readable summaries.
def _azimuth_label_zh(deg: float) -> str:
    deg = deg % 360
    table = [
        (22.5,  "正北"),
        (67.5,  "东北"),
        (112.5, "正东"),
        (157.5, "东南"),
        (202.5, "正南"),
        (247.5, "西南"),
        (292.5, "正西"),
        (337.5, "西北"),
    ]
    for upper, label in table:
        if deg < upper:
            return label
    return "正北"


@dataclass(frozen=True, slots=True)
class ForegroundFact:
    """One usable foreground element seen during the scan."""
    azimuth_deg: float
    label: str
    quadrant: str          # canvas position (top_left/.../bottom_edge/...)
    distance_m: Optional[float]


@dataclass(frozen=True, slots=True)
class SceneAggregate:
    """Structured aggregate result; converted to prompt text below."""
    n_frames: int
    azimuth_span_deg: float
    brightest_azimuth: Optional[float]
    darkest_azimuth: Optional[float]
    luma_contrast_ratio: float            # max / mean luma; > 1.5 = contrasty
    busiest_azimuth: Optional[float]      # by blur_score (sharpness proxy)
    cleanest_azimuth: Optional[float]
    person_azimuths: list[float]          # frames where person_box present
    largest_person_azimuth: Optional[float]
    largest_person_area: float            # in [0, 1]
    median_horizon_tilt_deg: Optional[float]
    needs_leveling: bool
    saliency_distribution: dict[str, int] # quadrant → count
    dominant_quadrant: Optional[str]
    foreground_facts: list[ForegroundFact]   # FOREGROUND DOCTRINE input
    near_depth_pct: Optional[float]          # avg near_pct across frames
    far_depth_pct: Optional[float]
    depth_source: Optional[str]              # midas_web / midas_ios / avdepth_*
    # ---- v10 lens + tilt ----------------------------------------
    subject_distance_m: Optional[float]      # from heightRatio model
    subject_height_ratio: Optional[float]    # ankle_y - nose_y, 0..1
    recommended_lens: Optional[str]          # ultrawide_13 / main_26 / main_2x / tele_77
    lens_rationale_zh: Optional[str]         # one-line explanation
    median_pitch_deg: Optional[float]        # from frame_meta.pitch_deg
    median_pose_center_y: Optional[float]    # (nose_y + ankle_y) / 2
    tilt_advice_zh: Optional[str]            # 蹲下 / 举高 / 平举 / unknown
    # ---- v12 horizon triangulation + fine pose ---------------------
    horizon_consensus_y: Optional[float]     # multi-source vote
    horizon_confidence: Optional[str]        # "high" / "medium" / "low" / "none"
    sky_present: bool
    pose_facts_zh: tuple[str, ...]           # human-readable pose advice
    # ---- v12 composition metrics (Sprint 4) ------------------------
    rule_of_thirds_dist: Optional[float]     # subject centre to nearest 1/3 point (norm)
    symmetry_score: Optional[float]          # 0=asymmetric .. 1=mirrored
    composition_facts_zh: tuple[str, ...]    # rendered for the LLM
    # ---- v11 lighting ---------------------------------------------
    cct_k: Optional[int]
    tint: Optional[float]
    dynamic_range: Optional[str]
    light_direction: Optional[str]
    highlight_clip_pct: Optional[float]
    shadow_clip_pct: Optional[float]
    lighting_notes: tuple[str, ...]


def aggregate(
    frames: Iterable[FrameMeta],
    sun_azimuth_deg: Optional[float] = None,
) -> Optional[SceneAggregate]:
    """Build a SceneAggregate from a list of FrameMeta.

    Returns None when there are < 3 frames or no usable signals — the
    prompt builder treats that as "skip the SCENE INSIGHTS block".
    """
    frames = list(frames)
    if len(frames) < 3:
        return None

    azimuths = [f.azimuth_deg for f in frames]
    span = _azimuth_span(azimuths)

    # ---- luma axis ----------------------------------------------------
    luma_pairs = [(f.azimuth_deg, f.mean_luma) for f in frames if f.mean_luma is not None]
    brightest_az = darkest_az = None
    luma_contrast = 1.0
    if len(luma_pairs) >= 3:
        max_az, max_l = max(luma_pairs, key=lambda p: p[1])
        min_az, min_l = min(luma_pairs, key=lambda p: p[1])
        mean_l = statistics.fmean(p[1] for p in luma_pairs) or 1e-6
        brightest_az = round(max_az, 1)
        darkest_az = round(min_az, 1)
        luma_contrast = round(max_l / mean_l, 2)

    # ---- edge axis (using blur_score as a sharpness proxy; higher
    # sharpness == busier background details, so not a perfect "edge
    # density" estimator but works in practice — flat sky → low score). -
    edge_pairs = [(f.azimuth_deg, f.blur_score) for f in frames if f.blur_score is not None]
    busiest_az = cleanest_az = None
    if len(edge_pairs) >= 3:
        busiest_az = round(max(edge_pairs, key=lambda p: p[1])[0], 1)
        cleanest_az = round(min(edge_pairs, key=lambda p: p[1])[0], 1)

    # ---- person axis --------------------------------------------------
    person_frames = [f for f in frames if f.person_box]
    person_azs = [round(f.azimuth_deg, 1) for f in person_frames]
    largest_person_az = None
    largest_person_area = 0.0
    if person_frames:
        # box = [x, y, w, h] in 0..1
        big = max(person_frames, key=lambda f: (f.person_box[2] * f.person_box[3]))
        largest_person_az = round(big.azimuth_deg, 1)
        largest_person_area = round(big.person_box[2] * big.person_box[3], 3)

    # ---- horizon ------------------------------------------------------
    tilts = [f.horizon_tilt_deg for f in frames if f.horizon_tilt_deg is not None]
    median_tilt = round(statistics.median(tilts), 1) if tilts else None
    needs_level = median_tilt is not None and abs(median_tilt) >= 5

    # ---- saliency layout ---------------------------------------------
    sal = Counter(f.saliency_quadrant for f in frames if f.saliency_quadrant)
    dominant_quadrant = sal.most_common(1)[0][0] if sal else None

    # ---- foreground candidates --------------------------------------
    # Flatten all per-frame candidates and keep the best (largest box,
    # nearest distance when known) per (azimuth, label) pair so the
    # prompt isn't drowning in 30 redundant "tree" entries.
    fg_facts = _collect_foreground(frames)

    # ---- depth layers ------------------------------------------------
    depth_frames = [f for f in frames if f.depth_layers is not None]
    near_pct = far_pct = None
    depth_source = None
    if depth_frames:
        near_pct = round(statistics.fmean(f.depth_layers.near_pct for f in depth_frames), 3)
        far_pct  = round(statistics.fmean(f.depth_layers.far_pct  for f in depth_frames), 3)
        # Pick the most-trusted source seen (LiDAR > dual > monocular).
        priority = {"avdepth_lidar": 3, "avdepth_dual": 2,
                    "midas_ios": 1, "midas_web": 1}
        depth_source = max(
            (f.depth_layers.source for f in depth_frames),
            key=lambda s: priority.get(s, 0),
        )

    # ---- subject consensus (multi-person disambiguation) -----------
    # When a passer-by drifts through 1-2 frames, their subject_box
    # would otherwise hijack lens/tilt for that frame. We cluster the
    # per-frame subject centres and only trust frames inside the
    # majority cluster for downstream metrics.
    consensus_frames = _filter_to_subject_consensus(frames)
    lens_pick = _pick_lens(consensus_frames)
    tilt_pick = _pick_tilt(consensus_frames)

    # ---- lighting (Sprint 1: cct / tint / DR / clipping + direction) ----
    lighting = _build_lighting(frames)
    light_dir = _light_direction_from_sun(consensus_frames, sun_azimuth_deg)
    # ---- horizon triangulation + pose facts (Sprint 2) ------------
    horizon_y, horizon_conf, sky_ok = _vote_horizon(frames)
    pose_facts = _build_pose_facts(consensus_frames)
    rot_dist, symmetry, comp_facts = _build_composition(consensus_frames)

    return SceneAggregate(
        n_frames=len(frames),
        azimuth_span_deg=round(span, 1),
        brightest_azimuth=brightest_az,
        darkest_azimuth=darkest_az,
        luma_contrast_ratio=luma_contrast,
        busiest_azimuth=busiest_az,
        cleanest_azimuth=cleanest_az,
        person_azimuths=person_azs,
        largest_person_azimuth=largest_person_az,
        largest_person_area=largest_person_area,
        median_horizon_tilt_deg=median_tilt,
        needs_leveling=needs_level,
        saliency_distribution=dict(sal),
        dominant_quadrant=dominant_quadrant,
        foreground_facts=fg_facts,
        near_depth_pct=near_pct,
        far_depth_pct=far_pct,
        depth_source=depth_source,
        subject_distance_m=lens_pick.distance_m,
        subject_height_ratio=lens_pick.height_ratio,
        recommended_lens=lens_pick.lens,
        lens_rationale_zh=lens_pick.rationale_zh,
        median_pitch_deg=tilt_pick.pitch_deg,
        median_pose_center_y=tilt_pick.center_y,
        tilt_advice_zh=tilt_pick.advice_zh,
        cct_k=lighting.cct_k,
        tint=lighting.tint,
        dynamic_range=lighting.dynamic_range,
        light_direction=light_dir,
        highlight_clip_pct=lighting.highlight_clip_pct,
        shadow_clip_pct=lighting.shadow_clip_pct,
        lighting_notes=tuple(lighting.notes),
        horizon_consensus_y=horizon_y,
        horizon_confidence=horizon_conf,
        sky_present=sky_ok,
        pose_facts_zh=tuple(pose_facts),
        rule_of_thirds_dist=rot_dist,
        symmetry_score=symmetry,
        composition_facts_zh=tuple(comp_facts),
    )


from . import color_science


# ---------------------------------------------------------------------------
# Lighting aggregation (Sprint 1)
# ---------------------------------------------------------------------------
def _build_lighting(frames: list[FrameMeta]) -> color_science.LightingAggregate:
    stats = []
    for f in frames:
        stats.append(color_science.FrameLightingStats(
            rgb_mean=f.rgb_mean,
            luma_mean=f.mean_luma,
            luma_p05=f.luma_p05,
            luma_p95=f.luma_p95,
            highlight_clip_pct=f.highlight_clip_pct,
            shadow_clip_pct=f.shadow_clip_pct,
        ))
    return color_science.aggregate_lighting(stats)


def _vote_horizon(frames: list[FrameMeta]) -> tuple[Optional[float], Optional[str], bool]:
    """Triangulate the horizon line from up to three independent
    sources: image-gradient row (`horizon_y`), Vision pose-derived
    (`horizon_y_vision`), and gyro-pitch implied (only used when sky
    is detected). Returns (consensus_y, confidence, sky_ok).
    """
    sky_ratios = [f.sky_mask_top_pct for f in frames if f.sky_mask_top_pct is not None]
    sky_ok = bool(sky_ratios) and (statistics.median(sky_ratios) > 0.05)

    if not sky_ok:
        # No sky — horizon is meaningless / will pick random walls.
        return (None, "none", sky_ok)

    sources: list[tuple[str, float]] = []
    img_ys = [f.horizon_y for f in frames if f.horizon_y is not None]
    if img_ys:
        sources.append(("image", statistics.median(img_ys)))
    vis_ys = [f.horizon_y_vision for f in frames if f.horizon_y_vision is not None]
    if vis_ys:
        sources.append(("vision", statistics.median(vis_ys)))
    grav_ys = [f.horizon_y_gravity for f in frames if f.horizon_y_gravity is not None]
    if grav_ys:
        sources.append(("gravity", statistics.median(grav_ys)))
    if not sources:
        return (None, "low", sky_ok)

    if len(sources) == 1:
        return (round(sources[0][1], 3), "low", sky_ok)

    if len(sources) == 2:
        a, b = sources[0][1], sources[1][1]
        if abs(a - b) <= 0.10:
            return (round((a + b) / 2, 3), "high", sky_ok)
        return (round((a + b) / 2, 3), "low", sky_ok)

    # Three sources — pick the 2-of-3 majority cluster (any pair within
    # 0.10). High confidence if all three agree; medium if two agree;
    # low if all three disagree (then return median).
    vals = sorted(s[1] for s in sources)
    pairs = [(vals[0], vals[1]), (vals[1], vals[2]), (vals[0], vals[2])]
    close = [(a, b) for a, b in pairs if abs(a - b) <= 0.10]
    if len(close) == 3:
        return (round(statistics.median(vals), 3), "high", sky_ok)
    if close:
        a, b = close[0]
        return (round((a + b) / 2, 3), "medium", sky_ok)
    return (round(statistics.median(vals), 3), "low", sky_ok)


def _build_composition(frames: list[FrameMeta]) -> tuple[Optional[float], Optional[float], list[str]]:
    """Sprint 4 composition metrics:
        rule_of_thirds_dist — distance from subject centre to nearest
            of the four 1/3 intersections, normalised by frame diagonal.
        symmetry_score — 1 - |subject_x_offset_from_centre| × 2, so a
            perfectly centred subject scores 1.0.
    Both fall back to None when no consistent subject_box was tracked.
    Composition facts are emitted only when the metrics suggest a
    clear improvement opportunity.
    """
    centres: list[tuple[float, float]] = []
    for f in frames:
        b = f.subject_box
        if b and len(b) == 4:
            centres.append((b[0] + b[2] / 2, b[1] + b[3] / 2))
    if not centres:
        return (None, None, [])
    cx = statistics.median(c[0] for c in centres)
    cy = statistics.median(c[1] for c in centres)

    thirds = [(1/3, 1/3), (2/3, 1/3), (1/3, 2/3), (2/3, 2/3)]
    rot_dist = min(math.hypot(cx - tx, cy - ty) for tx, ty in thirds)
    rot_dist = round(rot_dist / math.sqrt(2), 3)   # normalise to [0, 1]

    symmetry = round(max(0.0, 1.0 - abs(cx - 0.5) * 2), 3)

    facts: list[str] = []
    if rot_dist > 0.15:
        # Recommend the *closer* third point.
        closest = min(thirds, key=lambda p: math.hypot(cx - p[0], cy - p[1]))
        side_x = "左" if closest[0] < 0.5 else "右"
        side_y = "上" if closest[1] < 0.5 else "下"
        facts.append(
            f"主体当前居中（symmetry={symmetry}），距离三分点偏远 ({rot_dist})。"
            f"如果不是对称构图，建议把主体往画面{side_x}{side_y}三分点靠近。"
        )
    if symmetry > 0.92 and rot_dist > 0.10:
        facts.append(
            f"主体高度居中（symmetry={symmetry}）— 适合对称构图，但记得"
            f"保留左右等量的负空间，避免被路人切边。"
        )
    return (rot_dist, symmetry, facts)


def _build_pose_facts(frames: list[FrameMeta]) -> list[str]:
    """Translate fine pose stats into Chinese cues. Each axis only
    fires if the median over consensus frames breaches a threshold
    (avoid noise from single-frame mis-detections)."""
    facts: list[str] = []
    shoulders = [f.shoulder_tilt_deg for f in frames if f.shoulder_tilt_deg is not None]
    hips      = [f.hip_offset_x      for f in frames if f.hip_offset_x      is not None]
    chins     = [f.chin_forward      for f in frames if f.chin_forward      is not None]
    spines    = [f.spine_curve       for f in frames if f.spine_curve       is not None]
    if shoulders:
        m = statistics.median(shoulders)
        if abs(m) > 5:
            side = "右肩偏高" if m > 0 else "左肩偏高"
            facts.append(f"肩线倾斜约 {abs(int(m))}°（{side}）— 提示主体放松或调整重心，让肩平。")
    if hips:
        m = statistics.median(hips)
        if abs(m) > 0.10:
            side = "右" if m > 0 else "左"
            facts.append(f"重心偏{side}（hip {m:+.2f}）— 让主体把重心移到另一只脚或重新站位。")
    if chins:
        m = statistics.median(chins)
        if abs(m) > 0.10:
            facts.append(f"下颌前伸 {m:+.2f} 倍肩宽 — 提醒主体把下巴轻微后收，避免「探头脖」。")
    if spines:
        m = statistics.median(spines)
        if m > 0.05:
            facts.append(f"脊柱有明显弯曲（curvature={m:.3f}）— 让主体伸直背、深呼一口气再拍。")
    return facts


def _light_direction_from_sun(
    frames: list[FrameMeta], sun_azimuth: Optional[float]
) -> Optional[str]:
    """Use the captured sun_azimuth (CaptureMeta-level) and the median
    frame azimuth where the subject lives to label the lighting as
    front / side / back. None when sun info is unavailable.
    """
    if sun_azimuth is None:
        return None
    subj_azs = [f.azimuth_deg for f in frames if f.subject_box and f.azimuth_deg is not None]
    if not subj_azs:
        return None
    cam_az = statistics.median(subj_azs)
    # The user is looking *at* the subject, so the camera-to-subject
    # axis is roughly cam_az. Sun behind camera (delta < 60°) = front
    # light; sun roughly perpendicular (60..120°) = side; sun in front
    # of camera (>120°) = back lighting.
    delta = abs((sun_azimuth - cam_az + 540) % 360 - 180)
    if delta < 60:  return "front"
    if delta < 120: return "side"
    return "back"


# ---------------------------------------------------------------------------
# Subject consensus across frames
# ---------------------------------------------------------------------------
def _filter_to_subject_consensus(frames: list[FrameMeta]) -> list[FrameMeta]:
    """Cluster the per-frame `subject_box` centres into rough buckets
    (Manhattan distance ≤ 0.20 in normalised coords) and keep only the
    frames belonging to the largest cluster. When fewer than 3 frames
    have subject_box data, just pass through (not enough signal to
    cluster). Frames without subject_box are always preserved — their
    pose / face fields might still be useful, we just can't tell who
    they are tracking.
    """
    indexed = []
    for i, f in enumerate(frames):
        sb = f.subject_box
        if sb and len(sb) == 4:
            cx = sb[0] + sb[2] / 2
            cy = sb[1] + sb[3] / 2
            indexed.append((i, cx, cy))
    if len(indexed) < 3:
        return frames

    # Greedy 1D cluster: O(n^2) is fine for n=10.
    clusters: list[list[int]] = []
    centres: list[tuple[float, float]] = []
    for i, cx, cy in indexed:
        joined = False
        for k, (ccx, ccy) in enumerate(centres):
            if abs(cx - ccx) + abs(cy - ccy) <= 0.20:
                clusters[k].append(i)
                # Update centroid (running mean).
                n = len(clusters[k])
                centres[k] = ((ccx * (n - 1) + cx) / n, (ccy * (n - 1) + cy) / n)
                joined = True
                break
        if not joined:
            clusters.append([i])
            centres.append((cx, cy))

    biggest = max(clusters, key=len)
    if len(biggest) < max(3, len(indexed) // 2):
        # No clear majority — don't filter; let the per-frame heuristics
        # do the best they can.
        return frames
    accepted = set(biggest)
    return [f for i, f in enumerate(frames)
            if (i in accepted) or (f.subject_box is None)]


# ---------------------------------------------------------------------------
# Lens / tilt helpers
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _LensPick:
    distance_m: Optional[float]
    height_ratio: Optional[float]
    lens: Optional[str]
    rationale_zh: Optional[str]


@dataclass(frozen=True, slots=True)
class _TiltPick:
    pitch_deg: Optional[float]
    center_y: Optional[float]
    advice_zh: Optional[str]


# Calibration constants. Body model: distance_m ≈ K_body / on-screen
# body-height ratio; K_body=1.25 is good to ±20% for an average 1.70 m
# human at a 26 mm-equivalent main lens. Face model: K_face=0.18 is
# tuned for an average 23 cm chin-to-crown face captured at the same
# lens (used when ankles are out of frame and the pose-based ratio
# would under-count).
_DISTANCE_K_BODY = 1.25
_DISTANCE_K_FACE = 0.18


def _k_face() -> float:
    """Return calibrated K_face if data/calibration.json overrides it,
    otherwise the in-source default. Hot-reloads on file change."""
    from . import calibration
    return calibration.current().k_face or _DISTANCE_K_FACE


def _k_body() -> float:
    from . import calibration
    return calibration.current().k_body or _DISTANCE_K_BODY

# Real-world subject sizes used by the EXIF-aware distance solver
# below. These are P50 adult averages; the calibration script
# (scripts/calibrate_distance.py) refines K_BODY/K_FACE empirically.
_REAL_FACE_HEIGHT_M = 0.23     # crown to chin
_REAL_BODY_HEIGHT_M = 1.70     # crown to floor


def _device_fov_h(f: FrameMeta) -> Optional[float]:
    """Return horizontal FOV in degrees from the frame's EXIF, or None.
    Prefers focal_length_mm + sensor_width_mm (true geometry); falls
    back to focal_length_35mm_eq using a 36mm reference width.
    """
    import math
    if f.focal_length_mm and f.sensor_width_mm and f.sensor_width_mm > 0:
        return 2 * math.degrees(math.atan(f.sensor_width_mm / (2 * f.focal_length_mm)))
    if f.focal_length_35mm_eq and f.focal_length_35mm_eq > 0:
        return 2 * math.degrees(math.atan(36.0 / (2 * f.focal_length_35mm_eq)))
    return None


def _solve_distance_with_fov(
    fov_deg: Optional[float],
    image_aspect: Optional[float],
    pixel_height_norm: float,
    real_world_m: float,
) -> Optional[float]:
    """Compute subject distance using real camera FOV instead of the
    empirical K. Returns metres, or None when FOV is unavailable.

    fov_deg is the *horizontal* FOV (what AVCaptureDevice exposes).
    Vertical FOV = 2 × atan(tan(hFOV/2) / image_aspect_ratio).
    """
    import math
    if not fov_deg or fov_deg <= 0:
        return None
    aspect = image_aspect or (16 / 9)   # video default
    h_fov_rad = fov_deg * math.pi / 180.0
    v_fov_rad = 2 * math.atan(math.tan(h_fov_rad / 2) / aspect)
    # Subject height in *world coords* at unit distance = 2 × tan(v_fov/2).
    # Subject's actual angular extent = pixel_height_norm × v_fov.
    # distance = real_world_m / (2 × tan(angular_half))
    angular_half = (pixel_height_norm * v_fov_rad) / 2.0
    if angular_half <= 0:
        return None
    return real_world_m / (2 * math.tan(angular_half))


def _pick_lens(frames: list[FrameMeta]) -> _LensPick:
    """Pick the frame that gives the sharpest distance estimate, in
    order of confidence: face_height_ratio (best for tight portraits)
    → pose ankle-nose body ratio → person_box height (rough fallback).
    Then map distance × ratio → one of the iPhone lens buckets.
    """
    candidates: list[tuple[float, float, str]] = []  # (ratio, distance_m, source)
    # Tier 1 — face bbox: most accurate for portrait framing.
    # When EXIF intrinsics are present we solve via real FOV, falling
    # back to the empirical K otherwise. Both paths land in the same
    # downstream bucket logic.
    for f in frames:
        if f.face_height_ratio and f.face_height_ratio > 0.01:
            r = f.face_height_ratio
            d_phys = _solve_distance_with_fov(
                f.focal_length_35mm_eq and (
                    # 35mm-eq FOV: hFOV = 2 × atan(36 / (2 × focal_eq))
                    2 * math.degrees(math.atan(36.0 / (2 * f.focal_length_35mm_eq)))
                ) if f.focal_length_35mm_eq else _device_fov_h(f),
                4/3, r, _REAL_FACE_HEIGHT_M,
            )
            d = d_phys if d_phys is not None else _k_face() / r
            equiv_body_ratio = min(1.0, r * 7.4)
            candidates.append((equiv_body_ratio, d, "face"))
    # Tier 2 — pose body ratio.
    if not candidates:
        for f in frames:
            if f.pose_nose_y is not None and f.pose_ankle_y is not None:
                ratio = f.pose_ankle_y - f.pose_nose_y
                if ratio > 0.05:
                    d_phys = _solve_distance_with_fov(
                        _device_fov_h(f), 4/3, ratio, _REAL_BODY_HEIGHT_M,
                    )
                    d = d_phys if d_phys is not None else _k_body() / ratio
                    candidates.append((ratio, d, "pose"))
    # Tier 3 — person_box height (often torso-only after cropping).
    if not candidates:
        for f in frames:
            if f.person_box and len(f.person_box) == 4 and f.person_box[3] > 0.05:
                ratio = f.person_box[3]
                d_phys = _solve_distance_with_fov(
                    _device_fov_h(f), 4/3, ratio, _REAL_BODY_HEIGHT_M,
                )
                d = d_phys if d_phys is not None else _k_body() / ratio
                candidates.append((ratio, d, "box"))
    if not candidates:
        return _LensPick(None, None, None, None)

    # Largest ratio = closest framing = most representative.
    candidates.sort(key=lambda t: t[0], reverse=True)
    ratio, distance_raw, _ = candidates[0]
    distance_m = round(distance_raw, 2)

    # Map distance + ratio → preferred iPhone lens. We assume the user
    # wants the subject to fill roughly half the frame (portrait
    # default); the LLM is allowed to override with rationale, but
    # this is the deterministic baseline.
    lens, why = _lens_bucket(distance_m, ratio)
    return _LensPick(
        distance_m=distance_m,
        height_ratio=round(ratio, 3),
        lens=lens,
        rationale_zh=why,
    )


def _lens_bucket(distance_m: float, ratio: float) -> tuple[str, str]:
    """Decision rule (calibrated for portrait defaults):

      ratio > 0.55  → subject already full-screen at 1× → use main_26mm
                       (anything longer would crop face, anything wider
                       wastes resolution).
      0.30 < ratio  → subject is half-screen → 2× crop or true 50mm
                       gives nicer portrait compression.
      0.18 < ratio  → subject ¼-screen → 3× tele to compress / pop.
      ratio <= 0.18 → subject is small in frame; either move closer or
                       use the ultrawide for a "tiny human in big
                       environment" scenery composition.
    """
    if ratio > 0.55:
        return ("wide_1x",
                f"主体身高约 {int(ratio*100)}% 画面，距离 ≈ {distance_m} m，"
                "已经够近，主摄 1×（wide_1x，26mm 等效）一拍即出，避免数码裁剪。")
    if ratio > 0.30:
        return ("tele_2x",
                f"主体半身入画（{int(ratio*100)}%），距离 ≈ {distance_m} m，"
                "建议切 2×（tele_2x，≈ 50mm 等效）做经典人像压缩，背景更纯。")
    if ratio > 0.18:
        return ("tele_3x",
                f"主体偏小（{int(ratio*100)}%），距离 ≈ {distance_m} m，"
                "切 3× 长焦（tele_3x，≈ 77mm）压缩空间、让人从背景中浮出。")
    return ("ultrawide_0_5x",
            f"主体在画面里很小（{int(ratio*100)}%，距离 ≈ {distance_m} m），"
            "建议要么往前走 2 步换主摄拍人，要么用 0.5× 超广角"
            "（ultrawide_0_5x）拍「小人 / 大环境」叙事感构图。")


def _pick_tilt(frames: list[FrameMeta]) -> _TiltPick:
    """Combine median pitch_deg + median pose center_y + median
    horizon_y into one of three actionable tilt verdicts: crouch / lift
    / level. horizon_y is a third independent signal that confirms or
    disputes the gyro-based pitch — gyro can drift on a re-mounted
    phone, but the visible horizon is ground truth.
    """
    pitches = [f.pitch_deg for f in frames if f.pitch_deg is not None]
    median_pitch = round(statistics.median(pitches), 1) if pitches else None

    centers = []
    for f in frames:
        if f.pose_nose_y is not None and f.pose_ankle_y is not None:
            centers.append((f.pose_nose_y + f.pose_ankle_y) / 2)
    median_center = round(statistics.median(centers), 3) if centers else None

    horizons = [f.horizon_y for f in frames if f.horizon_y is not None]
    median_horizon = round(statistics.median(horizons), 3) if horizons else None

    if median_center is None and median_pitch is None and median_horizon is None:
        return _TiltPick(None, None, None)

    advice = _tilt_verdict(median_pitch, median_center, median_horizon)
    return _TiltPick(median_pitch, median_center, advice)


def _tilt_verdict(
    pitch: Optional[float],
    center_y: Optional[float],
    horizon_y: Optional[float],
) -> Optional[str]:
    """Translate (pitch, center_y, horizon_y) → Chinese nudge.

    pitch >  +5  ≈ camera tilted down  (looking at ground / overhead-ish)
    pitch <  -5  ≈ camera tilted up    (looking at sky / "up at" angle)
    center_y < 0.4: subject sits high in frame ⇒ camera is *below* subject
                    ⇒ to flatter (eye-level), lift the camera.
    center_y > 0.6: subject sits low ⇒ camera is *above* subject ⇒ crouch.
    horizon_y < 0.40: a lot of sky ⇒ camera looking up; if pitch
                       disagrees, prefer horizon (gyro can be off after
                       re-mounting the phone or with mag-safe accessories).
    horizon_y > 0.60: a lot of ground ⇒ camera looking down.
    """
    parts = []
    # Pitch-only path when no pose available — but still defer to
    # horizon if it strongly disagrees (handled below).
    if center_y is None and pitch is not None and horizon_y is None:
        if pitch < -10:
            return f"镜头明显仰拍（pitch {pitch}°），适合拉腿长 / 留天空，但小心人脸畸变。"
        if pitch > 10:
            return f"镜头明显俯拍（pitch {pitch}°），适合拍小孩 / 食物 / 俯瞰，但避免顶光照脸。"
        return f"镜头基本平举（pitch {pitch}°），属于安全平拍角度。"

    if center_y is not None:
        if center_y <= 0.42:
            parts.append(
                f"主体重心偏画面上方（y={center_y}），相机站位偏低 — "
                "**蹲低半步或举高手机**让主体回到画面中线，否则容易把腿拉短"
            )
        elif center_y >= 0.58:
            parts.append(
                f"主体重心偏画面下方（y={center_y}），相机站位偏高 — "
                "**整体下蹲半步**或退一步让主体不被压扁"
            )
        else:
            parts.append(f"主体重心居中（y={center_y}），机位高度合适")

    if pitch is not None and abs(pitch) >= 8:
        sign = "俯" if pitch > 0 else "仰"
        parts.append(
            f"陀螺仪显示镜头{sign}拍 {abs(pitch)}°；"
            f"如果不是刻意叙事（小孩 / 拉腿 / 仰望天空），建议把手机摆正到 ±5° 以内"
        )

    # Horizon cross-check — only escalate when it disagrees with pitch
    # in a meaningful way (a lot of sky/ground but gyro says level).
    if horizon_y is not None:
        if horizon_y < 0.40 and (pitch is None or pitch > -5):
            parts.append(
                f"画面里识别到的水平线偏上（y={horizon_y}），实际仰拍倾向比"
                "陀螺仪显示得更明显（可能是手机刚重新装挂导致 IMU 偏置）；"
                "若不打算刻意仰拍，请把镜头放平 5°-10°"
            )
        elif horizon_y > 0.60 and (pitch is None or pitch < 5):
            parts.append(
                f"画面里识别到的水平线偏下（y={horizon_y}），地面占比过大；"
                "若不是想拍前景叙事，建议把手机抬平或往下沉机位"
            )

    return "；".join(parts) if parts else None


# ---------------------------------------------------------------------------
def _collect_foreground(frames: list[FrameMeta]) -> list[ForegroundFact]:
    """De-dupe + rank per-frame foreground candidates into a flat list of
    facts the LLM can quote ("on azimuth 240° there's a flowerbed at
    bottom-left, ~0.6 m away — staging it as bokeh foreground"). Caps
    at 6 entries to keep the prompt tight."""
    seen: dict[tuple[float, str], ForegroundFact] = {}
    for f in frames:
        if not f.foreground_candidates:
            continue
        for cand in f.foreground_candidates:
            x, y, w, h = cand.box
            quad = _box_to_quadrant(x, y, w, h)
            key = (round(f.azimuth_deg / 30) * 30.0, cand.label.lower())
            current = seen.get(key)
            # Prefer the entry with the largest box → most prominent.
            area = w * h
            if current is None or area > _quadrant_area_score(current):
                seen[key] = ForegroundFact(
                    azimuth_deg=round(f.azimuth_deg, 1),
                    label=cand.label.lower(),
                    quadrant=quad,
                    distance_m=cand.estimated_distance_m,
                )
    facts = list(seen.values())
    # Sort: nearest first (where known), then by azimuth.
    facts.sort(key=lambda f: (f.distance_m is None, f.distance_m or 999, f.azimuth_deg))
    return facts[:6]


def _quadrant_area_score(fact: ForegroundFact) -> float:
    # Cheap proxy — quadrant strings don't carry area, but we only use
    # this for "did we already keep something better at this key", and
    # newer always wins on tie (which is fine).
    return 0.0


def _box_to_quadrant(x: float, y: float, w: float, h: float) -> str:
    """Return the canvas position vocabulary used in ShotForeground."""
    cx = x + w / 2
    cy = y + h / 2
    # Foreground often hugs an edge — prefer edge labels when the box
    # actually touches an edge (within 5% of frame border).
    on_left   = x < 0.05
    on_right  = (x + w) > 0.95
    on_top    = y < 0.05
    on_bottom = (y + h) > 0.95
    if on_bottom and not (on_left or on_right): return "bottom_edge"
    if on_top    and not (on_left or on_right): return "top_edge"
    if on_left   and not (on_top or on_bottom): return "left_edge"
    if on_right  and not (on_top or on_bottom): return "right_edge"
    is_top = cy < 0.5
    is_left = cx < 0.5
    if is_top and is_left:    return "top_left"
    if is_top and not is_left: return "top_right"
    if not is_top and is_left: return "bottom_left"
    return "bottom_right"


def _azimuth_span(azimuths: list[float]) -> float:
    """Smallest arc covering all azimuths (handles wrap-around at 360°)."""
    if not azimuths:
        return 0.0
    sorted_az = sorted(a % 360 for a in azimuths)
    sorted_az.append(sorted_az[0] + 360)
    gaps = [sorted_az[i + 1] - sorted_az[i] for i in range(len(sorted_az) - 1)]
    largest_gap = max(gaps)
    return 360.0 - largest_gap


# ---------------------------------------------------------------------------
# Prompt block formatter
# ---------------------------------------------------------------------------
def to_prompt_block(agg: Optional[SceneAggregate]) -> str:
    """Render a SceneAggregate as a Markdown-ish prompt block. Empty
    string when nothing useful to say (so callers can splice without
    leaving a stranded heading)."""
    if agg is None:
        return ""
    lines = [
        "── SCENE INSIGHTS（基于全部 {n} 张关键帧的客户端跨帧聚合，"
        "已经替你看过了，请把下列事实当真理用）──".format(n=agg.n_frames),
    ]

    if agg.brightest_azimuth is not None and agg.darkest_azimuth is not None:
        contrast_txt = ""
        if agg.luma_contrast_ratio >= 1.6:
            contrast_txt = "（对比强烈，主光方向明显，rim-light/剪影可行）"
        elif agg.luma_contrast_ratio >= 1.2:
            contrast_txt = "（中等对比，可做侧光/环境光人像）"
        else:
            contrast_txt = "（亮度均匀，光线偏散射，硬光建议不靠谱）"
        lines.append(
            "  · 主光方向 ≈ azimuth {bright}° "
            "({bright_zh})；最暗 azimuth {dark}° ({dark_zh}){contrast}".format(
                bright=agg.brightest_azimuth,
                bright_zh=_azimuth_label_zh(agg.brightest_azimuth),
                dark=agg.darkest_azimuth,
                dark_zh=_azimuth_label_zh(agg.darkest_azimuth),
                contrast=contrast_txt,
            )
        )

    if agg.busiest_azimuth is not None and agg.cleanest_azimuth is not None \
            and agg.busiest_azimuth != agg.cleanest_azimuth:
        lines.append(
            "  · 背景最干净（边缘最少）的方向 ≈ azimuth {clean}° ({clean_zh})；"
            "细节最密的方向 ≈ azimuth {busy}° ({busy_zh})。"
            "想要 negative_space 优先选 clean 那一侧；想要城市感/引导线就拍 busy 那侧。".format(
                clean=agg.cleanest_azimuth,
                clean_zh=_azimuth_label_zh(agg.cleanest_azimuth),
                busy=agg.busiest_azimuth,
                busy_zh=_azimuth_label_zh(agg.busiest_azimuth),
            )
        )

    if agg.largest_person_azimuth is not None:
        lines.append(
            "  · 客户端检测到主体人物在 azimuth {az}° ({az_zh})，"
            "占画面约 {pct}%。pose.layout/azimuth_deg 优先围绕这个方位。".format(
                az=agg.largest_person_azimuth,
                az_zh=_azimuth_label_zh(agg.largest_person_azimuth),
                pct=int(agg.largest_person_area * 100),
            )
        )
    elif agg.person_azimuths:
        lines.append(
            "  · 客户端检测到主体人物在 azimuth {azs}（多帧），"
            "可在这些方向给出 portrait shot。".format(
                azs=", ".join(f"{a}°" for a in agg.person_azimuths[:4]),
            )
        )

    if agg.median_horizon_tilt_deg is not None:
        if agg.needs_leveling:
            sign = "右高左低" if agg.median_horizon_tilt_deg > 0 else "左高右低"
            lines.append(
                "  · 客户端测得视频整体地平线倾斜约 {t}° ({sign})——"
                "**rationale 必须提醒用户拿手机时往反方向微调**，否则成片自动歪。".format(
                    t=abs(agg.median_horizon_tilt_deg),
                    sign=sign,
                )
            )
        else:
            lines.append(
                "  · 客户端测得地平线基本水平 (中位倾角 {t}°)，构图无需特别提醒水平校准。".format(
                    t=agg.median_horizon_tilt_deg,
                )
            )

    if agg.dominant_quadrant:
        zh = {
            "top_left": "左上", "top_right": "右上",
            "bottom_left": "左下", "bottom_right": "右下",
            "center": "中央",
        }.get(agg.dominant_quadrant, agg.dominant_quadrant)
        lines.append(
            "  · 多数帧的视觉重心位于画面 {zh}；"
            "用三分法时尽量把主体放在这一象限/或刻意反向避开。".format(zh=zh)
        )

    # ---- foreground candidates --------------------------------------
    if agg.foreground_facts:
        lines.append("\n  ── FOREGROUND CANDIDATES（客户端检测，FOREGROUND DOCTRINE 优先采纳）──")
        for fact in agg.foreground_facts:
            dist_txt = f"，距离 ≈ {fact.distance_m} m" if fact.distance_m else ""
            quad_zh = _QUAD_ZH.get(fact.quadrant, fact.quadrant)
            lines.append(
                "    · azimuth {az}° ({az_zh}) 画面 {quad}：检测到 **{label}**{dist}".format(
                    az=fact.azimuth_deg,
                    az_zh=_azimuth_label_zh(fact.azimuth_deg),
                    quad=quad_zh,
                    label=fact.label,
                    dist=dist_txt,
                )
            )
        lines.append(
            "    填 ShotForeground 时，``source_azimuth_deg`` 必须是上面任一条目，"
            "``canvas_quadrant`` 优先用对应方位；distance < 1.5 m 才填 layer ≠ none。"
        )

    # ---- depth layers -----------------------------------------------
    if agg.near_depth_pct is not None:
        if agg.near_depth_pct >= 0.05:
            verdict = "前景层有料（≥5%），可放心安排 bokeh_plant / natural_frame"
        else:
            verdict = (
                "前景层几乎为空（<5%），场景偏开阔；如果给 layer ≠ 'none'，"
                "必须在 suggestion_zh 里告诉用户**蹲下/挪到植物边/换地方**才能造前景"
            )
        lines.append(
            "\n  ── DEPTH LAYERS（{src}，跨帧均值）──\n"
            "    · 近 {near}% / 远 {far}% — {verdict}".format(
                src=agg.depth_source or "monocular",
                near=int(agg.near_depth_pct * 100),
                far=int((agg.far_depth_pct or 0) * 100),
                verdict=verdict,
            )
        )

    # ---- lens recommendation ---------------------------------------
    if agg.recommended_lens:
        lines.append(
            "\n  ── LENS HINT（基于主体距离与画面占比的确定性推算）──"
        )
        lines.append(
            "    · 推荐镜头：**{lens}**\n"
            "    · 依据：{why}".format(
                lens=agg.recommended_lens,
                why=agg.lens_rationale_zh or "",
            )
        )
        lines.append(
            "    LENS DOCTRINE：每个 shot 必须填 ``camera.device_hints.iphone_lens``，"
            "默认采纳上面的推荐；若你给出了不同的镜头，rationale 必须说清原因"
            "（例如「想要广角夸张前景，所以用 ultrawide_13mm 即便主体小」）。"
        )

    # ---- tilt advice ------------------------------------------------
    if agg.tilt_advice_zh:
        lines.append(
            "\n  ── TILT HINT（基于 pitch_deg + 主体在画面里的竖向位置）──\n"
            "    · {advice}".format(advice=agg.tilt_advice_zh)
        )
        lines.append(
            "    TILT DOCTRINE：当上面提示「下蹲」或「举高」时，"
            "对应 shot 的 ``angle.height_hint`` 应该改为 low / high，"
            "并且 ``angle.pitch_deg`` 给一个能反映该姿态的数值（蹲下平拍 ≈ 0°，"
            "蹲下仰拍 ≈ -8°），coach_brief 里要直接喊出「蹲下来」/「举高」。"
        )

    # ---- lighting facts (Sprint 1) ---------------------------------
    if any([agg.cct_k, agg.dynamic_range, agg.highlight_clip_pct,
            agg.shadow_clip_pct, agg.lighting_notes]):
        lines.append(
            "\n  ── LIGHTING FACTS（基于像素直方图 + gray-world 的确定性测量）──"
        )
        if agg.cct_k is not None:
            warmth = "暖" if agg.cct_k < 4500 else "中性" if agg.cct_k < 6000 else "冷"
            lines.append(
                f"    · 色温 ≈ **{agg.cct_k} K**（{warmth}光）"
                + (f"，色偏 tint={agg.tint:+.2f}（正值偏品红 / 负值偏绿）" if agg.tint is not None else "")
            )
        if agg.light_direction:
            ld_zh = {"front": "顺光（太阳在相机背后，主体正面被照亮）",
                     "side":  "侧光（太阳在 90° 方位，立体感最强）",
                     "back":  "逆光 / 半逆光（太阳在主体后方，注意补光或剪影）"}[agg.light_direction]
            lines.append(f"    · 光线方向：{ld_zh}")
        if agg.dynamic_range:
            dr_zh = {"low": "低（柔和阴天 / 棚拍）",
                     "standard": "标准（手机宽容度内）",
                     "high": "高（光阴对比明显）",
                     "extreme": "极端（强光 + 深阴影，需 HDR）"}[agg.dynamic_range]
            lines.append(f"    · 动态范围：{dr_zh}")
        if agg.highlight_clip_pct is not None and agg.highlight_clip_pct > 0:
            lines.append(f"    · 高光裁剪：{agg.highlight_clip_pct*100:.1f}%")
        if agg.shadow_clip_pct is not None and agg.shadow_clip_pct > 0:
            lines.append(f"    · 暗部死黑：{agg.shadow_clip_pct*100:.1f}%")
        for note in agg.lighting_notes:
            lines.append(f"    · ⚠ {note}")
        lines.append(
            "    LIGHTING DOCTRINE：每个 shot 的 ``camera`` 必须根据上述测量"
            "给出一致的 ``white_balance`` (≈ 色温档位) / ``ev_bias`` (避免高光"
            "继续过曝 / 暗部继续欠曝) / ``hdr_mode`` (动态范围 high+ 时强制"
            "开启)；与测量冲突要在 rationale 解释。"
        )

    # ---- Sprint 2 horizon + pose facts -----------------------------
    if agg.horizon_consensus_y is not None:
        cf = {"high":"高","medium":"中","low":"低"}.get(agg.horizon_confidence or "low", "低")
        sky = "（含天空）" if agg.sky_present else ""
        lines.append(
            f"\n  ── HORIZON FACT — 跨源（画面梯度 + Vision 姿态）多源投票 ──"
            f"\n    · 水平线 y ≈ {agg.horizon_consensus_y}（置信度：{cf}{sky}）"
        )
    elif agg.sky_present is False:
        lines.append(
            "\n  ── HORIZON FACT — 室内/无天空场景，水平线推断已抑制 ──"
        )
    if agg.composition_facts_zh:
        lines.append("\n  ── COMPOSITION FACTS — 构图量化指标 ──")
        for fact in agg.composition_facts_zh:
            lines.append(f"    · {fact}")
        lines.append(
            "    COMPOSITION DOCTRINE：以上是客户端从跨帧 subject_box 算出的"
            "rule_of_thirds 和 symmetry 度量。若你的 composition.primary 与"
            "事实不一致，必须在 rationale 解释（例：选择对称是因为环境本身"
            "存在垂直引导线）。"
        )
    if agg.pose_facts_zh:
        lines.append("\n  ── POSE FACTS — 主体姿态需要修正的点 ──")
        for fact in agg.pose_facts_zh:
            lines.append(f"    · {fact}")
        lines.append(
            "    POSE DOCTRINE：以上是客户端从 33 个 pose keypoint 算出的"
            "几何事实。每条都要在 coach_brief 中被翻译成「请主体做 X」"
            "的明确话术，不允许沉默忽略。"
        )

    lines.append(
        "\n  使用规则：上面这些是客户端确定性算出来的，不是你的猜测。"
        "如果你的方案与之冲突（例如把主体放在 brightest azimuth 的反方向），"
        "**必须**在 rationale 里解释为什么——否则就以上述事实优先。"
    )

    return "\n".join(lines)


_QUAD_ZH = {
    "top_left":     "左上",
    "top_right":    "右上",
    "bottom_left":  "左下",
    "bottom_right": "右下",
    "left_edge":    "左侧贴边",
    "right_edge":   "右侧贴边",
    "top_edge":     "顶部贴边",
    "bottom_edge":  "底部贴边",
    "center":       "中央",
}
