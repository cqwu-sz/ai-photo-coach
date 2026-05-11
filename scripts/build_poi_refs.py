"""Fetch CC0/Unsplash-licensed reference photos per (POI, style).

For each POI in `backend/data/poi_kb.db`, search Unsplash's public napi
for "<poi_name> <style_keyword>" and store the top N download URLs +
attribution into `web/img/poi/<poi_slug>/<style>/manifest.json`.

The LLM (when geo lands inside `nearest_poi`) is then handed the manifest
URLs as REFERENCE EXIF / PEER SHOTS visual anchors at prompt-build time.

Why napi instead of the official `/search/photos` API:
  - The official endpoint requires an access key + has 50 req/h cap.
  - napi is the same data feed unsplash.com itself uses; it's anonymous,
    rate-limited per-IP at ~5 rps, and returns the same metadata.
  - The Unsplash License (https://unsplash.com/license) explicitly
    permits free commercial reuse with attribution, which we collect
    into CREDITS.md alongside the images.

Usage:
    python scripts/build_poi_refs.py --styles japanese,golden --per 4
    python scripts/build_poi_refs.py --poi "West Lake" --per 6 --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

# Windows console defaults to GBK; printing Chinese POI names blows up
# without this. Safe no-op on POSIX.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.services import poi_kb  # noqa: E402

OUT_ROOT = ROOT / "web" / "img" / "poi"
NAPI_SEARCH = "https://unsplash.com/napi/search/photos"
RATE_SLEEP_S = 0.4   # be kind to anonymous napi
USER_AGENT = "ai-ios-photo-coach/0.1 (poi refs builder)"


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--styles", default="japanese,ambient,golden",
                   help="Comma-separated style keywords used in search query.")
    p.add_argument("--per", type=int, default=4,
                   help="Photos per (poi, style) combination.")
    p.add_argument("--poi", help="Filter to POIs whose name contains this string.")
    p.add_argument("--limit", type=int, default=20,
                   help="Max number of POIs to process this run.")
    p.add_argument("--english-only", action="store_true",
                   help="Skip POIs whose name contains non-ASCII chars "
                        "(Unsplash search performs poorly on CJK).")
    p.add_argument("--kinds", default="",
                   help="Comma-separated kinds filter (viewpoint,peak,landmark,...).")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv[1:])

    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    pois = _list_pois(args.poi)
    if args.english_only:
        pois = [p for p in pois if all(ord(c) < 128 for c in p["name"])]
    if args.kinds:
        wanted = {k.strip() for k in args.kinds.split(",") if k.strip()}
        pois = [p for p in pois if p["kind"] in wanted]
    pois = pois[: args.limit]
    if not pois:
        print("no POIs in DB — run scripts/seed_poi.py first")
        return 1

    print(f"will fetch refs for {len(pois)} POIs × {len(styles)} styles × {args.per} photos each")
    if args.dry_run:
        for poi in pois[:5]:
            for style in styles:
                q = f"{poi['name']} {style}"
                print(f"  · search: {q!r}")
        return 0

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    total = 0
    for poi in pois:
        slug = _slugify(poi["name"])
        for style in styles:
            q = f"{poi['name']} {style}"
            try:
                items = _search(q, args.per)
            except Exception as e:
                print(f"  ! search failed for {q!r}: {e}")
                continue
            if not items:
                continue
            out_dir = OUT_ROOT / slug / style
            out_dir.mkdir(parents=True, exist_ok=True)
            manifest = []
            for it in items:
                url = it["urls"]["small"]
                manifest.append({
                    "id":          it["id"],
                    "url":         url,
                    "permalink":   it.get("links", {}).get("html"),
                    "author_name": (it.get("user") or {}).get("name"),
                    "author_url":  (it.get("user") or {}).get("links", {}).get("html"),
                })
            (out_dir / "manifest.json").write_text(
                json.dumps({"poi": poi["name"], "style": style, "items": manifest}, indent=2),
                encoding="utf-8",
            )
            (out_dir / "CREDITS.md").write_text(
                _credits_md(poi["name"], style, manifest), encoding="utf-8",
            )
            total += len(manifest)
            print(f"  · {poi['name']} / {style} → {len(manifest)} refs")
            time.sleep(RATE_SLEEP_S)

    print(f"done — wrote {total} ref entries across {len(pois)} POIs")
    return 0


def _list_pois(name_filter: str | None) -> list[dict]:
    with poi_kb._connect() as con:
        rows = con.execute("SELECT id, name, lat, lon, kind FROM pois ORDER BY id").fetchall()
    out = [{"id": r[0], "name": r[1], "lat": r[2], "lon": r[3], "kind": r[4]} for r in rows]
    if name_filter:
        nf = name_filter.lower()
        out = [p for p in out if nf in p["name"].lower()]
    return out


def _search(query: str, per_page: int) -> list[dict]:
    qs = urllib.parse.urlencode({"query": query, "per_page": str(per_page)})
    url = f"{NAPI_SEARCH}?{qs}"
    payload = _http_get_json(url)
    return (payload or {}).get("results") or []


def _http_get_json(url: str) -> dict | None:
    """Use curl.exe (Unsplash napi 401s on Python urllib UA filter)."""
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise RuntimeError("curl not found in PATH")
    out = subprocess.run(
        [curl, "-sS", "-A", USER_AGENT, "-H", "Accept: application/json", url],
        check=True, capture_output=True,
    )
    if not out.stdout:
        return None
    # Force utf-8 — Unsplash always returns UTF-8 JSON, but Windows
    # subprocess in text=True mode tries GBK and explodes on non-ASCII
    # author names ("Léa", etc.).
    return json.loads(out.stdout.decode("utf-8", errors="replace"))


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower())
    return s.strip("-") or "poi"


def _credits_md(poi: str, style: str, items: list[dict]) -> str:
    lines = [
        f"# {poi} — {style} refs",
        "",
        "All images licensed under [Unsplash License](https://unsplash.com/license)",
        "(free commercial / non-commercial use, no permission required, attribution appreciated).",
        "",
    ]
    for it in items:
        author = it.get("author_name") or "Unknown"
        url = it.get("permalink") or it["url"]
        lines.append(f"- [{author}]({it.get('author_url') or '#'}) — [photo]({url})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
