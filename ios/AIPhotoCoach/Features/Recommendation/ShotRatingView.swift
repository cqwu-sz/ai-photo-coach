// ShotRatingView.swift (W2.3)
//
// Compact 1-5 star rating widget shown on the recommendation result page
// after the user takes a photo. Submits to /feedback via FeedbackUploader.

import SwiftUI

struct ShotRatingView: View {
    let chosenPosition: ShotPosition?
    let analyzeRequestId: String?
    let sceneKind: String?
    let uploader: FeedbackUploader

    @State private var rating: Int = 0
    @State private var status: String? = nil
    @State private var submitting = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("这张机位拍得怎么样？")
                .font(.subheadline.weight(.semibold))
            HStack(spacing: 6) {
                ForEach(1...5, id: \.self) { n in
                    Button {
                        Task { await submit(n) }
                    } label: {
                        Image(systemName: n <= rating ? "star.fill" : "star")
                            .font(.title2)
                            .foregroundStyle(n <= rating ? .yellow : .secondary)
                    }
                    .buttonStyle(.plain)
                    .disabled(submitting)
                }
            }
            if let s = status {
                Text(s)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private func submit(_ n: Int) async {
        rating = n
        submitting = true
        status = "正在记录…"
        let action = await uploader.submitRating(
            analyzeRequestId: analyzeRequestId,
            chosenPosition: chosenPosition,
            rating: n,
            sceneKind: sceneKind,
        )
        submitting = false
        switch action {
        case "insert", "merge":
            status = "感谢！这个机位会被加入用户社区推荐"
        default:
            status = "感谢评分！"
        }
    }
}
