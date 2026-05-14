"""Maintainer-only fetch from a Xiaohongshu (小红书) user page.

Wraps ReaJason/xhs (https://github.com/ReaJason/xhs). NOT installed as a
hard requirement of this toolkit — install only if you need it:

    pip install xhs

Output lands in ``raw/xhs/<note_id>.jpg`` + ``<note_id>.json`` exactly
like Unsplash, so ``auto_annotate.py`` and ``review_ui.py`` consume the
same shape.

Authentication:
    set XHS_COOKIE="<your full cookie string>"
    set XHS_SIGNER="<optional sign-helper URL when needed>"

Privacy & legal:
    These items are for the **maintainer's local review queue only**.
    Approved entries land in ``backend/app/knowledge/works/`` but only
    after the reviewer fills in proper attribution and either confirms
    fair-use / removes the image_uri. The retrieval pipeline only ever
    uses the recipe text + embeddings — it does NOT serve raw xhs
    thumbnails to end-users (the review UI enforces this).
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

import httpx

from common import (
    RAW_DIR,
    empty_draft,
    ensure_dirs,
    safe_id,
    write_json,
)

log = logging.getLogger("xhs_fetch")


def _require_xhs():
    try:
        from xhs import XhsClient  # type: ignore
    except ImportError as exc:
        raise SystemExit(
            "xhs library missing — `pip install xhs` first (it's optional)."
        ) from exc
    return XhsClient


def _download(url: str) -> bytes:
    with httpx.Client(timeout=30) as cli:
        r = cli.get(url, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) works_crawler/0.1",
        })
        r.raise_for_status()
        return r.content


def fetch_user_notes(user_id: str, max_notes: int) -> list[dict]:
    XhsClient = _require_xhs()
    cookie = os.environ.get("XHS_COOKIE", "")
    if not cookie:
        raise SystemExit("XHS_COOKIE env var is required.")
    cli = XhsClient(cookie=cookie)
    notes: list[dict] = []
    cursor = ""
    # The library variants are slightly different — try the common ones.
    for _ in range(20):
        try:
            page = cli.get_user_notes(user_id=user_id, cursor=cursor)
        except Exception as exc:                      # noqa: BLE001
            log.warning("user_notes page failed: %s", exc)
            break
        items = page.get("notes", []) if isinstance(page, dict) else []
        if not items:
            break
        notes.extend(items)
        cursor = page.get("cursor", "") or ""
        if not cursor or len(notes) >= max_notes:
            break
    return notes[:max_notes]


def _first_image_url(note: dict) -> str | None:
    # Schema varies across xhs API responses; try a few common shapes.
    for key in ("image_list", "images", "image_urls"):
        v = note.get(key) or []
        if v and isinstance(v, list):
            x = v[0]
            if isinstance(x, str):
                return x
            if isinstance(x, dict):
                for k in ("url", "trace_url", "url_default", "url_pre"):
                    if x.get(k):
                        return x[k]
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", required=True, help="xhs user id (the numeric one, not the @handle)")
    ap.add_argument("--count", type=int, default=30)
    ap.add_argument("--handle", default="", help="Optional @handle to record in attribution")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_dirs()
    out_dir = RAW_DIR / "xhs"
    out_dir.mkdir(parents=True, exist_ok=True)

    notes = fetch_user_notes(args.user, args.count)
    log.info("fetched %d notes for user=%s", len(notes), args.user)

    handle = args.handle or ""
    if handle and not handle.startswith("@"):
        handle = "@" + handle

    for note in notes:
        note_id = (note.get("id") or note.get("note_id") or "").strip()
        if not note_id:
            continue
        img_url = _first_image_url(note)
        if not img_url:
            continue
        try:
            data = _download(img_url)
        except Exception as exc:                       # noqa: BLE001
            log.warning("download %s failed: %s", note_id, exc)
            continue
        img_path = out_dir / f"{note_id}.jpg"
        img_path.write_bytes(data)
        page_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        draft = empty_draft(
            work_id=safe_id("xhs", note_id),
            source_platform="xhs",
            source_url=page_url,
            author=handle or None,
            license="unknown",
            image_uri=str(img_path.relative_to(RAW_DIR.parent)),
        )
        # Stash the title as a soft hint to the annotator + reviewer.
        title = (note.get("title") or note.get("desc") or "").strip()
        draft["scene_tags_hint"] = title[:200]
        write_json(out_dir / f"{note_id}.json", draft)
        log.info("saved %s (%s)", note_id, page_url)


if __name__ == "__main__":
    sys.exit(main() or 0)
