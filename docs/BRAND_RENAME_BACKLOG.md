# 品牌迁移 backlog：AIPhotoCoach → 拾光 (Shiguang)

最后更新：2026-05-14

## 背景

项目立项时英文叫 **AI Photo Coach**，2026-05 决定中文品牌正式叫
**拾光**、功能名叫 **AI 取景者**（注意是"者"不是"师"），英文短名
**Shiguang**。

为了规避 Bundle ID / TestFlight 测试链 / IAP product id 等迁移成本，
本轮**只改了用户可见层**（文案、视觉、隐私政策、邮箱、URL 入口），
代码符号、target 名、Bundle ID、IAP product id 都保留 `AIPhotoCoach`
/ `com.aiphotocoach.app`。

本文记录"等真要彻底切品牌时"还需要做的事，按风险/成本排序。

---

## 1. 域名 & 邮箱（合规优先级最高）

**现状**

- 仓库里所有 URL/email 已经收敛到 `ios/AIPhotoCoach/Core/BrandConstants.swift`
  的 `domainHost` / `contactEmailDomain`，搜索 `aiphotocoach\.app` 应当只
  命中这一个文件 + `web/privacy.html` 内部锚点。
- 网站 `web/privacy.html` 的标题/正文已写「拾光 · AI 取景者」，但仍
  托管在 `aiphotocoach.app`。

**该做什么**

- [ ] 注册新域名（建议候选：`shiguang.app` / `shiguang.photo` / `getshiguang.com`）。
- [ ] DNS 把新域名指到现有 web 站点；老 `aiphotocoach.app` 做 301 → 新域。
- [ ] 申请 `privacy@<新域>` / `support@<新域>` 邮箱。
- [ ] 改 `BrandConstants.domainHost` & `contactEmailDomain` 各一行；
      运行 `rg "aiphotocoach\.app" ios/` 确认没漏。
- [ ] App Store Connect 同步新隐私政策 URL；老 URL 留 30 天 301。
- [ ] `ios/AIPhotoCoach/App/PrivacyInfo.xcprivacy`、
      `docs/PRIVACY_POLICY_DELTA.md` 等文档里的引用也一起检查。

**风险**

苹果审核 Guideline 5.1.1 会检查"隐私政策 URL 域 ↔ 应用主页域"；如果
两者都还叫 `aiphotocoach`，但 App 名字叫"拾光"，审核可能要求文字说明。

---

## 2. Bundle ID / Target 名（一次性大动作，建议留到 v2）

**现状**

```
ios/project.yml
  name: AIPhotoCoach
  bundleIdPrefix: com.aiphotocoach
  targets: AIPhotoCoach / AIPhotoCoach-Internal / AIPhotoCoachTests
  PRODUCT_BUNDLE_IDENTIFIER: com.aiphotocoach.app
```

**该做什么（如果将来真要切）**

- [ ] App Store Connect 不允许改 Bundle ID。**唯一路径是上架一个新 App
      （`com.shiguang.app`）+ 把老 App 标记 "deprecated"**。
- [ ] StoreKit IAP product id（`com.aiphotocoach.app.pro_yearly` 等）
      需要在新 App 下重新创建一套 → 老订阅用户需要做迁移引导。
- [ ] App Group / Keychain Access Group 一起换，否则用户重新登录会丢
      Keychain 里的 device UUID（→ 后端把他们当成新用户）。
- [ ] xcodegen 入参全改：
      `name`、`bundleIdPrefix`、3 个 target 名、`AppGroups`。
- [ ] 重命名 `@main struct AIPhotoCoachApp` → `ShiguangApp`；崩溃日志
      / Console / xctrace / Sentry 里都不再泄露老名。
- [ ] 测试 target 的 `@testable import AIPhotoCoach` 对应改名。

**风险**

非常大，等同于"重发一个 App"。**建议至少等 v2 大版本节点再做**，趁
机重做 IAP 套餐时一并迁移。

---

## 3. 仓库 / 路径名

**现状**

`ios/AIPhotoCoach/...` 这条 path 出现在 1100+ 文件引用里
（test 数据、CI 缓存路径、xcconfig template、`.cursor/plans/*.md` 等）。

**该做什么**

- [ ] 跟 Bundle ID 切换捆绑做。单独改路径会让 `git blame` / PR diff 完全不可读，
      ROI 极低。
- [ ] 真要做时用 `git mv` + 一次性提交，分两步：先 path 名，再代码符号。

---

## 4. CI / GitHub Actions / 包名

**现状**

`.github/workflows/ios-build.yml` 里 archive scheme = `AIPhotoCoach`，
artifact 名 = `AIPhotoCoach.ipa`。

**该做什么**

跟 Bundle ID 切换一起。artifact 名可以单独改成 "Shiguang.ipa"
不影响构建，但用户感知低，没必要单独 PR。

---

## 不在本 backlog 范围

- 中文功能名"取景者 vs 取景师"：本轮已统一成 **取景者**（iOS / web /
  manifest / 分享水印 / OTP 邮件）。注：`backend/app/services/prompts.py:105`
  里 LLM prompt "你是一位资深的现场取景师" 是给模型看的角色设定，**不
  改**——这是给 AI 演的角色，不是产品名。
- "AI Photo Coach" 字符串：本轮已清掉所有用户可见出现：
  - iOS 全部 UI 文案
  - web 全部 UI 文案 + manifest + 分享水印
  - OTP 验证码邮件标题/正文（`backend/.../otp.py`）
  - Pro feature 升级提示 message（`backend/.../auth.py`）

  剩余命中分两类，**当前不需要改**：
  - **代码符号 / Bundle ID / 仓库 path / IAP product id**：第 2 / 3 节工作
  - **运维内部串**：CLI 标题（`scripts/*.cmd`）、运营告警邮件
    （`alert_mailer.py` / `csv_scheduler.py`）、API title
    （`backend/app/main.py`）、log 字符串、SMS 签名 fallback、内部
    test fixture (`test_v17g_smoke.py:75`)、deploy 注释、`README.md` 等。
    这些不会出现在终端用户面前，跟着代码符号一起切即可。

---

## 验收清单

切品牌完成后，跑下面三条命令应当各自命中预期文件：

```powershell
# 1. 用户可见的英文老品牌名应该只剩文档/backlog
rg "AI Photo Coach" -g "!docs/" -g "!.cursor/" -g "!*.md"
# 期望：0 命中

# 2. 老域名应该只剩 BrandConstants.swift + 网站 privacy.html
rg "aiphotocoach\.app"
# 期望：仅这两个文件

# 3. 中文功能名应该只有"取景者"
rg "取景师"
# 期望：0 命中
```
