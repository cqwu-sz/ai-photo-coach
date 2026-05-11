"""W7 — time_optimal smoke."""
from __future__ import annotations

from app.services import time_optimal


def test_no_geo_returns_none():
    assert time_optimal.lookup(None, None) is None


def test_to_prompt_block_handles_none():
    assert time_optimal.to_prompt_block(None) == ""
