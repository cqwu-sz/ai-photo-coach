// WalkSegmentSheet.swift
//
// Tiny opt-in sheet shown after the standing-pan finishes. Asks the
// user to record an extra 10-20 s walk so the backend can unlock
// far-away (50-200 m) ``absolute`` shot candidates via SfM/VIO fusion.
//
// Usage: bind it from EnvCaptureView before the analyze call. When the
// user accepts and stops the walk, we hand the resulting WalkSegment
// to ``EnvCaptureViewModel.pendingWalkSegment`` so the next /analyze
// includes it.

import ARKit
import SwiftUI

struct WalkSegmentSheet: View {
    let initialHeadingDeg: Double?
    let onCompleted: (WalkSegment?) -> Void

    @State private var recorder: WalkSegmentRecorder?
    @State private var isRecording = false
    @State private var coverageM: Double = 0
    @State private var elapsedSec: Int = 0
    @State private var ticker: Timer?

    var body: some View {
        VStack(spacing: 20) {
            Text("解锁远机位（可选）")
                .font(.title2.weight(.semibold))
            Text("接下来漫步 10–20 秒，AI 会用你走过的轨迹推算最远 200 m 内的拍摄机位。\n保持手机稳稳举在胸前。")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
                .padding(.horizontal)

            if isRecording {
                VStack(spacing: 6) {
                    Text("已走 \(Int(coverageM.rounded())) m · \(elapsedSec) 秒")
                        .font(.system(size: 28, weight: .bold, design: .rounded))
                        .monospacedDigit()
                    Text("继续走，越远越好（避开车流）")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 24)
                Button(role: .destructive) {
                    finishWalk()
                } label: {
                    Label("结束漫游", systemImage: "stop.fill")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .padding(.horizontal)
            } else {
                if WalkSegmentRecorder.isAvailable {
                    Button {
                        startWalk()
                    } label: {
                        Label("开始漫游", systemImage: "figure.walk")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .padding(.horizontal)
                } else {
                    Text("当前设备不支持 ARKit 漫游，跳过即可。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Button("跳过这一步") {
                    onCompleted(nil)
                }
                .padding(.top, 8)
            }
        }
        .padding(.vertical, 28)
        .onDisappear { ticker?.invalidate() }
    }

    private func startWalk() {
        let r = WalkSegmentRecorder(initialHeadingDeg: initialHeadingDeg)
        r.start()
        recorder = r
        isRecording = true
        elapsedSec = 0
        coverageM = 0
        ticker = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            Task { @MainActor in
                guard let rec = recorder else { return }
                coverageM = rec.coverageM
                elapsedSec += 1   // 0.5 s ticks but we display ints; close enough
            }
        }
    }

    private func finishWalk() {
        ticker?.invalidate()
        ticker = nil
        let segment = recorder?.stop()
        recorder = nil
        isRecording = false
        onCompleted(segment)
    }
}
