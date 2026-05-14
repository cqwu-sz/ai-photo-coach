"""Server-side CLIP embedding shim.

We deliberately do NOT bake torch / open_clip into the runtime image —
the analyze pipeline can't pay a 600 MB model load at every cold start.
Instead this module provides two interchangeable backends:

1. ``OpenClipBackend``: loads ``open_clip_torch`` lazily (singleton).
   Only initialised when an env (``CLIP_BACKEND=local``) opts in.
2. ``RemoteEmbedBackend``: hits an OpenAI-compatible /embeddings
   endpoint that accepts image inputs. The default when
   ``CLIP_BACKEND`` is ``"remote"`` or unset and an ``OPENAI_API_KEY``
   is present.

Both backends produce L2-normalised float vectors of consistent dim
(384/512 depending on the model). Callers just see ``embed_image`` /
``embed_text`` — they should never special-case the backend.

For test environments + the default path we expose ``NoopBackend``
which returns ``None``. The ``works_retrieval`` module that
calls into here degrades to pure tag-based recall in that case.
"""
from __future__ import annotations

import base64
import logging
import math
import os
from functools import lru_cache
from typing import Optional, Protocol

log = logging.getLogger(__name__)


class ClipBackend(Protocol):
    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]: ...
    def embed_text(self,  text: str)       -> Optional[list[float]]: ...


class NoopBackend:
    """Returns None for everything. Used in tests + when no CLIP is configured."""
    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        return None
    def embed_text(self, text: str) -> Optional[list[float]]:
        return None


# ---------------------------------------------------------------------------
# Open-CLIP local backend (lazy)
# ---------------------------------------------------------------------------
class OpenClipBackend:
    """Loads ViT-B-32 (or whatever ``CLIP_MODEL`` env says) once on first
    use. Image and text embed share the same projection space so
    cross-modal recall works.
    """
    def __init__(self):
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._torch = None

    def _ensure(self):
        if self._model is not None:
            return
        try:
            import torch                                  # type: ignore
            import open_clip                              # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "OpenClipBackend selected but torch / open_clip_torch missing"
            ) from exc
        model_name = os.environ.get("CLIP_MODEL", "ViT-B-32")
        pretrained = os.environ.get("CLIP_PRETRAINED", "openai")
        log.info("loading CLIP model=%s pretrained=%s", model_name, pretrained)
        model, _, preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained,
        )
        model.eval()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(model_name)
        self._torch = torch

    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        try:
            from PIL import Image
            import io
        except ImportError:
            return None
        self._ensure()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        x = self._preprocess(img).unsqueeze(0)
        with self._torch.no_grad():
            feat = self._model.encode_image(x)[0].cpu().tolist()
        return _normalise(feat)

    def embed_text(self, text: str) -> Optional[list[float]]:
        if not text:
            return None
        self._ensure()
        tokens = self._tokenizer([text])
        with self._torch.no_grad():
            feat = self._model.encode_text(tokens)[0].cpu().tolist()
        return _normalise(feat)


# ---------------------------------------------------------------------------
# Remote backend (OpenAI-compatible /embeddings)
# ---------------------------------------------------------------------------
class RemoteEmbedBackend:
    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY", "")
        self.base    = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model   = os.environ.get("CLIP_REMOTE_MODEL", "clip-vit-l-14")

    def _post(self, payload: dict) -> Optional[list[float]]:
        if not self.api_key:
            return None
        try:
            import httpx
        except ImportError:
            return None
        try:
            with httpx.Client(timeout=60) as cli:
                r = cli.post(
                    f"{self.base.rstrip('/')}/embeddings",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                )
                r.raise_for_status()
                out = r.json()
        except Exception as exc:                            # noqa: BLE001
            log.warning("remote embedding failed: %s", exc)
            return None
        vec = (out.get("data") or [{}])[0].get("embedding") or []
        if not vec:
            return None
        return _normalise(vec)

    def embed_image(self, image_bytes: bytes) -> Optional[list[float]]:
        return self._post({
            "model": self.model,
            "input": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode(),
        })

    def embed_text(self, text: str) -> Optional[list[float]]:
        if not text:
            return None
        return self._post({"model": self.model, "input": text})


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_backend() -> ClipBackend:
    """Pick a backend based on ``CLIP_BACKEND`` env. Never raises —
    falls back to NoopBackend so analyze never errors due to CLIP."""
    choice = os.environ.get("CLIP_BACKEND", "").strip().lower()
    if choice == "local":
        try:
            return OpenClipBackend()
        except Exception as exc:                            # noqa: BLE001
            log.warning("OpenClipBackend init failed (%s); falling back to remote", exc)
            choice = "remote"
    if choice == "remote" or (choice == "" and os.environ.get("OPENAI_API_KEY")):
        return RemoteEmbedBackend()
    return NoopBackend()


def _normalise(vec: list[float]) -> list[float]:
    s = math.sqrt(sum(v * v for v in vec))
    if s <= 0:
        return vec
    return [v / s for v in vec]
