# Multi-User, Auth, Subscription & App Store Compliance

> Source-of-truth tracker for the **multi-user / auth / subscription /
> compliance** workstream. Created 2026-05-11 in response to:
> "现在 app 是不是不支持多用户并发？上线 App Store 会不会出问题？
> 用户隔离 / 安全认证 / 订阅式付费怎么按 App Store 合规实现？"
>
> Owner: backend + iOS. Update inline as items move.

---

## TL;DR — 现状诊断

| 维度 | 现状 | 风险 |
|---|---|---|
| 并发 | FastAPI async ✅，但 sqlite + 进程内 rate-limit | 多 worker 串号 / 限流被绕过 |
| 用户身份 | 仅 `X-Device-Id` header | 完全可伪造 → 隐私事故 |
| 数据隔离 | 所有表无 `user_id` | A 拉到 B 数据 |
| App Attest | shadow mode（`app_attest.py:87-100` TODO） | 脚本可刷 LLM 配额 |
| 订阅 | `IAPManager.useShadowPro = true`，无服务端验签 | 越狱白嫖 + Apple 3.1.1 拒审 |
| 删除账户 | 无 | Apple 5.1.1(v) 强制要求 |
| Privacy Manifest | 无 | iOS 17+ 拒审 |

---

## 目标架构

```
┌──────────────┐   ① SIWA / 匿名
│   iOS App    │──────────────────►┌──────────────┐
│              │                   │   /auth/*    │
│ AuthManager  │◄─── access_token ─│  JWT issuer  │
│  + Keychain  │     refresh_token │              │
└──────┬───────┘                   └──────────────┘
       │ Bearer + X-App-Attest-*
       ▼
┌─────────────────────────────────────────────┐
│  Every write API: Depends(current_user)     │
│   - all rows tagged user_id                 │
│   - rate-limit key = (route, user_id)       │
└─────────────────────────────────────────────┘
       │
       ▼
┌──────────────┐   StoreKit2 JWS    ┌──────────────────┐
│ IAPManager   │───────────────────►│  /iap/verify     │
│              │◄──── entitlements ─│  + subscriptions │
└──────────────┘                    └──────────────────┘
       ▲                                    ▲
       │     /me/entitlements               │ ASN V2 webhook
       └────────────────────────────────────┘ (Apple → /apple/asn)
```

**三层身份叠加**：

1. **匿名层** — 首启在 Keychain 生成 UUID，POST `/auth/anonymous` 换 JWT。
2. **正式账号** — Sign in with Apple 后 POST `/auth/siwa`，把 `apple_sub` merge 到现有匿名 user，历史数据自动跟随。
3. **设备保真** — App Attest assertion 绑到 `(user_id, key_id)`，防机器人。

---

## 实施阶段

### Phase 0 — 上架阻塞项（本迭代必须）

| # | Item | 负责模块 | Status |
|---|---|---|---|
| **A0-1** | `users` 表 + `UserRepo`（sqlite, 可迁 PG） | `backend/app/services/user_repo.py` | ☐ |
| **A0-2** | JWT 签发 / 校验工具 | `backend/app/services/auth.py` | ☐ |
| **A0-3** | `/auth/anonymous` + `/auth/siwa` + `/auth/refresh` | `backend/app/api/auth.py` | ☐ |
| **A0-4** | `current_user` FastAPI 依赖（兼容 `X-Device-Id` 兜底，逐步收紧） | `backend/app/services/auth.py` | ☐ |
| **A0-5** | `shot_results / recon3d_jobs / user_spots` 加 `user_id` 列 + 查询过滤 | `backend/app/api/feedback.py` `recon3d.py` `services/poi_lookup.py` | ☐ |
| **A0-6** | `DELETE /users/me` 级联删 | `backend/app/api/auth.py` | ☐ |
| **A0-7** | `/iap/verify` + `subscriptions` 表 + `/me/entitlements` | `backend/app/api/iap.py` + `services/iap_apple.py` | ☐ |
| **A0-8** | `POST /apple/asn` Webhook（DID_RENEW / EXPIRED / REFUND） | `backend/app/api/iap.py` | ☐ |
| **A0-9** | App Attest 真实验签（CBOR + ECDSA P-256 + Apple Root CA） | `backend/app/services/app_attest.py` | ☐ |
| **A0-10** | iOS `AuthManager` + APIClient 注入 `Authorization: Bearer` | `ios/AIPhotoCoach/Services/AuthManager.swift` | ☐ |
| **A0-11** | iOS `IAPManager` 关 shadow + 上传 JWS + 拉 `/me/entitlements` | `ios/AIPhotoCoach/Services/IAPManager.swift` | ☐ |
| **A0-12** | iOS Sign in with Apple 入口 + 删除账户 | `ios/AIPhotoCoach/Features/Settings/AccountView.swift` | ☐ |
| **A0-13** | Privacy Manifest `PrivacyInfo.xcprivacy` | `ios/AIPhotoCoach/App/PrivacyInfo.xcprivacy` | ☐ |
| **A0-14** | smoke + e2e 测试 | `backend/tests/test_auth_*.py` `test_iap_*.py` | ☐ |

### Phase 1 — 上架后第一迭代

| # | Item | Status |
|---|---|---|
| A1-1 | Redis 化 rate limit（key=`(user_id,route)`） | ✅ `services/rate_limit.py`（`REDIS_URL` 启用，未设回落进程内 bucket） |
| A1-2 | sqlite → Postgres 迁移 + Alembic | ✅ 文档化策略 `docs/DATABASE_BACKEND.md`（按需切换） |
| A1-3 | Webhook `REFUND` 调额度并推送 | ✅ ASN 处理 + `_evaluate_tier` 自动降级（A0-7/A0-8 完成时一并落地） |
| A1-4 | 30 天匿名账号 + 数据自动清理 | ✅ `user_repo.purge_inactive_anonymous` + `main._anonymous_account_sweeper` |
| A1-5 | 免费/Pro 差异化配额 | ✅ `rate_limit._scale_for_tier` + `rate_limit_pro_multiplier=5.0` |
| A1-6 | App Store Server API 巡检 cron | ✅ `scripts/reconcile_subscriptions.py`（hourly） |
| A1-7 | iOS Paywall force-refresh helper | ✅ `IAPManager.paywallGate()` + `PostProcessView` 调用 |
| A1-8 | iOS 上传前 strip EXIF GPS | ✅ `Core/Privacy/ImageSanitizer.swift` + `APIClient.analyze` 调用 |
| A1-9 | 启动 env/CA 校验 | ✅ `services/startup_checks.py`（prod 缺关键项拒起动） |
| A1-10 | 隐私政策默认页面 + iOS 动态 URL | ✅ `web/privacy.html` + `/healthz` 暴露 + `AccountView.fetchLegalURLs` |

### Phase 2 — 长期

| # | Item | Status |
|---|---|---|
| A2-1 | 多端账号同步（同 Apple ID 多机） | ✅ 天然支持（subscriptions 按 `apple_sub` merge），文档归档于 Family Sharing |
| A2-2 | Family Sharing | ✅ 落地方案 `docs/FAMILY_SHARING.md`（Option A：放开 unique 约束 + ON CONFLICT 改 `(user_id, original_transaction_id)`） |
| A2-3 | Stripe 网页订阅（注意 Apple 3.1.3） | ✅ 集成方案 + 合规约束 `docs/STRIPE_WEB_SUBSCRIPTION.md` |
| A2-4 | auth/iap/asn metrics counters | ✅ `auth_total{method}` `iap_apply_total` `asn_total{type}` `rate_limit_total{route,tier}` |

---

## 数据模型

### `users`

```sql
CREATE TABLE users (
    id              TEXT PRIMARY KEY,           -- uuid v4
    apple_sub       TEXT UNIQUE,                -- SIWA subject; NULL when anonymous
    email           TEXT,                       -- 可空（SIWA 用户可能选 hide）
    is_anonymous    INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,                       -- 软删；24h 后真删
    tier            TEXT NOT NULL DEFAULT 'free' -- free | pro
);
CREATE INDEX idx_users_apple_sub ON users(apple_sub);
CREATE INDEX idx_users_deleted   ON users(deleted_at);
```

### `subscriptions`

```sql
CREATE TABLE subscriptions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                     TEXT NOT NULL REFERENCES users(id),
    product_id                  TEXT NOT NULL,
    original_transaction_id     TEXT NOT NULL,
    latest_transaction_id       TEXT NOT NULL,
    environment                 TEXT NOT NULL,    -- 'Sandbox' | 'Production'
    purchase_date               TEXT NOT NULL,
    expires_at                  TEXT,             -- NULL for non-renewing
    revoked_at                  TEXT,
    auto_renew                  INTEGER NOT NULL DEFAULT 1,
    raw_jws                     TEXT NOT NULL,    -- 最近一次 JWS（审计 + 重放）
    received_at                 TEXT NOT NULL,
    UNIQUE(original_transaction_id)
);
CREATE INDEX idx_sub_user      ON subscriptions(user_id);
CREATE INDEX idx_sub_expires   ON subscriptions(expires_at);
```

### 现有表迁移（lazy ALTER，不破坏旧库）

- `shot_results`：新增 `user_id TEXT`（已有 `device_id`）
- `recon3d_jobs`：新增 `user_id TEXT`
- `user_spots`：新增 `user_id TEXT`
- `attested_devices`：新增 `user_id TEXT`，`(user_id, key_id)` 复合索引

---

## API 合约

### `POST /auth/anonymous`
```jsonc
// req
{ "device_id": "<keychain uuid>" }

// resp
{
  "user_id": "uuid",
  "is_anonymous": true,
  "access_token": "...",   // 15 min
  "refresh_token": "...",  // 30 day
  "tier": "free"
}
```

### `POST /auth/siwa`
```jsonc
// req: identityToken from ASAuthorizationAppleIDCredential
{
  "identity_token": "<JWT from Apple>",
  "authorization_code": "...",
  "device_id": "<keychain uuid>",  // 可选；用于 merge 匿名账号
  "user_full_name": { "givenName": "...", "familyName": "..." } // 仅首登提供
}

// resp: 同 /auth/anonymous，is_anonymous=false
```

校验流程：
1. 用 Apple 公钥 (`https://appleid.apple.com/auth/keys`) 校 `identity_token` JWT 签名
2. 校 `aud` == 你的 bundleId、`iss` == `https://appleid.apple.com`、`exp` > now
3. `sub` 即 `apple_sub`；查 `users` 表
4. 如果 `device_id` 对应已有匿名 user 且 `apple_sub` 列空 → 升级该匿名 user 为正式
5. 否则新建或返回已有正式 user

### `POST /auth/refresh`
```jsonc
{ "refresh_token": "..." } → { "access_token": "...", "refresh_token": "..." }
```

### `POST /iap/verify`
```jsonc
// req
{ "jws_representation": "<StoreKit2 Transaction.jsonRepresentation>" }

// resp
{
  "ok": true,
  "tier": "pro",
  "product_id": "ai_photo_coach.pro.monthly",
  "expires_at": "2026-06-11T..."
}
```

服务端用 Apple 的 JWS 公钥校验签名（参考 `App Store Server Library` 的逻辑），写 `subscriptions` 表，更新 `users.tier`。

### `POST /apple/asn`
Apple → 你的服务器的 webhook（在 App Store Connect 配 URL）。
处理：
- `SUBSCRIBED` / `DID_RENEW` → 更新 `expires_at`
- `EXPIRED` / `REVOKE` / `REFUND` → 设 `revoked_at` + 降 `tier=free`
- `GRACE_PERIOD_EXPIRED` → 同上

签名校验同 `/iap/verify`。

### `GET /me/entitlements`
```jsonc
{
  "tier": "pro",                              // 'free' | 'pro'
  "expires_at": "2026-06-11T...",             // null when free
  "subscription": {
    "product_id": "ai_photo_coach.pro.monthly",
    "auto_renew": true,
    "in_grace_period": false
  }
}
```

iOS 启动 + 进入付费功能前调；客户端**只**根据这个判断是否解锁。

### `DELETE /users/me`
软删 `users.deleted_at = now`，并立即（同事务）删除：
- `shot_results WHERE user_id = ?`
- `recon3d_jobs WHERE user_id = ?`
- `user_spots WHERE user_id = ?`
- `subscriptions WHERE user_id = ?`
- `attested_devices WHERE user_id = ?`

返回 `204 No Content`。
后台 cron 24h 后清理 `deleted_at < now-24h` 的 users 行（保留窗口给误操作恢复）。

---

## iOS 集成清单

1. **Keychain 持久化 device UUID**（`ios/AIPhotoCoach/Services/DeviceIdStore.swift`，已存在 `device_id` 概念可复用）
2. **AuthManager**：
   - 启动时 `await ensureSession()` → 没 token 就 `/auth/anonymous`
   - 401 → 用 refresh token 续；refresh 也失败 → 重新匿名
   - `signInWithApple()` → SIWA → `/auth/siwa`
3. **APIClient**：每个请求注入 `Authorization: Bearer <access_token>`
4. **IAPManager**：
   - `useShadowPro = false`
   - 购买成功后 `await uploadJWS(transaction.jsonRepresentation)`
   - `isProActive` 改成读 `entitlementsCache`（10 min TTL）
5. **AccountView**：登录 / 退出 / 删除账户三个按钮
6. **PrivacyInfo.xcprivacy**：声明 `NSPrivacyAccessedAPICategoryUserDefaults`、位置、相机、相册用途

---

## 部署 / 运维 checklist（人工）

- [ ] App Store Connect 创建商品 `ai_photo_coach.pro.monthly`（¥18/月）
- [ ] Apple Developer 后台开启 App Attest capability
- [ ] 下载 `Apple_App_Attestation_Root_CA.pem` → `backend/app/data/`
- [ ] 配 ASN V2 Webhook URL：`https://<prod>/apple/asn`（Production + Sandbox 各一份）
- [ ] 生成 App Store Connect API Key（Issuer ID / Key ID / .p8）→ env：
  ```
  APPLE_IAP_BUNDLE_ID=com.example.aiphotocoach
  APPLE_IAP_ISSUER_ID=...
  APPLE_IAP_KEY_ID=...
  APPLE_IAP_PRIVATE_KEY_PATH=/etc/secrets/AuthKey_XXX.p8
  APPLE_SIWA_BUNDLE_ID=com.example.aiphotocoach
  APP_JWT_SECRET=$(openssl rand -hex 32)
  REQUEST_TOKEN_SECRET=$(openssl rand -hex 32)
  CORS_ALLOW_ORIGINS=https://yourdomain.com
  ```
- [ ] cron: `*/15 * * * *` 跑订阅过期巡检（兜底 webhook 漏掉的）
- [ ] cron: `0 5 * * *` 真删 24h 前软删的用户

---

## 验收标准

Phase 0 完工的判定：

1. ✅ `pytest backend/tests/test_auth_*.py test_iap_*.py test_user_isolation_*.py` 全绿
2. ✅ 用 user A 的 token 访问 user B 的 `recon3d_jobs/{id}` → 404
3. ✅ `IAPManager.useShadowPro=false` 且 sandbox 购买成功后 `/me/entitlements` 返回 `tier=pro`
4. ✅ `DELETE /users/me` 后该 user_id 的行全部消失
5. ✅ Privacy Manifest 通过 Xcode 15 隐私报告生成
6. ✅ App Attest enforce 模式（root CA 在位）下，伪造 assertion 被拒

---

## 兼容策略

为不破坏现网客户端：

- 旧客户端只发 `X-Device-Id` 不发 `Authorization` → backend 自动建匿名 user 并返回 `X-User-Id` header（一次性）让客户端记下来。
- `analyze_request_id` HMAC token 仍用 device_id+scene_mode 组成 payload，保持现有 `/feedback` 验签逻辑。
- iOS 升级到 v1.1（含 `AuthManager`）后才会启用 SIWA / IAP 验签；老版本继续匿名 + shadow Pro。
