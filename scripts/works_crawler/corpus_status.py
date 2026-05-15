"""Curator dashboard: print where the works corpus stands and what's
still missing.

Counts:

  - approved   = JSON files under backend/app/knowledge/works/
  - drafts     = JSON files under scripts/works_crawler/drafts/ awaiting review
  - rejected   = JSON files under scripts/works_crawler/drafts/_rejected/
  - raw        = JSON files under scripts/works_crawler/raw/<platform>/

Coverage:

  - tally approved entries by (scene_tag, light_tag) pairs
  - surface the 8 most-common combos AND the combos that are stuck
    at 0 entries so the curator knows what to chase next.

Run::

    python scripts/works_crawler/corpus_status.py [--target-per-bucket 5]

Exit 0 always; intended for human consumption, not CI gating.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APPROVED_DIR = ROOT / "backend" / "app" / "knowledge" / "works"
DRAFT_DIR = ROOT / "scripts" / "works_crawler" / "drafts"
RAW_DIR = ROOT / "scripts" / "works_crawler" / "raw"

# Curator's target taxonomy — adjust here when the team agrees on a new
# scene / light dimension. Coverage report uses the cross-product of
# these as "buckets that should not stay at 0".
TARGET_SCENES = [
    "urban_street", "alleyway", "park", "garden", "beach", "mountain",
    "indoor_cafe", "indoor_home", "studio", "architecture", "rooftop",
    "subway", "harbor", "rural",
]
TARGET_LIGHTS = [
    "golden_hour", "blue_hour", "harsh_noon", "overcast", "indoor_warm",
    "indoor_cool", "backlight", "rim", "low_light", "mixed",
]


def load_jsons(dir_path: Path) -> list[dict]:
    """Load every .json under dir_path. Each file may be either a single
    work entry (dict) or an array of entries (list); both shapes are
    flattened into a single list of dicts."""
    if not dir_path.exists(): return []
    out: list[dict] = []
    for p in dir_path.glob("*.json"):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, list):
            out.extend(x for x in payload if isinstance(x, dict))
        elif isinstance(payload, dict):
            out.append(payload)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-per-bucket", type=int, default=3,
                     help="Treat (scene, light) buckets with fewer than "
                          "this many entries as 'underserved' and list them.")
    args = ap.parse_args()

    approved = load_jsons(APPROVED_DIR)
    drafts = load_jsons(DRAFT_DIR)
    rejected = load_jsons(DRAFT_DIR / "_rejected")
    raw_count = sum(1 for _ in RAW_DIR.glob("*/*.json")) if RAW_DIR.exists() else 0

    print("# 作品库冷启动状态\n")
    print(f"- approved:  **{len(approved)}**  ({APPROVED_DIR.relative_to(ROOT)})")
    print(f"- drafts:    **{len(drafts)}**  ({DRAFT_DIR.relative_to(ROOT)})")
    print(f"- rejected:  {len(rejected)}")
    print(f"- raw:       {raw_count}")
    print()

    if not approved:
        print("_corpus is empty — no coverage to report._")
        return 0

    # ---- coverage by tag pairs ----------------------------------------
    pair_counts: Counter[tuple[str, str]] = Counter()
    for w in approved:
        scenes = w.get("scene_tags") or []
        lights = w.get("light_tags") or []
        for s, l in product(scenes, lights):
            pair_counts[(s, l)] += 1

    print("## Top buckets (most works)\n")
    print("| scene | light | n |")
    print("|---|---|---:|")
    for (s, l), n in pair_counts.most_common(8):
        print(f"| {s} | {l} | {n} |")

    print("\n## Underserved buckets (< {} works)\n".format(args.target_per_bucket))
    underserved = []
    for s in TARGET_SCENES:
        for l in TARGET_LIGHTS:
            n = pair_counts.get((s, l), 0)
            if n < args.target_per_bucket:
                underserved.append((s, l, n))
    if not underserved:
        print("_all target buckets meet the threshold — corpus is well-rounded._")
    else:
        # Sort by lowest count first so the curator chases empty cells.
        underserved.sort(key=lambda t: (t[2], t[0], t[1]))
        print("| scene | light | n |")
        print("|---|---|---:|")
        for s, l, n in underserved[:30]:
            print(f"| {s} | {l} | {n} |")
        if len(underserved) > 30:
            print(f"\n_... {len(underserved) - 30} more underserved buckets — run with `--target-per-bucket 1` to focus on truly empty cells._")

    print(f"\n_total target buckets: {len(TARGET_SCENES) * len(TARGET_LIGHTS)};"
          f" filled ≥{args.target_per_bucket}: "
          f"{len(TARGET_SCENES) * len(TARGET_LIGHTS) - len(underserved)}_")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
