// ShotFramingOverlay.swift (W8.3)
//
// 2D overlay drawn on top of the AR view once the user reaches the
// recommended ShotPosition. Renders the recommended composition frame
// (rule-of-thirds / centred / leading-line variants) plus a translucent
// silhouette of where the subject should stand.

import SwiftUI

struct ShotFramingOverlay: View {
    let composition: Composition?
    let subjectPositionHint: String?

    var body: some View {
        GeometryReader { geo in
            ZStack {
                gridLines(in: geo.size)
                    .stroke(.white.opacity(0.4), lineWidth: 1)
                if let hint = subjectPositionHint {
                    Text(hint)
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10)
                        .padding(.vertical, 6)
                        .background(.ultraThinMaterial, in: Capsule())
                        .position(x: geo.size.width / 2,
                                  y: geo.size.height - 64)
                }
                subjectMarker(in: geo.size)
            }
        }
        .allowsHitTesting(false)
    }

    private func gridLines(in size: CGSize) -> Path {
        Path { p in
            let w = size.width, h = size.height
            switch composition?.primary {
            case .symmetry, .centered:
                p.move(to: CGPoint(x: w / 2, y: 0))
                p.addLine(to: CGPoint(x: w / 2, y: h))
            case .diagonal:
                p.move(to: CGPoint(x: 0, y: h))
                p.addLine(to: CGPoint(x: w, y: 0))
            default:
                let x1 = w / 3, x2 = 2 * w / 3
                let y1 = h / 3, y2 = 2 * h / 3
                p.move(to: CGPoint(x: x1, y: 0)); p.addLine(to: CGPoint(x: x1, y: h))
                p.move(to: CGPoint(x: x2, y: 0)); p.addLine(to: CGPoint(x: x2, y: h))
                p.move(to: CGPoint(x: 0, y: y1)); p.addLine(to: CGPoint(x: w, y: y1))
                p.move(to: CGPoint(x: 0, y: y2)); p.addLine(to: CGPoint(x: w, y: y2))
            }
        }
    }

    private func subjectMarker(in size: CGSize) -> some View {
        let cx = size.width * 0.66
        let cy = size.height * 0.6
        return Circle()
            .stroke(.yellow, style: StrokeStyle(lineWidth: 2, dash: [6, 6]))
            .frame(width: 44, height: 44)
            .position(x: cx, y: cy)
    }
}
