"""Calibrate K_face / K_body against ground-truth distance measurements.

Usage:
    python scripts/calibrate_distance.py samples.csv

Where samples.csv has columns:
    face_height_ratio, body_height_ratio, ground_truth_distance_m,
    focal_length_35mm_eq (optional)

We fit:
    distance ≈ K_face / face_height_ratio
    distance ≈ K_body / (ankle_y - nose_y)

via geometric median (robust to mis-detections), and print the
suggested constants. Operator copy-paste into scene_aggregate.py.
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: calibrate_distance.py samples.csv", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        return 2

    face_ks: list[float] = []
    body_ks: list[float] = []
    by_focal: dict[int, list[float]] = defaultdict(list)
    with path.open() as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                gt = float(row["ground_truth_distance_m"])
            except (KeyError, ValueError):
                continue
            f_ratio = _maybe_float(row.get("face_height_ratio"))
            b_ratio = _maybe_float(row.get("body_height_ratio"))
            focal = _maybe_float(row.get("focal_length_35mm_eq"))
            if f_ratio and f_ratio > 0:
                face_ks.append(gt * f_ratio)
                if focal:
                    by_focal[round(focal)].append(gt * f_ratio)
            if b_ratio and b_ratio > 0:
                body_ks.append(gt * b_ratio)

    if not face_ks and not body_ks:
        print("no valid samples", file=sys.stderr)
        return 1

    if face_ks:
        print(f"K_face: median={median(face_ks):.3f}  n={len(face_ks)}")
    if body_ks:
        print(f"K_body: median={median(body_ks):.3f}  n={len(body_ks)}")
    if by_focal:
        print("\nPer-focal-length K_face medians (helps spot lens-specific bias):")
        for focal, ks in sorted(by_focal.items()):
            print(f"  {focal}mm-eq: K_face={median(ks):.3f} (n={len(ks)})")
    return 0


def _maybe_float(s):
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
