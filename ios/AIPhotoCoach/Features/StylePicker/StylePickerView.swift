// StylePickerView.swift
//
// SwiftUI mirror of web/js/style_picker.js. Replaces the "type English
// keywords" + horizontal-chip suggestions UX on Step 3 of the wizard
// with five visual style cards plus a real-time "可行性" badge driven by
// the backend's GET /style-feasibility?lat=&lon=[&picks=] endpoint.
//
// Selected styles are written back to the same `aphc.styleKeywords`
// AppStorage string the rest of the wizard already consumes (comma-
// separated English keywords like "cinematic, moody"), so there is zero
// downstream migration work.
//
// Cards display:
//   • a placeholder gradient (real photos live on the backend at
//     /web/img/style/<id>/01.jpg — when the iOS app eventually proxies
//     the same statics this view will pick them up automatically; until
//     then the gradient + label is the safe offline fallback).
//   • the Chinese label (氛围感 / 清爽日系 / 温柔暖光 / 自然随手 / 大片感)
//   • a one-line blurb
//   • a feasibility badge (✓ 推荐 / △ 勉强 / ⚠ 不推荐) once the API
//     responds. Hidden until then to avoid flashing wrong state.
//   • "更优时段" banner above the grid when every picked style scores
//     < 0.5 in the current environment.
//
// Networking is fire-and-forget — the picker stays usable even when
// /style-feasibility is unreachable; you just lose the badges.

import SwiftUI

private let MAX_PICKS = 2

// ---------------------------------------------------------------------------
// Catalog — kept inline (not fetched from manifest.json) so the picker
// works offline and during cold start. Keep in sync with
// web/img/style/manifest.json + backend STYLE_LABELS_ZH.
// ---------------------------------------------------------------------------
struct StyleCatalogEntry: Identifiable, Hashable {
    let id: String                     // style id, matches backend
    let labelZh: String
    let blurbZh: String
    let keywords: [String]             // English tokens written into styleKeywords
    let gradientStart: Color
    let gradientEnd: Color
}

enum StyleCatalog {
    static let all: [StyleCatalogEntry] = [
        .init(id: "cinematic_moody", labelZh: "氛围感",
              blurbZh: "黄昏 / 夜晚 · 想要点情绪",
              keywords: ["cinematic", "moody"],
              gradientStart: Color(red: 0.18, green: 0.13, blue: 0.27),
              gradientEnd:   Color(red: 0.06, green: 0.04, blue: 0.13)),
        .init(id: "clean_bright", labelZh: "清爽日系",
              blurbZh: "白墙 / 海边 · 不复杂的明亮",
              keywords: ["clean", "bright"],
              gradientStart: Color(red: 0.92, green: 0.94, blue: 0.97),
              gradientEnd:   Color(red: 0.78, green: 0.85, blue: 0.92)),
        .init(id: "film_warm", labelZh: "温柔暖光",
              blurbZh: "黄金时段 · 胶片暖调",
              keywords: ["film", "warm"],
              gradientStart: Color(red: 0.95, green: 0.72, blue: 0.45),
              gradientEnd:   Color(red: 0.62, green: 0.34, blue: 0.20)),
        .init(id: "street_candid", labelZh: "自然随手",
              blurbZh: "街头抓拍 · 不挑环境",
              keywords: ["street", "candid"],
              gradientStart: Color(red: 0.36, green: 0.42, blue: 0.50),
              gradientEnd:   Color(red: 0.20, green: 0.24, blue: 0.30)),
        .init(id: "editorial_fashion", labelZh: "大片感",
              blurbZh: "杂志 / 品牌 · 强姿态",
              keywords: ["editorial", "fashion"],
              gradientStart: Color(red: 0.10, green: 0.10, blue: 0.10),
              gradientEnd:   Color(red: 0.32, green: 0.05, blue: 0.10)),
    ]

    static func entry(for keyword: String) -> StyleCatalogEntry? {
        let kw = keyword.lowercased()
        return all.first { $0.keywords.contains(kw) }
    }
}

// ---------------------------------------------------------------------------
// API response models (loose decoding — only the fields we render)
// ---------------------------------------------------------------------------
struct StyleFeasibilityScore: Codable, Hashable, Identifiable {
    let styleId: String
    let labelZh: String
    let score: Double
    let tier: String                   // "recommended" / "marginal" / "discouraged" / "unknown"
    let reasonZh: String

    var id: String { styleId }

    enum CodingKeys: String, CodingKey {
        case styleId  = "style_id"
        case labelZh  = "label_zh"
        case score, tier
        case reasonZh = "reason_zh"
    }
}

struct StyleBetterTimeSuggestion: Codable, Hashable {
    let timestamp: String
    let phase: String?
    let bestScore: Double
    let currentScore: Double
    let delta: Double
    let reasonZh: String

    enum CodingKeys: String, CodingKey {
        case timestamp, phase, delta
        case bestScore    = "best_score"
        case currentScore = "current_score"
        case reasonZh     = "reason_zh"
    }
}

struct StyleFeasibilityResponse: Codable {
    let scores: [StyleFeasibilityScore]
    let betterTime: StyleBetterTimeSuggestion?

    enum CodingKeys: String, CodingKey {
        case scores
        case betterTime = "better_time"
    }
}

// ---------------------------------------------------------------------------
// Service — wraps APIClient.shared.urlSession to call /style-feasibility
// ---------------------------------------------------------------------------
enum StyleFeasibilityService {
    static func fetch(lat: Double, lon: Double, picks: [String] = []) async -> StyleFeasibilityResponse? {
        var components = URLComponents(
            url: APIConfig.baseURL.appendingPathComponent("style-feasibility"),
            resolvingAgainstBaseURL: false,
        )
        var items: [URLQueryItem] = [
            URLQueryItem(name: "lat", value: String(lat)),
            URLQueryItem(name: "lon", value: String(lon)),
        ]
        if !picks.isEmpty {
            items.append(URLQueryItem(name: "picks", value: picks.joined(separator: ",")))
        }
        components?.queryItems = items
        guard let url = components?.url else { return nil }
        // Use a dedicated short-timeout session — feasibility is a UX
        // hint, never block the picker if backend is slow.
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 4.0
        cfg.timeoutIntervalForResource = 4.0
        let session = URLSession(configuration: cfg)
        do {
            let (data, _) = try await session.data(from: url)
            let decoder = JSONDecoder()
            return try decoder.decode(StyleFeasibilityResponse.self, from: data)
        } catch {
            print("[StylePicker] feasibility fetch failed: \(error)")
            return nil
        }
    }
}

// ---------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------
struct StylePickerView: View {
    /// Two-way binding to the wizard's persisted style string
    /// (comma-separated English keywords). Picker keeps it in sync.
    @Binding var styleInput: String

    @State private var picks: Set<String> = []
    @State private var scores: [String: StyleFeasibilityScore] = [:]
    @State private var betterTime: StyleBetterTimeSuggestion?
    @State private var hasLoadedScores = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let bt = betterTime, !picks.isEmpty {
                betterTimeBanner(bt)
            }
            grid
            customField
        }
        .onAppear {
            picks = derivePicks(from: styleInput)
            Task { await refreshScores() }
        }
        .onChange(of: styleInput) { _, newValue in
            // External edits (e.g. user typed in custom field) should
            // pull selection back into sync without re-saving.
            let derived = derivePicks(from: newValue)
            if derived != picks { picks = derived }
        }
    }

    // ── grid of 5 cards ──
    private var grid: some View {
        let cols = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]
        return LazyVGrid(columns: cols, spacing: 10) {
            ForEach(StyleCatalog.all) { entry in
                card(entry)
            }
        }
    }

    private func card(_ entry: StyleCatalogEntry) -> some View {
        let isPicked = picks.contains(entry.id)
        let verdict = scores[entry.id]
        let dimmed = verdict?.tier == "discouraged" && !isPicked

        return Button {
            toggle(entry.id)
        } label: {
            VStack(alignment: .leading, spacing: 0) {
                ZStack(alignment: .topTrailing) {
                    LinearGradient(
                        colors: [entry.gradientStart, entry.gradientEnd],
                        startPoint: .topLeading, endPoint: .bottomTrailing,
                    )
                    .frame(height: 92)
                    if isPicked {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.white)
                            .font(.system(size: 22))
                            .padding(8)
                            .shadow(color: .black.opacity(0.4), radius: 4)
                    }
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.labelZh)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                    Text(entry.blurbZh)
                        .font(.system(size: 10.5))
                        .foregroundStyle(.white.opacity(0.65))
                        .lineLimit(2)
                    if let v = verdict, v.tier != "unknown" {
                        feasibilityBadge(v)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 9)
            }
            .background(Color.black.opacity(0.35))
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .stroke(isPicked ? Color.accentColor : Color.white.opacity(0.10),
                            lineWidth: isPicked ? 2 : 1),
            )
            .opacity(dimmed ? 0.62 : 1)
        }
        .buttonStyle(.plain)
    }

    private func feasibilityBadge(_ v: StyleFeasibilityScore) -> some View {
        let (icon, color): (String, Color) = {
            switch v.tier {
            case "recommended": return ("✓", Color(red: 0.49, green: 0.86, blue: 0.62))
            case "marginal":    return ("△", Color(red: 0.96, green: 0.72, blue: 0.38))
            case "discouraged": return ("⚠", Color(red: 1.00, green: 0.55, blue: 0.55))
            default:            return ("", .gray)
            }
        }()
        return Text("\(icon) \(v.reasonZh)")
            .font(.system(size: 9.5, weight: .semibold))
            .foregroundStyle(color)
            .padding(.top, 3)
            .lineLimit(2)
    }

    private func betterTimeBanner(_ bt: StyleBetterTimeSuggestion) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "clock.arrow.circlepath")
                .foregroundStyle(Color(red: 0.36, green: 0.61, blue: 1.00))
                .font(.system(size: 18))
            VStack(alignment: .leading, spacing: 4) {
                Text("当前环境对你选的风格不太友好")
                    .font(.system(size: 12.5, weight: .semibold))
                    .foregroundStyle(.white)
                Text(bt.reasonZh)
                    .font(.system(size: 11.5))
                    .foregroundStyle(.white.opacity(0.75))
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color(red: 0.36, green: 0.61, blue: 1.00).opacity(0.08))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Color(red: 0.36, green: 0.61, blue: 1.00).opacity(0.35), lineWidth: 1)
        )
    }

    // ── custom keyword fallback (DisclosureGroup matches web's <details>) ──
    private var customField: some View {
        DisclosureGroup("我想自己输入关键词") {
            TextField("",
                      text: $styleInput,
                      prompt: Text("例如：cinematic, moody — 多个用逗号分隔；与上方卡片自动同步")
                        .foregroundColor(.white.opacity(0.4)))
                .font(.system(size: 13))
                .foregroundStyle(.white)
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Color.black.opacity(0.28))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .padding(.top, 8)
        }
        .font(.system(size: 12, weight: .medium))
        .foregroundStyle(.white.opacity(0.65))
        .accentColor(.white.opacity(0.8))
    }

    // ── selection logic ──
    private func toggle(_ id: String) {
        if picks.contains(id) {
            picks.remove(id)
        } else {
            if picks.count >= MAX_PICKS, let oldest = picks.first {
                picks.remove(oldest)
            }
            picks.insert(id)
        }
        syncInputFromPicks()
        // Re-fetch better-time using the new pick set (cheap when scores
        // are already cached server-side for this hour).
        Task { await refreshBetterTime() }
    }

    private func syncInputFromPicks() {
        let tokens = StyleCatalog.all
            .filter { picks.contains($0.id) }
            .flatMap { $0.keywords }
        styleInput = tokens.joined(separator: ", ")
    }

    private func derivePicks(from input: String) -> Set<String> {
        let tokens = input
            .lowercased()
            .split(whereSeparator: { ",;，；".contains($0) })
            .map { $0.trimmingCharacters(in: .whitespaces) }
        var out = Set<String>()
        for entry in StyleCatalog.all {
            if entry.keywords.contains(where: { tokens.contains($0) }) {
                out.insert(entry.id)
            }
        }
        return out
    }

    // ── network ──
    private func refreshScores() async {
        guard !hasLoadedScores else { return }
        // Use cached fix only — never trigger a permission prompt from
        // the wizard. The capture flow is responsible for that.
        guard let fix = await LocationProvider.shared.cachedGeoFix() else {
            return
        }
        if let resp = await StyleFeasibilityService.fetch(
            lat: fix.lat, lon: fix.lon,
            picks: Array(picks),
        ) {
            await MainActor.run {
                self.scores = Dictionary(uniqueKeysWithValues: resp.scores.map { ($0.styleId, $0) })
                self.betterTime = pickedNeedsBetterTime() ? resp.betterTime : nil
                self.hasLoadedScores = true
            }
        }
    }

    private func refreshBetterTime() async {
        guard let fix = await LocationProvider.shared.cachedGeoFix() else { return }
        guard pickedNeedsBetterTime() else {
            await MainActor.run { self.betterTime = nil }
            return
        }
        if let resp = await StyleFeasibilityService.fetch(
            lat: fix.lat, lon: fix.lon, picks: Array(picks),
        ) {
            await MainActor.run { self.betterTime = resp.betterTime }
        }
    }

    private func pickedNeedsBetterTime() -> Bool {
        guard !picks.isEmpty, !scores.isEmpty else { return false }
        return picks.allSatisfy { (scores[$0]?.score ?? 1.0) < 0.5 }
    }
}

// ---------------------------------------------------------------------------
// LocationProvider helper — read-only cache accessor so the picker
// never triggers a permission prompt of its own.
// ---------------------------------------------------------------------------
extension LocationProvider {
    /// Returns the on-disk cached fix without ever asking CoreLocation
    /// for a fresh one. Returns nil when the user has never granted
    /// location permission for any prior capture.
    func cachedGeoFix() async -> GeoFix? {
        guard let data = UserDefaults.standard.data(forKey: "aphc.geofix") else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(GeoFix.self, from: data)
    }
}
