// LoginView.swift  (PR3 of subscription/auth rework)
//
// Three-tab login screen:
//   1. 手机号 + 短信验证码
//   2. 邮箱 + 邮件验证码
//   3. Sign in with Apple
//
// Compliance:
//   - Privacy / EULA agreement is REQUIRED before the OTP send button
//     becomes active (Apple 5.1.1 + 国内合规).
//   - Phone & email are sent over HTTPS only; no third-party SDK gets
//     the raw value (we hit our own /auth/otp/* endpoints).
//   - 60s resend cooldown matches the backend throttle so the user
//     sees consistent state.

import SwiftUI
import AuthenticationServices

struct LoginView: View {
    enum Channel: String, CaseIterable, Identifiable {
        case sms, email, apple
        var id: String { rawValue }
        var label: String {
            switch self {
            case .sms:   return "手机号"
            case .email: return "邮箱"
            case .apple: return "Apple"
            }
        }
    }

    @ObservedObject private var auth = AuthManager.shared
    @State private var channel: Channel = .sms
    @State private var phone: String = ""
    @State private var email: String = ""
    @State private var code: String = ""
    @State private var agreed: Bool = false

    @State private var sending: Bool = false
    @State private var verifying: Bool = false
    @State private var cooldown: Int = 0
    @State private var cooldownTimer: Timer?

    /// 玻璃 toast 状态。替代原生 alert，让登录失败的反馈与整体
    /// CinemaTheme 一致。`toastIsError` 决定描边/图标的色相。
    @State private var toastMessage: String?
    @State private var toastIsError: Bool = true
    @State private var toastDismissTask: Task<Void, Never>?
    #if INTERNAL_BUILD
    @State private var showEndpointSheet: Bool = false
    #endif

    private let privacyURL = BrandConstants.privacyURL
    private let eulaURL = BrandConstants.appleEulaURL

    var body: some View {
        ZStack(alignment: .bottom) {
            CinemaBackdrop()

            ScrollView {
                VStack(spacing: 22) {
                    header
                    if !APIConfig.isConfigured {
                        endpointWarningBanner
                            .transition(.move(edge: .top).combined(with: .opacity))
                    }
                    valuePropPills
                        .padding(.horizontal, 24)
                    channelPicker
                        .padding(.horizontal, 24)

                    Group {
                        switch channel {
                        case .sms:   smsForm
                        case .email: emailForm
                        case .apple: appleForm
                        }
                    }
                    .transition(.asymmetric(
                        insertion: .opacity.combined(with: .move(edge: .trailing)),
                        removal: .opacity.combined(with: .move(edge: .leading))
                    ))

                    agreement
                    legalLinks
                    #if INTERNAL_BUILD
                    internalConnectionLink
                    #endif
                }
                .padding(.top, 44)
                .padding(.bottom, 40)
                .animation(.spring(response: 0.42, dampingFraction: 0.82), value: channel)
                .animation(.easeInOut(duration: 0.25), value: APIConfig.isConfigured)
            }

            if let toastMessage {
                LoginToast(message: toastMessage, isError: toastIsError) {
                    dismissToast()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
                .transition(.move(edge: .bottom).combined(with: .opacity))
                .zIndex(10)
            }
        }
        .preferredColorScheme(.dark)
        .animation(.spring(response: 0.4, dampingFraction: 0.78), value: toastMessage)
        #if INTERNAL_BUILD
        .sheet(isPresented: $showEndpointSheet) {
            NavigationStack { ServerEndpointPublicView() }
        }
        #endif
    }

    // MARK: - Value-prop pills (登录前先看到产品价值)

    private var valuePropPills: some View {
        HStack(spacing: 8) {
            valuePill(icon: "camera.viewfinder", text: "环视 10 秒")
            valuePill(icon: "person.2.fill", text: "7 个虚拟模特")
            valuePill(icon: "sparkles", text: "3 套出片方案")
        }
    }

    private func valuePill(icon: String, text: String) -> some View {
        HStack(spacing: 5) {
            Image(systemName: icon)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(CinemaTheme.accentWarm)
            Text(text)
                .font(.system(size: 10.5, weight: .heavy))
                .foregroundStyle(CinemaTheme.inkSoft)
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .frame(maxWidth: .infinity)
        .background(.ultraThinMaterial,
                    in: Capsule(style: .continuous))
        .overlay(
            Capsule(style: .continuous)
                .stroke(CinemaTheme.borderSoft, lineWidth: 1)
        )
    }

    // MARK: - Endpoint not configured banner (Production + Internal)

    private var endpointWarningBanner: some View {
        let warning = HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(CinemaTheme.accentCoral)
            VStack(alignment: .leading, spacing: 2) {
                Text("未配置服务器")
                    .font(.system(size: 13, weight: .heavy))
                    .foregroundStyle(CinemaTheme.ink)
                Text(bannerSubtitle)
                    .font(.system(size: 11))
                    .foregroundStyle(CinemaTheme.inkSoft)
            }
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(.ultraThinMaterial,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(CinemaTheme.accentCoral.opacity(0.4), lineWidth: 1)
        )
        .padding(.horizontal, 24)

        #if INTERNAL_BUILD
        return Button { showEndpointSheet = true } label: { warning }
        #else
        return warning
        #endif
    }

    private var bannerSubtitle: String {
        #if INTERNAL_BUILD
        return "点击进入「连接设置」填入后端地址。"
        #else
        return "服务器配置异常，请稍后重试或联系客服。"
        #endif
    }

    // MARK: - Internal connection link

    #if INTERNAL_BUILD
    private var internalConnectionLink: some View {
        Button {
            showEndpointSheet = true
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "gearshape")
                Text("连接设置 · Internal Build")
            }
            .font(.caption2)
            .foregroundStyle(CinemaTheme.inkMuted)
        }
    }
    #endif

    // MARK: - Header

    private var header: some View {
        VStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(AngularGradient(
                        colors: [CinemaTheme.accentWarm,
                                 CinemaTheme.accentCoral,
                                 CinemaTheme.accentCool,
                                 CinemaTheme.accentWarm],
                        center: .center))
                    .frame(width: 64, height: 64)
                    .blur(radius: 0.5)
                Circle()
                    .fill(CinemaTheme.bgBase)
                    .frame(width: 30, height: 30)
                Image(systemName: "camera.aperture")
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(CinemaTheme.accentGradient)
            }
            .shadow(color: CinemaTheme.accentWarm.opacity(0.45), radius: 18, y: 8)

            VStack(spacing: 6) {
                HStack(spacing: 8) {
                    Circle()
                        .fill(CinemaTheme.accentWarm)
                        .frame(width: 5, height: 5)
                        .shadow(color: CinemaTheme.accentWarm.opacity(0.7), radius: 5)
                    Text("CINEMA HOUSE · AI · \(BrandConstants.brandYearTag)")
                        .font(.system(size: 10, weight: .heavy))
                        .tracking(2.4)
                        .foregroundStyle(CinemaTheme.accentWarm)
                }

                Text("拾光")
                    .font(.system(size: 34, weight: .heavy))
                    .tracking(2)
                    .foregroundStyle(CinemaTheme.heroGradient)

                Text("AI 取景者 · 拾起每一束光")
                    .font(.system(size: 11.5, weight: .heavy))
                    .tracking(1.6)
                    .foregroundStyle(CinemaTheme.inkMuted)

                Text("登录后开始你的拍摄之旅")
                    .font(.system(size: 13))
                    .foregroundStyle(CinemaTheme.inkSoft)
                    .padding(.top, 2)
            }
        }
    }

    // MARK: - Channel picker (cinema-style chips)

    private var channelPicker: some View {
        HStack(spacing: 8) {
            ForEach(Channel.allCases) { c in
                channelChip(c)
            }
        }
        .padding(4)
        .background(.ultraThinMaterial,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(CinemaTheme.borderSoft, lineWidth: 1)
        )
    }

    private func channelChip(_ c: Channel) -> some View {
        let isActive = channel == c
        return Button {
            withAnimation(.spring(response: 0.32, dampingFraction: 0.78)) {
                channel = c
            }
        } label: {
            Text(c.label)
                .font(.system(size: 13.5, weight: .heavy))
                .foregroundStyle(isActive ? Color.black.opacity(0.88) : CinemaTheme.inkSoft)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 10)
                .background(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(isActive
                              ? AnyShapeStyle(CinemaTheme.accentGradient)
                              : AnyShapeStyle(Color.clear))
                )
                .shadow(color: isActive ? CinemaTheme.accentWarm.opacity(0.35) : .clear,
                        radius: 8, y: 3)
        }
        .buttonStyle(.plain)
    }

    // MARK: - SMS form

    private var smsForm: some View {
        VStack(spacing: 12) {
            inputCard {
                HStack(spacing: 8) {
                    // BrandConstants.defaultPhoneCountryCode 当前是 +86；
                    // 之后接入国家码切换器只需替换这个 Text 为 Menu。
                    Text(BrandConstants.defaultPhoneCountryCode)
                        .font(.system(size: 14, weight: .heavy))
                        .foregroundStyle(CinemaTheme.accentWarm)
                    TextField("", text: $phone, prompt:
                        Text("手机号").foregroundColor(CinemaTheme.inkMuted))
                        .keyboardType(.numberPad)
                        .foregroundStyle(CinemaTheme.ink)
                        .textContentType(.telephoneNumber)
                }
            }
            otpField
            sendCodeButton(target: phone, isValid: isValidCnPhone(phone))
            verifyButton(target: phone, isValid: isValidCnPhone(phone))
        }
        .padding(.horizontal, 24)
    }

    private var emailForm: some View {
        VStack(spacing: 12) {
            inputCard {
                TextField("", text: $email, prompt:
                    Text("邮箱地址").foregroundColor(CinemaTheme.inkMuted))
                    .keyboardType(.emailAddress)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                    .foregroundStyle(CinemaTheme.ink)
                    .textContentType(.emailAddress)
            }
            otpField
            sendCodeButton(target: email, isValid: isValidEmail(email))
            verifyButton(target: email, isValid: isValidEmail(email))
        }
        .padding(.horizontal, 24)
    }

    private var appleForm: some View {
        VStack(spacing: 14) {
            Text("使用你的 Apple ID 一键登录，安全且便捷。")
                .multilineTextAlignment(.center)
                .foregroundStyle(CinemaTheme.inkSoft)
                .font(.system(size: 13.5))
                .padding(.horizontal, 24)

            SignInWithAppleButton(
                .signIn,
                onRequest: { req in
                    req.requestedScopes = [.fullName, .email]
                },
                onCompletion: { _ in
                    // The actual SIWA request flow is owned by
                    // AuthManager.signInWithApple() (it handles
                    // identity_token extraction + /auth/siwa).
                    Task { await runSiwa() }
                },
            )
            .signInWithAppleButtonStyle(.white)
            .frame(height: 50)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .opacity(agreed ? 1.0 : 0.4)
            .disabled(!agreed)
            .padding(.horizontal, 24)
            .shadow(color: .black.opacity(0.4), radius: 12, y: 6)

            if !agreed {
                Text("请先勾选下方协议再登录")
                    .font(.caption)
                    .foregroundStyle(CinemaTheme.accentCoral)
            }
        }
    }

    private var otpField: some View {
        inputCard {
            HStack {
                TextField("", text: $code, prompt:
                    Text("6 位验证码").foregroundColor(CinemaTheme.inkMuted))
                    .keyboardType(.numberPad)
                    .foregroundStyle(CinemaTheme.ink)
                    .tracking(2)
                    .onChange(of: code) { newValue in
                        let filtered = newValue.filter { $0.isNumber }
                        let trimmed = String(filtered.prefix(6))
                        if trimmed != newValue { code = trimmed }
                    }
                if cooldown > 0 {
                    Text("\(cooldown)s")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(CinemaTheme.accentWarm)
                }
            }
        }
    }

    @ViewBuilder
    private func inputCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .font(.system(size: 15, weight: .semibold))
            .padding(.horizontal, 16)
            .padding(.vertical, 15)
            .background(.ultraThinMaterial,
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color.white.opacity(0.04))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(CinemaTheme.borderSoft, lineWidth: 1)
            )
    }

    private func sendCodeButton(target: String, isValid: Bool) -> some View {
        let canSend = agreed && isValid && cooldown == 0 && !sending
        return Button {
            Task { await sendCode(target: target) }
        } label: {
            HStack(spacing: 8) {
                if sending {
                    ProgressView().tint(CinemaTheme.accentWarm)
                }
                Text(cooldown > 0 ? "\(cooldown) 秒后可重发" : "发送验证码")
                    .font(.system(size: 14, weight: .heavy))
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(.ultraThinMaterial,
                        in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(canSend
                            ? CinemaTheme.accentWarm.opacity(0.55)
                            : CinemaTheme.borderSoft,
                            lineWidth: 1)
            )
            .foregroundStyle(canSend ? CinemaTheme.accentWarm : CinemaTheme.inkMuted)
        }
        .disabled(!canSend)
    }

    private func verifyButton(target: String, isValid: Bool) -> some View {
        let canVerify = agreed && isValid && code.count >= 4 && !verifying
        return Button {
            Task { await verify(target: target) }
        } label: {
            HStack(spacing: 10) {
                if verifying { ProgressView().tint(.black) }
                Text("登录")
                    .font(.system(size: 16.5, weight: .heavy))
                Image(systemName: "arrow.right")
                    .font(.system(size: 14, weight: .heavy))
            }
            .foregroundStyle(.black.opacity(0.9))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 16)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(canVerify
                          ? AnyShapeStyle(CinemaTheme.ctaGradient)
                          : AnyShapeStyle(Color.white.opacity(0.18)))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(Color.white.opacity(canVerify ? 0.16 : 0.08), lineWidth: 1)
            )
            .shadow(color: canVerify ? CinemaTheme.accentWarm.opacity(0.45) : .clear,
                    radius: 16, y: 6)
            .opacity(canVerify ? 1.0 : 0.55)
        }
        .disabled(!canVerify)
    }

    // MARK: - Agreement / legal

    private var agreement: some View {
        HStack(alignment: .top, spacing: 10) {
            Button {
                withAnimation(.spring(response: 0.25, dampingFraction: 0.8)) {
                    agreed.toggle()
                }
            } label: {
                Image(systemName: agreed ? "checkmark.square.fill" : "square")
                    .font(.system(size: 18, weight: .bold))
                    .foregroundStyle(agreed ? CinemaTheme.accentWarm : CinemaTheme.inkMuted)
            }
            (Text("我已阅读并同意 ")
             + Text("《用户协议》").foregroundColor(CinemaTheme.accentWarm)
             + Text(" 与 ")
             + Text("《隐私政策》").foregroundColor(CinemaTheme.accentWarm)
             + Text("。验证码仅用于本次登录认证，不会用于营销。"))
                .font(.system(size: 12))
                .foregroundStyle(CinemaTheme.inkSoft)
                .lineSpacing(2)
        }
        .padding(.horizontal, 24)
    }

    private var legalLinks: some View {
        HStack(spacing: 14) {
            Link("隐私政策", destination: privacyURL)
            Text("·").foregroundStyle(CinemaTheme.inkMuted)
            Link("用户协议 (EULA)", destination: eulaURL)
        }
        .font(.system(size: 11.5, weight: .semibold))
        .tint(CinemaTheme.inkSoft)
        .foregroundStyle(CinemaTheme.inkSoft)
    }

    // MARK: - Validation

    private func isValidCnPhone(_ s: String) -> Bool {
        let trimmed = s.trimmingCharacters(in: .whitespaces)
        return trimmed.range(of: BrandConstants.defaultPhoneRegex,
                             options: .regularExpression) != nil
    }

    private func isValidEmail(_ s: String) -> Bool {
        let trimmed = s.trimmingCharacters(in: .whitespaces).lowercased()
        return trimmed.range(of: "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$",
                              options: .regularExpression) != nil
    }

    // MARK: - Actions

    private func sendCode(target: String) async {
        guard !sending else { return }
        guard ensureConfigured() else { return }
        sending = true
        defer { sending = false }
        do {
            try await auth.requestOtp(channel: channel.rawValue, target: target)
            startCooldown()
        } catch {
            present(error: error)
        }
    }

    private func verify(target: String) async {
        guard !verifying else { return }
        guard ensureConfigured() else { return }
        verifying = true
        defer { verifying = false }
        do {
            try await auth.verifyOtp(channel: channel.rawValue,
                                      target: target, code: code)
        } catch {
            present(error: error)
        }
    }

    private func runSiwa() async {
        guard ensureConfigured() else { return }
        do {
            try await auth.signInWithApple()
        } catch {
            present(error: error)
        }
    }

    /// Short-circuit any network action when the base URL hasn't been
    /// resolved yet — otherwise we'd send the request to the sentinel
    /// 192.0.2.1 host and wait for the timeout. Surfaces the same
    /// banner copy the user already sees at the top of the screen.
    private func ensureConfigured() -> Bool {
        if APIConfig.isConfigured { return true }
        present(message: APIConfigError.endpointNotConfigured.localizedDescription,
                isError: true)
        return false
    }

    private func startCooldown() {
        cooldown = 60
        cooldownTimer?.invalidate()
        cooldownTimer = Timer.scheduledTimer(withTimeInterval: 1.0,
                                              repeats: true) { t in
            DispatchQueue.main.async {
                if cooldown > 0 { cooldown -= 1 }
                if cooldown == 0 { t.invalidate() }
            }
        }
    }

    private func present(error: Error) {
        var msg = (error as NSError).localizedDescription
        if msg.isEmpty { msg = "请稍后重试" }
        present(message: msg, isError: true)
    }

    /// 显示一个玻璃 toast。`isError=true` 走 coral 描边，否则走 cool 蓝。
    /// 4.5 秒后自动收起；用户也可点 toast 上的关闭按钮立即收起。
    private func present(message: String, isError: Bool) {
        toastDismissTask?.cancel()
        toastIsError = isError
        toastMessage = message
        toastDismissTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 4_500_000_000)
            if !Task.isCancelled { dismissToast() }
        }
    }

    private func dismissToast() {
        toastDismissTask?.cancel()
        toastDismissTask = nil
        toastMessage = nil
    }
}

// MARK: - Glass toast (替代系统 alert)

/// 底部胶囊 toast，跟 RootView 的 ReuseChip 同族视觉。错误用 coral 描边，
/// 提示用 cool 蓝。点叉号或等 4.5s 后自动收起。
private struct LoginToast: View {
    let message: String
    let isError: Bool
    let onClose: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: isError ? "exclamationmark.triangle.fill" : "info.circle.fill")
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(isError ? CinemaTheme.accentCoral : CinemaTheme.accentCool)
                .padding(.top, 1)

            Text(message)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(CinemaTheme.ink)
                .lineSpacing(2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .multilineTextAlignment(.leading)

            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(CinemaTheme.inkMuted)
                    .padding(6)
                    .background(Circle().fill(Color.white.opacity(0.06)))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 12)
        .background(.ultraThinMaterial,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .background(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .fill(Color.black.opacity(0.35))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke((isError ? CinemaTheme.accentCoral : CinemaTheme.accentCool)
                            .opacity(0.45),
                        lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.55), radius: 18, y: 10)
    }
}

#Preview {
    LoginView()
}
