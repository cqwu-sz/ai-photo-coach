"""Smoke-test that the shipped baseline LUTs parse cleanly and cover
exactly the 8 keys ``PostProcessRecipe.filter_preset`` accepts. Runs
the same parser ``FilterEngine.parseCubeLUT`` uses on iOS so a malformed
.cube file is caught at CI time instead of becoming a silent fallback
at runtime.

Skipped when the LUT directory isn't present (e.g. minimal backend-only
clone)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "luts" / "verify_luts.py"
LUT_DIR = REPO_ROOT / "ios" / "AIPhotoCoach" / "Resources" / "LUTs"


@pytest.mark.skipif(not LUT_DIR.exists(), reason="LUT bundle dir absent")
def test_lut_bundle_verifies() -> None:
    assert VERIFY_SCRIPT.exists(), f"verify_luts.py missing: {VERIFY_SCRIPT}"
    result = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"verify_luts.py failed (rc={result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
