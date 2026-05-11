"""Generic OpenAI Chat-Completions client (image input via image_url blocks).

Tested vendors:
  - api.openai.com                              (gpt-4o family)
  - open.bigmodel.cn/api/paas/v4                (智谱 GLM-4.6V / 4V-Plus)
  - dashscope.aliyuncs.com/compatible-mode/v1   (Qwen-VL)
  - api.deepseek.com/v1                         (DeepSeek-VL2)
  - api.moonshot.cn/v1                          (Moonshot Vision)

Image-only providers can't ingest video natively, so we subsample the
keyframes down to ``config.max_images`` and ship them as base64 data URLs.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

import httpx

from ...models import AnalyzeResponse, CaptureMeta
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

# httpx timeout: large enough for slow vendors but bounded so we don't
# hang the request forever. Some vendors take 60s+ on 8 image inputs.
_DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=5.0)


class OpenAICompatProvider:
    def __init__(
        self,
        config: ProviderConfig,
        api_key: str | None,
        base_url: str | None = None,
    ):
        self.config = config
        self.api_key = api_key
        self.base_url = (base_url or config.base_url or "").rstrip("/")
        self._json_schema: dict[str, Any] | None = None

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
        # OpenAI-compatible providers don't take video; ignore. Panorama
        # is sent as the first image block when present.
        sampled = _subsample(frames, self.config.max_images)
        ref_sampled = _subsample(references, max(0, self.config.max_images // 2))

        user_prompt = build_user_prompt(
            meta=meta,
            pose_library_summary=pose_summary,
            camera_kb_summary=camera_summary,
            has_references=bool(references),
            scene_mode=scene_mode,
            has_panorama=panorama_jpeg is not None,
            has_video=False,
        )

        content: list[dict[str, Any]] = []
        if panorama_jpeg:
            content.extend(_image_blocks([panorama_jpeg]))
        content.extend(_image_blocks(sampled))
        if ref_sampled:
            content.append(
                {"type": "text", "text": f"--- 用户参考样片 (共 {len(ref_sampled)} 张) ---"}
            )
            content.extend(_image_blocks(ref_sampled))
        content.append({"type": "text", "text": user_prompt})

        body: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": content},
            ],
            "temperature": 0.5,
        }
        body.update(self._response_format())

        log.info(
            "calling openai_compat",
            extra={
                "model": self.config.id,
                "frames": len(sampled),
                "references": len(ref_sampled),
                "scene_mode": scene_mode,
            },
        )

        text = await self._post_chat(body)
        return _parse_json(text)

    async def repair(
        self,
        meta: CaptureMeta,
        prev_output: str,
        validation_errors: list[dict],
        scene_mode: str,
    ) -> dict[str, Any]:
        prompt = build_repair_prompt(prev_output, validation_errors)
        body: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        body.update(self._response_format())
        text = await self._post_chat(body)
        return _parse_json(text)

    async def ping(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.config.model_id,
            "messages": [{"role": "user", "content": "OK"}],
            "max_tokens": 8,
        }
        text = await self._post_chat(body)
        return {"ok": True, "snippet": text.strip()[:32]}

    # ---- internals -------------------------------------------------------

    def _response_format(self) -> dict[str, Any]:
        mode = self.config.json_schema_mode
        if mode == "schema":
            return {
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "AnalyzeResponse",
                        "schema": self._get_json_schema(),
                        "strict": False,
                    },
                }
            }
        if mode == "object":
            return {"response_format": {"type": "json_object"}}
        return {}

    def _get_json_schema(self) -> dict[str, Any]:
        if self._json_schema is None:
            self._json_schema = _analyze_response_json_schema()
        return self._json_schema

    async def _post_chat(self, body: dict[str, Any]) -> str:
        if not self.api_key:
            raise ProviderUnauthorized(
                f"{self.config.id}: API key not provided"
            )
        if not self.base_url:
            raise ProviderError(
                f"{self.config.id}: base_url not configured"
            )
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            r = await client.post(url, json=body, headers=headers)

        if r.status_code in (401, 403):
            raise ProviderUnauthorized(
                f"{self.config.id}: HTTP {r.status_code} {r.text[:200]}"
            )
        if r.status_code == 429:
            raise ProviderQuotaExceeded(
                f"{self.config.id}: HTTP 429 {r.text[:200]}"
            )
        if r.status_code >= 400:
            raise ProviderError(
                f"{self.config.id}: HTTP {r.status_code} {r.text[:300]}"
            )
        try:
            data = r.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"{self.config.id}: non-JSON HTTP body {r.text[:200]}"
            ) from exc
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                f"{self.config.id}: unexpected response shape: "
                f"{json.dumps(data)[:300]}"
            ) from exc


# ---- helpers ---------------------------------------------------------------


def _image_blocks(images: list[bytes]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for raw in images:
        b64 = base64.b64encode(raw).decode("ascii")
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )
    return blocks


def _subsample(items: list[bytes], n: int) -> list[bytes]:
    if n <= 0:
        return []
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # Some vendors love wrapping JSON in markdown fences despite asking
    # for json_object. Strip ```json … ``` if present.
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("openai_compat returned non-JSON: %s", text[:500])
        raise ProviderError(f"non-JSON response: {exc}") from exc


def _analyze_response_json_schema() -> dict[str, Any]:
    """Build a JSON Schema for AnalyzeResponse usable as
    response_format.json_schema. Pydantic v2 produces an OpenAPI 3.1 / JSON
    Schema 2020-12 representation that most OpenAI-compat vendors accept,
    but some (notably DashScope) reject ``$ref`` cycles and the
    ``exclusive*`` markers, so we resolve refs inline and strip them.
    """
    schema = AnalyzeResponse.model_json_schema(by_alias=True)
    return _strip_unsupported(schema)


def _strip_unsupported(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].rsplit("/", 1)[-1]
                target = defs.get(ref, {})
                merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
                return resolve(merged)
            new: dict[str, Any] = {}
            for k, v in node.items():
                if k in ("exclusiveMinimum", "exclusiveMaximum", "format"):
                    continue
                new[k] = resolve(v)
            return new
        if isinstance(node, list):
            return [resolve(x) for x in node]
        return node

    return resolve(schema)
