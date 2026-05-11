// ShotPositionCard.swift
//
// Renders the unified ``ShotPosition`` from a recommendation. Two
// modes share one card so the result UI can drop a single component
// inside each shot's swipe page:
//
//   - .relative -> compass arrow + "原地附近 · 4.2 m" subtitle
//   - .absolute -> MapKit snapshot with user pin + shot pin + bearing
//                  arrow + "走 78 m · ≈ 1 分钟" subtitle
//
// MapKit's `Map` view (iOS 17+) is used directly with a tiny region
// around both pins. We don't need driving directions — just a
// situational map that tells the user which way to walk.

import MapKit
import SwiftUI

struct ShotPositionCard: View {
    let position: ShotPosition
    let userLocation: CLLocationCoordinate2D?
    /// W8.5 — invoked when the user taps the "AR 带我去拍" button on an
    /// absolute card. Hosts typically push a ShotNavigationView.
    var onTapArNavigate: (() -> Void)? = nil

    var body: some View {
        switch position.kind {
        case .relative:
            relativeCard
        case .absolute:
            absoluteCard
        case .indoor:
            indoorCard
        }
    }

    private var indoorCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "building.2.fill")
                    .foregroundStyle(.tint)
                Text(position.nameZh ?? "室内热点")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                confidenceBadge
            }
            Text(position.summaryZh)
                .font(.footnote)
                .foregroundStyle(.secondary)
            if let imgRef = position.indoor?.imageRef, !imgRef.isEmpty {
                AsyncImage(url: URL(string: imgRef)) { phase in
                    switch phase {
                    case .empty: ProgressView()
                    case .success(let img): img.resizable().scaledToFit()
                    case .failure: Color.gray.opacity(0.1)
                    @unknown default: Color.gray.opacity(0.1)
                    }
                }
                .frame(maxHeight: 140)
                .clipShape(RoundedRectangle(cornerRadius: 10))
            }
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    // MARK: - Relative

    private var relativeCard: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(Color.blue.opacity(0.12))
                    .frame(width: 52, height: 52)
                Image(systemName: "location.north.fill")
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(.blue)
                    .rotationEffect(.degrees(position.azimuthDeg ?? 0))
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(position.nameZh ?? "原地附近机位")
                    .font(.subheadline.weight(.semibold))
                Text(position.summaryZh)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                if let pitch = position.pitchDeg {
                    Text("方位 \(Int((position.azimuthDeg ?? 0).rounded()))° · 仰角 \(Int(pitch.rounded()))°")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            Spacer()
            confidenceBadge
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    // MARK: - Absolute

    @State private var cameraPos: MapCameraPosition = .automatic

    private var absoluteCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(position.nameZh ?? "外部机位")
                        .font(.subheadline.weight(.semibold))
                    Text(position.summaryZh)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                confidenceBadge
            }
            mapView
                .frame(height: 140)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            if let note = position.walkabilityNoteZh, !note.isEmpty {
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .lineLimit(2)
            }
            if onTapArNavigate != nil {
                let isFar = (position.walkDistanceM ?? 0) > 30
                Button {
                    // Pre-warm the avatar manifest the moment the user
                    // commits to AR navigation, so the Stage-2 ghost
                    // can mount the moment they reach the spot rather
                    // than waiting on a cold network fetch.
                    ARGuidePreWarm.preWarmManifest()
                    onTapArNavigate?()
                } label: {
                    Label(isFar ? "AR 带我去拍 →" : "AR 引导",
                          systemImage: "arkit")
                        .font(isFar ? .callout.weight(.semibold)
                                    : .caption.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, isFar ? 4 : 0)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(isFar ? .regular : .small)
                .tint(isFar ? .orange : .accentColor)
            }
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .onAppear { cameraPos = computeRegion() }
    }

    private var mapView: some View {
        Map(position: $cameraPos, interactionModes: [.pan, .zoom]) {
            if let user = userLocation {
                Marker("你", coordinate: user)
                    .tint(.blue)
            }
            if let lat = position.lat, let lon = position.lon {
                Marker(position.nameZh ?? "机位",
                       coordinate: .init(latitude: lat, longitude: lon))
                    .tint(.orange)
            }
        }
    }

    private func computeRegion() -> MapCameraPosition {
        guard let lat = position.lat, let lon = position.lon else { return .automatic }
        guard let user = userLocation else {
            return .region(.init(
                center: .init(latitude: lat, longitude: lon),
                span: .init(latitudeDelta: 0.005, longitudeDelta: 0.005)
            ))
        }
        let centerLat = (user.latitude + lat) / 2
        let centerLon = (user.longitude + lon) / 2
        let dLat = abs(user.latitude - lat) * 2 + 0.0015
        let dLon = abs(user.longitude - lon) * 2 + 0.0015
        return .region(.init(
            center: .init(latitude: centerLat, longitude: centerLon),
            span: .init(latitudeDelta: dLat, longitudeDelta: dLon)
        ))
    }

    // MARK: - Confidence badge

    @ViewBuilder
    private var confidenceBadge: some View {
        let pct = Int((position.confidence * 100).rounded())
        let label: String = {
            switch position.source {
            case .poiKb, .poiOnline: return "权威 POI"
            case .poiUgc:            return "用户验证"
            case .poiIndoor:         return "室内热点"
            case .sfmIos:            return "漫游验证"
            case .sfmWeb:            return "估算路径"
            case .triangulated:      return "远景三角化"
            case .recon3d:           return "3D 重建"
            case .llmRelative:       return "AI 推断"
            }
        }()
        Text("\(label) · \(pct)%")
            .font(.caption2.weight(.medium))
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color.secondary.opacity(0.15),
                        in: Capsule(style: .continuous))
            .foregroundStyle(.secondary)
    }
}
