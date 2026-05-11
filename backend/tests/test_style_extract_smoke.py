"""W6 — style_extract smoke. Empty/garbage input must not raise."""
from __future__ import annotations

from app.services import style_extract


def test_empty_inputs_returns_empty_list():
    assert style_extract.extract_fingerprints([]) == []


def test_garbage_input_is_skipped():
    out = style_extract.extract_fingerprints([b"not a real jpeg"])
    assert isinstance(out, list)


def test_to_prompt_block_handles_empty():
    assert style_extract.to_prompt_block([]) == ""
