"""Structured JSON logging for the backend.

P0-3.3: includes a Redactor filter that strips sensitive fields
(``gps_track``, ``keyframes_b64``, ``geo_lat``, ``geo_lon``,
``model_api_key``) from log records before serialisation.
"""
from __future__ import annotations

import logging
import re
import sys

try:
    from pythonjsonlogger.json import JsonFormatter as _JsonFormatter
except ImportError:
    from pythonjsonlogger.jsonlogger import JsonFormatter as _JsonFormatter


_SENSITIVE_KEYS = {
    "gps_track", "keyframes_b64",
    "geo_lat", "geo_lon", "lat", "lon",
    "model_api_key", "api_key", "secret",
    "x_app_attest_assertion", "attestation_b64",
}
_INLINE_PATTERNS = [
    re.compile(r'(?P<k>"(?:gps_track|keyframes_b64|model_api_key|attestation_b64)")\s*:\s*"[^"]*"'),
    re.compile(r'(?P<k>"(?:geo_lat|geo_lon|lat|lon)")\s*:\s*-?\d+\.\d{3,}'),
]


class _RedactorFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub structured extras.
        for k in list(record.__dict__.keys()):
            if k in _SENSITIVE_KEYS:
                record.__dict__[k] = "<redacted>"
        # Scrub inline message text — best effort.
        msg = record.getMessage()
        for pat in _INLINE_PATTERNS:
            msg = pat.sub(lambda m: f'{m.group("k")}: "<redacted>"', msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    formatter = _JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    handler.setFormatter(formatter)
    handler.addFilter(_RedactorFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    for noisy in ("uvicorn.error", "uvicorn.access"):
        logging.getLogger(noisy).handlers = [handler]
