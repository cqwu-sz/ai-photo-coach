"""Gemini multimodal client for the analyze pipeline.

Hardening compared to v0:
  - We build a Gemini-native Schema mirroring the AnalyzeResponse shape and
    pass it via `response_schema`, so the model is structurally constrained.
  - The system prompt + few-shot teach taste (Chinese rationale, scene
    specificity, multi-shot diversity).
  - On Pydantic validation failure we run one repair pass: re-prompt Gemini
    with the prior raw output + the list of validation errors.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..config import Settings
from ..models import (
    AnalyzeResponse,
    CaptureMeta,
    CompositionType,
    Difficulty,
    HeightHint,
    IphoneLens,
    Layout,
    Lighting,
)
from .prompts import SYSTEM_INSTRUCTION, build_repair_prompt, build_user_prompt

log = logging.getLogger(__name__)


class GeminiUnavailable(RuntimeError):
    pass


def _build_gemini_schema():
    """Construct a google.genai.types.Schema that mirrors AnalyzeResponse.

    Lazy-imported because the SDK is optional at import time (we want unit
    tests to run without network/SDK calls).
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

    scene = S(
        type=T.OBJECT,
        properties={
            "type": S(type=T.STRING),
            "lighting": enum_str(Lighting),
            "background_summary": S(type=T.STRING),
            "cautions": S(type=T.ARRAY, items=S(type=T.STRING)),
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


class GeminiClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self._schema_obj = None

    def _ensure(self):
        if self._client is not None:
            return self._client
        if not self.settings.gemini_api_key:
            raise GeminiUnavailable("GEMINI_API_KEY is not set")
        try:
            from google import genai  # type: ignore
        except ImportError as exc:
            raise GeminiUnavailable(
                "google-genai not installed. `pip install google-genai`"
            ) from exc
        self._client = genai.Client(api_key=self.settings.gemini_api_key)
        if self._schema_obj is None:
            self._schema_obj = _build_gemini_schema()
        return self._client

    def _model_name(self, meta: CaptureMeta) -> str:
        return (
            self.settings.gemini_model_high
            if meta.quality_mode.value == "high"
            else self.settings.gemini_model_fast
        )

    async def analyze(
        self,
        meta: CaptureMeta,
        frames: list[bytes],
        references: list[bytes],
        pose_summary: str,
        camera_summary: str,
    ) -> dict[str, Any]:
        client = self._ensure()
        from google.genai import types  # type: ignore

        parts: list[Any] = []
        for raw in frames:
            parts.append(types.Part.from_bytes(data=raw, mime_type="image/jpeg"))
        for raw in references:
            parts.append(types.Part.from_bytes(data=raw, mime_type="image/jpeg"))

        user_prompt = build_user_prompt(
            meta=meta,
            pose_library_summary=pose_summary,
            camera_kb_summary=camera_summary,
            has_references=bool(references),
        )
        parts.append(types.Part.from_text(text=user_prompt))

        model_name = self._model_name(meta)
        log.info(
            "calling gemini",
            extra={
                "model": model_name,
                "frames": len(frames),
                "references": len(references),
            },
        )

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=self._schema_obj,
            temperature=0.5,
        )

        response = client.models.generate_content(
            model=model_name,
            contents=parts,
            config=config,
        )
        text = (response.text or "").strip()
        if not text:
            raise GeminiUnavailable("empty response from Gemini")

        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            log.error("Gemini returned non-JSON: %s", text[:500])
            raise GeminiUnavailable(f"non-JSON response: {exc}") from exc

    async def repair(
        self,
        meta: CaptureMeta,
        prev_output: str,
        validation_errors: list[dict],
    ) -> dict[str, Any]:
        """Second-chance call: feed the LLM its broken output + the list of
        Pydantic errors and ask it to fix the structure only."""
        client = self._ensure()
        from google.genai import types  # type: ignore

        prompt = build_repair_prompt(prev_output, validation_errors)
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=self._schema_obj,
            temperature=0.1,
        )

        response = client.models.generate_content(
            model=self._model_name(meta),
            contents=[types.Part.from_text(text=prompt)],
            config=config,
        )
        text = (response.text or "").strip()
        if not text:
            raise GeminiUnavailable("empty response from Gemini repair pass")

        return json.loads(text)
