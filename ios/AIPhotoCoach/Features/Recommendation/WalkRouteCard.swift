// WalkRouteCard.swift (W3.2)
//
// Renders a WalkRoute as a MapKit polyline + collapsible step list. The
// shot card embeds this when a recommended absolute ShotPosition has a
// walk_route attached.

import SwiftUI
import MapKit

struct WalkRouteCard: View {
    let userLat: Double
    let userLon: Double
    let target: ShotPosition
    let route: WalkRoute

    @State private var stepsExpanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "figure.walk")
                Text(String(format: "步行 %.0f m · %.1f 分钟",
                            route.distanceM, route.durationMin))
                    .font(.subheadline.weight(.medium))
                Spacer()
                Text("路径来源：\(route.provider ?? "amap")")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            mapView
                .frame(height: 160)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            DisclosureGroup(isExpanded: $stepsExpanded) {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(Array(route.steps.enumerated()), id: \.offset) { _, step in
                        HStack(alignment: .top) {
                            Image(systemName: "arrow.turn.up.right")
                                .foregroundStyle(.tint)
                            VStack(alignment: .leading) {
                                Text(step.instructionZh)
                                    .font(.caption)
                                Text(String(format: "%.0f m · %.0fs",
                                            step.distanceM, step.durationS))
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                .padding(.top, 6)
            } label: {
                Text(stepsExpanded ? "收起步骤" : "展开 \(route.steps.count) 步")
                    .font(.caption)
            }
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    @ViewBuilder
    private var mapView: some View {
        if let lat = target.lat, let lon = target.lon {
            let mid = CLLocationCoordinate2D(
                latitude: (userLat + lat) / 2,
                longitude: (userLon + lon) / 2,
            )
            let span = max(abs(userLat - lat), abs(userLon - lon)) * 2.5 + 0.0005
            Map(initialPosition: .region(MKCoordinateRegion(
                center: mid,
                span: MKCoordinateSpan(latitudeDelta: span, longitudeDelta: span),
            ))) {
                Marker("你", coordinate: CLLocationCoordinate2D(latitude: userLat, longitude: userLon))
                    .tint(.blue)
                Marker(target.nameZh ?? "机位", coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon))
                    .tint(.orange)
                if let coords = decodePolyline(route.polyline) {
                    MapPolyline(coordinates: coords)
                        .stroke(.blue, lineWidth: 4)
                }
            }
        } else {
            Color.gray.opacity(0.1)
        }
    }

    /// Permissive parser for both "lon,lat;lon,lat;..." (AMap-style raw) and
    /// straight-line two-point fallbacks. Doesn't decode AMap's polyline6
    /// — that would need a 30-line decoder we can add later.
    private func decodePolyline(_ s: String) -> [CLLocationCoordinate2D]? {
        guard !s.isEmpty else { return nil }
        var out: [CLLocationCoordinate2D] = []
        for chunk in s.split(separator: ";") {
            let parts = chunk.split(separator: ",")
            if parts.count >= 2,
               let lon = Double(parts[0]), let lat = Double(parts[1]) {
                out.append(CLLocationCoordinate2D(latitude: lat, longitude: lon))
            }
        }
        return out.count >= 2 ? out : nil
    }
}
