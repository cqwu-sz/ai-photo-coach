// StaticGuideCard.swift
//
// Non-AR fallback for ShotNavigationView. Used when ARWorldTracking
// isn't supported (older iPad / simulator) or the user denied camera
// permission. Shows the same shot information as a static 2D card so
// the user can still execute the recommendation manually.

import SwiftUI

struct StaticGuideCard: View {
    let shot: ShotRecommendation

    @Environment(\.horizontalSizeClass) private var hSize

    var body: some View {
        ScrollView {
            // Landscape / iPad: place the diagram next to the pose
            // list so the user gets both at a glance. Portrait phone:
            // stack vertically as before.
            if hSize == .regular {
                HStack(alignment: .top, spacing: 20) {
                    VStack(alignment: .leading, spacing: 16) {
                        header
                        topDownDiagram
                    }
                    .frame(maxWidth: .infinity)
                    poseList
                        .frame(maxWidth: .infinity)
                }
                .padding(20)
            } else {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    topDownDiagram
                    poseList
                    Spacer(minLength: 24)
                }
                .padding(20)
            }
        }
        .navigationTitle("拍摄引导")
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(shot.title ?? "推荐机位")
                .font(.title3.weight(.semibold))
            Text("当前设备不支持 AR 实时引导，已切换为图文版")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack(spacing: 8) {
                badge(String(format: "推荐距离 %.1f m", shot.angle.distanceM))
                badge("方位 \(Int(shot.angle.azimuthDeg.rounded()))°")
                badge("仰角 \(Int(shot.angle.pitchDeg.rounded()))°")
            }
        }
    }

    /// Top-down diagram: camera at the bottom, subject placed by
    /// distance + azimuth. A simple GeometryReader-based plot is more
    /// useful than a wall of text.
    private var topDownDiagram: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h: CGFloat = 240
            let camera = CGPoint(x: w / 2, y: h - 24)
            let azRad = shot.angle.azimuthDeg * .pi / 180
            // Adaptive scale: keep the subject inside the diagram with
            // ~28pt margin from the closest edge, regardless of how far
            // the recommendation is.
            let usable = min(w, h) - 56
            let distance = max(0.5, shot.angle.distanceM)
            let pxPerMeter = CGFloat(usable / 2.0) / distance
            let dx = sin(azRad) * shot.angle.distanceM * pxPerMeter
            let dz = cos(azRad) * shot.angle.distanceM * pxPerMeter
            let subject = CGPoint(x: camera.x + dx, y: camera.y - dz)

            ZStack {
                RoundedRectangle(cornerRadius: 14)
                    .fill(Color.secondary.opacity(0.08))
                Path { p in
                    p.move(to: camera); p.addLine(to: subject)
                }
                .stroke(.tint, style: StrokeStyle(lineWidth: 2, dash: [6, 4]))
                Image(systemName: "camera.fill")
                    .foregroundStyle(.tint)
                    .position(camera)
                Image(systemName: "person.fill")
                    .font(.title2)
                    .foregroundStyle(.orange)
                    .position(subject)
                Text(String(format: "%.1f m", shot.angle.distanceM))
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(.ultraThinMaterial, in: Capsule())
                    .position(x: (camera.x + subject.x) / 2,
                              y: (camera.y + subject.y) / 2)
            }
            .frame(height: h)
        }
        .frame(height: 240)
    }

    private var poseList: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("姿势 & 表情").font(.headline)
            ForEach(Array((shot.poses.first?.persons ?? []).enumerated()), id: \.offset) { idx, p in
                VStack(alignment: .leading, spacing: 4) {
                    Text("第 \(idx + 1) 位 · \(p.role)")
                        .font(.subheadline.weight(.semibold))
                    if let stance = p.stance { row("姿态", stance) }
                    if let upper = p.upperBody { row("上身", upper) }
                    if let hands = p.hands { row("手势", hands) }
                    if let gaze = p.gaze { row("视线", gaze) }
                    if let exp = p.expression { row("表情", exp) }
                    if let hint = p.positionHint { row("位置", hint) }
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(.secondary).frame(width: 44, alignment: .leading)
            Text(value).fixedSize(horizontal: false, vertical: true)
        }
        .font(.caption)
    }

    private func badge(_ text: String) -> some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(Color.secondary.opacity(0.15), in: Capsule())
    }
}
