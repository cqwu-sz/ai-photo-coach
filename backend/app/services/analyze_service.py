"""Top-level orchestrator wired up by /analyze.

Flow:
  1. If mock mode -> return canned response.
  2. Otherwise call Gemini with frames + references + KB summaries.
  3. Validate the response into Pydantic models.
     - On ValidationError, run one repair pass: feed the LLM its bad
       output + the Pydantic errors and let it fix structure.
  4. Run deterministic post-passes:
        - repair camera settings
        - map poses to library entries
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pydantic import ValidationError

from ..config import Settings
from ..models import (
    AnalyzeResponse,
    CaptureMeta,
    Lighting,
    ShotRecommendation,
    StyleInspiration,
)
from . import camera_params, pose_engine
from .gemini_video import GeminiClient, GeminiUnavailable
from .knowledge import (
    load_camera_kb,
    load_composition_kb,
    load_poses,
    summarize_camera_kb,
    summarize_poses,
)
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
    ) -> AnalyzeResponse:
        if self.settings.mock_mode:
            log.info(
                "mock_mode active, returning canned response",
                extra={"references": len(references)},
            )
            response = make_mock_response(meta)
            if references:
                response.debug["personalization"] = (
                    f"Style anchored on {len(references)} user references"
                )
            return response

        poses = load_poses(str(self.settings.kb_poses_path))
        cam_kb = load_camera_kb(str(self.settings.kb_camera_path))
        load_composition_kb(str(self.settings.kb_composition_path))

        client = GeminiClient(self.settings)
        try:
            raw = await client.analyze(
                meta=meta,
                frames=frames,
                references=references,
                pose_summary=summarize_poses(poses, meta.person_count),
                camera_summary=summarize_camera_kb(cam_kb),
            )
        except GeminiUnavailable as exc:
            log.warning("gemini unavailable, falling back to mock: %s", exc)
            return make_mock_response(meta)

        try:
            response = AnalyzeResponse.model_validate(raw)
        except ValidationError as exc:
            log.warning(
                "Gemini response failed validation, attempting repair: %s",
                exc.errors(include_url=False, include_context=False)[:5],
            )
            try:
                fixed = await client.repair(
                    meta=meta,
                    prev_output=json.dumps(raw, ensure_ascii=False),
                    validation_errors=exc.errors(
                        include_url=False, include_context=False
                    ),
                )
                response = AnalyzeResponse.model_validate(fixed)
                log.info("repair pass succeeded")
            except (GeminiUnavailable, ValidationError) as exc2:
                log.error("repair pass failed: %s", exc2)
                raise

        repaired_shots = [
            self._repair_shot(shot, response.scene.lighting, meta.person_count, poses)
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

        # Pick a representative frame for any shot that didn't get one,
        # using the closest-azimuth heuristic so the UI always has a backdrop.
        if meta.frame_meta:
            for s in response.shots:
                if s.representative_frame_index is None or not (
                    0 <= s.representative_frame_index < len(meta.frame_meta)
                ):
                    s.representative_frame_index = self._closest_frame_index(
                        s.angle.azimuth_deg, meta
                    )

        response.generated_at = datetime.now(timezone.utc)
        if not response.model:
            response.model = (
                self.settings.gemini_model_high
                if meta.quality_mode.value == "high"
                else self.settings.gemini_model_fast
            )
        return response

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
    ) -> ShotRecommendation:
        shot.camera = camera_params.repair_camera_settings(
            shot.camera, lighting, person_count
        )
        shot.poses = [
            pose_engine.map_to_library(p, pose_library) for p in shot.poses
        ]
        return shot
