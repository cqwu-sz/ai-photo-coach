"""Fetch a batch of photos from Unsplash via the napi search endpoint.

Reuses the same napi entry-point that ``scripts/build_style_assets.py``
already uses, plus the ``UNSPLASH_ACCESS_KEY`` env var (set up for the
project's dev credentials). Output drops into
``scripts/works_crawler/raw/unsplash/`` ready for ``auto_annotate.py``.

Usage:
    set UNSPLASH_ACCESS_KEY=<dev key>
    python scripts/works_crawler/unsplash_fetch.py --query "urban portrait golden hour" --count 25

We deliberately keep this thin — we *don't* want to ship a generic
multi-platform abstraction here, just enough to seed the corpus.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

import httpx

from common import (
    RAW_DIR,
    ensure_dirs,
    empty_draft,
    safe_id,
    write_json,
)

log = logging.getLogger("unsplash_fetch")
NAPI = "https://unsplash.com/napi/search/photos"


def fetch_photo_bytes(url: str) -> bytes:
    with httpx.Client(timeout=30) as cli:
        r = cli.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content


def search(query: str, per_page: int = 30, page: int = 1) -> list[dict]:
    """Return the raw ``results`` list from the napi search endpoint."""
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY", "")
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (works_crawler/0.1)",
    }
    params = {
        "query": query,
        "per_page": per_page,
        "page": page,
        "content_filter": "high",
    }
    if access_key:
        # napi tolerates either Authorization header or no header (it
        # serves the public results either way). We send it when set to
        # raise rate-limits and respect ToS.
        headers["Authorization"] = f"Client-ID {access_key}"
    with httpx.Client(timeout=30) as cli:
        r = cli.get(NAPI, params=params, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"Unsplash napi {r.status_code}: {r.text[:200]}")
        return r.json().get("results", []) or []


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--query", required=True, help="Search query, e.g. 'urban portrait golden hour'")
    ap.add_argument("--count", type=int, default=20, help="How many photos to download")
    ap.add_argument("--per-page", type=int, default=30)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ensure_dirs()
    out_dir = RAW_DIR / "unsplash"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    page = 1
    while len(results) < args.count and page <= 6:
        batch = search(args.query, per_page=args.per_page, page=page)
        if not batch:
            break
        results.extend(batch)
        page += 1
        time.sleep(0.4)
    results = results[: args.count]
    log.info("got %d results for query=%r", len(results), args.query)

    for r in results:
        photo_id = r.get("id") or ""
        if not photo_id:
            continue
        urls = r.get("urls") or {}
        thumb_url = urls.get("small") or urls.get("regular")
        if not thumb_url:
            continue
        try:
            data = fetch_photo_bytes(thumb_url)
        except Exception as exc:                 # noqa: BLE001
            log.warning("download %s failed: %s", photo_id, exc)
            continue
        wid = safe_id("unsplash", photo_id)
        img_path = out_dir / f"{photo_id}.jpg"
        img_path.write_bytes(data)
        author = ((r.get("user") or {}).get("username") or "") or None
        author_handle = f"@{author}" if author else None
        page_url = (r.get("links") or {}).get("html") or f"https://unsplash.com/photos/{photo_id}"
        draft = empty_draft(
            work_id=wid,
            source_platform="unsplash",
            source_url=page_url,
            author=author_handle,
            license="unsplash",
            image_uri=str(img_path.relative_to(RAW_DIR.parent)),
        )
        # Stamp the alt_description as a starting hint for the
        # annotator — gets overwritten by the LLM but useful for the
        # reviewer to sanity-check the topic match.
        draft["scene_tags_hint"] = (r.get("alt_description") or "").strip()
        write_json(out_dir / f"{photo_id}.json", draft)
        log.info("saved %s (%s)", wid, page_url)


if __name__ == "__main__":
    sys.exit(main() or 0)
