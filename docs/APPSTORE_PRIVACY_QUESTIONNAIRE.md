# App Store Connect "App Privacy" 问卷答题表（v17j）

> 版本：与 `ios/AIPhotoCoach/App/PrivacyInfo.xcprivacy` 1:1 对齐。提交新版前，先校对 PrivacyInfo 是否被改过；如改过，先更新这份文档再提审。

提审入口：App Store Connect → 你的 App → "App 隐私"（App Privacy）→ "编辑数据收集"

下方按 Apple 问卷的实际控件顺序列出，**每一项都给出"勾还是不勾"+ 理由**，避免下次提审时来回猜。

---

## Part 1：是否收集任何数据？

**勾选：是（Yes）**

我们收集了 device id、location、photos、email、phone、product interaction、purchase history、crash、performance — 任何一项都已构成"收集数据"。

---

## Part 2：每项数据类型逐条作答

> 共 9 项。每一项 Apple 都会问 4 个子问题：
>
> 1. 用途（purpose）
> 2. 是否与用户身份关联（linked to user）
> 3. 是否用于追踪（tracking）
> 4. 收集与否（toggle）

| 数据类型（Apple 控件名） | 收集？ | 关联用户？ | 用于追踪？ | 用途勾选项 |
|---|---|---|---|---|
| Identifiers → **Device ID** | ✅ | ✅ | ❌ | App Functionality |
| Location → **Coarse Location** | ✅ | ✅ | ❌ | App Functionality + Personalization |
| User Content → **Photos or Videos** | ✅ | ✅ | ❌ | App Functionality |
| Contact Info → **Email Address** | ✅ | ✅ | ❌ | App Functionality |
| Contact Info → **Phone Number** | ✅ | ✅ | ❌ | App Functionality |
| Usage Data → **Product Interaction** | ✅ | ✅ | ❌ | App Functionality + Analytics + Personalization |
| Purchases → **Purchase History** | ✅ | ✅ | ❌ | App Functionality |
| Diagnostics → **Crash Data** | ✅ | ✅ | ❌ | App Functionality + Analytics |
| Diagnostics → **Performance Data** | ✅ | ✅ | ❌ | App Functionality + Analytics |

**未收集（不要勾选）：**
- Precise Location（仅粗略位置，已经服务端取整到 ~11m）
- Name / Physical Address / Other User Contact Info
- Health & Fitness / Financial Info / Sensitive Info
- Browsing History / Search History
- Audio Data / Other User Content（不上传音频）
- Other Diagnostic Data / Other Data Types
- Advertising Data / Other Identifiers（不接 IDFA）

---

## Part 3：每项的解释（粘进 App Store Connect 的 "Data Use" 自由文本框）

### Device ID
Used to anchor the 5-shot free trial quota to a specific device, preventing abuse via account enumeration. Stored as a SHA-256 fingerprint server-side; the raw Keychain UUID never leaves the device. Not used for advertising or cross-app tracking.

### Coarse Location
Used to recommend lighting and composition appropriate to the user's locale (e.g. golden-hour timing). Server-side, the coordinate is rounded to ~11m (`geo_round_decimals=4`) before storage.

### Photos or Videos
Only EXIF metadata and a small set of frame thumbnails are uploaded to power composition / lighting analysis. Source images stay on the device. Never used for ML training without separate user opt-in.

### Email Address
Collected only when the user logs in via Sign in with Apple **with the "Share Email" toggle on**, or when they choose Email OTP. Used solely for authentication and account communication. Never sold or shared.

### Phone Number
Collected only when the user picks the phone-OTP login channel (Aliyun SMS). Used to deliver the 6-digit verification code; not linked to any third-party identifier.

### Product Interaction
Tracks which scene mode, quality mode, style keywords, and proposal the user picks. Each row is stored linked-to-account so the user can see, export, or delete it.

We use this data in three ways:
1. **Personalization (v18)** — when the user explicitly taps a thumbs-up/down on a captured shot, we store that single bit alongside the (scene, style) tuple. Future analyze calls for this same user bias toward styles they've historically enjoyed. The signal stays scoped to the user's own account; nothing about other users is exposed to them.
2. **Cross-user aggregation (admin-gated)** — the same satisfaction signal is anonymously rolled up into `satisfaction_aggregates`. Only when a (scene, style) bucket has at least 30 distinct users AND meets a configurable satisfaction-rate floor does it enter the prompt as a soft hint for all users. The whole pipeline is OFF by default and requires admin opt-in.
3. **Insights (analytics)** — aggregated, k-anonymized (k≥5) usage stats inform product decisions. No row-level data is exposed; user_id never appears in admin insight responses.

No photo bytes are ever uploaded; the satisfaction signal is one boolean plus an optional ≤200-character note.

### Purchase History
Apple receipt → product ID, expiration timestamp, environment. Used to gate access to paid features and reconcile renewals via Apple Server Notifications.

### Crash Data
Stack traces and OS metadata for crash diagnosis. Linked-to-account where available so we can correlate "this user keeps hitting bug X". Not used for ad targeting.

### Performance Data
Cold-start time, network latency, frame drops. Same handling as Crash Data.

---

## Part 4：第三方 SDK 隐私清单（Privacy Manifest 依赖）

依赖图里以下 SDK 必须随 v17j 一起提交它们的 `PrivacyInfo.xcprivacy`（Apple 自 2024 春起强制）：

- ❌ 暂未集成 Datadog RUM iOS（计划中，集成时**必须**勾上 Crash Data + Performance Data 的 "third party" 子选项；此处先按照"自有诊断"勾选）

如果未来引入：

| SDK | 必须报告的数据类型 |
|---|---|
| Datadog RUM | Crash Data, Performance Data, Device ID |
| Sentry | Crash Data, Performance Data |
| Firebase Analytics | Product Interaction, Device ID |
| Adjust / AppsFlyer | ⚠️ 触发 Tracking=YES，要求 ATT 弹窗 |

---

## Part 5：提交检查清单（每次 release 前过一遍）

- [ ] `PrivacyInfo.xcprivacy` 与本表 Part 2 一致（diff 一下 `git log -p ios/AIPhotoCoach/App/PrivacyInfo.xcprivacy`）
- [ ] App Store Connect → 数据收集 Toggle 与 Part 2 一致
- [ ] 自由文本框内容与 Part 3 一致（已粘贴）
- [ ] 是否新接入了任何第三方 SDK？如有，更新 Part 4 并查它的 manifest
- [ ] 隐私政策网站（`docs/PRIVACY_POLICY_DELTA.md`）与本表同步过

---

## Part 6：常见提审驳回原因 & 对应修复

| 驳回理由 | 触发条件 | 修复 |
|---|---|---|
| "Inaccurate disclosure of data use" | App 实际用途超出 questionnaire 勾选项 | 把对应 purpose 也勾上，重提 |
| "Missing privacy manifest for SDK X" | SDK 未带 `PrivacyInfo.xcprivacy` | 升级到自带 manifest 的版本，或换 SDK |
| "Tracking requires ATT prompt" | 勾了 Tracking=YES 但没在 Info.plist 加 `NSUserTrackingUsageDescription` 并在代码里调 `ATTrackingManager.requestTrackingAuthorization` | 我们当前 Tracking=NO，**不要**手贱去开 |
| "Account deletion path missing" | 自 2022 起强制 | 已实现：`/auth/me` DELETE，对应 UI 在"个人资料"→"删除账户"。提审时录屏一遍即可 |

---

最后修订时间：2026-05-12（v17j）
