"""OpenAICompatProvider tests with respx-mocked vendor endpoints."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.models import CaptureMeta, FrameMeta, SceneMode
from app.services.llm.openai_compat import OpenAICompatProvider
from app.services.llm.registry import find_model


def _meta() -> CaptureMeta:
    return CaptureMeta(
        person_count=1,
        scene_mode=SceneMode.portrait,
        frame_meta=[FrameMeta(index=i, azimuth_deg=i * 45) for i in range(8)],
    )


def _frames() -> list[bytes]:
    return [b"\xff\xd8\xff\xd9"] * 8


_GOOD_BODY = {
    "scene": {
        "type": "outdoor_park",
        "lighting": "golden_hour",
        "background_summary": "ok",
        "cautions": [],
    },
    "shots": [
        {
            "id": "shot_1",
            "title": "首选",
            "angle": {"azimuth_deg": 30, "pitch_deg": 0, "distance_m": 2.0},
            "composition": {"primary": "rule_of_thirds"},
            "camera": {
                "focal_length_mm": 50,
                "aperture": "f/1.8",
                "shutter": "1/250",
                "iso": 200,
            },
            "poses": [
                {
                    "person_count": 1,
                    "layout": "single",
                    "persons": [{"role": "person_a"}],
                }
            ],
            "rationale": "我建议你转到 30 度方向",
            "coach_brief": "看远点",
            "representative_frame_index": 0,
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_success_glm():
    cfg = find_model("glm-4.6v")
    assert cfg is not None
    provider = OpenAICompatProvider(cfg, api_key="testkey")

    route = respx.post(f"{cfg.base_url}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(_GOOD_BODY)}}
                ]
            },
        )
    )

    out = await provider.analyze(
        meta=_meta(),
        frames=_frames(),
        references=[],
        pose_summary="",
        camera_summary="",
        scene_mode="portrait",
    )

    assert route.called
    sent = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert sent["model"] == "glm-4.6v"
    # GLM uses json_object, not json_schema
    assert sent["response_format"] == {"type": "json_object"}
    # User content has interleaved image_url + text blocks
    user_content = sent["messages"][1]["content"]
    assert any(b["type"] == "image_url" for b in user_content)
    assert any(b["type"] == "text" for b in user_content)
    # Auth header carries the BYOK key
    assert route.calls[0].request.headers["authorization"] == "Bearer testkey"
    assert out["scene"]["lighting"] == "golden_hour"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_unauthorized_maps_to_provider_unauthorized():
    from app.services.llm.base import ProviderUnauthorized

    cfg = find_model("glm-4.6v")
    provider = OpenAICompatProvider(cfg, api_key="badkey")
    respx.post(f"{cfg.base_url}/chat/completions").mock(
        return_value=httpx.Response(401, text="invalid api key")
    )

    with pytest.raises(ProviderUnauthorized):
        await provider.analyze(
            meta=_meta(),
            frames=_frames(),
            references=[],
            pose_summary="",
            camera_summary="",
            scene_mode="portrait",
        )


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_strips_markdown_fence():
    cfg = find_model("glm-4.6v")
    provider = OpenAICompatProvider(cfg, api_key="testkey")
    fenced = "```json\n" + json.dumps(_GOOD_BODY) + "\n```"
    respx.post(f"{cfg.base_url}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": fenced}}]}
        )
    )
    out = await provider.analyze(
        meta=_meta(),
        frames=_frames(),
        references=[],
        pose_summary="",
        camera_summary="",
        scene_mode="portrait",
    )
    assert out["shots"][0]["id"] == "shot_1"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_schema_mode_uses_json_schema():
    """OpenAI gpt-4o uses json_schema mode."""
    cfg = find_model("gpt-4o")
    provider = OpenAICompatProvider(cfg, api_key="testkey")
    route = respx.post(f"{cfg.base_url}/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(_GOOD_BODY)}}]},
        )
    )
    await provider.analyze(
        meta=_meta(),
        frames=_frames(),
        references=[],
        pose_summary="",
        camera_summary="",
        scene_mode="portrait",
    )
    sent = json.loads(route.calls[0].request.content.decode("utf-8"))
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["name"] == "AnalyzeResponse"


@pytest.mark.asyncio
@respx.mock
async def test_openai_compat_ping():
    cfg = find_model("glm-4.6v")
    provider = OpenAICompatProvider(cfg, api_key="testkey")
    respx.post(f"{cfg.base_url}/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )
    )
    res = await provider.ping()
    assert res == {"ok": True, "snippet": "OK"}
