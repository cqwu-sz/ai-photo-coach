"""Self-check for baseline LUTs.

Parses every ``ios/AIPhotoCoach/Resources/LUTs/*.cube`` and asserts:

  1. ``LUT_3D_SIZE`` header is present and parses to N >= 2.
  2. Body has exactly N**3 RGB triplets (no missing / duplicate lines).
  3. All RGB values lie in [0.0, 1.0].
  4. File parses through the same minimal grammar ``FilterEngine.parseCubeLUT``
     accepts on iOS — that includes skipping ``TITLE`` / ``DOMAIN_*`` /
     ``LUT_1D_*`` lines and comments.
  5. Filename ``<id>.cube`` matches the 8 keys the backend Literal allows
     (no orphan files, no missing presets).

Run::

    python scripts/luts/verify_luts.py

Exit code 0 on success; non-zero otherwise (suitable for CI gating).
"""
from __future__ import annotations

import sys
from pathlib import Path

LUT_DIR = Path(__file__).resolve().parents[2] / "ios" / "AIPhotoCoach" / "Resources" / "LUTs"

EXPECTED_KEYS = {
    "natural", "film_warm", "film_cool", "mono",
    "hk_neon", "japanese_clean", "golden_glow", "moody_fade",
}


def parse_cube(text: str) -> tuple[int, list[tuple[float, float, float]]]:
    dim = 0
    rows: list[tuple[float, float, float]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        upper = line.upper()
        if upper.startswith("LUT_3D_SIZE"):
            dim = int(line.split()[1])
            continue
        if (upper.startswith("TITLE")
                or upper.startswith("DOMAIN")
                or upper.startswith("LUT_1D")):
            continue
        parts = line.split()
        if len(parts) < 3:
            raise ValueError(f"unexpected line: {line!r}")
        r, g, b = float(parts[0]), float(parts[1]), float(parts[2])
        rows.append((r, g, b))
    return dim, rows


def check(path: Path) -> list[str]:
    errs: list[str] = []
    text = path.read_text(encoding="utf-8")
    try:
        dim, rows = parse_cube(text)
    except Exception as e:                              # noqa: BLE001
        return [f"{path.name}: parse error — {e}"]

    if dim < 2:
        errs.append(f"{path.name}: LUT_3D_SIZE missing or too small ({dim})")
    expected = dim ** 3
    if len(rows) != expected:
        errs.append(f"{path.name}: got {len(rows)} rows, expected {expected} (={dim}^3)")

    for i, (r, g, b) in enumerate(rows):
        if not (0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0):
            errs.append(f"{path.name}: row {i} out of [0,1]: ({r}, {g}, {b})")
            if len(errs) > 5:
                errs.append(f"{path.name}: ...further out-of-range rows suppressed")
                break
    return errs


def main() -> int:
    if not LUT_DIR.exists():
        print(f"LUT dir not found: {LUT_DIR}", file=sys.stderr)
        return 2

    cubes = sorted(LUT_DIR.glob("*.cube"))
    if not cubes:
        print(f"no .cube files in {LUT_DIR}", file=sys.stderr)
        return 3

    found_keys = {p.stem for p in cubes}
    missing = EXPECTED_KEYS - found_keys
    extra = found_keys - EXPECTED_KEYS
    failures: list[str] = []
    if missing:
        failures.append(f"missing presets: {sorted(missing)}")
    if extra:
        failures.append(f"unexpected presets (not in backend Literal): {sorted(extra)}")

    for path in cubes:
        failures.extend(check(path))

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        print(f"\n{len(failures)} failure(s) across {len(cubes)} file(s)")
        return 1

    print(f"OK: {len(cubes)} LUT files verified ({cubes[0].stat().st_size // 1024} KB each)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
