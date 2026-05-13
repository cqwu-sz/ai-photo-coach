#!/usr/bin/env bash
# backup.sh — daily DB backup for AI Photo Coach (PR12)
#
# Supports two backends transparently:
#   - SQLite (灰度 / fly.io 早期):  使用 sqlite3 .backup 安全快照
#   - PostgreSQL (生产):            使用 pg_dump --format=custom -Z 9
#
# 输出文件命名: aphc-{db_kind}-{ts}.dump
#
# 上传位置 (按 ENV 优先级):
#   1. R2_BUCKET + R2_ACCOUNT_ID + R2_ACCESS_KEY + R2_SECRET_KEY → Cloudflare R2
#   2. OSS_BUCKET + OSS_ENDPOINT + OSS_ACCESS_KEY + OSS_SECRET_KEY → 阿里云 OSS
#   3. 否则只保留本地 backup 目录, 由系统 cron 自己上传
#
# 同时清理本地 N 天前的旧文件 (RETENTION_DAYS, 默认 7).
#
# Cron 示例 (每天 03:15 UTC):
#   15 3 * * * /opt/aphc/deploy/backup/backup.sh >> /var/log/aphc-backup.log 2>&1
#
# Restore: 见 deploy/backup/restore.sh

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/aphc}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

if [[ -n "${POSTGRES_DSN:-}" ]]; then
    KIND="pg"
    OUT="$BACKUP_DIR/aphc-${KIND}-${TS}.dump"
    echo "[backup] dumping Postgres → $OUT"
    pg_dump --format=custom --compress=9 --no-owner --no-acl \
            --dbname="$POSTGRES_DSN" --file="$OUT"
elif [[ -n "${SQLITE_PATH:-}" ]]; then
    KIND="sqlite"
    OUT="$BACKUP_DIR/aphc-${KIND}-${TS}.db"
    echo "[backup] sqlite snapshot → $OUT"
    # `.backup` is the only safe way to copy a live sqlite db without
    # locking writers; it streams pages via the WAL.
    sqlite3 "$SQLITE_PATH" ".backup '$OUT'"
    gzip -9 "$OUT"
    OUT="${OUT}.gz"
else
    echo "[backup] FATAL: set either POSTGRES_DSN or SQLITE_PATH" >&2
    exit 2
fi

# ---- Upload --------------------------------------------------------------

if [[ -n "${R2_BUCKET:-}" && -n "${R2_ACCOUNT_ID:-}" \
      && -n "${R2_ACCESS_KEY:-}" && -n "${R2_SECRET_KEY:-}" ]]; then
    echo "[backup] uploading to R2 bucket=$R2_BUCKET"
    AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$R2_SECRET_KEY" \
        aws s3 cp "$OUT" \
            "s3://$R2_BUCKET/$(basename "$OUT")" \
            --endpoint-url "https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com" \
            --no-progress
elif [[ -n "${OSS_BUCKET:-}" && -n "${OSS_ENDPOINT:-}" \
        && -n "${OSS_ACCESS_KEY:-}" && -n "${OSS_SECRET_KEY:-}" ]]; then
    echo "[backup] uploading to OSS bucket=$OSS_BUCKET"
    # ossutil 必须预装 (apt: ossutil; 阿里云镜像默认带)
    ossutil cp "$OUT" "oss://$OSS_BUCKET/$(basename "$OUT")" \
        -e "$OSS_ENDPOINT" -i "$OSS_ACCESS_KEY" -k "$OSS_SECRET_KEY" \
        --update
else
    echo "[backup] no remote configured; keeping local copy only"
fi

# ---- Local retention -----------------------------------------------------

find "$BACKUP_DIR" -name 'aphc-*' -type f -mtime "+${RETENTION_DAYS}" \
     -print -delete || true

echo "[backup] done: $OUT"
