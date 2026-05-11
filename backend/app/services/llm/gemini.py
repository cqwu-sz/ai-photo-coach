"""GeminiProvider — wraps the google-genai SDK behind VisionProvider.

Native multimodal video understanding + ``response_schema`` for hard
structural enforcement. The schema is built once and reused.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ...models import (
    CaptureMeta,
    CompositionType,
    Difficulty,
    HeightHint,
    IphoneLens,
    Layout,
    Lighting,
)
from ..prompts import (
    SYSTEM_INSTRUCTION,
    build_repair_prompt,
    build_user_prompt,
)
from .base import (
    ProviderConfig,
    ProviderError,
    ProviderQuotaExceeded,
    ProviderUnauthorized,
)

log = logging.getLogger(__name__)


class GeminiProvider:
    def __init__(self, config: ProviderConfig, api_key: str | None):
        self.config = config
        self.api_key = api_key
        self._client: Any | None = None
        self._schema_obj = None

    # ---- internal --------------------------------------------------------

    def _ensure(self):
        if self._client is not None:
            return self._client
        if not self.api_key:
            raise ProviderUnauthorized(
                f"{self.config.id}: API key not provided"
            )
        try:
            from google import genai  # type: ignore
        except ImportError as exc:
            raise ProviderError(
                "google-genai not installed. `pip install google-genai`"
            ) from exc
        self._client = genai.Client(api_key=self.api_key)
        if self._schema_obj is None:
            self._schema_obj = _build_gemini_schema()
        return self._client

    # ---- VisionProvider --------------------------------------------------

    async def analyze(
        self,
        meta: CaptureMeta,
        frames: list[bytes],
        references: list[bytes],
        pose_summary: str,
        camera_summary: str,
        scene_mode: str,
        panorama_jpeg: bytes | None = None,
        video_mp4: bytes | None = None,
    ) -> dict[str, Any]:
        from google.genai import types  # type: ignore

        client = self._ensure()
        parts: list[Any] = []
        # Panorama goes FIRST so the LLM sees the global layout before
        # zooming into the per-direction keyframes.
        if panorama_jpeg:
            parts.append(types.Part.from_bytes(
                data=panorama_jpeg, mime_type="image/jpeg",
            ))
        # Optional 720p H.264 (high quality mode). Gemini accepts video
        # parts; we cap at one short clip (~8s) so token cost stays sane.
        if video_mp4:
            parts.append(types.Part.from_bytes(
                data=video_mp4, mime_type="video/mp4",
            ))
        for raw in frames:
            parts.append(types.Part.from_bytes(data=raw, mime_type="image/jpeg"))
        for raw in references:
            parts.append(types.Part.from_bytes(data=raw, mime_type="image/jpeg"))

        user_prompt = build_user_prompt(
            meta=meta,
            pose_library_summary=pose_summary,
            camera_kb_summary=camera_summary,
            has_references=bool(references),
            scene_mode=scene_mode,
            has_panorama=panorama_jpeg is not None,
            has_video=video_mp4 is not None,
        )
        parts.append(types.Part.from_text(text=user_prompt))

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=self._schema_obj,
            temperature=0.5,
        )

        log.info(
            "calling gemini",
            extra={
                "model": self.config.model_id,
                "frames": len(frames),
                "references": len(references),
                "scene_mode": scene_mode,
            },
        )

        # google-genai is sync; offload so we don't block the event loop.
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.config.model_id,
            contents=parts,
            config=config,
        )
        text = (response.text or "").strip()
        if not text:
            raise ProviderError("empty response from Gemini")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            log.error("Gemini returned non-JSON: %s", text[:500])
            raise ProviderError(f"non-JSON response: {exc}") from exc

    async def repair(
        self,
        meta: CaptureMeta,
        prev_output: str,
        validation_errors: list[dict],
        scene_mode: str,
    ) -> dict[str, Any]:
        from google.genai import types  # type: ignore

        client = self._ensure()
        prompt = build_repair_prompt(prev_output, validation_errors)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=self._schema_obj,
            temperature=0.1,
        )
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=self.config.model_id,
            contents=[types.Part.from_text(text=prompt)],
            config=config,
        )
        text = (response.text or "").strip()
        if not text:
            raise ProviderError("empty response from Gemini repair pass")
        return json.loads(text)

    async def ping(self) -> dict[str, Any]:
        from google.genai import types  # type: ignore

        client = self._ensure()
        try:
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model=self.config.model_id,
                contents=[types.Part.from_text(text="OK")],
                config=types.GenerateContentConfig(
                    temperature=0,
                    max_output_tokens=8,
                ),
            )
            return {"ok": True, "snippet": (resp.text or "").strip()[:32]}
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            upper = msg.upper()
            if "401" in msg or "PERMISSION" in upper or "UNAUTHENTICATED" in upper:
                raise ProviderUnauthorized(msg) from exc
            if "429" in msg or "RESOURCE_EXHAUSTED" in upper or "QUOTA" in upper:
                raise ProviderQuotaExceeded(msg) from exc
            raise ProviderError(msg) from exc


# ---------------------------------------------------------------------------
# Gemini-native schema construction (mirrors AnalyzeResponse)
# ---------------------------------------------------------------------------


def _build_gemini_schema():
    """Construct a google.genai.types.Schema mirroring AnalyzeResponse.

    Lazy-imported because the SDK is optional at import time so unit tests
    can run without google-genai installed.
    """
    from google.genai import types as gtypes  # type: ignore

    T = gtypes.Type
    S = gtypes.Schema

    def enum_str(enum_cls):
        return S(type=T.STRING, enum=[e.value for e in enum_cls])

    angle = S(
        type=T.OBJECT,
        properties={
            "azimuth_deg": S(type=T.NUMBER),
            "pitch_deg": S(type=T.NUMBER),
            "distance_m": S(type=T.NUMBER),
            "height_hint": enum_str(HeightHint),
        },
        required=["azimuth_deg", "pitch_deg", "distance_m"],
    )

    composition = S(
        type=T.OBJECT,
        properties={
            "primary": enum_str(CompositionType),
            "secondary": S(type=T.ARRAY, items=S(type=T.STRING)),
            "notes": S(type=T.STRING),
        },
        required=["primary"],
    )

    device_hints = S(
        type=T.OBJECT,
        properties={
            "iphone_lens": enum_str(IphoneLens),
            "third_party_app": S(type=T.STRING),
        },
    )

    camera = S(
        type=T.OBJECT,
        properties={
            "focal_length_mm": S(type=T.NUMBER),
            "aperture": S(type=T.STRING),
            "shutter": S(type=T.STRING),
            "iso": S(type=T.INTEGER),
            "white_balance_k": S(type=T.INTEGER),
            "ev_compensation": S(type=T.NUMBER),
            "rationale": S(type=T.STRING),
            "device_hints": device_hints,
        },
        required=["focal_length_mm", "aperture", "shutter", "iso"],
    )

    person_pose = S(
        type=T.OBJECT,
        properties={
            "role": S(type=T.STRING),
            "stance": S(type=T.STRING),
            "upper_body": S(type=T.STRING),
            "hands": S(type=T.STRING),
            "gaze": S(type=T.STRING),
            "expression": S(type=T.STRING),
            "position_hint": S(type=T.STRING),
        },
        required=["role"],
    )

    pose_suggestion = S(
        type=T.OBJECT,
        properties={
            "person_count": S(type=T.INTEGER),
            "layout": enum_str(Layout),
            "persons": S(type=T.ARRAY, items=person_pose),
            "interaction": S(type=T.STRING),
            "reference_thumbnail_id": S(type=T.STRING),
            "difficulty": enum_str(Difficulty),
        },
        required=["person_count", "layout", "persons"],
    )

    criteria_score = S(
        type=T.OBJECT,
        properties={
            "composition": S(type=T.INTEGER),
            "light":       S(type=T.INTEGER),
            "color":       S(type=T.INTEGER),
            "depth":       S(type=T.INTEGER),
        },
    )
    criteria_notes = S(
        type=T.OBJECT,
        properties={
            "composition": S(type=T.STRING),
            "light":       S(type=T.STRING),
            "color":       S(type=T.STRING),
            "depth":       S(type=T.STRING),
        },
    )

    shot = S(
        type=T.OBJECT,
        properties={
            "id": S(type=T.STRING),
            "title": S(type=T.STRING),
            "angle": angle,
            "composition": composition,
            "camera": camera,
            "poses": S(type=T.ARRAY, items=pose_suggestion),
            "rationale": S(type=T.STRING),
            "coach_brief": S(type=T.STRING),
            "representative_frame_index": S(type=T.INTEGER),
            "confidence": S(type=T.NUMBER),
            "criteria_score": criteria_score,
            "criteria_notes": criteria_notes,
            "strongest_axis": S(type=T.STRING),
            "weakest_axis":   S(type=T.STRING),
            "iphone_tips":    S(type=T.ARRAY, items=S(type=T.STRING)),
        },
        required=["id", "angle", "composition", "camera", "poses", "rationale"],
    )

    style_inspiration = S(
        type=T.OBJECT,
        properties={
            "used_count": S(type=T.INTEGER),
            "summary": S(type=T.STRING),
            "inherited_traits": S(type=T.ARRAY, items=S(type=T.STRING)),
        },
    )

    vision_light = S(
        type=T.OBJECT,
        properties={
            "direction_deg": S(type=T.NUMBER),
            "quality":       S(type=T.STRING),
            "confidence":    S(type=T.NUMBER),
            "notes":         S(type=T.STRING),
        },
    )

    scene = S(
        type=T.OBJECT,
        properties={
            "type": S(type=T.STRING),
            "lighting": enum_str(Lighting),
            "background_summary": S(type=T.STRING),
            "cautions": S(type=T.ARRAY, items=S(type=T.STRING)),
            "vision_light": vision_light,
        },
        required=["type", "lighting", "background_summary"],
    )

    return S(
        type=T.OBJECT,
        properties={
            "scene": scene,
            "shots": S(type=T.ARRAY, items=shot),
            "style_inspiration": style_inspiration,
            "model": S(type=T.STRING),
            "generated_at": S(type=T.STRING),
        },
        required=["scene", "shots"],
    )
