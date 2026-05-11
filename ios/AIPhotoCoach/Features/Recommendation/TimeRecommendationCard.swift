// TimeRecommendationCard.swift (W7.3)

import SwiftUI

struct TimeRecommendationCard: View {
    let recommendation: TimeRecommendation

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "clock.arrow.2.circlepath")
                .font(.title2)
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 4) {
                Text("今晚几点拍更好")
                    .font(.subheadline.weight(.semibold))
                Text(recommendation.blurbZh ??
                     "附近 \(recommendation.sampleN) 张照片在 \(timeString(recommendation.bestHourLocal)) 评分最高（\(String(format: "%.1f", recommendation.score))）。")
                    .font(.caption)
                if let runner = recommendation.runnerUpHourLocal {
                    Text("次优时段：\(timeString(runner))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if let mins = recommendation.minutesUntilBest {
                    Text(mins > 0 ? "距最佳时段 \(Int(mins)) 分钟" : "现在就是最佳时段")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private func timeString(_ hour: Int) -> String {
        String(format: "%02d:00", hour)
    }
}
