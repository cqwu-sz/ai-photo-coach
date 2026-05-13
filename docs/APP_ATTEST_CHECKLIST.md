# App Attest 开通操作清单

辅助 `APP_ATTEST_ROLLOUT.md` 的「第 0 步」。打勾过一遍即可。

## Apple 侧（Web 控制台）

- [ ] 登录 [developer.apple.com](https://developer.apple.com) → Account → Certificates, IDs & Profiles
- [ ] Identifiers → 选中 `com.yourorg.aiphotocoach`（或当前 Bundle ID）
- [ ] 在 Capabilities 里勾选 **App Attest**
- [ ] Save。Xcode 会在下次 fetch profile 时自动同步，无需 regenerate provisioning profile（App Attest 不挂在 profile 里，挂在 entitlements 里）

## Xcode 侧

- [ ] 打开 `ios/AIPhotoCoach.xcodeproj`，Target → Signing & Capabilities
- [ ] 点 `+ Capability`，搜索 `App Attest`，添加
- [ ] Build → 检查 `AIPhotoCoach.entitlements` 是否多了 `com.apple.developer.devicecheck.appattest-environment`，值为 `production`（Release）/ `development`（Debug）

## 后端侧

- [ ] 下载 root CA：

  ```bash
  curl -fsSL https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem \
    -o backend/app/data/apple_app_attest_root_ca.pem
  ```

- [ ] 校验指纹（截至 2026 年正确值，每年体检一次）：

  ```bash
  openssl x509 -in backend/app/data/apple_app_attest_root_ca.pem -noout -fingerprint -sha256
  # 应为：BB:DC:14:35:2B:25:50:9A:8B:BE:32:73:B4:13:5C:4A:36:A6:64:E0:46:75:38:C7:0F:5B:62:D8:64:55:48:14
  ```

- [ ] 提交到仓库（pem 是公开的）：

  ```bash
  git add backend/app/data/apple_app_attest_root_ca.pem
  git commit -m "chore(attest): bundle Apple App Attest root CA"
  ```

- [ ] 部署后用一台 *真机*（模拟器不支持 App Attest）首次启动 app，应在 1-2s 内看到日志：

  ```
  app_attest: bootstrap success keyId=xxxx...
  ```

  如果是模拟器或越狱机，会静默 no-op，正常。

## 监控

- [ ] Datadog 添加 dashboard 卡：`avg(rate(attest_required[5m]))` < 1%
- [ ] Datadog 添加 dashboard 卡：`avg(rate(attest_invalid[5m]))` < 0.5%
- [ ] iOS Crashlytics（或当前 SDK）追踪 `AppAttestManager.bootstrap` 失败率

## 灰度门槛

| 步骤 | 触发开关 | 安全门槛 |
|---|---|---|
| Shadow（默认） | flag = false | 无门槛，先发版收 keyId |
| OTP 强制 | `REQUIRE_APP_ATTEST_ON_OTP=true` | 7 天内 ≥ 95% OTP 请求带 keyId 且 verifier 误拒 < 0.5% |
| Analyze 强制 | `REQUIRE_APP_ATTEST_ON_ANALYZE=true` | OTP 强制后再观察 7 天，无投诉激增 |

回滚：env var 改回 false → `kill -HUP` 或重启 fastapi 进程即生效。
