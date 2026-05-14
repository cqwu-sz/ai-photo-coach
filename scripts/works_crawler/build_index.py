"""Backfill CLIP embeddings into approved works/*.json entries.

Reads ``backend/app/knowledge/works/*.json``. For every entry whose
``embedding`` is null AND whose image is available, computes a CLIP
embedding and writes it back in-place.

We support two modes:

1. Local (PyTorch) — preferred when a GPU / OpenCLIP install is
   already on the maintainer's machine:

       pip install torch open_clip_torch pillow
       python scripts/works_crawler/build_index.py --local

2. Remote API — pings an OpenAI-compatible embeddings endpoint that
   accepts image input. Useful when running on a thin laptop:

       set OPENAI_API_KEY=<key>
       set OPENAI_EMBED_MODEL=clip-vit-l-14
       python scripts/works_crawler/build_index.py --remote

Both modes write back a list[float] L2-normalised vector. Search uses
cosine similarity, so the normalisation is what makes Jaccard tags +
embeddings comparable.

For seed entries with no ``image_uri`` (i.e. retired-from-public xhs
items), we skip — the recipe text similarity still works downstream
via tag overlap.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from common import APPROVED_DIR, RAW_DIR, read_json, write_json

log = logging.getLogger("build_index")


def _normalise(vec: list[float]) -> list[float]:
    import math
    s = math.sqrt(sum(v * v for v in vec))
    if s <= 0:
        return vec
    return [v / s for v in vec]


# --- local mode -----------------------------------------------------
def _local_embedder():
    try:
        import torch
        import open_clip
        from PIL import Image                            # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "Local mode needs `torch`, `open_clip_torch`, `pillow`. "
            "Install with: pip install torch open_clip_torch pillow"
        ) from exc

    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="openai",
    )
    model.eval()
    return model, preprocess, torch


def _embed_local(path: Path, model, preprocess, torch) -> list[float]:
    from PIL import Image
    img = Image.open(path).convert("RGB")
    x = preprocess(img).unsqueeze(0)
    with torch.no_grad():
        feat = model.encode_image(x)[0].cpu().tolist()
    return _normalise(feat)


# --- remote mode ----------------------------------------------------
def _embed_remote(path: Path) -> list[float]:
    import base64
    import httpx
    api_key = os.environ.get("OPENAI_API_KEY")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_EMBED_MODEL", "clip-vit-l-14")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY required for remote mode.")
    with path.open("rb") as fp:
        data = fp.read()
    payload = {
        "model": model,
        "input": "data:image/jpeg;base64," + base64.b64encode(data).decode(),
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=120) as cli:
        r = cli.post(f"{base.rstrip('/')}/embeddings", json=payload, headers=headers)
        r.raise_for_status()
        out = r.json()
    vec = out.get("data", [{}])[0].get("embedding") or []
    if not vec:
        raise RuntimeError(f"empty embedding for {path}")
    return _normalise(vec)


# --- runner ---------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--local", action="store_true")
    g.add_argument("--remote", action="store_true")
    ap.add_argument("--force", action="store_true",
                     help="Re-embed even if `embedding` is already populated.")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.local:
        model, preprocess, torch = _local_embedder()

    files = sorted(APPROVED_DIR.glob("*.json"))
    log.info("scanning %d works", len(files))
    for path in files:
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        if data.get("embedding") and not args.force:
            continue
        rel = data.get("image_uri") or ""
        if not rel:
            log.info("skip %s (no image_uri — recipe-only entry)", data.get("id"))
            continue
        img_path = (RAW_DIR.parent / rel).resolve()
        if not img_path.exists():
            log.warning("missing image %s for %s", img_path, data.get("id"))
            continue
        try:
            if args.local:
                emb = _embed_local(img_path, model, preprocess, torch)
            else:
                emb = _embed_remote(img_path)
        except Exception as exc:                           # noqa: BLE001
            log.error("embed %s failed: %s", data.get("id"), exc)
            continue
        data["embedding"] = emb
        write_json(path, data)
        log.info("embedded %s (dim=%d)", data.get("id"), len(emb))


if __name__ == "__main__":
    sys.exit(main() or 0)
