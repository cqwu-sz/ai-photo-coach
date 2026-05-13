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

    @State private var showError: Bool = false
    @State private var errorMessage: String = ""

    private let privacyURL = URL(string: "https://aiphotocoach.app/privacy")!
    private let eulaURL = URL(string: "https://www.apple.com/legal/internet-services/itunes/dev/stdeula/")!

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color(red: 0.04, green: 0.05, blue: 0.10),
                    Color(red: 0.10, green: 0.07, blue: 0.18),
                ],
                startPoint: .top, endPoint: .bottom,
            ).ignoresSafeArea()

            ScrollView {
                VStack(spacing: 28) {
                    header
                    Picker("登录方式", selection: $channel) {
                        ForEach(Channel.allCases) { c in
                            Text(c.label).tag(c)
                        }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 24)

                    Group {
                        switch channel {
                        case .sms:   smsForm
                        case .email: emailForm
                        case .apple: appleForm
                        }
                    }
                    .animation(.easeInOut(duration: 0.2), value: channel)

                    agreement
                    legalLinks
                }
                .padding(.top, 56)
                .padding(.bottom, 40)
            }
        }
        .alert("登录失败", isPresented: $showError) {
            Button("好") { }
        } message: {
            Text(errorMessage)
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(spacing: 8) {
            Image(systemName: "camera.aperture")
                .font(.system(size: 56, weight: .light))
                .foregroundStyle(.white)
            Text("AI Photo Coach")
                .font(.system(size: 26, weight: .semibold))
                .foregroundStyle(.white)
            Text("登录后开始你的拍摄之旅")
                .font(.subheadline)
                .foregroundStyle(.white.opacity(0.7))
        }
    }

    // MARK: - SMS form

    private var smsForm: some View {
        VStack(spacing: 14) {
            inputCard {
                HStack(spacing: 8) {
                    Text("+86").foregroundStyle(.white.opacity(0.6))
                    TextField("手机号", text: $phone)
                        .keyboardType(.numberPad)
                        .foregroundStyle(.white)
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
        VStack(spacing: 14) {
            inputCard {
                TextField("邮箱地址", text: $email)
                    .keyboardType(.emailAddress)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                    .foregroundStyle(.white)
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
                .foregroundStyle(.white.opacity(0.75))
                .font(.subheadline)
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
            .frame(height: 48)
            .opacity(agreed ? 1.0 : 0.4)
            .disabled(!agreed)
            .padding(.horizontal, 24)

            if !agreed {
                Text("请先勾选下方协议再登录")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
    }

    private var otpField: some View {
        inputCard {
            HStack {
                TextField("6 位验证码", text: $code)
                    .keyboardType(.numberPad)
                    .foregroundStyle(.white)
                    .onChange(of: code) { newValue in
                        let filtered = newValue.filter { $0.isNumber }
                        let trimmed = String(filtered.prefix(6))
                        if trimmed != newValue { code = trimmed }
                    }
                if cooldown > 0 {
                    Text("\(cooldown)s")
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.5))
                }
            }
        }
    }

    @ViewBuilder
    private func inputCard<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content()
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(Color.white.opacity(0.08))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private func sendCodeButton(target: String, isValid: Bool) -> some View {
        let canSend = agreed && isValid && cooldown == 0 && !sending
        return Button {
            Task { await sendCode(target: target) }
        } label: {
            HStack {
                if sending {
                    ProgressView().tint(.white)
                }
                Text(cooldown > 0 ? "\(cooldown) 秒后可重发" : "发送验证码")
                    .fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(canSend
                        ? Color.white.opacity(0.18)
                        : Color.white.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .foregroundStyle(canSend ? .white : .white.opacity(0.4))
        }
        .disabled(!canSend)
    }

    private func verifyButton(target: String, isValid: Bool) -> some View {
        let canVerify = agreed && isValid && code.count >= 4 && !verifying
        return Button {
            Task { await verify(target: target) }
        } label: {
            HStack {
                if verifying { ProgressView().tint(.black) }
                Text("登录").fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(canVerify ? Color.white : Color.white.opacity(0.3))
            .foregroundStyle(canVerify ? .black : .white.opacity(0.6))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .disabled(!canVerify)
    }

    // MARK: - Agreement / legal

    private var agreement: some View {
        HStack(alignment: .top, spacing: 8) {
            Button {
                agreed.toggle()
            } label: {
                Image(systemName: agreed ? "checkmark.square.fill" : "square")
                    .foregroundStyle(agreed ? Color.accentColor : .white.opacity(0.6))
            }
            (Text("我已阅读并同意 ")
             + Text("《用户协议》").foregroundColor(.accentColor)
             + Text(" 与 ")
             + Text("《隐私政策》").foregroundColor(.accentColor)
             + Text("。验证码仅用于本次登录认证，不会用于营销。"))
                .font(.footnote)
                .foregroundStyle(.white.opacity(0.7))
        }
        .padding(.horizontal, 24)
    }

    private var legalLinks: some View {
        HStack(spacing: 16) {
            Link("隐私政策", destination: privacyURL)
            Text("·").foregroundStyle(.white.opacity(0.4))
            Link("用户协议 (EULA)", destination: eulaURL)
        }
        .font(.caption)
        .foregroundStyle(.white.opacity(0.6))
    }

    // MARK: - Validation

    private func isValidCnPhone(_ s: String) -> Bool {
        let trimmed = s.trimmingCharacters(in: .whitespaces)
        return trimmed.range(of: "^1[3-9]\\d{9}$", options: .regularExpression) != nil
    }

    private func isValidEmail(_ s: String) -> Bool {
        let trimmed = s.trimmingCharacters(in: .whitespaces).lowercased()
        return trimmed.range(of: "^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$",
                              options: .regularExpression) != nil
    }

    // MARK: - Actions

    private func sendCode(target: String) async {
        guard !sending else { return }
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
        do {
            try await auth.signInWithApple()
        } catch {
            present(error: error)
        }
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
        errorMessage = (error as NSError).localizedDescription
        if errorMessage.isEmpty { errorMessage = "请稍后重试" }
        showError = true
    }
}

#Preview {
    LoginView()
}
