# Windows 上侧载 iOS App 到自己的 iPhone

这份文档把"我没 Mac，但想在自己 iPhone 上跑这个原生 App"这件事走通：
我们用 GitHub Actions 在云端编译出 **未签名的 .ipa**，然后在 Windows
上用 **Sideloadly** + 你自己的 **免费 Apple ID** 给它本地签名安装。
有效期 7 天，过期再用 Sideloadly 重签即可（每天能签 10 次足够）。

> 这套流程**不需要** $99/年的 Apple Developer 账号，只要一个普通的免费
> Apple ID（就是你日常登录 iCloud 的那个）。

---

## 一、前置条件

| 项 | 说明 |
| --- | --- |
| Apple ID | 免费的就行；如果开了双因素验证（建议开），需要准备一个 [App 专用密码](https://account.apple.com/account/manage)。 |
| iPhone 数据线 | Lightning 或者 USB-C，能稳定连 PC。 |
| iTunes for Windows | Sideloadly 需要 iTunes 提供 Apple Mobile Device 驱动。从 [Apple 官网](https://www.apple.com/itunes/download/) 下载 **Windows 版**，**不要从微软商店装**（商店版没驱动）。 |
| Sideloadly | 免费工具，从 [https://sideloadly.io](https://sideloadly.io) 下载 Windows 版安装。 |
| iPhone 设置 | iOS 16+：Settings → Privacy & Security → 开启 **Developer Mode**（首次开启需要重启手机）。 |

---

## 二、拿到 .ipa（两种方式任选）

### 方式 A：从 GitHub Actions 下

1. 把项目 push 到自己的 GitHub 仓库（fork 或者私有都行）。
2. 进入仓库 → Actions → 选 **iOS Build (unsigned IPA)** 工作流。
3. 点右上角 `Run workflow`（或者直接 push 一次到 main 自动触发）。
4. 等 5–10 分钟跑完，进入这次 run 的页面，下方 Artifacts 区有
   `AIPhotoCoach-unsigned-ipa.zip`，下载到本地后解压得到
   `AIPhotoCoach-unsigned.ipa`。

### 方式 B：发个 tag 走 Release

```bash
git tag v0.1.0
git push origin v0.1.0
```

CI 跑完会自动把 .ipa 挂到 GitHub Releases 页，免登录也能下。

---

## 三、用 Sideloadly 装到 iPhone

1. 用数据线连 iPhone，iPhone 上点 **信任此电脑**。
2. 打开 Sideloadly，左上角能看到你 iPhone 的型号。
3. 把 `AIPhotoCoach-unsigned.ipa` 拖进 Sideloadly 中间的虚线框。
4. **Apple ID** 填你的 Apple ID 邮箱；**Password** 填上面提到的
   App 专用密码（不是 iCloud 主密码！）。
5. 点 `Start`。Sideloadly 会：
   - 给 .ipa 重新签名（用一个临时的 7 天 provisioning profile）
   - 通过 iTunes 的驱动安装到 iPhone
6. 安装后 iPhone 上会出现一个图标 `AI Photo Coach`，但点开会提示
   "未受信任的开发者"。
7. 去 **Settings → General → VPN & Device Management** 里找到你的
   Apple ID，点进去 → **Trust "你的邮箱"** → 再点 Trust。
8. 回桌面打开应用，**第一次**会要求授权摄像头、相册、运动传感器
   ——全部允许。

> **如果失败排查**：
> - "Could not pair iPhone" → 重新插拔数据线，iPhone 上再点一次"信任"。
> - "Maximum number of apps installed" → 免费 Apple ID 同时只能装
>   3 个用 7 天证书签的 app，把别的删掉再装。
> - "Invalid credentials" → 你没用 App 专用密码，去
>   appleid.apple.com 生成一个。

---

## 四、连接你自己的后端

App 默认会去找 `http://localhost:8000`。但是 iPhone 不能访问你 PC 的
localhost，需要：

1. 把 PC 和 iPhone **接到同一个 Wi-Fi**。
2. 在 PC 上找内网 IP：PowerShell 跑 `ipconfig`，找到 `IPv4 Address`，
   类似 `192.168.1.42`。
3. 启动后端时绑到 0.0.0.0：

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

4. iPhone 上的 App 当前是硬编码的 `localhost`，需要改一下：编辑
   `ios/AIPhotoCoach/Core/APIClient/APIConfig.swift`，把 baseURL 改成
   `http://192.168.1.42:8000`，然后重新走"二、三"两步。
5. **明文 HTTP 注意**：iOS 默认禁止明文 HTTP，但项目的 Info.plist 已经
   把 `NSAllowsLocalNetworking` 设为 true，局域网 HTTP 是放行的。

> 想云端永久部署后端再连？把 backend 部署到 Render / Fly.io（免费档），
> 拿到 https URL 替换 baseURL 即可，不用再改安全例外。

---

## 五、7 天后过期了怎么办

Sideloadly 重新签名一次就行：

1. 把 iPhone 连上电脑、打开 Sideloadly。
2. 在它的 **Sideloads** 标签页里找到这个 app，右键 → `Re-sign & Install`。
3. 不需要重新下载 .ipa，也不会丢 app 内的数据。

如果你嫌麻烦，可以装 [AltStore](https://altstore.io)，它能在你 PC
开着的时候每天后台自动给所有侧载 app 续签。

---

## 六、常见问题

**Q: 用 Sideloadly 安全吗？**
A: 它是开源工具，全程在你电脑本地跟 Apple 服务器通信，App ID 密码不发
给任何第三方服务器（建议用 App 专用密码再保险一层）。

**Q: 为啥不直接用 TestFlight？**
A: TestFlight 需要付费 Apple Developer Program（$99/年）。如果你打算正式
发版给朋友用，那条路是对的；本仓库 CI 也能轻松改成 TestFlight 上传，
只要你提供签名证书 + provisioning profile，参考 fastlane match 文档。

**Q: 在 macOS Mac 上能否直接 Xcode 跑？**
A: 完全可以，跳过这份文档的 .ipa 部分，直接 `cd ios && xcodegen
generate && open AIPhotoCoach.xcodeproj`。
