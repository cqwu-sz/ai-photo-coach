// BrandConstants.swift
//
// Single source of truth for all user-visible brand strings: product name,
// localized function name, hosting domain, contact email, copyright year tag,
// and the default i18n country code for phone OTP.
//
// 历史背景：
//   - 项目立项时英文叫 "AI Photo Coach"，对应仓库名、Bundle ID、target 名、
//     `aiphotocoach.app` 域名 + `privacy@aiphotocoach.app` 邮箱都用了这个英
//     文名。
//   - 2026-05 决定中文品牌正式叫「拾光」、功能描述叫「AI 取景者」（注意
//     是"者"不是"师"），英文短名 "Shiguang"。代码符号 / Bundle ID 暂不动，
//     但所有面向用户的字符串、链接、邮箱都要从这里读，方便后续切域名。
//
// 切换品牌域名的步骤：
//   1. 改下面 `domainHost` / `contactEmailDomain`。
//   2. 跑 grep 确认没有任何代码硬编码 "aiphotocoach.app"。
//   3. 部署对应的 https 与邮箱信箱。
//   4. App Store Connect 同步隐私政策 URL。

import Foundation

enum BrandConstants {
    // MARK: - 品牌名 ----------------------------------------------------------

    /// 中文品牌名，唯一对外标识。
    static let productNameCn = "拾光"

    /// 中文功能描述，跟在品牌名后做副标。注意是"者"不是"师"。
    static let functionTaglineCn = "AI 取景者"

    /// 英文短名，导出/分享水印的 fallback；也用作海外平台的兜底品牌词。
    static let productNameEn = "Shiguang"

    /// 英文功能描述，用于英文环境下的副标。
    static let functionTaglineEn = "AI Photo Director"

    /// 完整中文 lockup："拾光 · AI 取景者"
    static var fullCn: String { "\(productNameCn) · \(functionTaglineCn)" }

    /// 完整英文 lockup："Shiguang · AI Photo Director"
    static var fullEn: String { "\(productNameEn) · \(functionTaglineEn)" }

    /// 当前系统语言下应该展示的完整品牌串（中文区给中文，其它给英文）。
    /// 用于分享水印、footer caption 这种"对谁都说得通"的场景。
    static var localized: String {
        Locale.current.language.languageCode?.identifier == "zh" ? fullCn : fullEn
    }

    // MARK: - 域名 / 邮箱 -----------------------------------------------------

    /// 品牌主域名。**TODO**: 切到 `shiguang.app` 之类与品牌名一致的域名后，
    /// 在这里改一行即可。
    static let domainHost = "aiphotocoach.app"

    /// 联系/隐私邮箱使用的域名。通常等于 `domainHost`，但留一个独立变量
    /// 方便公司主体和产品域名分离的情况。
    static let contactEmailDomain = "aiphotocoach.app"

    /// 隐私政策 URL（公开网站）。
    static var privacyURL: URL {
        URL(string: "https://\(domainHost)/privacy")!
    }

    /// EULA / 用户协议。Apple 默认条款，所有内购 App 都可以套用。
    static let appleEulaURL = URL(string:
        "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")!

    /// 隐私问题联系邮箱。
    static var privacyContactEmail: String { "privacy@\(contactEmailDomain)" }

    // MARK: - 版权 / 年份 -----------------------------------------------------

    /// 出现在 hero "CINEMA HOUSE · AI · 2026" 这类 eyebrow 上的年份标签。
    /// 用动态年份避免硬编码每年都要改。
    static var brandYearTag: String {
        let y = Calendar(identifier: .gregorian).component(.year, from: Date())
        return String(y)
    }

    // MARK: - 国际化 ---------------------------------------------------------

    /// 默认手机号国家码。当前阶段只支持中国大陆 (+86)；这里抽出来是为了
    /// 将来加国家码切换器时只改一处入口。
    static let defaultPhoneCountryCode = "+86"

    /// 与默认国家码对应的手机号校验正则（中国大陆 11 位）。
    static let defaultPhoneRegex = "^1[3-9]\\d{9}$"
}
