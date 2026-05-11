"""Maintain the Step-3 style picker assets in `web/img/style/`.

This is the single tool you should run after editing the STYLES list
below. It will:

  1. Re-download each photo into `web/img/style/<slug>/0N.jpg` via
     Unsplash's anonymous `/photos/<id>/download?w=640` endpoint.
  2. Fetch each photo's metadata (author + permalink) via Unsplash's
     public `/napi/photos/<id>` endpoint so we can build a complete
     attribution table.
  3. Regenerate `web/img/style/manifest.json` (consumed by
     `web/js/style_picker.js`) and `web/img/style/CREDITS.md`
     (App Store due-diligence record).

Why a script and not a one-off:
  - You'll want to swap photos as taste evolves; manual file ops +
    hand-editing manifest is error-prone.
  - Keeping authors in sync with whatever images are on disk is the
    one part of "App Store IP compliance" that's easy to get wrong.

Usage (from repo root):
  python scripts/build_style_assets.py            # fetch missing only
  python scripts/build_style_assets.py --force    # re-fetch everything
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = REPO_ROOT / "web" / "img" / "style"

# When you tweak the picker:
#   - Change `images` (preserve list order; that controls 01..0N filenames).
#   - Re-run this script. Manifest + credits + JPEGs all stay in sync.
STYLES: list[dict] = [
    {
        "id": "cinematic_moody",
        "label_zh": "氛围感",
        "label_en": "cinematic moody",
        "keywords": ["cinematic", "moody"],
        "blurb_zh": "黄昏 / 夜晚 · 想要点情绪",
        "summary_long_zh": (
            "光线偏暗、阴影占大半、画面像电影截图一样有故事感。"
            "适合黄昏、夜晚、雾天、霓虹街头这种「光不那么平」的时候。"
        ),
        "images": [
            ("l7R85WBKl1c", "portrait"),
            ("G0KVzxBb2xo", "scenery"),
            ("EKQMsJPxv3g", "portrait"),
            ("6iCxGiQ8bws", "scenery"),
            ("nTZZj8dPQhM", "scenery"),
        ],
    },
    {
        "id": "clean_bright",
        "label_zh": "清爽日系",
        "label_en": "clean bright",
        "keywords": ["clean", "bright"],
        "blurb_zh": "白墙 / 海边 · 不复杂的明亮",
        "summary_long_zh": (
            "画面亮、色少、留白多，像晴天午后随手一拍那种感觉。"
            "适合白墙建筑、浅色小巷、海滩、沙漠等明亮干净的户外环境。"
        ),
        "images": [
            ("sj2YTX5K_BU", "portrait"),
            ("6gLsBZ1_4Gg", "scenery"),
            ("EDjJ0eKEW9Y", "scenery"),
            ("XvWl5RZ51gQ", "scenery"),
        ],
    },
    {
        "id": "film_warm",
        "label_zh": "温柔暖光",
        "label_en": "film warm",
        "keywords": ["film", "warm"],
        "blurb_zh": "金光时分 · 想要复古一点",
        "summary_long_zh": (
            "整张图发暖、偏黄、肤色奶油，像老胶片冲出来的样子。"
            "适合日落前一小时的海边、森林、田野这种暖光时刻。"
        ),
        "images": [
            ("xnS3upQYaOk", "portrait"),
            ("NB4Rnh9zjKM", "portrait"),
            ("-s8_61ynxAY", "portrait"),
            ("z7nAuJa-YIg", "scenery"),
            ("aCJPS1G7GKU", "scenery"),
        ],
    },
    {
        "id": "street_candid",
        "label_zh": "自然随手",
        "label_en": "street candid",
        "keywords": ["street", "candid"],
        "blurb_zh": "走路 / 不摆 pose · 像旅行随拍",
        "summary_long_zh": (
            "不用专门站好，自然走动、回头、笑就行；画面里能看到周围环境。"
            "适合街市、巷子、人群里那种「正在发生」的时刻。"
        ),
        "images": [
            ("03di_wcQlj4", "portrait"),
            ("wsdV6Tts8sA", "portrait"),
            ("2h0Vt8l-_GQ", "portrait"),
            ("OfsOzOIvNFc", "portrait"),
            ("fy-VKC2BYHM", "portrait"),
            ("ld4I1YL70YI", "scenery"),
        ],
    },
    {
        "id": "editorial_fashion",
        "label_zh": "大片感",
        "label_en": "editorial fashion",
        "keywords": ["editorial", "fashion"],
        "blurb_zh": "服装 / 想拍出杂志封面感",
        "summary_long_zh": (
            "姿态明显、背景干净或对比强、构图大胆，像杂志里那种「摆得很正经」的照片。"
            "适合屋顶、沙漠、海边、混凝土建筑这种本身就有设计感的环境。"
        ),
        "images": [
            ("WOZYzurGqwM", "portrait"),
            ("cmDq1L-4MXE", "portrait"),
            ("qC_XoctgYKw", "portrait"),
            ("fRnYYL4jn5I", "portrait"),
            ("O6i6D2x9xts", "portrait"),
            ("BdeFclXOQ2Y", "scenery"),
        ],
    },
]


def http_get(url: str, *, accept_json: bool = True) -> bytes:
    """Use curl.exe — Unsplash's napi 401s on python urllib (UA filter)."""
    headers = ["-A", "curl/8.18.0"]
    if accept_json:
        headers += ["-H", "Accept: application/json"]
    out = subprocess.run(
        ["curl.exe", "-L", "-s", "-f", *headers, url],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        raise RuntimeError(
            f"curl failed for {url}: rc={out.returncode} stderr={out.stderr!r}"
        )
    return out.stdout


def download_photo(style_id: str, n: int, photo_id: str, force: bool) -> bool:
    """Download `0N.jpg`. Returns True if a network fetch happened."""
    out_dir = ASSETS_DIR / style_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{n:02d}.jpg"
    if out_path.exists() and out_path.stat().st_size > 5_000 and not force:
        return False
    url = f"https://unsplash.com/photos/{photo_id}/download?w=640"
    data = http_get(url, accept_json=False)
    if len(data) < 5_000:
        raise RuntimeError(
            f"downloaded {style_id}/{n:02d} too small ({len(data)}b) — likely 404 page"
        )
    out_path.write_bytes(data)
    return True


def fetch_meta(photo_id: str) -> dict:
    raw = http_get(f"https://unsplash.com/napi/photos/{photo_id}")
    return json.loads(raw)


def build_manifest(meta_cache: dict[str, dict]) -> dict:
    out = {
        "version": 2,
        "license": "Unsplash License",
        "license_url": "https://unsplash.com/license",
        "note": (
            "All images downloaded under the free Unsplash License "
            "(commercial use allowed, attribution not required by Unsplash "
            "but kept here as App Store due-diligence). "
            "See CREDITS.md for the full per-image table."
        ),
        "styles": [],
    }
    for s in STYLES:
        images = []
        for n, (pid, kind) in enumerate(s["images"], start=1):
            meta = meta_cache.get(pid, {})
            user = (meta.get("user") or {})
            images.append({
                "file": f"{n:02d}.jpg",
                "kind": kind,
                "id": pid,
                "author": user.get("name") or "Unknown",
                "author_url": (user.get("links") or {}).get("html") or "",
                "source": f"https://unsplash.com/photos/{pid}",
            })
        out["styles"].append({
            "id": s["id"],
            "label_zh": s["label_zh"],
            "label_en": s["label_en"],
            "keywords": s["keywords"],
            "blurb_zh": s["blurb_zh"],
            "summary_long_zh": s["summary_long_zh"],
            "images": images,
        })
    return out


def build_credits(manifest: dict) -> str:
    lines = [
        "# 风格示例图来源（Photo Credits）",
        "",
        "所有图片来自 [Unsplash](https://unsplash.com)，遵循 ",
        "[Unsplash License](https://unsplash.com/license)：可免费用于商业与非",
        "商业用途，**Unsplash 不强制署名**，但 App Store 审核惯例上"
        "建议保留一份完整 credits 表以示已尽合理注意义务，因此本表保留。",
        "",
        "**iOS 上架时**：把本表内容（或其精简版）放到 App 的 ",
        "`Settings → 致谢 / Credits` 里，链接指向各摄影师的 Unsplash 主页。",
        "",
        "_本文件由 `scripts/build_style_assets.py` 自动生成，请勿手动编辑。_",
        "",
    ]
    for s in manifest["styles"]:
        lines.append(f"## {s['label_zh']}（`{s['id']}` · {s['label_en']}）")
        lines.append("")
        lines.append("| file | kind | photographer | source |")
        lines.append("|---|---|---|---|")
        for im in s["images"]:
            kind_zh = "环境" if im["kind"] == "scenery" else "人像"
            author = im["author"]
            au = im["author_url"]
            author_md = (
                f"[{author}]({au})" if au and author != "Unknown" else author
            )
            lines.append(
                f"| `{im['file']}` | {kind_zh} | {author_md} | "
                f"[Unsplash]({im['source']}) |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-download images even if they already exist on disk.",
    )
    args = ap.parse_args()

    # Pass 1 — make sure all JPEGs are on disk.
    fetched = 0
    for s in STYLES:
        for n, (pid, _kind) in enumerate(s["images"], start=1):
            try:
                if download_photo(s["id"], n, pid, force=args.force):
                    fetched += 1
                    print(f"  [+] {s['id']}/{n:02d}.jpg  ({pid})")
                    time.sleep(0.15)
                else:
                    print(f"  [=] {s['id']}/{n:02d}.jpg  ({pid})")
            except Exception as e:
                print(f"  [!] {s['id']}/{n:02d}.jpg  ({pid}): {e}")
                return 1
    print(f"images: {fetched} fetched, "
          f"{sum(len(s['images']) for s in STYLES) - fetched} cached")

    # Pass 2 — fetch napi metadata for credits.
    meta_cache: dict[str, dict] = {}
    for s in STYLES:
        for pid, _ in s["images"]:
            try:
                meta_cache[pid] = fetch_meta(pid)
                time.sleep(0.15)
            except Exception as e:
                print(f"  [!] napi {pid}: {e}")
                meta_cache[pid] = {}
    unknown_authors = sum(
        1 for m in meta_cache.values()
        if not (m.get("user") or {}).get("name")
    )
    print(f"napi: {len(meta_cache)} fetched, {unknown_authors} unknown")

    # Pass 3 — write manifest + credits.
    manifest = build_manifest(meta_cache)
    (ASSETS_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (ASSETS_DIR / "CREDITS.md").write_text(
        build_credits(manifest) + "\n", encoding="utf-8",
    )
    total = sum(len(s["images"]) for s in manifest["styles"])
    print(f"wrote manifest.json + CREDITS.md ({total} images, "
          f"{unknown_authors} unknown authors)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
