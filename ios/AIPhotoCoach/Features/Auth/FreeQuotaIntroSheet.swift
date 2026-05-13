// FreeQuotaIntroSheet.swift  (v17c)
//
// One-shot welcome sheet shown immediately after first successful
// login. Sets expectations on the free quota and the paid tiers so
// the user doesn't run into a 402 wall later without context.
// Skipped for admin users (they have ∞ quota).

import SwiftUI

struct FreeQuotaIntroSheet: View {
    let onDismiss: () -> Void
    @State private var goPaywall = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("欢迎来到拾光").font(.title.bold())
                        Text("AI 取景师，按出片方案计费。")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }

                    Card(title: "免费体验",
                         body: "登录即送 5 次出片方案，足够把功能试个遍。" +
                                "出方案才扣次数；服务出错或你没拍照，不扣。",
                         color: .green)

                    Card(title: "订阅后无限用",
                         body: "月度 100 次 / ¥39，季度 500 次 / ¥108，年度 2000 次 / ¥412。" +
                                "续订或升级当即重置，过期次数不滚存。",
                         color: .accentColor)

                    Card(title: "为什么按次计费",
                         body: "每出一套方案要消耗模型 token。按次扣是为了让滥用账号不影响你的稳定使用。",
                         color: .orange)

                    HStack(spacing: 10) {
                        Button {
                            onDismiss()
                        } label: {
                            Text("先逛逛").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.bordered)

                        Button {
                            goPaywall = true
                        } label: {
                            Text("看订阅方案").frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    .padding(.top, 8)
                }
                .padding(20)
            }
            .navigationTitle("使用说明")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("跳过") { onDismiss() }
                }
            }
            .sheet(isPresented: $goPaywall) {
                PaywallView()
            }
        }
    }

    private struct Card: View {
        let title: String
        let body: String
        let color: Color

        var body: some View {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.headline)
                Text(body).font(.callout).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(color.opacity(0.10),
                          in: RoundedRectangle(cornerRadius: 12))
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(color.opacity(0.30), lineWidth: 1)
            )
        }
    }
}
