import SwiftUI

// MARK: - Welcome / splash --------------------------------------------------

/// First-launch splash. Shown once (gated by `@AppStorage("aphc.welcomeSeen")`)
/// and then again only if the user explicitly opts in. The marquee at the
/// center auto-scrolls a "portfolio" of sample shots so the app feels alive
/// before any wizard work begins.
struct WelcomeView: View {
    var onContinue: () -> Void

    var body: some View {
        ZStack {
            CinemaBackdrop()
            VStack(spacing: 0) {
                header
                    .padding(.horizontal, 22)
                    .padding(.top, 4)

                heroBlock
                    .padding(.horizontal, 22)
                    .padding(.top, 24)

                MarqueeBand()
                    .frame(height: 220)
                    .padding(.top, 22)

                featureRow
                    .padding(.horizontal, 22)
                    .padding(.top, 22)

                Spacer(minLength: 14)

                ctaBlock
                    .padding(.horizontal, 22)
                    .padding(.bottom, 22)
            }
        }
        .preferredColorScheme(.dark)
        .ignoresSafeArea(edges: .bottom)
    }

    // ---------------------------------------------------------------------
    // Header
    // ---------------------------------------------------------------------

    private var header: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(AngularGradient(
                        colors: [CinemaTheme.accentWarm,
                                 CinemaTheme.accentCoral,
                                 CinemaTheme.accentCool,
                                 CinemaTheme.accentWarm],
                        center: .center))
                    .frame(width: 30, height: 30)
                Circle()
                    .fill(CinemaTheme.bgBase)
                    .frame(width: 12, height: 12)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("拾光")
                    .font(.system(size: 19, weight: .heavy))
                    .tracking(1.5)
                    .foregroundStyle(CinemaTheme.heroGradient)
                Text("AI 取景师 · 拾起每一束光")
                    .font(.system(size: 10, weight: .semibold))
                    .tracking(1.2)
                    .foregroundStyle(CinemaTheme.inkMuted)
            }
            Spacer()
        }
    }

    // ---------------------------------------------------------------------
    // Hero (eyebrow + title + subtitle)
    // ---------------------------------------------------------------------

    private var heroBlock: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 6) {
                Circle()
                    .fill(CinemaTheme.accentWarm)
                    .frame(width: 5, height: 5)
                    .shadow(color: CinemaTheme.accentWarm.opacity(0.7), radius: 5)
                Text("CINEMA HOUSE · AI · 2026")
                    .font(.system(size: 10.5, weight: .heavy))
                    .tracking(2.6)
                    .foregroundStyle(CinemaTheme.accentWarm)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 5)
            .background(
                Capsule().fill(CinemaTheme.accentWarm.opacity(0.10))
            )
            .overlay(
                Capsule().stroke(CinemaTheme.accentWarm.opacity(0.36), lineWidth: 1)
            )

            (
                Text("环视一圈，\n")
                    .foregroundStyle(CinemaTheme.ink)
                +
                Text("AI 给你导演级出片方案")
                    .foregroundStyle(LinearGradient(
                        colors: [CinemaTheme.accentWarm,
                                 CinemaTheme.accentCoral,
                                 CinemaTheme.accentCool],
                        startPoint: .leading, endPoint: .trailing))
            )
            .font(.system(size: 30, weight: .heavy))
            .lineSpacing(1)

            Text("机位 · 构图 · 焦段 · 光圈 · 快门 · ISO · 人物姿势\n一键生成 2 到 3 套方案，1 到 4 人，含纯风景模式")
                .font(.system(size: 13, weight: .medium))
                .foregroundStyle(CinemaTheme.inkSoft)
                .lineSpacing(3)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // ---------------------------------------------------------------------
    // Numbered feature row
    // ---------------------------------------------------------------------

    private var featureRow: some View {
        HStack(spacing: 10) {
            featureCell(num: "01", title: "环视 10 秒",
                        sub: "陀螺仪 + 关键帧抽取，AI 看遍每个角度")
            featureCell(num: "02", title: "虚拟模特",
                        sub: "7 个内置角色，每个机位都摆好姿势给你看")
            featureCell(num: "03", title: "3 套方案",
                        sub: "焦段 · 光圈 · 快门 · ISO，连姿势都讲清楚")
        }
    }

    private func featureCell(num: String, title: String, sub: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(num)
                .font(.system(size: 10.5, weight: .heavy))
                .tracking(2)
                .foregroundStyle(CinemaTheme.accentWarm)
            Text(title)
                .font(.system(size: 13.5, weight: .heavy))
                .foregroundStyle(CinemaTheme.ink)
            Text(sub)
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(CinemaTheme.inkMuted)
                .lineLimit(3)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial,
                    in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(CinemaTheme.borderSoft, lineWidth: 1)
        )
        .overlay(alignment: .top) {
            LinearGradient(colors: [.clear, CinemaTheme.accentWarm.opacity(0.55), .clear],
                           startPoint: .leading, endPoint: .trailing)
                .frame(height: 1)
                .padding(.horizontal, 16)
        }
    }

    // ---------------------------------------------------------------------
    // CTA
    // ---------------------------------------------------------------------

    private var ctaBlock: some View {
        VStack(spacing: 8) {
            Button(action: onContinue) {
                HStack(spacing: 12) {
                    Text("开始拍片")
                        .font(.system(size: 17, weight: .heavy))
                    Image(systemName: "arrow.right")
                        .font(.system(size: 16, weight: .bold))
                }
                .foregroundStyle(Color.black)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 17)
                .background(CinemaTheme.accentGradient)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                .shadow(color: CinemaTheme.accentWarm.opacity(0.5),
                        radius: 22, y: 14)
            }
            .buttonStyle(.plain)

            Text("数据只在你这台设备 · 后续会加登录 / 收藏 / 作品同步")
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(CinemaTheme.inkMuted)
        }
    }
}

// MARK: - Marquee band ------------------------------------------------------

/// Two parallax rows of poster cards scrolling in opposite directions.
/// Pure SwiftUI / Canvas — no network or asset bundle needed.
private struct MarqueeBand: View {
    var body: some View {
        VStack(spacing: 12) {
            MarqueeRow(direction: .left, duration: 36, posters: WelcomePosters.envFrames)
            MarqueeRow(direction: .right, duration: 48, posters: WelcomePosters.refPosters)
        }
        .overlay(alignment: .leading) {
            LinearGradient(colors: [CinemaTheme.bgBase, .clear],
                           startPoint: .leading, endPoint: .trailing)
                .frame(width: 50)
        }
        .overlay(alignment: .trailing) {
            LinearGradient(colors: [.clear, CinemaTheme.bgBase],
                           startPoint: .leading, endPoint: .trailing)
                .frame(width: 50)
        }
    }
}

private enum MarqueeDirection { case left, right }

private struct MarqueeRow: View {
    let direction: MarqueeDirection
    let duration: Double
    let posters: [PosterSpec]

    @State private var phase: CGFloat = 0
    @State private var rowWidth: CGFloat = 0

    var body: some View {
        GeometryReader { geo in
            HStack(spacing: 12) {
                ForEach(0..<posters.count * 2, id: \.self) { idx in
                    PosterCard(spec: posters[idx % posters.count])
                }
            }
            .background(GeometryReader { g in
                Color.clear.onAppear { rowWidth = g.size.width }
            })
            .offset(x: offsetForPhase(phase, totalWidth: rowWidth))
            .onAppear {
                phase = direction == .left ? 0 : 1
                withAnimation(.linear(duration: duration).repeatForever(autoreverses: false)) {
                    phase = direction == .left ? 1 : 0
                }
            }
            .frame(width: geo.size.width, alignment: .leading)
        }
    }

    private func offsetForPhase(_ p: CGFloat, totalWidth: CGFloat) -> CGFloat {
        guard totalWidth > 0 else { return 0 }
        // Each phase 0..1 maps to 0..-half so the duplicated row loops seamlessly.
        let half = totalWidth / 2
        return -half * p
    }
}

private struct PosterCard: View {
    let spec: PosterSpec

    var body: some View {
        ZStack(alignment: .bottomLeading) {
            spec.gradient
                .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
            spec.decoration
            VStack {
                HStack {
                    Spacer()
                    Text(spec.azimuth)
                        .font(.system(size: 9, weight: .heavy))
                        .tracking(1)
                        .foregroundStyle(CinemaTheme.accentWarm)
                        .padding(.top, 18).padding(.trailing, 12)
                }
                Spacer()
            }
            // Top film strip
            HStack(spacing: 8) {
                ForEach(0..<10, id: \.self) { _ in
                    Capsule()
                        .fill(CinemaTheme.accentWarm.opacity(0.85))
                        .frame(width: 4, height: 3.5)
                }
            }
            .padding(.leading, 10)
            .padding(.top, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            // Tag
            Text(spec.tag)
                .font(.system(size: 10, weight: .heavy))
                .tracking(1.6)
                .foregroundStyle(CinemaTheme.ink)
                .padding(.horizontal, 8).padding(.vertical, 4)
                .background(.ultraThinMaterial, in: Capsule())
                .overlay(Capsule().stroke(.white.opacity(0.18), lineWidth: 1))
                .padding(.leading, 12)
                .padding(.bottom, 12)
        }
        .frame(width: spec.size == .wide ? 220 : 160,
               height: spec.size == .wide ? 165 : 215)
        .shadow(color: .black.opacity(0.55), radius: 14, y: 10)
    }
}

// MARK: - Poster catalogue --------------------------------------------------

private enum PosterSize { case tall, wide }

private struct PosterSpec: Identifiable {
    let id = UUID()
    let tag: String
    let azimuth: String
    let size: PosterSize
    let gradient: AnyView
    let decoration: AnyView

    static func fromColors(tag: String,
                           azimuth: String,
                           size: PosterSize = .tall,
                           top: Color, bottom: Color,
                           accent: Color = .clear,
                           sun: CGPoint? = nil) -> PosterSpec {
        let grad = LinearGradient(colors: [top, bottom],
                                  startPoint: .top, endPoint: .bottom)
        let decoration = ZStack {
            // ground band
            VStack(spacing: 0) {
                Spacer()
                Rectangle()
                    .fill(accent.opacity(0.5))
                    .frame(height: 50)
            }
            if let sun = sun {
                Circle()
                    .fill(RadialGradient(colors: [.white.opacity(0.85),
                                                  Color(red: 1.0, green: 0.86, blue: 0.55).opacity(0.6),
                                                  .clear],
                                         center: .center, startRadius: 6, endRadius: 80))
                    .frame(width: 100, height: 100)
                    .position(x: sun.x, y: sun.y)
            }
        }
        return PosterSpec(tag: tag, azimuth: azimuth, size: size,
                          gradient: AnyView(grad),
                          decoration: AnyView(decoration))
    }
}

private enum WelcomePosters {
    static let envFrames: [PosterSpec] = [
        .fromColors(tag: "Sunset", azimuth: "000°",
                    top: Color(red: 0.14, green: 0.12, blue: 0.27),
                    bottom: Color(red: 1.0, green: 0.51, blue: 0.31),
                    accent: Color(red: 0.37, green: 0.43, blue: 0.24),
                    sun: CGPoint(x: 90, y: 130)),
        .fromColors(tag: "Bench", azimuth: "045°",
                    top: Color(red: 0.20, green: 0.24, blue: 0.43),
                    bottom: Color(red: 0.94, green: 0.67, blue: 0.43),
                    accent: Color(red: 0.37, green: 0.43, blue: 0.24)),
        .fromColors(tag: "Block", azimuth: "090°",
                    top: Color(red: 0.16, green: 0.27, blue: 0.51),
                    bottom: Color(red: 0.67, green: 0.75, blue: 0.86),
                    accent: Color(red: 0.24, green: 0.37, blue: 0.27)),
        .fromColors(tag: "Trees", azimuth: "135°",
                    top: Color(red: 0.12, green: 0.20, blue: 0.37),
                    bottom: Color(red: 0.43, green: 0.59, blue: 0.78),
                    accent: Color(red: 0.24, green: 0.37, blue: 0.21)),
        .fromColors(tag: "Fount.", azimuth: "180°",
                    top: Color(red: 0.08, green: 0.12, blue: 0.27),
                    bottom: Color(red: 0.24, green: 0.35, blue: 0.59),
                    accent: Color(red: 0.24, green: 0.37, blue: 0.24)),
        .fromColors(tag: "Statue", azimuth: "225°",
                    top: Color(red: 0.12, green: 0.16, blue: 0.31),
                    bottom: Color(red: 0.35, green: 0.47, blue: 0.71),
                    accent: Color(red: 0.27, green: 0.39, blue: 0.27)),
        .fromColors(tag: "Skyline", azimuth: "270°",
                    top: Color(red: 0.14, green: 0.14, blue: 0.33),
                    bottom: Color(red: 0.71, green: 0.59, blue: 0.71),
                    accent: Color(red: 0.20, green: 0.31, blue: 0.21)),
        .fromColors(tag: "Mixed", azimuth: "315°",
                    top: Color(red: 0.16, green: 0.16, blue: 0.35),
                    bottom: Color(red: 0.78, green: 0.63, blue: 0.71),
                    accent: Color(red: 0.31, green: 0.43, blue: 0.27)),
    ]

    static let refPosters: [PosterSpec] = [
        .fromColors(tag: "Moody", azimuth: "FILM",
                    size: .wide,
                    top: Color(red: 0.20, green: 0.10, blue: 0.31),
                    bottom: Color(red: 0.71, green: 0.39, blue: 0.43),
                    accent: Color(red: 0.43, green: 0.20, blue: 0.16)),
        .fromColors(tag: "Bright", azimuth: "FILM",
                    size: .wide,
                    top: Color(red: 0.86, green: 0.82, blue: 0.76),
                    bottom: Color(red: 0.94, green: 0.90, blue: 0.84),
                    accent: Color(red: 0.71, green: 0.65, blue: 0.55)),
        .fromColors(tag: "Film", azimuth: "FILM",
                    size: .wide,
                    top: Color(red: 0.33, green: 0.51, blue: 0.27),
                    bottom: Color(red: 0.24, green: 0.37, blue: 0.20),
                    accent: Color(red: 0.71, green: 0.59, blue: 0.43)),
        .fromColors(tag: "Sunset", azimuth: "000°",
                    top: Color(red: 0.14, green: 0.12, blue: 0.27),
                    bottom: Color(red: 1.0, green: 0.51, blue: 0.31),
                    accent: Color(red: 0.37, green: 0.43, blue: 0.24)),
        .fromColors(tag: "Block", azimuth: "090°",
                    top: Color(red: 0.16, green: 0.27, blue: 0.51),
                    bottom: Color(red: 0.67, green: 0.75, blue: 0.86),
                    accent: Color(red: 0.24, green: 0.37, blue: 0.27)),
        .fromColors(tag: "Skyline", azimuth: "270°",
                    top: Color(red: 0.14, green: 0.14, blue: 0.33),
                    bottom: Color(red: 0.71, green: 0.59, blue: 0.71),
                    accent: Color(red: 0.20, green: 0.31, blue: 0.21)),
    ]
}
