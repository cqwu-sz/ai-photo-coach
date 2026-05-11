// Recon3DView.swift (W9.3)
//
// Triggers a /recon3d/start job, polls /recon3d/{job_id}, and renders a
// progress bar + the resulting sparse-point summary.

import SwiftUI

struct Recon3DJobStatus: Codable, Sendable {
    let jobId: String
    let status: String
    let progress: Double
    let error: String?
    let model: SparseModel?

    enum CodingKeys: String, CodingKey {
        case jobId = "job_id"
        case status, progress, error, model
    }
}

@MainActor
final class Recon3DController: ObservableObject {
    @Published var jobId: String?
    @Published var status: String = "idle"
    @Published var progress: Double = 0
    @Published var model: SparseModel?
    @Published var errorMessage: String?

    private let baseURL: URL
    init(baseURL: URL) { self.baseURL = baseURL }

    func start(imagesB64: [String], originLat: Double?, originLon: Double?) async {
        let url = baseURL.appendingPathComponent("/recon3d/start")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.addValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "images_b64": imagesB64,
            "origin_lat": originLat as Any? ?? NSNull(),
            "origin_lon": originLon as Any? ?? NSNull(),
        ]
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        do {
            let (data, _) = try await URLSession.shared.data(for: req)
            let job = try JSONDecoder().decode(Recon3DJobStatus.self, from: data)
            self.jobId = job.jobId
            self.status = job.status
            self.progress = job.progress
            await poll()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func poll() async {
        guard let id = jobId else { return }
        let url = baseURL.appendingPathComponent("/recon3d/\(id)")
        for _ in 0..<60 {
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                let job = try JSONDecoder().decode(Recon3DJobStatus.self, from: data)
                self.status = job.status
                self.progress = job.progress
                if job.status == "done" {
                    self.model = job.model
                    return
                }
                if job.status == "error" {
                    self.errorMessage = job.error ?? "unknown"
                    return
                }
            } catch {
                self.errorMessage = error.localizedDescription
                return
            }
        }
    }
}

struct Recon3DView: View {
    @StateObject var controller: Recon3DController
    let imagesB64: [String]
    let originLat: Double?
    let originLon: Double?

    @State private var started = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("3D 重建")
                .font(.title3.weight(.semibold))
            if !started {
                Button {
                    started = true
                    Task { await controller.start(imagesB64: imagesB64,
                                                   originLat: originLat,
                                                   originLon: originLon) }
                } label: {
                    Label("开始 3D 重建", systemImage: "cube.transparent")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            } else {
                ProgressView(value: controller.progress, total: 1.0) {
                    Text("状态：\(controller.status)")
                        .font(.caption)
                }
                .progressViewStyle(.linear)
            }
            if let m = controller.model {
                VStack(alignment: .leading, spacing: 6) {
                    Text("✅ 完成：\(m.pointsCount) 个稀疏点 · \(m.camerasCount) 帧")
                        .font(.caption.weight(.semibold))
                    if let bboxLat = m.bboxLat, let bboxLon = m.bboxLon, bboxLat.count >= 2 {
                        Text(String(format: "覆盖范围：%.5f-%.5f / %.5f-%.5f",
                                    bboxLat[0], bboxLat[1], bboxLon[0], bboxLon[1]))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            if let err = controller.errorMessage {
                Text("失败：\(err)")
                    .font(.caption)
                    .foregroundStyle(.red)
            }
        }
        .padding()
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }
}
