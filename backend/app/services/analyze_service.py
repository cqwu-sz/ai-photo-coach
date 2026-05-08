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
    pose_engine,
    prompts as prompts_mod,
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
    ) -> AnalyzeResponse:
        scene_mode = _scene_mode_str(meta)

        # Pre-fetch weather (Open-Meteo, no key, 1.5s timeout) so the prompt
        # builders can fold it into ENVIRONMENT FACTS. We stash it in a
        # ContextVar so the synchronous provider Protocol doesn't need a
        # new kwarg. None on failure — analyze keeps working without it.
        weather_snap = await self._fetch_weather(meta)
        prompts_mod.set_request_weather(weather_snap)

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

        try:
            raw = await provider.analyze(
                meta=meta,
                frames=frames,
                references=references,
                pose_summary=summarize_poses(poses, max(meta.person_count, 1)),
                camera_summary=summarize_camera_kb(cam_kb),
                scene_mode=scene_mode,
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
        return response

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
