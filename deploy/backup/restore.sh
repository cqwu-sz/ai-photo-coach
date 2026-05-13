#!/usr/bin/env bash
# restore.sh — restore a previously dumped DB (PR12)
#
# 用法:
#   POSTGRES_DSN=...  ./restore.sh aphc-pg-20260512T031500Z.dump
#   SQLITE_PATH=...   ./restore.sh aphc-sqlite-20260512T031500Z.db.gz
#
# 强制要求:
#   - 你必须 export RESTORE_CONFIRM=yes 才会真正执行 (防误删生产数据)
#   - 还原前会先 dump 当前 DB 到 ${BACKUP_DIR:-/var/backups/aphc}/preimage-* 防误操作

set -euo pipefail

if [[ "${RESTORE_CONFIRM:-no}" != "yes" ]]; then
    echo "Refusing to run without RESTORE_CONFIRM=yes" >&2
    exit 2
fi

INPUT="${1:-}"
if [[ -z "$INPUT" || ! -f "$INPUT" ]]; then
    echo "usage: RESTORE_CONFIRM=yes $0 <dump_file>" >&2
    exit 2
fi

BACKUP_DIR="${BACKUP_DIR:-/var/backups/aphc}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"

if [[ -n "${POSTGRES_DSN:-}" ]]; then
    echo "[restore] dumping current Postgres for safety"
    pg_dump --format=custom --compress=9 --no-owner --no-acl \
            --dbname="$POSTGRES_DSN" \
            --file="$BACKUP_DIR/preimage-pg-${TS}.dump"
    echo "[restore] dropping objects + restoring from $INPUT"
    pg_restore --clean --if-exists --no-owner --no-acl \
               --dbname="$POSTGRES_DSN" "$INPUT"
elif [[ -n "${SQLITE_PATH:-}" ]]; then
    echo "[restore] copying current sqlite to preimage"
    cp "$SQLITE_PATH" "$BACKUP_DIR/preimage-sqlite-${TS}.db"
    if [[ "$INPUT" == *.gz ]]; then
        gunzip -k -c "$INPUT" > "$SQLITE_PATH.tmp"
    else
        cp "$INPUT" "$SQLITE_PATH.tmp"
    fi
    mv "$SQLITE_PATH.tmp" "$SQLITE_PATH"
else
    echo "[restore] FATAL: set POSTGRES_DSN or SQLITE_PATH" >&2
    exit 2
fi

echo "[restore] done. preimage saved next to backup dir."
