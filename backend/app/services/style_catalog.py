"""Single source of truth for the 5 fixed style_ids and their EN
keyword mapping (v18).

iOS writes `style_keywords = ["cinematic","moody"]` (or any subset of
the keywords for one of the 5 cards) into `usage_records.step_config`.
This module's `infer_style_id` reverses that mapping so the
`user_preferences` and `satisfaction_aggregates` tables can roll up
on a stable, finite key space.

Keep `_KW_TO_ID` aligned with `ios/AIPhotoCoach/Features/StylePicker/
StylePickerView.swift::StyleCatalog`. There are 5 styles, 10 keywords;
this is small enough to inline rather than ship a JSON config.
"""
from __future__ import annotations

from typing import Iterable, Optional

# (id, label_zh, [keywords]) — matches StyleCatalog on iOS.
_STYLES: list[tuple[str, str, list[str]]] = [
    ("cinematic_moody",   "氛围感",     ["cinematic", "moody"]),
    ("clean_bright",      "清爽日系",   ["clean", "bright"]),
    ("film_warm",         "温柔暖光",   ["film", "warm"]),
    ("street_candid",     "自然随手",   ["street", "candid"]),
    ("editorial_fashion", "大片感",     ["editorial", "fashion"]),
]

ALL_STYLE_IDS: list[str] = [s[0] for s in _STYLES]
LABEL_ZH: dict[str, str] = {s[0]: s[1] for s in _STYLES}

# v18 s2 — single source of truth for scene_mode → 中文 label.
# Previously duplicated in api/analyze.py and (implicitly) in iOS.
# user_preferences / satisfaction_aggregates render via this map so
# the LLM and admin UI see consistent wording.
SCENE_LABEL_ZH: dict[str, str] = {
    "portrait":     "人像",
    "closeup":      "特写",
    "full_body":    "全身",
    "documentary":  "纪实",
    "scenery":      "风景",
    "light_shadow": "光影",
}


def scene_label_zh(scene_mode: str) -> str:
    return SCENE_LABEL_ZH.get(scene_mode, scene_mode)

_KW_TO_ID: dict[str, str] = {}
for sid, _, kws in _STYLES:
    for kw in kws:
        _KW_TO_ID[kw.lower()] = sid


def infer_style_id(style_keywords: Iterable[str]) -> Optional[str]:
    """Return the first matching style_id for the given keyword list.

    Why "first": iOS may write 1-2 cards' worth of keywords (e.g.
    `["cinematic","moody","clean","bright"]`) when the user picks
    multiple cards. We attribute the satisfaction signal to the first
    style_id we see — picking proportionally would dilute the signal
    and over-attribute when N cards are picked. If you later care
    about "this user picked 2 styles", emit two preference rows
    instead of dividing the score.
    """
    if not style_keywords:
        return None
    for kw in style_keywords:
        if not isinstance(kw, str):
            continue
        sid = _KW_TO_ID.get(kw.strip().lower())
        if sid:
            return sid
    return None


def label_zh(style_id: str) -> str:
    return LABEL_ZH.get(style_id, style_id)


__all__ = [
    "ALL_STYLE_IDS", "LABEL_ZH", "SCENE_LABEL_ZH",
    "infer_style_id", "label_zh", "scene_label_zh",
]
