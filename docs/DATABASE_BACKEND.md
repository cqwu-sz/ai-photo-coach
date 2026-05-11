# Database backend (A1-2 of MULTI_USER_AUTH)

> Why this doc exists: we ship sqlite by default for zero-friction
> dev, but `gunicorn -w N` against sqlite eventually hits
> `database is locked` once write QPS climbs. This doc tells you
> when to flip to Postgres and exactly what changes.

## TL;DR — when do I switch?

| Signal | Action |
|---|---|
| < 5 RPS write, single host | Stay on sqlite. Don't pre-optimise. |
| Multi-worker (`-w >1`) AND non-trivial write rate | Switch `users.db` to Postgres |
| 2+ app hosts | Switch ALL state (users + recon3d_jobs + shot_results + poi_kb + attested_devices) |
| Need point-in-time restore / replication | Postgres |

## Current layout

| File | Owned by | What's in it |
|---|---|---|
| `data/users.db` | `services/user_repo.py` | users, device_bindings, subscriptions, refresh_tokens |
| `data/shot_results.db` | `api/feedback.py` | shot_results, post_process_events, ar_nav_events |
| `data/recon3d_jobs.db` | `services/recon3d.py` | recon3d_jobs (+ MODEL_CACHE_DIR/...) |
| `data/poi_kb.db` | `services/poi_lookup.py` | pois, user_spots, user_spot_votes |
| `data/attested_devices.db` | `services/app_attest.py` | devices |

## Migration strategy

We deliberately did **not** wrap every sqlite call in an ORM. The
codebase is ~5 modules with raw SQL — easier to port surgically when
we need to, easier to read until we do.

Recommended order:

1. **`users.db` first.** Highest blast radius on lock contention
   (every authenticated request hits it via `current_user → touch`).
2. **`shot_results.db`** — high write volume (every photo).
3. **`recon3d_jobs.db`** — moderate; status polling reads dominate.
4. **`poi_kb.db`** — read-heavy; sqlite is actually fine for a long
   time here.
5. **`attested_devices.db`** — write-once-per-device; lowest priority.

For each module the recipe is:

```python
# 1. Add psycopg dependency
#    requirements.txt += "psycopg[binary,pool]>=3.2"

# 2. Switch the module's `_connect()` helper to read settings.db_backend:

@contextmanager
def _connect():
    if get_settings().db_backend == "postgres":
        import psycopg
        with psycopg.connect(get_settings().postgres_dsn,
                             row_factory=psycopg.rows.dict_row) as con:
            _ensure_schema_pg(con)   # CREATE TABLE IF NOT EXISTS ... (PG dialect)
            yield con
    else:
        # existing sqlite path
        ...
```

3. **Translate the schema once.** sqlite `INTEGER PRIMARY KEY
   AUTOINCREMENT` → PG `BIGSERIAL`; `TEXT` stays `TEXT`; `BLOB` →
   `BYTEA`; `INSERT OR REPLACE` → `INSERT ... ON CONFLICT (...) DO
   UPDATE SET ...` (you'll find we already use this style in
   `subscriptions`).

4. **Run a one-shot data copy** (only if there's data worth keeping):

```bash
python -m scripts.migrate_sqlite_to_pg \
  --source data/users.db \
  --dsn "$POSTGRES_DSN" \
  --tables users,device_bindings,subscriptions,refresh_tokens
```

(That script is intentionally not yet written — write it the day you
flip the switch and tailor to your row volumes. Don't ship a generic
"just works" migrator; one dataset's edge cases bite you.)

5. **Smoke**: `pytest tests/test_user_isolation_smoke.py
   tests/test_iap_smoke.py tests/test_auth_smoke.py` against the new
   DSN before pointing prod at it.

## Why no Alembic yet?

Alembic gives migration history + autogenerate. With our current
"lazy `ALTER TABLE IF NOT EXISTS column`" pattern in `_ensure_schema`
we get the autogenerate behaviour for free without the alembic.ini
ceremony. The downside: rollback is manual. When we move to PG and
want production CI/CD-driven schema changes, *that's* the moment to
introduce Alembic — set up `alembic init migrations` and import each
table's current shape as the baseline migration.

## Connection pooling

Sqlite: not applicable (per-process file lock).

Postgres: use `psycopg_pool.ConnectionPool` with `min_size=2,
max_size=10` per app process. Anything more usually starves PG.

## Backups

- sqlite: `cp data/*.db backups/$(date +%F)/` is fine when shutting
  down or via `sqlite3 .backup` for hot copy.
- Postgres: managed providers (Supabase / Neon / RDS) handle this.
  Self-hosted: set up `pgBackRest` or `wal-g`.

## Observability after switch

- Add a Datadog dashboard tile for `pg_stat_activity` waiting locks.
- Alert on `pg_stat_database.deadlocks > 0` over 5 min.
- Keep `ai_photo_coach_auth_total{method=...}` so we can see whether
  legacy device_id traffic is going down (signal that we can flip
  `enable_legacy_device_id_auth=False`).
