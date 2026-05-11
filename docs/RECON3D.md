# Recon3D (W9 — C1)

Full incremental Structure-from-Motion run on the user's panoramic +
walk keyframes (optionally seeded with ARKit camera poses as priors).
Output is a `SparseModel` with a (lat, lon)-aligned point cloud the
client can preview.

## Why opt-in?

Recon takes 5-15 s wall-clock per ~20-frame request and uses real
memory. We don't run it on every `/analyze`; the user explicitly taps
"做 3D 重建" on the result page, which calls `POST /recon3d/start`.

## Wire shape

```
POST /recon3d/start { images_b64: [...], priors?: [...], origin_lat?, origin_lon? }
  → { job_id, status: "queued", progress: 0 }

GET  /recon3d/{job_id}
  → { job_id, status, progress, error?, model? }
```

`status` cycles: `queued → running → done | error`. The job is held in
memory; horizontal scaling needs Redis or DB-backed state (TODO).

## Backend layout

- `services/recon3d.py` — owns the job dict + the
  `asyncio.Semaphore(JOB_QUEUE_LIMIT)` that bounds concurrency.
  `_run_pycolmap` is the synchronous SfM core; we offload it via
  `asyncio.to_thread`. When `pycolmap` isn't installed the function
  returns a stub `SparseModel` so the API still works in dev.
- `api/recon3d.py` — the FastAPI router exposing `/start` + `/{job_id}`.

## Installing pycolmap

```bash
pip install pycolmap>=0.6
# On Linux you may need: apt install colmap libcolmap-dev
```

If you don't, every job will return a stub `{points_count: 0, ...}` and
the iOS / Web UI will simply show "完成：0 个稀疏点".

## Future work

- Persist completed jobs to disk so the result UI can re-attach after
  navigation.
- Add a sparse point preview thumbnail (we have the data; just need a
  matplotlib-style PNG render and to expose it via `thumbnail_ref`).
- Reuse the recovered scale + cameras inside `triangulation.py` for a
  much sharper FarPoint precision.
