import asyncio

from app.services import recon3d


def test_submit_persists_and_reads_back():
    job = recon3d.submit_job([b"x"], origin_lat=31.23, origin_lon=121.49)
    fetched = recon3d.get_job(job.job_id)
    assert fetched is not None
    assert fetched.job_id == job.job_id
    assert fetched.status in {"queued", "running", "done"}


def test_list_jobs_returns_recent():
    a = recon3d.submit_job([b"1"])
    b = recon3d.submit_job([b"2"])
    ids = {j.job_id for j in recon3d.list_jobs()}
    assert a.job_id in ids and b.job_id in ids


def test_geohash_buckets():
    # Two close points → same bucket; far points → different bucket.
    g1 = recon3d._geohash(31.2389, 121.4905, precision=6)
    g2 = recon3d._geohash(31.2390, 121.4906, precision=6)
    assert g1 == g2
    g3 = recon3d._geohash(40.7128, -74.0060, precision=6)
    assert g1 != g3
