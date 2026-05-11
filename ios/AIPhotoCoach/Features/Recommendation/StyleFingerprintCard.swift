// StyleFingerprintCard.swift (W6.3)
//
// "借鉴自参考 #N" — small swatch row under each shot card showing the
// dominant palette and mood keywords from the user's reference image.

import SwiftUI

struct StyleFingerprintCard: View {
    let fingerprint: ReferenceFingerprint

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("借鉴自参考 #\(fingerprint.index + 1)")
                    .font(.caption.weight(.semibold))
                Text(fingerprint.moodKeywords.joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            HStack(spacing: 4) {
                ForEach(Array(fingerprint.palette.prefix(5).enumerated()),
                        id: \.offset) { _, hex in
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color(hex: hex) ?? .gray)
                        .frame(width: 18, height: 18)
                        .overlay(RoundedRectangle(cornerRadius: 4)
                            .stroke(.white.opacity(0.6), lineWidth: 1))
                }
            }
        }
        .padding(10)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
    }
}

private extension Color {
    init?(hex: String) {
        var s = hex
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let n = UInt32(s, radix: 16) else { return nil }
        let r = Double((n >> 16) & 0xFF) / 255.0
        let g = Double((n >>  8) & 0xFF) / 255.0
        let b = Double( n        & 0xFF) / 255.0
        self = Color(red: r, green: g, blue: b)
    }
}
