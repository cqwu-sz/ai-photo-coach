"""Reference-image fingerprint extraction (W6).

Given the user's uploaded reference photos (raw bytes), produce a
``ReferenceFingerprint`` per image:
  - Top-5 colour palette (hex + weight) via k-means on downsampled pixels.
  - Contrast band (low/mid/high) from luma p5/p95 spread.
  - Saturation band (low/mid/high) from mean HSV saturation.
  - Mood keywords inferred from palette + contrast/saturation heuristics.
  - Optional CLIP embedding (when ``open_clip_torch`` is installed) — the
    embedding itself isn't shipped to the client, only its dimensionality
    so the LLM knows the fingerprint is dense.

We avoid hard-importing torch / open_clip / colorthief at module load so
the analyze service never fails on dependency mismatch — extraction
gracefully degrades to colour palette only when those libs are absent.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

from ..models.schemas import ReferenceFingerprint

log = logging.getLogger(__name__)


def extract_fingerprints(images_bytes: list[bytes],
                         enable_embedding: bool = False) -> list[ReferenceFingerprint]:
    out: list[ReferenceFingerprint] = []
    for i, buf in enumerate(images_bytes):
        try:
            out.append(_extract_one(i, buf, enable_embedding))
        except Exception as e:                                       # noqa: BLE001
            log.info("style fingerprint #%d failed: %s", i, e)
    return out


def _extract_one(index: int, buf: bytes,
                 enable_embedding: bool) -> ReferenceFingerprint:
    palette, weights, contrast_band, sat_band = _color_summary(buf)
    mood = _mood_keywords(palette, contrast_band, sat_band)
    emb_dim = _maybe_embed(buf) if enable_embedding else None
    return ReferenceFingerprint(
        index=index,
        palette=palette,
        palette_weights=weights,
        contrast_band=contrast_band,
        saturation_band=sat_band,
        mood_keywords=mood,
        embedding_dims=emb_dim,
    )


def _color_summary(buf: bytes) -> tuple[list[str], list[float], str, str]:
    """Returns (palette_hex, weights, contrast_band, saturation_band)."""
    try:
        from PIL import Image
        import numpy as np
    except Exception:                                                # noqa: BLE001
        return ([], [], "mid", "mid")
    img = Image.open(io.BytesIO(buf)).convert("RGB")
    img.thumbnail((192, 192))
    arr = np.asarray(img, dtype="float32") / 255.0
    flat = arr.reshape(-1, 3)

    # K-means in numpy (no sklearn dep). 5 clusters, 8 iterations, k++ish init.
    rng = np.random.default_rng(seed=42)
    k = 5
    init_idx = rng.choice(len(flat), size=k, replace=False)
    centroids = flat[init_idx].copy()
    for _ in range(8):
        d = ((flat[:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        labels = d.argmin(axis=1)
        for j in range(k):
            mask = labels == j
            if mask.any():
                centroids[j] = flat[mask].mean(axis=0)

    counts = [(int((labels == j).sum()), centroids[j]) for j in range(k)]
    counts.sort(key=lambda t: t[0], reverse=True)
    total = max(1, sum(c for c, _ in counts))
    palette: list[str] = []
    weights: list[float] = []
    for c, rgb in counts:
        r, g, b = (int(round(v * 255)) for v in rgb)
        palette.append(f"#{r:02x}{g:02x}{b:02x}")
        weights.append(round(c / total, 3))

    luma = (0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2])
    p5, p95 = float(np.quantile(luma, 0.05)), float(np.quantile(luma, 0.95))
    spread = p95 - p5
    contrast_band = "low" if spread < 0.35 else "high" if spread > 0.65 else "mid"

    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    sat = ((mx - mn) / np.where(mx < 1e-6, 1.0, mx))
    sat_mean = float(sat.mean())
    sat_band = "low" if sat_mean < 0.18 else "high" if sat_mean > 0.45 else "mid"
    return palette, weights, contrast_band, sat_band


def _mood_keywords(palette: list[str], contrast: str, saturation: str) -> list[str]:
    """Hand-rolled rules: avoids any LLM call at extraction time."""
    out: list[str] = []
    if contrast == "low":
        out.append("低对比")
    elif contrast == "high":
        out.append("高对比")
    if saturation == "low":
        out.append("低饱和")
    elif saturation == "high":
        out.append("高饱和")
    # Crude warm/cool tilt from the dominant colour.
    if palette:
        try:
            r = int(palette[0][1:3], 16)
            b = int(palette[0][5:7], 16)
            if r > b + 25:
                out.append("暖调")
            elif b > r + 25:
                out.append("冷调")
        except Exception:                                            # noqa: BLE001
            pass
    if not out:
        out.append("中性")
    return out


def _maybe_embed(buf: bytes) -> Optional[int]:
    """Try to compute an OpenCLIP embedding. Returns the dimensionality
    on success, ``None`` if the library isn't installed."""
    try:
        import open_clip                                             # noqa: F401
    except Exception:                                                # noqa: BLE001
        return None
    try:
        from PIL import Image
        import torch
        model, _, preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k",
        )
        model.eval()
        img = Image.open(io.BytesIO(buf)).convert("RGB")
        with torch.no_grad():
            t = preprocess(img).unsqueeze(0)
            emb = model.encode_image(t).squeeze(0).cpu().numpy()
        return int(emb.shape[0])
    except Exception as e:                                           # noqa: BLE001
        log.info("clip embed failed: %s", e)
        return None


def to_prompt_block(fps: list[ReferenceFingerprint]) -> str:
    if not fps:
        return ""
    lines = ["── REFERENCE FINGERPRINTS（用户参考图的客观特征摘要）──"]
    for fp in fps:
        palette_str = " ".join(fp.palette[:5]) if fp.palette else "(no palette)"
        lines.append(
            f"  · ref#{fp.index}: 调色板 {palette_str}; 对比 {fp.contrast_band}; "
            f"饱和 {fp.saturation_band}; 关键词 {','.join(fp.mood_keywords) or '中性'}"
        )
    lines.append(
        "  在 rationale / camera 选择中**显式引用** ref#N 的特征，例如「参考 #2 "
        "的低对比暖调，建议 EV -0.3、白平衡 4500K」。"
    )
    return "\n".join(lines)


def palette_match_score(shot_camera_temp_k: Optional[int],
                        shot_palette_estimate: Optional[list[str]],
                        ref: ReferenceFingerprint) -> float:
    """Return a [0,1] score for how well a recommended shot matches the
    reference fingerprint. Used by ``style_compliance`` (W6.2)."""
    score = 0.5
    if shot_camera_temp_k is not None:
        # Map ref colour bias (warm/cool) to a target K.
        target_k = 5500
        if "暖调" in ref.mood_keywords:
            target_k = 4500
        elif "冷调" in ref.mood_keywords:
            target_k = 6500
        delta = abs(shot_camera_temp_k - target_k)
        score = max(0.0, 1.0 - delta / 3000.0)
    if shot_palette_estimate and ref.palette:
        overlap = len(set(_quantize(shot_palette_estimate)) & set(_quantize(ref.palette)))
        score = (score + overlap / max(1, len(ref.palette))) / 2.0
    return round(min(1.0, max(0.0, score)), 3)


def _quantize(palette: list[str]) -> list[str]:
    """Reduce '#a3b1c2' → '#abc' for fuzzy palette overlap."""
    out: list[str] = []
    for hx in palette:
        if hx.startswith("#") and len(hx) == 7:
            out.append("#" + hx[1] + hx[3] + hx[5])
    return out
