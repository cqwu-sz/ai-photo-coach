"""Lightweight UGC content filter (P0-1.5).

Used by ``record_user_spot`` to drop garbage / abusive ``derived_from``
labels before they leak into the public POI tier. Kept intentionally
small — for serious moderation switch to a hosted service later (e.g.
Tencent / 阿里云内容安全 / OpenAI moderation).
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Coarse-grained block list; expand from real UGC samples post-launch.
# Keep keys lowercase ASCII / Chinese — _normalise() folds case + strips
# common dressing characters.
_BAD_TOKENS_ZH = {
    "操你",  "傻逼", "煞笔", "狗东西", "fuck", "shit", "bitch",
    "习近平", "毛泽东", "六四", "法轮功",   # politically sensitive
}
_URL_RE = re.compile(r"https?://|www\.")
_PUNCT_RE = re.compile(r"[\s\u3000\.,!?;:'\"()\[\]{}—–_=+\-]+")
_MAX_LEN = 32
_MIN_LEN = 2


def _normalise(text: str) -> str:
    return _PUNCT_RE.sub("", text or "").lower()


def is_clean(text: str) -> bool:
    """Return True iff the candidate is safe to persist verbatim."""
    if not text:
        return False
    text = text.strip()
    if not (_MIN_LEN <= len(text) <= _MAX_LEN):
        return False
    if _URL_RE.search(text):
        return False
    norm = _normalise(text)
    if not norm:
        return False
    for bad in _BAD_TOKENS_ZH:
        if bad in norm:
            log.info("content_filter: dropped (token=%s)", bad)
            return False
    return True


def sanitise(text: str | None) -> str | None:
    """Return the input when clean, else ``None``."""
    if text is None:
        return None
    return text if is_clean(text) else None
