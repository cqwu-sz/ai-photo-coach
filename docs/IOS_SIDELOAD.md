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

## 二、选择构建变体

每次 CI 跑完会产出 **两个** IPA，按用途选：

| 变体 | Artifact 名 | 桌面图标/名字 | 用途 |
| --- | --- | --- | --- |
| **Internal**（联调） | `AIPhotoCoach-Internal-unsigned-ipa` | Sunset 图标 + **拾光 Dev** | 团队联调、局域网开发、staging 测试 |
| **Production**（正式） | `AIPhotoCoach-unsigned-ipa` | 默认图标 + **拾光** | 模拟"用户真正会装到手机上的包"用于回归 |

两者 **Bundle ID 不同**（`com.aiphotocoach.app.internal` vs `com.aiphotocoach.app`），
可以同时装在同一台 iPhone 上互不冲突。

- 第一次联调 → 装 **Internal**：它在登录页底部多了 *连接设置 · Internal Build*
  入口，能让你点到本地或局域网后端。
- 走全链路冒烟 / 演示给非开发者 → 装 **Production**：它的服务器地址在构建时
  从 `secrets.PROD_API_BASE_URL` 烤进二进制，启动后直连正式后端。

## 三、拿到 .ipa（两种方式任选）

### 方式 A：从 GitHub Actions 下

1. 把项目 push 到自己的 GitHub 仓库（fork 或者私有都行）。
2. 进入仓库 → Actions → 选 **iOS Build (unsigned IPA)** 工作流。
3. 点右上角 `Run workflow`（或者直接 push 一次到 main 自动触发）。
4. 等 5–10 分钟跑完，进入这次 run 的页面，下方 Artifacts 区有
   `AIPhotoCoach-Internal-unsigned-ipa.zip`（联调用）和
   `AIPhotoCoach-unsigned-ipa.zip`（正式包冒烟用）。
   按上一节的说明选一个，下载到本地后解压得到对应的 .ipa。

### 方式 B：发个 tag 走 Release

```bash
git tag v0.1.0
git push origin v0.1.0
```

CI 跑完会自动把 **正式包** .ipa 挂到 GitHub Releases 页，免登录也能下。
Internal 包**永远不会**挂到 Release，只能从 Actions artifact 拿到，
避免误把内部调试包当成正式包发出去。

---

## 四、用 Sideloadly 装到 iPhone

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

## 五、连接你自己的后端（**Internal 包专属**）

> 这一节只对 **拾光 Dev**（Internal 包）有意义。正式包的服务器地址是构建
> 时烤进二进制的，启动后用户无法切换。

App 不再硬编码 `localhost`。装上 Internal 包后第一次启动会看到登录页
**顶部有一条橙色警示条**："未配置服务器，点击进入连接设置"——按它做：

1. 把 PC 和 iPhone **接到同一个 Wi-Fi**。
2. 在 PC 上找内网 IP：PowerShell 跑 `ipconfig`，找到 `IPv4 Address`，
   类似 `192.168.1.42`。
3. 启动后端时绑到 0.0.0.0：

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

4. 打开 *拾光 Dev* → 点页面顶部警示条（或登录页底部 *连接设置 · Internal Build*）。
5. 填 `http://192.168.1.42:8000` → 点 **测试连接**（探 `/healthz`，5 秒超时）
   → 绿色 ✓ 出现后点 **应用并保存**。
6. 回到登录页，验证码 / Apple 登录会直接打到你笔记本上的后端。
7. **明文 HTTP 注意**：iOS 默认禁止明文 HTTP；Internal 包的 Info.plist 已
   把 `NSAllowsLocalNetworking` 设为 true，局域网 HTTP 是放行的。公网域名
   必须用 `https://`。

> **连不上自救**：如果你填错了 URL 导致登录请求超时，进 *连接设置* 页面
> 下面有"最近 5 次变更（点击回滚）"列表，点之前能用的那条一键恢复，
> 不需要卸载重装。

### 服务器地址安全规则（Internal 包）

应用允许写的 URL 受以下规则约束（输入即时校验，违规直接拒绝保存）：

| 协议 | 允许的 host | 备注 |
| --- | --- | --- |
| `https://` | 任何 | 公网/staging 推荐 |
| `http://` | `localhost`、`*.local`、`10.x.x.x`、`192.168.x.x`、`172.16-31.x.x` | 仅放行内网/回环 |
| 任意 | `169.254.169.254` 等云元数据 IP | **拒绝**（防 SSRF 误填） |

每次切换都会本地保留 5 条历史，并尽力上报后端 `/api/telemetry/endpoint_override`
（仅含 device 指纹哈希 + 新旧 URL，不含任何账号信息），便于客服按指纹查
"该用户什么时候改的地址、新地址是否健康"。Admin 可以从「服务器地址配置」
旁边的「本机覆盖审计」入口查询所有历史。

### 团队批量分发（QR 网页）

后端跑起来后，在浏览器访问 `http(s)://<backend>/web/dev/endpoint-qr.html`，
把 URL 填进去，会实时生成二维码 —— 投到屏幕或者点 "下载 PNG" 打印贴墙，
组内人 iPhone 打开拾光 Dev → 连接设置 → 右上角扫码图标，一键完成配置。
URL 也支持参数预填，例如 `…/endpoint-qr.html?url=http://192.168.1.42:8000`
直接出二维码。

> ⚠ 这个页面**只在 dev / staging 后端可访问**。生产环境后端会通过
> `disable_web_routes_in_prod` 把整个 `/web` 路径 unmount，此时访问会
> 返回 404 —— 这是有意为之，正式用户不该看到任何内部工具。如果你需要
> 在没有 dev 后端的场景下生成二维码，本地用任意离线 QR 工具即可（页面
> 本身就是纯静态 HTML，可以保存到本地用浏览器打开）。

> 想云端永久部署后端再连？把 backend 部署到 Render / Fly.io（免费档），
> 拿到 https URL 在"连接设置"里填即可，不用再改 Xcode 工程。

---

## 六、7 天后过期了怎么办

Sideloadly 重新签名一次就行：

1. 把 iPhone 连上电脑、打开 Sideloadly。
2. 在它的 **Sideloads** 标签页里找到这个 app，右键 → `Re-sign & Install`。
3. 不需要重新下载 .ipa，也不会丢 app 内的数据。

如果你嫌麻烦，可以装 [AltStore](https://altstore.io)，它能在你 PC
开着的时候每天后台自动给所有侧载 app 续签。

---

## 七、常见问题

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
