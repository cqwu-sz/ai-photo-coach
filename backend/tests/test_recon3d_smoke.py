"""W9 — recon3d smoke. Stub model is returned when pycolmap is missing."""
from __future__ import annotations

import asyncio

from app.services import recon3d


def test_submit_job_returns_job_record():
    job = recon3d.submit_job([b"\xff\xd8\xff\xd9"])
    assert job.job_id
    assert job.status in ("queued", "running", "done", "error")


def test_get_unknown_job_returns_none():
    assert recon3d.get_job("does-not-exist") is None
