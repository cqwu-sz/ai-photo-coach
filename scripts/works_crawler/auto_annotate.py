"""Auto-annotate raw downloads into draft works/ JSON via an LLM.

Inputs come from ``raw/<platform>/<id>.json`` (created by the fetchers)
plus the local image file referenced via ``image_uri``. Outputs land in
``drafts/<id>.json`` for human review.

The prompt is intentionally schema-constrained — we ask for the same
shape ``knowledge/works/`` consumes, with a fixed vocabulary on tags so
retrieval is stable. Vendor: works with any OpenAI-compatible chat
endpoint that supports image_url inputs (OpenAI proper, OpenRouter,
deepseek-vl, qwen-vl, etc.). Configure with:

    set OPENAI_API_KEY=<key>
    set OPENAI_BASE_URL=https://api.openai.com/v1
    set OPENAI_MODEL=gpt-4o-mini   # or your preferred VLM

Run:

    python scripts/works_crawler/auto_annotate.py \
        --in scripts/works_crawler/raw \
        --out scripts/works_crawler/drafts

Idempotent: if a draft already exists for the same id and is newer than
the raw json, the script skips re-annotating. Use ``--force`` to redo.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from common import ROOT, DRAFT_DIR, RAW_DIR, ensure_dirs, read_json, write_json

log = logging.getLogger("auto_annotate")


SYSTEM_PROMPT = """You are a senior photo critic. You will be shown a single photograph and asked to
deconstruct it into reusable craft choices that a learner could replicate in a
similar environment. Your output MUST be a single JSON object that conforms
EXACTLY to the schema described below — no prose, no Markdown fences, no extra
keys.

Tag vocabulary (use ONLY these values; multiple allowed):
  scene_tags:        ["urban","street","alleyway","architecture","interior",
                       "courtyard","park","forest","beach","mountain","lake",
                       "field","studio","rooftop","cafe","subway","museum",
                       "garden","village","temple","desert","snow","night"]
  light_tags:        ["golden_hour","blue_hour","harsh_noon","overcast",
                       "shade","indoor_warm","indoor_cool","low_light",
                       "backlight","side_light","front_light","rim",
                       "soft_top","mixed"]
  composition_tags:  ["rule_of_thirds","leading_line","symmetry",
                       "frame_within_frame","negative_space","centered",
                       "diagonal","golden_ratio","layered_depth"]

Output schema:
{
  "scene_tags":        list[str],   // 1..3 from scene_tags vocab
  "light_tags":        list[str],   // 1..3 from light_tags vocab
  "composition_tags":  list[str],   // 1..3 from composition_tags vocab
  "person_count":      int | null,  // 0,1,2,3,4 — null if not a portrait
  "why_good":          list[str],   // 2..4 Chinese sentences describing
                                    //   what works in this photo, citing
                                    //   light direction, depth layers,
                                    //   gesture, framing.
  "reusable_recipe": {
    "subject_pose":     str,        // Chinese, body / face / gesture
    "camera_position":  str,        // Chinese, distance + height + angle
    "framing":          str,        // Chinese, where subject sits in
                                    //   the frame + foreground use
    "focal_length":     str,        // e.g. "50mm equivalent (tele_2x on
                                    //   iPhone)" — actionable for phone shooters
    "aperture":         str,        // e.g. "f/2.0 大光圈" or "f/8 全清"
    "post_style":       str,        // Chinese, palette + grain + tone
    "applicable_to": {
      "needs_stereo":        bool,  // requires multi-height landmarks
      "needs_leading_line":  bool,
      "scene_modes":         list[str]   // subset of
        // ["portrait","closeup","full_body","documentary","scenery","light_shadow"]
    }
  }
}

Constraints:
- All Chinese text fields use simplified Chinese.
- DO NOT invent person_count > what's visible. If unsure, say null.
- DO NOT suggest scene_modes the photo doesn't support
  (e.g. light_shadow only when there's clearly directional light).
- camera_position must be concrete ("蹲低到主体腰部高度, 距离 2.5m") not abstract ("creative angle").
"""


def _img_to_data_url(path: Path) -> str:
    data = path.read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def call_llm(image_path: Path, hint: str) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY env var is required.")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": (
                        f"Reviewer hint (may be misleading; verify visually): "
                        f"{hint!r}\n\nReturn ONLY the JSON object."
                    )},
                    {"type": "image_url", "image_url": {"url": _img_to_data_url(image_path)}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
    }
    with httpx.Client(timeout=120) as cli:
        r = cli.post(f"{base.rstrip('/')}/chat/completions", json=body, headers=headers)
        r.raise_for_status()
        out = r.json()
    text = (((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    # Some providers stuff the JSON in a fenced block despite response_format.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return json.loads(text)


def merge_into_draft(raw: dict, annotation: dict) -> dict:
    """Take the empty_draft + LLM annotation, produce a draft ready for review."""
    draft = dict(raw)
    draft.update({
        "scene_tags":      annotation.get("scene_tags", []),
        "light_tags":      annotation.get("light_tags", []),
        "composition_tags": annotation.get("composition_tags", []),
        "person_count":    annotation.get("person_count"),
        "why_good":        annotation.get("why_good", []),
    })
    recipe = annotation.get("reusable_recipe") or {}
    base = draft.get("reusable_recipe") or {}
    base.update(recipe)
    base.setdefault("applicable_to", {}).setdefault("scene_modes", ["portrait"])
    draft["reusable_recipe"] = base
    draft.pop("scene_tags_hint", None)
    return draft


def iter_raw_pairs(raw_root: Path):
    for jp in raw_root.rglob("*.json"):
        meta = read_json(jp)
        if not isinstance(meta, dict):
            continue
        img_rel = meta.get("image_uri") or ""
        if not img_rel:
            continue
        img_path = (raw_root.parent / img_rel)
        if not img_path.exists():
            log.warning("image missing for %s: %s", jp.name, img_path)
            continue
        yield meta, jp, img_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="in_dir", default=str(RAW_DIR))
    ap.add_argument("--out", dest="out_dir", default=str(DRAFT_DIR))
    ap.add_argument("--force", action="store_true", help="Re-annotate even if draft exists & is newer")
    ap.add_argument("--limit", type=int, default=0, help="0 = no limit")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_dirs()
    raw_root = Path(args.in_dir).resolve()
    out_root = Path(args.out_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    pairs = list(iter_raw_pairs(raw_root))
    if args.limit:
        pairs = pairs[: args.limit]
    log.info("found %d raw items", len(pairs))

    for meta, raw_json_path, img_path in pairs:
        wid = meta.get("id") or raw_json_path.stem
        out_path = out_root / f"{wid}.json"
        if out_path.exists() and not args.force:
            if out_path.stat().st_mtime > raw_json_path.stat().st_mtime:
                log.info("skip %s (draft newer)", wid)
                continue
        hint = (meta.get("scene_tags_hint") or "").strip()
        try:
            annotation = call_llm(img_path, hint)
        except Exception as exc:                       # noqa: BLE001
            log.error("annotate %s failed: %s", wid, exc)
            continue
        draft = merge_into_draft(meta, annotation)
        draft["added_at"] = time.strftime("%Y-%m-%d")
        draft["reviewed_by"] = None
        write_json(out_path, draft)
        log.info("wrote %s", out_path)
        time.sleep(0.5)  # polite to vendor


if __name__ == "__main__":
    sys.exit(main() or 0)
