"""Full SfM reconstruction worker (W9, hardened in P0-2).

Runs incremental Structure-from-Motion via ``pycolmap`` over the user's
panoramic + walk keyframes, optionally seeded with ARKit camera poses
as priors. Output is a ``SparseModel`` with a (lat, lon)-aligned point
cloud the client can preview as a thumbnail.

Persistence (P0-2.1):
    Job state lives in SQLite (``data/recon3d_jobs.db``) so
    ``gunicorn -w N`` workers can all see the same job — request can hit
    one worker for ``/start`` and another for the polling GET.

Cleanup (P0-2.2):
    A ``cleanup_loop()`` coroutine (started from main.lifespan) drops
    finished jobs after ``CLEANUP_DONE_SEC`` and errored jobs after
    ``CLEANUP_ERROR_SEC``.

Geo cache (P2-11.1):
    Successful ``SparseModel`` outputs are also indexed by GeoHash so
    later analyze calls in the same area can reuse the model as a
    triangulation prior.

This module is intentionally **import-safe without pycolmap** — the
top-level service queue and API layer always work; only ``run_job``
fails fast when the dependency is missing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from ..models.schemas import SparseModel

log = logging.getLogger(__name__)

JOB_QUEUE_LIMIT = 1
CLEANUP_INTERVAL_SEC = 60
CLEANUP_DONE_SEC = 7 * 24 * 3600
CLEANUP_ERROR_SEC = 60 * 60

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "recon3d_jobs.db"
MODEL_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "recon3d_models"


@dataclass
class Recon3DJob:
    job_id: str
    status: str = "queued"          # queued | running | done | error
    progress: float = 0.0
    error: Optional[str] = None
    model: Optional[SparseModel] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_sema = asyncio.Semaphore(JOB_QUEUE_LIMIT)


# ---------------------------------------------------------------------------
# Persistence layer
# ---------------------------------------------------------------------------


@contextmanager
def _connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS recon3d_jobs ("
            "job_id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL, "
            "progress REAL NOT NULL DEFAULT 0, "
            "error TEXT, "
            "model_json TEXT, "
            "origin_lat REAL, origin_lon REAL, "
            "created_at TEXT NOT NULL, "
            "updated_at TEXT NOT NULL)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_recon3d_status_updated "
            "ON recon3d_jobs(status, updated_at)"
        )
        # Lazy migration (A0-5): user_id for multi-user isolation.
        cols = {row[1] for row in con.execute("PRAGMA table_info(recon3d_jobs)").fetchall()}
        if "user_id" not in cols:
            con.execute("ALTER TABLE recon3d_jobs ADD COLUMN user_id TEXT")
            con.execute("CREATE INDEX IF NOT EXISTS idx_recon3d_user ON recon3d_jobs(user_id)")
        yield con
        con.commit()
    finally:
        con.close()


def _row_to_job(row) -> Recon3DJob:
    # Tolerate the legacy 9-col shape (pre A0-5) and the new 10-col shape.
    if len(row) >= 10:
        job_id, status, progress, err, model_json, _o_lat, _o_lon, c_at, u_at, _user_id = row[:10]
    else:
        job_id, status, progress, err, model_json, _o_lat, _o_lon, c_at, u_at = row
    model = None
    if model_json:
        try:
            model = SparseModel.model_validate_json(model_json)
        except Exception:                                       # noqa: BLE001
            model = None
    return Recon3DJob(
        job_id=job_id, status=status, progress=float(progress or 0),
        error=err, model=model,
        created_at=datetime.fromisoformat(c_at),
        updated_at=datetime.fromisoformat(u_at),
    )


def _persist(job: Recon3DJob, *, origin_lat: Optional[float] = None,
              origin_lon: Optional[float] = None,
              user_id: Optional[str] = None) -> None:
    try:
        with _connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO recon3d_jobs "
                "(job_id, status, progress, error, model_json, "
                "origin_lat, origin_lon, created_at, updated_at, user_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, "
                "COALESCE((SELECT created_at FROM recon3d_jobs WHERE job_id = ?), ?), ?, "
                "COALESCE(?, (SELECT user_id FROM recon3d_jobs WHERE job_id = ?)))",
                (job.job_id, job.status, job.progress, job.error,
                 job.model.model_dump_json() if job.model else None,
                 origin_lat, origin_lon,
                 job.job_id, job.created_at.isoformat(),
                 job.updated_at.isoformat(),
                 user_id, job.job_id),
            )
    except sqlite3.DatabaseError as e:
        log.info("recon3d persist failed: %s", e)


def list_jobs() -> list[Recon3DJob]:
    try:
        with _connect() as con:
            rows = con.execute(
                "SELECT job_id, status, progress, error, model_json, "
                "origin_lat, origin_lon, created_at, updated_at "
                "FROM recon3d_jobs ORDER BY created_at DESC"
            ).fetchall()
    except sqlite3.DatabaseError:
        return []
    return [_row_to_job(r) for r in rows]


def get_job(job_id: str, *, user_id: Optional[str] = None) -> Optional[Recon3DJob]:
    """When `user_id` is provided, only return the job if it belongs to
    that user — A0-5 isolation. Pass `user_id=None` for admin / cron
    sweeps that legitimately need cross-user visibility.
    """
    try:
        with _connect() as con:
            if user_id is None:
                row = con.execute(
                    "SELECT job_id, status, progress, error, model_json, "
                    "origin_lat, origin_lon, created_at, updated_at, user_id "
                    "FROM recon3d_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT job_id, status, progress, error, model_json, "
                    "origin_lat, origin_lon, created_at, updated_at, user_id "
                    "FROM recon3d_jobs WHERE job_id = ? "
                    "AND (user_id IS NULL OR user_id = ?)",
                    (job_id, user_id),
                ).fetchone()
    except sqlite3.DatabaseError:
        return None
    return _row_to_job(row) if row else None


def submit_job(image_blobs: list[bytes], priors: Optional[list[dict]] = None,
               origin_lat: Optional[float] = None,
               origin_lon: Optional[float] = None,
               user_id: Optional[str] = None) -> Recon3DJob:
    job = Recon3DJob(job_id=uuid.uuid4().hex[:12])
    _persist(job, origin_lat=origin_lat, origin_lon=origin_lon, user_id=user_id)
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run_async(job, image_blobs, priors, origin_lat, origin_lon, user_id))
    except RuntimeError:
        log.info("submit_job: no running loop, leaving job queued")
    return job


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _run_async(job: Recon3DJob, blobs: list[bytes],
                     priors: Optional[list[dict]],
                     origin_lat: Optional[float],
                     origin_lon: Optional[float],
                     user_id: Optional[str] = None) -> None:
    async with _sema:
        job.status = "running"
        job.updated_at = datetime.now(timezone.utc)
        _persist(job, origin_lat=origin_lat, origin_lon=origin_lon, user_id=user_id)
        try:
            model = await asyncio.to_thread(
                _run_pycolmap, job.job_id, blobs, priors, origin_lat, origin_lon,
            )
            job.model = model
            job.status = "done"
            job.progress = 1.0
            _maybe_cache_model(model, origin_lat, origin_lon)
        except Exception as e:                                       # noqa: BLE001
            log.exception("recon3d job %s failed", job.job_id)
            job.status = "error"
            job.error = str(e)
        finally:
            job.updated_at = datetime.now(timezone.utc)
            _persist(job, origin_lat=origin_lat, origin_lon=origin_lon, user_id=user_id)


async def cleanup_loop() -> None:
    """P0-2.2 background sweep: drop finished/errored jobs older than
    the configured TTLs. Started from main.lifespan."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL_SEC)
            now = datetime.now(timezone.utc)
            done_cutoff = (now - timedelta(seconds=CLEANUP_DONE_SEC)).isoformat()
            err_cutoff = (now - timedelta(seconds=CLEANUP_ERROR_SEC)).isoformat()
            with _connect() as con:
                con.execute(
                    "DELETE FROM recon3d_jobs WHERE status = 'done' AND updated_at < ?",
                    (done_cutoff,),
                )
                con.execute(
                    "DELETE FROM recon3d_jobs WHERE status = 'error' AND updated_at < ?",
                    (err_cutoff,),
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:                                  # noqa: BLE001
            log.info("recon3d cleanup tick failed: %s", e)


# ---------------------------------------------------------------------------
# pycolmap pipeline
# ---------------------------------------------------------------------------


def _run_pycolmap(job_id: str, blobs: list[bytes],
                  priors: Optional[list[dict]],
                  origin_lat: Optional[float],
                  origin_lon: Optional[float]) -> SparseModel:
    """Synchronous pycolmap pipeline. Falls back to a stub model when
    pycolmap isn't installed so the API can still return a usable shape
    in dev environments."""
    try:
        import pycolmap                                              # noqa: F401
    except Exception:                                                # noqa: BLE001
        log.warning("pycolmap unavailable; returning stub SparseModel")
        return SparseModel(
            job_id=job_id,
            points_count=0,
            cameras_count=len(blobs),
            scale_m_per_unit=1.0,
        )
    import pycolmap

    workdir = Path(tempfile.mkdtemp(prefix=f"recon3d_{job_id}_"))
    images_dir = workdir / "images"
    images_dir.mkdir()
    for i, b in enumerate(blobs):
        (images_dir / f"img_{i:04d}.jpg").write_bytes(b)
    db_path = workdir / "database.db"

    pycolmap.extract_features(database_path=str(db_path),
                              image_path=str(images_dir))
    pycolmap.match_exhaustive(database_path=str(db_path))
    maps = pycolmap.incremental_mapping(
        database_path=str(db_path),
        image_path=str(images_dir),
        output_path=str(workdir / "sparse"),
    )
    if not maps:
        return SparseModel(job_id=job_id, points_count=0, cameras_count=0)

    biggest = max(maps.values(), key=lambda r: r.num_points3D())
    scale = _compute_scale(biggest, priors)
    bbox_lat: Optional[list[float]] = None
    bbox_lon: Optional[list[float]] = None
    if origin_lat is not None and origin_lon is not None:
        bbox_lat, bbox_lon = _bbox_latlon(biggest, scale, origin_lat, origin_lon)
    return SparseModel(
        job_id=job_id,
        points_count=int(biggest.num_points3D()),
        cameras_count=int(biggest.num_reg_images()),
        scale_m_per_unit=scale,
        bbox_lat=bbox_lat,
        bbox_lon=bbox_lon,
    )


def _compute_scale(model, priors: Optional[list[dict]]) -> float:
    """If the client supplied at least two ARKit poses, use the inter-
    camera distance ratio to estimate the metric scale of the recon."""
    if not priors or len(priors) < 2:
        return 1.0
    try:
        names_to_t = {p["image_name"]: p["t"] for p in priors}
        sample = list(model.images.values())[:6]
        ratios = []
        for i in range(len(sample)):
            for j in range(i + 1, len(sample)):
                ni = sample[i].name
                nj = sample[j].name
                if ni in names_to_t and nj in names_to_t:
                    arkit_d = math.dist(names_to_t[ni], names_to_t[nj])
                    colmap_d = math.dist(
                        sample[i].projection_center(),
                        sample[j].projection_center(),
                    )
                    if colmap_d > 1e-6:
                        ratios.append(arkit_d / colmap_d)
        return sum(ratios) / len(ratios) if ratios else 1.0
    except Exception:                                                # noqa: BLE001
        return 1.0


def _bbox_latlon(model, scale: float,
                 origin_lat: float, origin_lon: float) -> tuple[list[float], list[float]]:
    xs, ys = [], []
    for p in model.points3D.values():
        xs.append(p.xyz[0] * scale)
        ys.append(p.xyz[1] * scale)
    if not xs:
        return ([origin_lat, origin_lat], [origin_lon, origin_lon])
    cos_lat = max(0.05, math.cos(math.radians(origin_lat)))
    return (
        [origin_lat + min(ys) / 111_320.0, origin_lat + max(ys) / 111_320.0],
        [origin_lon + min(xs) / (111_320.0 * cos_lat),
         origin_lon + max(xs) / (111_320.0 * cos_lat)],
    )


# ---------------------------------------------------------------------------
# P2-11.1 — GeoHash-bucketed model cache
# ---------------------------------------------------------------------------


def _geohash(lat: float, lon: float, precision: int = 6) -> str:
    """Tiny inline geohash impl (no external dep)."""
    base32 = "0123456789bcdefghjkmnpqrstuvwxyz"
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch |= 1 << (4 - bit); lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch |= 1 << (4 - bit); lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(base32[ch])
            bit, ch = 0, 0
    return "".join(out)


def _maybe_cache_model(model: SparseModel,
                       origin_lat: Optional[float],
                       origin_lon: Optional[float]) -> None:
    if origin_lat is None or origin_lon is None:
        return
    if model.points_count <= 0:
        return
    try:
        gh = _geohash(origin_lat, origin_lon, precision=6)
        out_dir = MODEL_CACHE_DIR / gh
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{model.job_id}.json"
        path.write_text(model.model_dump_json(), encoding="utf-8")
    except Exception as e:                                       # noqa: BLE001
        log.info("recon3d cache write failed: %s", e)


def lookup_cached_models(lat: float, lon: float,
                         precision: int = 6) -> list[SparseModel]:
    """P2-11.2 — return any cached SparseModels for the same GeoHash."""
    try:
        gh = _geohash(lat, lon, precision=precision)
        d = MODEL_CACHE_DIR / gh
        if not d.exists():
            return []
        out: list[SparseModel] = []
        for p in d.glob("*.json"):
            try:
                out.append(SparseModel.model_validate_json(p.read_text(encoding="utf-8")))
            except Exception:                                    # noqa: BLE001
                continue
        return out
    except Exception:                                            # noqa: BLE001
        return []
