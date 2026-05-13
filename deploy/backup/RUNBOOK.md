# 备份与恢复演练 Runbook (PR12)

## 目标

每月一次主动演练「从最新备份恢复一个 staging 环境并跑一组烟雾测试」，
保证：备份能用 → 恢复脚本能跑 → schema_v2 兼容老备份。

## 节奏

| 频率 | 动作 | 责任人 |
| --- | --- | --- |
| 每天 03:15 UTC | `backup.sh` 自动跑、上传 R2/OSS | cron |
| 每周一 09:00 | Datadog monitor 检查最近 7 天备份是否齐全 | ops oncall |
| 每月 1 号 | 跑一次 **完整恢复演练** (本 runbook) | ops oncall |
| 每季度 | 邀外部审计员看流程 | CTO |

## 月度演练步骤

### 1. 拉最新备份到本机

```bash
# Postgres / fly.io
aws s3 cp s3://aphc-backups/aphc-pg-$(date -u +%Y%m%d)*.dump ./latest.dump \
    --endpoint-url https://<account>.r2.cloudflarestorage.com

# 或 SQLite / 阿里云 OSS
ossutil cp oss://aphc-backups/aphc-sqlite-$(date -u +%Y%m%d)*.db.gz ./latest.db.gz
```

### 2. 起一个 throwaway DB

```bash
# fly.io
fly pg create --name aphc-restore-$(date +%Y%m%d) --region nrt
fly pg attach aphc-restore-$(date +%Y%m%d) --app aphc-restore

# 阿里云 RDS: 用 RDS 控制台的「克隆实例」按钮一键拉
# 本地: docker run --rm --name pg-restore -e POSTGRES_PASSWORD=x -p 55432:5432 postgres:16
```

### 3. 还原

```bash
RESTORE_CONFIRM=yes \
POSTGRES_DSN=postgresql://...:55432/aphc \
deploy/backup/restore.sh latest.dump
```

### 4. 跑烟雾测试

```bash
# 表都在
psql "$POSTGRES_DSN" -c "\dt" | grep -E "users|otp_codes|usage_periods|usage_records|admin_audit_log"

# 行数合理 (与生产相差应 < 1%)
psql "$POSTGRES_DSN" -c "SELECT COUNT(*) FROM users;"
psql "$POSTGRES_DSN" -c "SELECT COUNT(*) FROM usage_records;"

# schema 升级幂等
APP_ENV=staging POSTGRES_DSN=$POSTGRES_DSN python -c \
    "from app.services import user_repo; user_repo._connect().__enter__()"
```

### 5. 确认 RPO / RTO 达标

- **RPO** (数据丢失窗口)：≤ 24h（每天备份 + 阿里云 RDS / Fly PG 自身的连续 WAL）
- **RTO** (恢复时间目标)：≤ 60min（实测演练 < 30min）

记录到 `deploy/backup/runs/YYYY-MM.md`，列出耗时与是否成功。

### 6. 销毁演练实例

```bash
fly pg destroy aphc-restore-$(date +%Y%m%d) --yes
# or 阿里云 RDS 控制台释放克隆实例
```

## 失败应急

- **备份缺失** → 立即手工触发 `backup.sh`，并把 cron 监控降级
- **恢复脚本报 schema mismatch** → 检查 `_ensure_schema_v2` 是否在还原后被调用过；先跑一次普通启动让它跑迁移
- **R2/OSS 认证失败** → 把 secret 重新生成；旧备份不会丢，只是新备份没上传
