"""Top-level orchestrator wired up by /analyze.

Flow:
  1. If mock mode -> return canned response.
  2. Otherwise resolve a VisionProvider via the factory (defaults to
     Gemini, supports BYOK overrides for any vendor in the registry).
  3. Validate the response into Pydantic models.
     - On ValidationError, run one repair pass: feed the LLM its bad
       output + the Pydantic errors and let it fix structure.
  4. Run deterministic post-passes:
        - repair camera settings
        - map poses to library entries (skipped for scenery scene_mode)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from ..config import Settings
from ..models import (
    AnalyzeResponse,
    CaptureMeta,
    Lighting,
    ShotRecommendation,
    StyleInspiration,
)
from . import (
    camera_apply,
    camera_params,
    keyframe_score,
    landmark_graph as landmark_graph_service,
    light_pro as light_pro_service,
    panorama as panorama_service,
    poi_indoor as poi_indoor_service,
    poi_lookup as poi_lookup_service,
    pose_engine,
    potential_evaluator as potential_evaluator_service,
    prompts as prompts_mod,
    route_planner as route_planner_service,
    shot_fusion as shot_fusion_service,
    style_compliance as style_compliance_service,
    style_extract as style_extract_service,
    time_optimal as time_optimal_service,
    triangulation as triangulation_service,
    walk_geometry as walk_geometry_service,
    weather as weather_service,
)
from .knowledge import (
    load_camera_kb,
    load_composition_kb,
    load_poses,
    summarize_camera_kb,
    summarize_composition_kb,
    summarize_poses,
)
from .llm import ProviderError, get_provider
from .mock_provider import make_mock_response

log = logging.getLogger(__name__)


class AnalyzeService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def run(
        self,
        meta: CaptureMeta,
        frames: list[bytes],
        references: list[bytes],
        model_id: Optional[str] = None,
        model_api_key: Optional[str] = None,
        model_base_url: Optional[str] = None,
        video_mp4: Optional[bytes] = None,
    ) -> AnalyzeResponse:
        scene_mode = _scene_mode_str(meta)

        # P1-5: parallelise every independent prefetch with a TaskGroup
        # so the slowest single upstream (POI / weather / minutely) sets
        # the wall-clock floor instead of the sum of them all.
        import asyncio as _asyncio
        async def _weather():       return await self._fetch_weather(meta)
        async def _forecast():      return await self._fetch_light_forecast(meta)
        async def _poi():           return await self._fetch_poi_candidates(meta)
        async def _indoor():        return await self._fetch_indoor_positions(meta)
        async def _time_opt():      return self._fetch_time_optimal(meta)
        async def _sfm():           return self._derive_sfm_candidates(meta)
        async def _refs():
            # CPU-bound (PIL + numpy k-means); shove to a worker thread
            # so the event loop keeps draining other prefetches.
            return await _asyncio.to_thread(self._extract_style_fingerprints, references)
        weather_snap, light_forecast, poi_candidates, indoor_positions, \
            time_recommendation, sfm_candidates, reference_fingerprints = (
            await _asyncio.gather(
                _weather(), _forecast(), _poi(), _indoor(),
                _time_opt(), _sfm(), _refs(),
                return_exceptions=False,
            )
        )
        prompts_mod.set_request_weather(weather_snap)
        prompts_mod.set_request_poi_block(
            poi_lookup_service.to_prompt_block(poi_candidates)
            + ("\n" + poi_indoor_service.to_prompt_block(indoor_positions) if indoor_positions else "")
        )
        prompts_mod.set_request_walk_block(
            walk_geometry_service.to_prompt_block(meta.walk_segment, sfm_candidates)
            + ("\n" + style_extract_service.to_prompt_block(reference_fingerprints)
               if reference_fingerprints else "")
            + ("\n" + time_optimal_service.to_prompt_block(time_recommendation)
               if time_recommendation else "")
        )

        if self.settings.mock_mode:
            log.info(
                "mock_mode active, returning canned response",
                extra={"references": len(references), "scene_mode": scene_mode},
            )
            response = make_mock_response(meta)
            if references:
                response.debug["personalization"] = (
                    f"Style anchored on {len(references)} user references"
                )
            # Even mock responses get a real sun snapshot when the client
            # supplies a geo fix — that way the result page demos the
            # compass + countdown UI without a real LLM round-trip.
            if meta.geo is not None:
                response.environment = self._build_environment_snapshot(meta, weather_snap)
                response.shots = self._reorder_by_time_sensitivity(
                    response.shots, response.environment, scene_mode,
                )
            response.light_recapture_hint = self._decide_recapture_hint(
                meta, response, scene_mode,
            )
            self._enforce_capture_advisory(response)
            for s in response.shots:
                s.overall_score = self._compute_overall_score(s, response.environment)
            # Mock mode also runs fusion so the result UI's map / pin
            # rendering is exercised end-to-end without a real LLM.
            response.shots = shot_fusion_service.fuse(
                response.shots,
                poi_candidates,
                sfm_candidates,
                response.environment,
                meta.geo,
                indoor_positions=indoor_positions,
            )
            await self._enrich_walk_routes(response.shots, meta)
            if reference_fingerprints:
                response.reference_fingerprints = reference_fingerprints
            if time_recommendation:
                if response.environment is None:
                    response.environment = self._build_environment_snapshot(meta, weather_snap)
                response.time_recommendation = time_recommendation
            self._attach_pro_signals(response, meta)
            return response

        if not self.settings.enable_byok:
            model_api_key = None
            model_base_url = None

        provider = get_provider(
            self.settings,
            model_id=model_id,
            api_key_override=model_api_key,
            base_url_override=model_base_url,
        )

        poses = load_poses(str(self.settings.kb_poses_path))
        cam_kb = load_camera_kb(str(self.settings.kb_camera_path))
        comp_kb = load_composition_kb(str(self.settings.kb_composition_path))
        comp_summary = summarize_composition_kb(
            comp_kb,
            scene_mode=scene_mode,
            person_count=meta.person_count,
        )
        prompts_mod.set_request_composition_kb(comp_summary)

        # Build a low-res 1024x512 panorama thumbnail to give the LLM a
        # global spatial map. Cheap (< 100ms for 10 frames) and bounded
        # (~80KB JPEG). On any error we just skip it — the per-frame
        # keyframes still carry the same info, just less ergonomically.
        panorama_jpeg: bytes | None = None
        try:
            if frames and len(frames) == len(meta.frame_meta):
                panorama_jpeg = panorama_service.make_panorama(
                    frames,
                    meta.frame_meta,
                    cfg=panorama_service.PanoramaConfig(width=1024, height=512),
                )
        except Exception as e:   # noqa: BLE001
            log.info("panorama prefetch failed (non-fatal): %s", e)

        try:
            raw = await provider.analyze(
                meta=meta,
                frames=frames,
                references=references,
                pose_summary=summarize_poses(poses, max(meta.person_count, 1)),
                camera_summary=summarize_camera_kb(cam_kb),
                scene_mode=scene_mode,
                panorama_jpeg=panorama_jpeg,
                video_mp4=video_mp4,
            )
        except ProviderError as exc:
            log.warning(
                "provider %s unavailable, falling back to mock: %s",
                provider.config.id,
                exc,
            )
            return make_mock_response(meta)

        try:
            response = AnalyzeResponse.model_validate(raw)
        except ValidationError as exc:
            log.warning(
                "provider %s response failed validation, attempting repair: %s",
                provider.config.id,
                exc.errors(include_url=False, include_context=False)[:5],
            )
            try:
                fixed = await provider.repair(
                    meta=meta,
                    prev_output=json.dumps(raw, ensure_ascii=False),
                    validation_errors=exc.errors(
                        include_url=False, include_context=False
                    ),
                    scene_mode=scene_mode,
                )
                response = AnalyzeResponse.model_validate(fixed)
                log.info("repair pass succeeded")
            except (ProviderError, ValidationError) as exc2:
                log.error("repair pass failed: %s", exc2)
                raise

        repaired_shots = [
            self._repair_shot(
                shot,
                response.scene.lighting,
                meta.person_count,
                poses,
                scene_mode,
            )
            for shot in response.shots
        ]
        response.shots = repaired_shots

        # Style compliance — clamp any camera knob the LLM let drift
        # outside the user's chosen style range, and log how often it
        # got it right on its own. Runs AFTER _repair_shot so we don't
        # fight the iPhone-specific aperture/shutter normalisation.
        # v11: also pass scene-level color science aggregate so the
        # palette drift check (cct / saturation / contrast bands per
        # style) can warn the user when "your scene's color is off
        # for the style you picked".
        from . import scene_aggregate as _sa
        _scene = _sa.aggregate(meta.frame_meta) if meta.frame_meta else None
        _cct = _scene.cct_k if _scene else None
        _sat_vals = [f.saturation_mean for f in (meta.frame_meta or []) if f.saturation_mean is not None]
        _sat_mean = sum(_sat_vals) / len(_sat_vals) if _sat_vals else None
        _contrast = None
        if _scene and _scene.dynamic_range:
            _contrast = {"low": 0.30, "standard": 0.55, "high": 0.75, "extreme": 0.90}.get(_scene.dynamic_range)
        compliance = style_compliance_service.validate_and_clamp(
            response.shots, meta.style_keywords or [],
            scene_cct_k=_cct, scene_saturation=_sat_mean, scene_contrast=_contrast,
        )
        # Expose scene-level lighting aggregate (Sprint 1) so the
        # result UI can render color-temp / clipping / direction chips
        # without re-deriving them from raw frames.
        if _scene is not None and (_scene.cct_k or _scene.dynamic_range or _scene.lighting_notes):
            response.debug["lighting"] = {
                "cct_k":              _scene.cct_k,
                "tint":               _scene.tint,
                "dynamic_range":      _scene.dynamic_range,
                "light_direction":    _scene.light_direction,
                "highlight_clip_pct": _scene.highlight_clip_pct,
                "shadow_clip_pct":    _scene.shadow_clip_pct,
                "notes":              list(_scene.lighting_notes),
            }
        if light_forecast is not None:
            response.debug["light_forecast"] = light_forecast
        if _scene is not None and (_scene.composition_facts_zh or _scene.rule_of_thirds_dist is not None):
            response.debug["composition"] = {
                "rule_of_thirds_dist": _scene.rule_of_thirds_dist,
                "symmetry":            _scene.symmetry_score,
                "facts":               list(_scene.composition_facts_zh),
            }
        if _scene is not None and (_scene.pose_facts_zh or _scene.horizon_consensus_y is not None):
            response.debug["pose_horizon"] = {
                "pose_facts":          list(_scene.pose_facts_zh),
                "horizon_y":           _scene.horizon_consensus_y,
                "horizon_confidence":  _scene.horizon_confidence,
                "sky_present":         _scene.sky_present,
            }
        if compliance.total_checks or (compliance.palette_drift or []):
            log.info("style compliance report", extra=compliance.to_log_dict())
            response.debug["style_compliance"] = {
                "rate":          round(compliance.rate, 3),
                "total":         compliance.total_checks,
                "clamped":       compliance.clamped_count,
                "per_shot":      compliance.per_shot,
                "palette_drift": compliance.palette_drift or [],
            }

        # Synthesize style_inspiration if the model left it empty but the
        # user actually uploaded reference photos. The UI relies on this
        # card to make "AI 借鉴了你的图" tangible — we never want it blank.
        ref_count = len(references)
        si = response.style_inspiration
        needs_synth = ref_count > 0 and (
            si is None or si.used_count == 0 or not si.summary
        )
        if needs_synth:
            response.style_inspiration = self._synthesize_inspiration(
                ref_count, meta.style_keywords or []
            )

        # Pick a representative frame for any shot that didn't get one.
        # Strategy: when we have the raw frame bytes at hand, score each
        # frame on sharpness/exposure/edge-density and combine the score
        # with azimuth proximity so a rich, in-focus frame can still win
        # over a marginally-closer-but-blurry one.
        if meta.frame_meta:
            azs = [fm.azimuth_deg for fm in meta.frame_meta]
            # Only score when the lengths match (analyze always uploads
            # one frame per FrameMeta, but Gemini sometimes drops a frame).
            scored: list[Optional[keyframe_score.FrameScore]] = (
                keyframe_score.score_frames(frames)
                if len(frames) == len(azs) else [None] * len(azs)
            )
            for s in response.shots:
                if s.representative_frame_index is None or not (
                    0 <= s.representative_frame_index < len(meta.frame_meta)
                ):
                    pick = keyframe_score.best_frame_index(
                        s.angle.azimuth_deg, azs, scored,
                    )
                    if pick is None:
                        pick = self._closest_frame_index(s.angle.azimuth_deg, meta)
                    s.representative_frame_index = pick

        response.generated_at = datetime.now(timezone.utc)
        if not response.model:
            response.model = provider.config.model_id or provider.config.id

        # Attach environment snapshot when the client supplied a geo fix.
        # The result UI uses this to render the sun compass and the
        # golden-hour countdown badge — and we sort the shots by time
        # sensitivity so the most fleeting one is at the top.
        if meta.geo is not None:
            response.environment = self._build_environment_snapshot(
                meta, weather_snap,
            )
            response.shots = self._reorder_by_time_sensitivity(
                response.shots, response.environment, scene_mode,
            )
        # Even without geo we may have a weather-only environment; mirror
        # the LLM-derived vision_light into env so the UI can still draw
        # a (dashed) light indicator on the compass.
        else:
            response.environment = self._maybe_vision_only_environment(response)

        # Decide whether to nudge the user to shoot a 10s light-pass. This
        # only ever fires for light_shadow mode and only when we don't
        # have enough light evidence to plan reliably.
        response.light_recapture_hint = self._decide_recapture_hint(
            meta, response, scene_mode,
        )
        # When the LLM self-reports very low capture_quality, trim shots
        # to a single conservative fallback so the UI advisory banner
        # can dominate the screen rather than 3 shots dressed up like
        # they're confident.
        self._enforce_capture_advisory(response)

        # Compute overall_score for ranking (0..5). Used by Phase 3
        # ranking chips on Web/iOS; backend pre-computes once so each
        # client doesn't need to duplicate the formula.
        for s in response.shots:
            s.overall_score = self._compute_overall_score(s, response.environment)

        # v13 — three-source fusion: combine LLM relative shots with
        # POI + SfM/VIO candidates, dedup, rank, and guarantee at least
        # one ``relative`` shot survives for map-less clients.
        far_points = self._derive_far_points(meta, frames)
        response.shots = shot_fusion_service.fuse(
            response.shots,
            poi_candidates,
            sfm_candidates,
            response.environment,
            meta.geo,
            far_points=far_points,
            indoor_positions=indoor_positions,
        )
        # Re-score the new clones so their badge matches the rest.
        for s in response.shots:
            if s.overall_score is None:
                s.overall_score = self._compute_overall_score(s, response.environment)
        await self._enrich_walk_routes(response.shots, meta)
        if reference_fingerprints:
            response.reference_fingerprints = reference_fingerprints
            self._apply_palette_match(response.shots, reference_fingerprints)
        if time_recommendation:
            response.time_recommendation = time_recommendation
        self._attach_pro_signals(response, meta)
        return response

    @staticmethod
    def _attach_pro_signals(response: AnalyzeResponse, meta: CaptureMeta) -> None:
        """Compute the core-pro upgrade signals (landmark graph + light_pro
        + potential coach lines) and fold them into the response.

        - ``coach_lines`` is a top-level wire field consumed by the iOS
          VoiceCoach; we always overwrite it (deterministic, no LLM).
        - ``debug.landmark`` / ``debug.light_pro`` / ``debug.potential``
          are exposed for the result UI's "details" panel.

        All four computations degrade gracefully — older clients
        without ``landmark_candidates`` get only the light_pro +
        potential evaluation that can be derived from existing
        ``scene_aggregate`` outputs.
        """
        from . import scene_aggregate as _sa
        if not meta.frame_meta:
            return
        scene_agg = _sa.aggregate(meta.frame_meta)
        graph = landmark_graph_service.aggregate(meta.frame_meta)
        sun_alt = None
        if response.environment is not None and response.environment.sun is not None:
            sun_alt = response.environment.sun.altitude_deg
        light_pro = light_pro_service.aggregate(
            meta.frame_meta,
            sun_altitude_deg=sun_alt,
            cct_k=scene_agg.cct_k if scene_agg else None,
            highlight_clip_pct=scene_agg.highlight_clip_pct if scene_agg else None,
            shadow_clip_pct=scene_agg.shadow_clip_pct if scene_agg else None,
            light_direction=scene_agg.light_direction if scene_agg else None,
        )
        evaluation = potential_evaluator_service.evaluate(scene_agg, graph, light_pro)

        from ..models import CoachLineModel
        if evaluation is not None:
            response.coach_lines = [
                CoachLineModel(text_zh=c.text_zh, emotion=c.emotion, priority=c.priority)
                for c in evaluation.coach_lines
            ]
            response.debug["potential"] = {
                "internal_score": evaluation.internal_score,
                "axes": {
                    "light":      evaluation.breakdown.light,
                    "background": evaluation.breakdown.background,
                    "subject":    evaluation.breakdown.subject,
                    "layering":   evaluation.breakdown.layering,
                    "uniqueness": evaluation.breakdown.uniqueness,
                },
            }
        if graph is not None and graph.nodes:
            response.debug["landmark_graph"] = {
                "node_count":             len(graph.nodes),
                "has_stereo_opportunity": graph.has_stereo_opportunity,
                "ground_y":               graph.ground_y,
                "nodes": [
                    {
                        "id":                       n.node_id,
                        "label":                    n.label,
                        "azimuth_deg":              n.azimuth_from_origin_deg,
                        "horizontal_distance_m":    n.horizontal_distance_m,
                        "height_above_ground_m":    n.height_above_ground_m,
                        "height_bucket":            n.height_bucket,
                    }
                    for n in graph.nodes
                ],
            }
        if light_pro is not None:
            response.debug["light_pro"] = light_pro.to_dict()

    @staticmethod
    def _apply_palette_match(shots: list[ShotRecommendation],
                              fingerprints: list) -> None:
        """Compute a per-shot palette_match_score against the strongest
        reference and fold it into ShotStyleMatch.fixes audit trail (W6.2).

        We don't add a new top-level field on ShotRecommendation to avoid
        breaking older clients; instead the score lands as a {knob:
        'palette_match', from: ref_idx, to: score} entry that the UI can
        surface in the compliance panel."""
        if not fingerprints:
            return
        # Pick the highest-weight reference as the canonical target.
        ref = fingerprints[0]
        for s in shots:
            try:
                k = s.camera.white_balance_k if s.camera else None
                score = style_extract_service.palette_match_score(k, None, ref)
                if s.style_match is None:
                    continue
                s.style_match.fixes.append({
                    "knob": "palette_match",
                    "from": f"ref#{ref.index}",
                    "to": score,
                })
            except Exception:                                # noqa: BLE001
                continue

    @staticmethod
    def _enforce_capture_advisory(response: AnalyzeResponse) -> None:
        """When LLM says ``should_retake`` and score <= 2, keep the user's
        attention on the advisory by limiting shots to 1. We don't drop
        shots entirely — the banner asks the user to retake, but if they
        choose to proceed anyway we want to give them at least one
        conservative fallback so the UX doesn't dead-end."""
        cq = response.scene.capture_quality if response.scene else None
        if cq is None:
            return
        if cq.should_retake and cq.score <= 2 and len(response.shots) > 1:
            response.shots = response.shots[:1]

    @staticmethod
    def _compute_overall_score(
        shot: ShotRecommendation,
        env: Optional["EnvironmentSnapshot"],
    ) -> float:
        """Backend-side ranking score in [0, 5]. Splits 0.5 / 0.3 / 0.2
        between criteria average / confidence / time-bonus.

        - criteria avg: mean of all 7 axes (or 4 if old data); 0..5
        - confidence:  0..1 -> 0..5 multiplier
        - time bonus:  +1 when env.sun has < 30 min countdown
                       (tight golden / blue window — capture now!)
        """
        score = shot.criteria_score
        if score is None:
            crit_avg = (shot.confidence or 0.7) * 5
        else:
            vals = [
                score.composition, score.light, score.color, score.depth,
                score.subject_fit, score.background, score.theme,
            ]
            crit_avg = sum(vals) / len(vals)

        conf = (shot.confidence or 0.7) * 5
        time_bonus = 0.0
        if env is not None and env.sun is not None:
            countdown = env.sun.minutes_to_golden_end or env.sun.minutes_to_blue_end
            if countdown is not None and countdown < 30:
                time_bonus = 5.0  # max so the weighted contribution becomes 1.0
        weighted = 0.5 * crit_avg + 0.3 * conf + 0.2 * time_bonus
        return round(min(5.0, max(0.0, weighted)), 2)

    async def _fetch_poi_candidates(
        self, meta: CaptureMeta,
    ) -> list["poi_lookup_service.POICandidate"]:
        """Look up nearby POIs for the user's GeoFix. Returns ``[]`` when
        no geo, when the feature is disabled in settings, or on any
        error — POI is purely additive.
        """
        if not getattr(self.settings, "enable_poi_lookup", True):
            return []
        if meta.geo is None:
            return []
        try:
            return await poi_lookup_service.search_nearby(
                meta.geo.lat, meta.geo.lon,
                radius_m=getattr(self.settings, "poi_lookup_radius_m", 300),
                amap_key=getattr(self.settings, "amap_key", "") or None,
            )
        except Exception as e:                              # noqa: BLE001
            log.info("poi_lookup failed (non-fatal): %s", e)
            return []

    async def _fetch_indoor_positions(self, meta: CaptureMeta) -> list:
        """Look up indoor hotspots via poi_indoor when GeoFix is inside a
        known building. Returns a list of ShotPosition(kind=indoor)."""
        if not getattr(self.settings, "enable_indoor_poi", True):
            return []
        if meta.geo is None:
            return []
        try:
            ctxs = await poi_indoor_service.lookup_indoor(
                meta.geo.lat, meta.geo.lon,
                provider=getattr(self.settings, "indoor_provider", "amap"),
                amap_key=getattr(self.settings, "amap_indoor_key", "") or None,
                mapbox_token=getattr(self.settings, "mapbox_token", "") or None,
            )
        except Exception as e:                              # noqa: BLE001
            log.info("indoor poi failed (non-fatal): %s", e)
            return []
        from ..models import ShotPosition, ShotPositionKind
        out = []
        for c in ctxs:
            out.append(ShotPosition(
                kind=ShotPositionKind.indoor,
                source="poi_indoor",
                confidence=0.78,
                indoor=c,
                name_zh=c.hotspot_label_zh or c.building_name_zh,
            ))
        return out

    def _fetch_time_optimal(self, meta: CaptureMeta):
        if not getattr(self.settings, "enable_time_optimal", True):
            return None
        if meta.geo is None:
            return None
        try:
            return time_optimal_service.lookup(meta.geo.lat, meta.geo.lon)
        except Exception as e:                              # noqa: BLE001
            log.info("time_optimal failed (non-fatal): %s", e)
            return None

    def _extract_style_fingerprints(self, references: list[bytes]):
        if not references or not getattr(self.settings, "enable_style_extract", True):
            return []
        try:
            return style_extract_service.extract_fingerprints(
                references, enable_embedding=False,
            )
        except Exception as e:                              # noqa: BLE001
            log.info("style_extract failed (non-fatal): %s", e)
            return []

    def _derive_far_points(self, meta: CaptureMeta, frames: list[bytes]):
        """Run two-view triangulation (W4). Currently a thin wrapper —
        we don't have per-frame intrinsics from this code path so we hand
        off when the client supplies focal_length and walk_segment poses
        in the future. Returns ``[]`` as a safe default today.
        """
        if not getattr(self.settings, "enable_triangulation", True):
            return []
        try:
            # Building TriangulationFrame requires per-frame R/t in ENU
            # plus intrinsics; until the iOS / Web clients ship those
            # alongside frames, derive_far_points naturally returns [].
            return triangulation_service.derive_far_points(
                [], meta.geo.lat if meta.geo else 0.0,
                meta.geo.lon if meta.geo else 0.0,
                initial_heading_deg=(meta.walk_segment.initial_heading_deg
                                      if meta.walk_segment else 0.0) or 0.0,
            )
        except Exception as e:                              # noqa: BLE001
            log.info("triangulation failed (non-fatal): %s", e)
            return []

    async def _enrich_walk_routes(self, shots: list[ShotRecommendation],
                                   meta: CaptureMeta) -> None:
        """For every absolute shot beyond the route_planner threshold,
        fire off a walking-route lookup in parallel and attach it."""
        if not getattr(self.settings, "enable_route_planner", True):
            return
        if meta.geo is None:
            return
        threshold = getattr(self.settings, "route_planner_distance_threshold_m", 50)
        amap_key = getattr(self.settings, "amap_key", "") or None
        from ..models import ShotPositionKind
        targets = []
        for s in shots:
            pos = s.position
            if pos is None or pos.kind != ShotPositionKind.absolute:
                continue
            if pos.lat is None or pos.lon is None:
                continue
            if (pos.walk_distance_m or 0) < threshold:
                continue
            targets.append(pos)
        if not targets:
            return
        import asyncio as _asyncio
        async def _go(pos):
            try:
                pos.walk_route = await route_planner_service.plan_route(
                    meta.geo.lat, meta.geo.lon,
                    pos.lat, pos.lon, amap_key=amap_key,
                )
            except Exception as e:                          # noqa: BLE001
                log.info("route_planner failed for %s: %s", pos.name_zh, e)
        await _asyncio.gather(*(_go(p) for p in targets), return_exceptions=True)

    def _derive_sfm_candidates(self, meta: CaptureMeta) -> list:
        """Convert the optional walk_segment into ShotPosition candidates.
        Returns ``[]`` when the user didn't opt into the walk or when the
        feature is disabled in settings."""
        if not getattr(self.settings, "enable_walk_segment", True):
            return []
        if meta.walk_segment is None or meta.geo is None:
            return []
        try:
            return walk_geometry_service.derive_candidates(
                meta.walk_segment, meta.geo,
            )
        except Exception as e:                              # noqa: BLE001
            log.info("walk_geometry failed (non-fatal): %s", e)
            return []

    @staticmethod
    async def _fetch_weather(meta: CaptureMeta) -> Optional["weather_service.WeatherSnapshot"]:
        """Fetch current weather from Open-Meteo when geo is available.
        Falls back to ``None`` on any error — analyze must never block on
        weather. Cached for 5 minutes per (lat,lon) inside the client."""
        if meta.geo is None:
            return None
        try:
            return await weather_service.fetch_current(meta.geo.lat, meta.geo.lon)
        except Exception as e:
            log.info("weather fetch failed, continuing without it: %s", e)
            return None

    @staticmethod
    async def _fetch_light_forecast(meta: CaptureMeta) -> Optional[dict]:
        """v12 — predict cloud_in_30min + golden_hour_countdown so the
        UI can warn 'lock in your shot — sun goes behind clouds in 12 min'.
        Best-effort, returns None on any failure.
        """
        if meta.geo is None:
            return None
        try:
            from . import sun as sun_service
            from datetime import timedelta
            minutely = await weather_service.PROVIDER.fetch_minutely_15(
                meta.geo.lat, meta.geo.lon, hours=1,
            )
            cloud_in_30 = weather_service.predict_cloud_in_30min(minutely or [])
            now = meta.geo.timestamp or datetime.now(timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            alt_now = sun_service.compute(meta.geo.lat, meta.geo.lon, now).altitude_deg
            alt_15 = sun_service.compute(
                meta.geo.lat, meta.geo.lon, now + timedelta(minutes=15)
            ).altitude_deg
            golden = weather_service.golden_hour_countdown(alt_now, alt_15)
            if cloud_in_30 is None and golden is None:
                return None
            return {"cloud_in_30min": cloud_in_30, "golden_hour_countdown_min": golden}
        except Exception as e:
            log.info("light forecast failed: %s", e)
            return None

    @staticmethod
    def _build_environment_snapshot(
        meta: CaptureMeta,
        weather_snap: Optional["weather_service.WeatherSnapshot"] = None,
    ) -> "EnvironmentSnapshot":
        """Compute SunSnapshot + WeatherSnapshot + mirror LLM vision_light."""
        from .sun import compute as sun_compute  # local import to avoid cycle
        from ..models import (
            EnvironmentSnapshot,
            SunSnapshot,
            WeatherSnapshot as WeatherModel,
        )

        assert meta.geo is not None
        t = meta.geo.timestamp or datetime.now(timezone.utc)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        info = sun_compute(meta.geo.lat, meta.geo.lon, t)
        snapshot = SunSnapshot(
            azimuth_deg=info.azimuth_deg,
            altitude_deg=info.altitude_deg,
            phase=info.phase,
            color_temp_k_estimate=info.color_temp_k_estimate,
            minutes_to_golden_end=info.minutes_to_golden_end,
            minutes_to_blue_end=info.minutes_to_blue_end,
            minutes_to_sunset=info.minutes_to_sunset,
            minutes_to_sunrise=info.minutes_to_sunrise,
        )
        weather_model: Optional[WeatherModel] = None
        if weather_snap is not None:
            weather_model = WeatherModel(**weather_snap.to_dict())
        return EnvironmentSnapshot(
            sun=snapshot,
            weather=weather_model,
            timestamp=t,
        )

    @staticmethod
    def _maybe_vision_only_environment(
        response: AnalyzeResponse,
    ) -> Optional["EnvironmentSnapshot"]:
        """When geo is missing but the LLM still filled scene.vision_light,
        bubble it up into AnalyzeResponse.environment so the UI can render
        a (dashed) light indicator on the compass even without a sun
        calculation. Returns ``None`` if vision_light is also missing."""
        from ..models import EnvironmentSnapshot

        vl = response.scene.vision_light
        if vl is None or vl.direction_deg is None:
            return None
        return EnvironmentSnapshot(vision_light=vl)

    @staticmethod
    def _decide_recapture_hint(
        meta: CaptureMeta,
        response: AnalyzeResponse,
        scene_mode: str,
    ) -> Optional["LightRecaptureHint"]:
        """Decide whether to ask the user for a 10-second light-pass.

        Fires only for ``light_shadow`` mode, and only when we lack
        confident light direction:
          - no geo fix, AND
          - vision_light missing OR confidence < 0.3 OR quality == 'unknown'.

        When fired we still pre-fill ``suggested_azimuth_deg`` from the
        LLM's best guess (or the first shot's azimuth) so the recapture
        screen can centre the new pass on the most likely light source.
        """
        from ..models import LightRecaptureHint

        if scene_mode != "light_shadow":
            return None
        if meta.geo is not None:
            return None
        vl = response.scene.vision_light
        confidence = (vl.confidence if vl is not None else None) or 0.0
        quality = (vl.quality if vl is not None else "unknown") or "unknown"
        if vl is not None and confidence >= 0.3 and quality != "unknown":
            return None

        suggested = None
        if vl is not None and vl.direction_deg is not None:
            suggested = float(vl.direction_deg)
        elif response.shots:
            suggested = response.shots[0].angle.azimuth_deg

        return LightRecaptureHint(
            enabled=True,
            title="光线证据不足，建议补一段定向环视",
            detail=(
                "当前光影场景下，AI 对主光方向的把握不够。"
                "对着最亮的方向慢转 10 秒，给我更多光线证据，建议会更稳。"
            ),
            suggested_azimuth_deg=suggested,
        )

    @staticmethod
    def _reorder_by_time_sensitivity(
        shots: list[ShotRecommendation],
        env: "EnvironmentSnapshot",
        scene_mode: str,
    ) -> list[ShotRecommendation]:
        """Light_shadow + tight golden window? Put the shot whose azimuth is
        closest to the sun at the front so the user shoots that angle while
        the warm rim light is still around. For other modes (or if the sun
        is high), preserve the LLM's original ordering.
        """
        if scene_mode != "light_shadow":
            return shots
        if env.sun is None:
            return shots
        # Only reorder when the time-pressure window is tight (< 30 min).
        countdown = env.sun.minutes_to_golden_end or env.sun.minutes_to_blue_end
        if countdown is None or countdown > 30:
            return shots
        sun_az = env.sun.azimuth_deg

        def _delta(s: ShotRecommendation) -> float:
            d = abs(s.angle.azimuth_deg - sun_az) % 360
            return min(d, 360 - d)

        return sorted(shots, key=_delta)

    # ------------------------------------------------------------------

    @staticmethod
    def _synthesize_inspiration(
        ref_count: int, style_keywords: list[str]
    ) -> StyleInspiration:
        traits: list[str] = []
        if style_keywords:
            traits.extend(style_keywords[:3])
        # Add a couple of safe defaults so the UI tag row isn't lonely.
        defaults = ["色调倾向", "构图偏好", "人物站位"]
        for d in defaults:
            if d not in traits:
                traits.append(d)
        traits = traits[:5]
        kw_clause = (
            f"以及你给的关键词「{', '.join(style_keywords[:3])}」"
            if style_keywords
            else ""
        )
        summary = (
            f"AI 已把你这 {ref_count} 张参考图当成风格锚点{kw_clause}，"
            "上面每个机位的 rationale 会显式说明它从哪几张图里学到了什么。"
        )
        return StyleInspiration(
            used_count=ref_count,
            summary=summary,
            inherited_traits=traits,
        )

    @staticmethod
    def _closest_frame_index(target_az: float, meta: CaptureMeta) -> int:
        best_i = 0
        best_d = 1e9
        for i, fm in enumerate(meta.frame_meta):
            d = abs(((fm.azimuth_deg - target_az + 540) % 360) - 180)
            if d < best_d:
                best_d = d
                best_i = i
        return best_i

    def _repair_shot(
        self,
        shot: ShotRecommendation,
        lighting: Lighting,
        person_count: int,
        pose_library: list[dict],
        scene_mode: str,
    ) -> ShotRecommendation:
        shot.camera = camera_params.repair_camera_settings(
            shot.camera, lighting, person_count, scene_mode=scene_mode
        )
        # Compute the iPhone-applicable plan once the camera settings are
        # finalised. Doing it here means it's based on the *repaired*
        # values, not the raw LLM output (so e.g. ISO clamping flows
        # through into the plan that AVCaptureDevice will execute).
        shot.camera.iphone_apply_plan = camera_apply.build_plan(shot.camera)
        if scene_mode == "scenery":
            # Scenery shots may have empty poses; do not synthesise.
            shot.poses = [p for p in shot.poses if p.persons]
        else:
            shot.poses = [
                pose_engine.map_to_library(p, pose_library) for p in shot.poses
            ]
        return shot


def _scene_mode_str(meta: CaptureMeta) -> str:
    """Tolerant accessor: CaptureMeta gains a scene_mode field in
    milestone 2. Until then, default to ``portrait``."""
    raw = getattr(meta, "scene_mode", None)
    if raw is None:
        return "portrait"
    if hasattr(raw, "value"):
        return raw.value
    return str(raw)
