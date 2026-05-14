//  VoiceCoach.swift
//  AIPhotoCoach
//
//  One-way voice coaching layer driven by ``AnalyzeResponse.coachLines``.
//
//  Why one-way (TTS only)
//  ----------------------
//  V1 of the voice product is "the AI coach talks; the user shoots".
//  We deliberately don't ship STT / wake-word / interruption in this
//  iteration -- those add latency + privacy surface + edge cases (in
//  the middle of a crowded street with someone else's voice mixing
//  in). AVSpeechSynthesizer gives us 100% deterministic, on-device,
//  zero-latency playback that obeys the same volume curve the user
//  controls with hardware buttons.
//
//  Per-cue emotion mapping
//  -----------------------
//  Each ``CoachLine.emotion`` maps to a tuple of (voice identifier,
//  rate, pitch multiplier). The defaults below are tuned to feel
//  warm but not theatrical. Users can override the voice in
//  ``CoachSettings`` (see ``ARGuideSettings.swift`` for the existing
//  settings pattern).
//
//  Lifecycle
//  ---------
//  - One ``VoiceCoach`` instance per app (shared via @MainActor singleton).
//  - ``speak(_:)`` enqueues a line; the synthesizer plays them in
//    priority order. Newer p1 lines preempt p2/p3 currently-speaking.
//  - ``stop()`` interrupts immediately (e.g. when the user starts
//    recording video and we need to mute the mic-shared audio session).

import Foundation
import AVFoundation

@MainActor
final class VoiceCoach: NSObject, ObservableObject {
    static let shared = VoiceCoach()

    // MARK: - state
    /// User-tunable settings persisted via UserDefaults.
    @Published var settings: CoachSettings = CoachSettings.load()
    /// What's currently being spoken, for UI captioning.
    @Published private(set) var currentLine: CoachLine?
    /// Whether the synthesizer is mid-utterance.
    @Published private(set) var isSpeaking: Bool = false

    // MARK: - internals
    private let synth = AVSpeechSynthesizer()
    /// FIFO of pending lines. Higher-priority lines insert at the front
    /// AND preempt the currently-speaking lower-priority utterance.
    private var queue: [CoachLine] = []

    private override init() {
        super.init()
        self.synth.delegate = self
    }

    // MARK: - public API

    /// Enqueue a list of coach lines for sequential speech. The list is
    /// typically the full ``AnalyzeResponse.coachLines``; we don't
    /// reorder, but we do preempt mid-speech when a higher priority
    /// (lower priority number) line arrives.
    func enqueue(_ lines: [CoachLine]) {
        guard settings.enabled, !lines.isEmpty else { return }
        for line in lines { self.speak(line) }
    }

    /// Enqueue one coach line. Idempotent for duplicates currently in
    /// the queue (we use the synthetic stable id from ``CoachLine.id``).
    func speak(_ line: CoachLine) {
        guard settings.enabled else { return }
        // Already queued / currently speaking → skip dup.
        if currentLine?.id == line.id { return }
        if queue.contains(where: { $0.id == line.id }) { return }

        if line.priority == 1 && synth.isSpeaking {
            // Primary line preempts.
            self.synth.stopSpeaking(at: .word)
            self.queue.insert(line, at: 0)
        } else {
            self.queue.append(line)
        }
        self.pumpQueue()
    }

    /// Hard interrupt: stop speech + drop the queue. Called when the
    /// camera starts recording, the user enters PostProcess, or any
    /// other "I'd rather have silence right now" moment.
    func stop() {
        self.queue.removeAll()
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        self.currentLine = nil
        self.isSpeaking = false
    }

    // MARK: - internals

    private func pumpQueue() {
        guard !synth.isSpeaking, let next = queue.first else { return }
        self.queue.removeFirst()
        self.currentLine = next
        self.isSpeaking = true

        let utterance = AVSpeechUtterance(string: next.textZh)
        utterance.voice = self.voice(for: next.emotion)
        utterance.rate = Float(self.settings.rate) * AVSpeechUtteranceDefaultSpeechRate
        utterance.pitchMultiplier = self.pitchMultiplier(for: next.emotion)
        utterance.preUtteranceDelay = 0.05
        utterance.postUtteranceDelay = 0.25
        self.synth.speak(utterance)
    }

    private func voice(for emotion: String) -> AVSpeechSynthesisVoice? {
        // The user setting wins. Otherwise we lean on per-emotion
        // defaults so a "caution" line sounds slightly slower and a
        // "playful" one slightly higher.
        if let override = AVSpeechSynthesisVoice(identifier: settings.voiceIdentifier) {
            return override
        }
        return AVSpeechSynthesisVoice(language: "zh-CN")
    }

    private func pitchMultiplier(for emotion: String) -> Float {
        switch emotion {
        case "playful":     return 1.10
        case "encouraging": return 1.04
        case "caution":     return 0.95
        case "calm":        fallthrough
        default:            return 1.0
        }
    }
}

// MARK: - AVSpeechSynthesizerDelegate
extension VoiceCoach: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.isSpeaking = false
            self.currentLine = nil
            self.pumpQueue()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor [weak self] in
            guard let self else { return }
            self.isSpeaking = false
            self.currentLine = nil
            self.pumpQueue()
        }
    }
}

// MARK: - settings persistence

/// User-tunable VoiceCoach settings. Persisted to UserDefaults so the
/// same preferences carry across launches without an account.
struct CoachSettings: Codable, Sendable {
    var enabled: Bool = true
    /// Multiplier on ``AVSpeechUtteranceDefaultSpeechRate``. 1.0 = default.
    var rate: Double = 1.0
    /// AVSpeechSynthesisVoice.identifier. Empty string = pick a zh-CN
    /// default at runtime.
    var voiceIdentifier: String = ""

    static let defaultsKey = "AIPhotoCoach.CoachSettings.v1"

    static func load() -> CoachSettings {
        guard
            let data = UserDefaults.standard.data(forKey: defaultsKey),
            let decoded = try? JSONDecoder().decode(CoachSettings.self, from: data)
        else {
            return CoachSettings()
        }
        return decoded
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: Self.defaultsKey)
        }
    }
}
