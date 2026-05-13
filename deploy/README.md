# 部署手册 (PR11)

两套生产部署方案。fly.io 用于灰度 / 海外，阿里云用于国内主战场。

## 共同前置

| 项 | 说明 |
| --- | --- |
| 域名 | `api.aiphotocoach.app` (国际)；`api.aiphotocoach.cn` (备案后) |
| TLS | fly.io 自动签发；阿里云用 ALB + ACM 证书 |
| Web 关闭 | 设置 `APP_ENV=production` 时 `/web/*`、`/docs`、`/redoc`、`/openapi.json` 全部关闭 (`DISABLE_WEB_ROUTES_IN_PROD=true`) |
| 公开端点 | `/api/*` / `/auth/*` / `/me/*` / `/iap/*` / `/apple/asn` / `/healthz` |
| 受限端点 | `/admin/*` 双重保护：RBAC + IP 白名单 (`ADMIN_IP_ALLOWLIST`) |

## 方案 A · fly.io 灰度

最适合初期 1-3 天就能上线的小流量灰度。

```bash
fly launch --copy-config --no-deploy --config deploy/fly.toml
fly volumes create ai_photo_coach_data --size 10 --region nrt
fly redis create                  # 自动注入 REDIS_URL
fly pg create --name aphc-pg      # 切到 Postgres 后把 SQLite volume 退役
fly pg attach aphc-pg

# 设置敏感凭证（一次性）
fly secrets set \
  APP_JWT_SECRET=$(openssl rand -hex 32) \
  OTP_HASH_SECRET=$(openssl rand -hex 32) \
  ALIYUN_SMS_ACCESS_KEY=... ALIYUN_SMS_ACCESS_SECRET=... \
  ALIYUN_SMS_SIGN_NAME='AI Photo Coach' ALIYUN_SMS_TEMPLATE_CODE=SMS_xxx \
  ALIYUN_DM_ACCESS_KEY=... ALIYUN_DM_ACCESS_SECRET=... \
  ALIYUN_DM_SENDER='no-reply@aiphotocoach.app' \
  ADMIN_BOOTSTRAP='13800000000:sms,owner@aiphotocoach.app:email' \
  ADMIN_IP_ALLOWLIST='198.51.100.0/24'

fly deploy
```

存储映射：

| 资源 | fly.io 选型 |
| --- | --- |
| 关系数据 | Fly Postgres (3 节点高可用) |
| 缓存 / token bucket | Upstash Redis (Fly redis create 包装) |
| 静态/CDN | Cloudflare R2 + Pages |
| 备份 | Fly snapshots → 每天到 R2 (PR12) |

## 方案 B · 阿里云 ECS 主生产

国内主战场，ICP 备案 + 微信小程序合规两件套都靠它。

### 资源清单

| 资源 | 规格 | 备注 |
| --- | --- | --- |
| ECS | ecs.c7.large × 2 (北京/上海多可用区) | 跑 backend 容器 |
| ALB | 1 实例 + WAF | TLS 终止 + 防 CC |
| RDS PostgreSQL | rds.pg.s2.large | 主备 + 7 天备份 |
| 云数据库 Redis | 1G 主从 | session / token bucket |
| OSS | 1 桶 | 仅未来扩展用，目前不存原图 |
| SLS | 1 项目 | 接 Datadog Agent → Datadog logs |

### 起服

把 `.env.prod` 放到 ECS `/opt/aphc/.env.prod`，然后：

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file /opt/aphc/.env.prod up -d
```

ALB 上把 `0.0.0.0:443` → `ECS:8000`；监听器加：

- 路径 `/admin/*` → 仅放 office VPN 的 IP（与 `ADMIN_IP_ALLOWLIST` 一致）
- 路径 `/web/*`、`/docs`、`/redoc` → 直接 404 (后端已自动 404，但加规则做纵深防御)

### ICP / 合规

- 域名 `*.aiphotocoach.cn` 必须先经过 ICP 备案，CDN 才能开放
- 隐私政策 / EULA URL 通过 `PRIVACY_POLICY_URL`、`EULA_URL` 注入
- 实名制要求：阿里云 SMS 模板上线前要走「APP 推广」类型审核

## 验证清单

```bash
curl -I https://api.aiphotocoach.app/web/index.html       # 期望 404
curl -I https://api.aiphotocoach.app/docs                 # 期望 404
curl -I https://api.aiphotocoach.app/healthz              # 期望 200
curl -X POST https://api.aiphotocoach.app/auth/anonymous \
     -H 'Content-Type: application/json' -d '{}'          # 期望 410 anonymous_disabled
curl -H "Authorization: Bearer fake" \
     https://api.aiphotocoach.app/admin/audit/summary     # 期望 401 (token 不合法) 或 403 (admin_ip_denied)
```
