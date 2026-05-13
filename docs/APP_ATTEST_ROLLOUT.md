# App Attest 启用流程（v17c）

## 现状

| Flag | 默认 | 含义 |
|---|---|---|
| `require_app_attest_on_otp` | `False` | 启用后 `/auth/otp/request` 必须携带 `X-Attest-KeyId` + `X-Attest-Assertion`，否则 403 |
| `require_app_attest_on_analyze` | `False` | 同上，作用于 `/analyze` |
| `app_attest.is_enforcing()` | 取决于 `data/apple_app_attest_root_ca.pem` 是否存在 | 即使 require 为 True，root CA 不存在则 verifier 走 shadow-mode（接受任何 assertion）|

## 上线步骤

### 0. 准备 Apple 资源（一次性）

1. Apple Developer Portal → Identifiers → 你的 Bundle ID → 启用 **App Attest** capability。
2. 下载 [Apple App Attestation Root CA](https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem)，放到 `backend/app/data/apple_app_attest_root_ca.pem`。
3. 生产 env 设置 `APPLE_SIWA_BUNDLE_ID`、`APPLE_SIWA_TEAM_ID`（已存在），verifier 用它们算 RP id hash。

### 1. iOS 端先行（影子模式）

- 已实现 `AppAttestManager.shared.bootstrap()`（启动时调）+ `assertionHeaders(for:)`（OTP/analyze 时附加）。
- 不需要改后端 flag，先发版让全量用户的设备做一次 attest（注册 key_id）。
- 观察 `/devices/attest` 的 200/4xx 比例 ≥ **95% 成功 ≥ 7 天**，再进入第 2 步。
- 不达标的常见原因：模拟器（不支持，正常忽略）、越狱设备（拒绝，按预期）、网络抖动（用户重启 app 重试）。

### 2. 后端开始统计 assertion 命中率（不强制）

观察 `/analyze` 与 `/auth/otp/request` 收到的 `X-Attest-KeyId` header 比例。可以临时在 nginx/ALB 加一行日志统计；或者扩 `app_attest.verify_assertion`，在 shadow-mode 时也按命中/缺失计数到 Datadog。

**门槛**：≥ **95% 请求带 keyId**、verifier 误拒率 < **0.5%**，才能进入第 3 步。

### 3. 灰度开启 OTP 强制（先收紧短信账单）

```bash
export REQUIRE_APP_ATTEST_ON_OTP=true
```

OTP 是最值钱的接口（每次掉账单），先开 OTP。观察 7 天内：

- `attest_required` 错误率
- 客服工单关键词「收不到验证码」「APP 升级」

异常激增 → 立刻关掉 flag 回到 shadow-mode（无需重启，下次请求生效）。

### 4. 灰度开启 analyze 强制

```bash
export REQUIRE_APP_ATTEST_ON_ANALYZE=true
```

analyze 重要性低于 OTP（按次扣费已有限速），但 token 成本贵，最后一步开。

### 5. 监控指标

- `attest_required` 4xx 比例（应 < 1%）
- `attest_invalid` 4xx 比例（应 < 0.5%；高于此值通常说明 root CA 配错）
- iOS 端 `AppAttestManager.bootstrap()` 失败率（在 metrics 中追踪）

### 回滚

环境变量改回 false 即可。无需重启（fastapi `get_settings()` 缓存了 settings；如有缓存重启 fastapi 进程）。

## FAQ

**Q：模拟器调试怎么办？**
A：模拟器 `DCAppAttestService.isSupported == false`，`bootstrap()` 直接返回 nil。flag 开启后模拟器会被 403 拦截。开发期请保持 flag = false，生产单独翻。

**Q：用户换设备恢复 keychain 后 keyId 还有效吗？**
A：不能。App Attest key 绑定 Secure Enclave，跨设备不可迁移。我们存了 keyId 在 UserDefaults，新设备首次启动会重新 attest 拿到新 keyId，自动覆盖。

**Q：assertion 计算成本？**
A：实测 50-200ms 一次（Secure Enclave 签名）。OTP 路径用户能感知，建议每个 challenge 缓存 30s（见 `n2-runtime-settings` 优化项）。
