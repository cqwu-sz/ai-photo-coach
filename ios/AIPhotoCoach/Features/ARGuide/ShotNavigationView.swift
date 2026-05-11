// ShotNavigationView.swift (W8.1 + Ghost Avatar productization pass)
//
// Three-stage AR experience: PickShot -> NavMode (走位) -> FrameMode
// (取景) -> ParamMode (参数). Uses ARGeoAnchor when available + GPS is
// good, otherwise falls back to ARWorldAnchor + on-screen compass arrow.
//
// Ghost-Avatar additions (this file is the single integration surface):
//   * Stage 2 mounts a translucent digital human at the shot's azimuth
//     + distance, raycast onto the ground.
//   * Shutter notifications hide every overlay so the captured frame is
//     clean.
//   * Indoor shots skip GeoAnchor; ground-scan failure hides the ghost.
//   * Multi-person layouts mount one ghost per pose.person.
//   * Real-human arrival detection drives haptics + a green halo.
//   * Drag-to-fine-tune updates a "实际 vs 推荐" distance HUD; reset
//     button restores the recommended position.
//   * Long idle drops to 30fps; nav stage doesn't load avatar meshes
//     to save battery.
//   * Privacy mode + first-run intro + AR-unavailable 2D fallback.

import SwiftUI
import ARKit
import AVFoundation
import Photos
import RealityKit
import CoreLocation

extension Notification.Name {
    /// Posted by the photo-capture button right before AVCapturePhotoOutput
    /// fires. ShotNavigationModel listens and hides the AR overlay so
    /// the resulting image has no ghost / disc / arrow in it.
    static let arGuideWillCapture = Notification.Name("arGuide.willCapture")
    /// Posted ~300ms after capture completes; the model re-shows the
    /// overlay so the user can shoot again without re-anchoring.
    static let arGuideDidCapture  = Notification.Name("arGuide.didCapture")
}

@MainActor
final class ShotNavigationModel: NSObject, ObservableObject {
    enum Stage { case nav, framing, params }

    @Published var stage: Stage = .nav
    @Published var distanceM: Float = .infinity
    @Published var liveHUD = ShotParameterHUD.Live(
        zoomFactor: 1.0, ev: 0.0, subjectDistanceM: nil,
    )
    /// Set when a horizontal plane has not yet been detected. Drives
    /// the "请扫一下地面" hint and keeps the ghost hidden so it never
    /// appears floating in mid-air.
    @Published var groundReady: Bool = false
    /// Distance from the recommended position to the user's drag-tweaked
    /// position. Used by the HUD to surface "拖偏了" warnings.
    @Published var dragOffsetM: Float = 0
    /// Set to true while the shutter is auto-hiding the overlay.
    @Published var capturing: Bool = false
    /// Set when a real human is detected within the ghost's standing
    /// circle. Drives a green halo + haptic.
    @Published var modelArrived: Bool = false
    /// True when the user has activated "只显示脚印" privacy mode.
    @Published var privacyMode: Bool = false

    let target: ShotPosition
    // nonisolated: ARSessionDelegate (nonisolated) needs the recommended
    // distance to scale depth thresholds. Stored `let` of a Sendable type.
    nonisolated let shot: ShotRecommendation
    let userStartLat: Double
    let userStartLon: Double

    private weak var arView: ARView?
    private var baseAnchor: AnchorEntity?
    private var tweakAnchor: AnchorEntity?
    // nonisolated(unsafe): read from the ARSessionDelegate queue when
    // computing the compass arrow. Mutations only happen on the main
    // actor (attachMarker) so there's no contention in practice.
    nonisolated(unsafe) private var marker: ShotMarkerEntity?
    private var ghosts: [GhostAvatarEntity] = []
    private var arrivalHaloEntities: [ModelEntity] = []
    private let arrivalRadiusM: Float = 3.0
    private(set) var usingGeoAnchor = false
    private(set) var anchorMode: AnchorMode = .world

    enum AnchorMode { case geo, world, indoor }

    private var willCaptureObserver: NSObjectProtocol?
    private var didCaptureObserver: NSObjectProtocol?
    private var lastInteractionAt: Date = Date()
    private var idleThrottleTimer: Timer?

    /// Captures every meaningful state change so we can ship a snapshot
    /// to /feedback after the photo is taken.
    // Externally mutable so the SwiftUI view layer can mark handoff/
    // arrival from outside the model (see triggerArrivalFlashAndMaybeHandoff).
    var telemetry = ARGuideTelemetry()

    /// Throttle the depth-based human estimation to ~5 Hz; running it
    /// every frame is wasteful and arrival is a slow signal anyway.
    /// Read/written from the nonisolated ARSessionDelegate callback —
    /// the callback is ARKit's serial queue so concurrent access is
    /// not actually possible.
    nonisolated(unsafe) private var lastDepthEstimateAt: TimeInterval = 0
    /// Track arrival flips so we can report flicker count in feedback.
    private var arrivalFlipCount: Int = 0
    /// Records the XZ distance at every arrival flip so the backend
    /// can analyse the magnitude of the chatter — flips at 0.45m vs
    /// 1.5m mean very different things for "indoor lighting wonky"
    /// vs "wrong shot recommendation".
    private var arrivalFlipMagnitudes: [Float] = []
    /// Tracks the last reported viewport size so depth back-projection
    /// can use the right `displayTransform`.
    // nonisolated(unsafe): captured by ARSessionDelegate. Writes
    // originate from the SwiftUI layer (MainActor) but the AR queue
    // only reads — single-writer is safe without a lock.
    nonisolated(unsafe) fileprivate var viewportSize: CGSize = .zero
    nonisolated(unsafe) fileprivate var viewportOrientation: UIInterfaceOrientation = .portrait

    /// Pre-resolved Avatar preset id list — populated up front so the
    /// handoff to ARGuideView always has a real id to pass even when
    /// this view itself never mounts ghosts.
    private(set) var resolvedPresetIds: [String] = []

    /// Cached ARView snapshot taken at the moment the user pressed the
    /// shutter — paired with the Photos library's latest asset in the
    /// post-capture trust sheet so users can directly compare "what
    /// the AR overlay looked like" vs "what actually got saved".
    @Published var lastARSnapshot: UIImage? = nil
    /// Wall-clock time of the last shutter press. Used by the trust
    /// sheet to scope the Photos query to exactly this capture rather
    /// than a sliding 60s window (which could pick up an unrelated
    /// earlier photo if the user dawdled).
    @Published var lastShutterAt: Date? = nil
    /// True once `resolvedPresetIds` has been populated; the SwiftUI
    /// view observes this to enable the handoff link only after we
    /// know which avatar to hand to `ARGuideView`.
    @Published var presetsResolved: Bool = false
    /// P1-8.3 — set when GPS accuracy is so poor we should warn the
    /// user that the AR position may be off by 5-10 m.
    @Published var weakGpsBanner: Bool = false
    /// P1-8.4 — yaw (in screen degrees) toward the marker when it
    /// is currently outside the viewport. nil when in view.
    @Published var compassArrowDeg: Double? = nil

    init(shot: ShotRecommendation,
         target: ShotPosition,
         userLat: Double,
         userLon: Double) {
        self.shot = shot
        self.target = target
        self.userStartLat = userLat
        self.userStartLon = userLon
        super.init()
        installShutterObservers()
        startIdleThrottleTimer()
        privacyMode = UserDefaults.standard.bool(forKey: ARGuideSettingsKeys.privacyMode)
        Task { [weak self] in await self?.resolvePresets() }
    }

    /// Resolve the avatar preset ids up front, so handoff to ARGuideView
    /// always has a real id. Dedups consecutive picks (couples/family)
    /// so we never get "two identical twins" in a multi-person shot.
    private func resolvePresets() async {
        let manifest = await AvatarManifest.shared.load()
        let presets = manifest?.presets ?? []
        let count = max(shot.poses.first?.personCount ?? 1,
                        shot.poses.first?.persons.count ?? 1, 1)
        var picks: [String] = []
        var used: Set<String> = []
        for i in 0..<count {
            var picked = AvatarPicker.pick(personIndex: i, from: presets)
                ?? "female_youth_18"
            if used.contains(picked) {
                // Walk through the preset list to find a different one.
                if let alt = presets.first(where: { !used.contains($0.id) }) {
                    picked = alt.id
                }
            }
            used.insert(picked)
            picks.append(picked)
        }
        resolvedPresetIds = picks
        telemetry.presetIds = picks
        presetsResolved = true
    }

    /// Cleanly stop the ARSession — used right before handing off to
    /// ARGuideView so the two ARViews don't try to drive the camera
    /// concurrently (which on iOS leads to a black or frozen feed).
    func pauseSession() {
        arView?.session.pause()
    }

    deinit {
        if let willCaptureObserver {
            NotificationCenter.default.removeObserver(willCaptureObserver)
        }
        if let didCaptureObserver {
            NotificationCenter.default.removeObserver(didCaptureObserver)
        }
        idleThrottleTimer?.invalidate()
    }

    func attach(_ arView: ARView) {
        self.arView = arView
        let cfg = ARWorldTrackingConfiguration()
        cfg.planeDetection = [.horizontal]
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.personSegmentationWithDepth) {
            cfg.frameSemantics.insert(.personSegmentationWithDepth)
        }

        // Indoor shots can carry GPS coords from the POI database, but
        // those are coarse rooftop-projected centroids. Forcing
        // GeoAnchor would drop the ghost in the wrong room. Always
        // walk indoor through the world-anchor path and surface a hint.
        if target.kind == .indoor {
            anchorMode = .indoor
            telemetry.anchorMode = "indoor"
            installWorldAnchor()
        } else if ARGeoTrackingConfiguration.isSupported {
            ARGeoTrackingConfiguration.checkAvailability { [weak self] available, _ in
                guard let self else { return }
                if available, let lat = self.target.lat, let lon = self.target.lon {
                    self.installGeoAnchor(lat: lat, lon: lon)
                } else {
                    self.installWorldAnchor()
                }
            }
        } else {
            installWorldAnchor()
        }
        arView.session.run(cfg)
        arView.session.delegate = self
    }

    private func installGeoAnchor(lat: Double, lon: Double) {
        guard let arView else { return }
        usingGeoAnchor = true
        anchorMode = .geo
        telemetry.anchorMode = "geo"
        let geo = ARGeoAnchor(coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon))
        arView.session.add(anchor: geo)
        let base = AnchorEntity(anchor: geo)
        arView.scene.addAnchor(base)
        installContent(on: base)
        // P1-8.3 — sample GPS accuracy via CoreLocation; if > 20m, warn.
        startGpsQualityMonitor()
    }

    private var gpsManager: CLLocationManager?
    private var gpsDelegate: GpsDelegate?

    private func startGpsQualityMonitor() {
        let mgr = CLLocationManager()
        let delegate = GpsDelegate { [weak self] accuracy in
            Task { @MainActor in
                self?.weakGpsBanner = accuracy > 20
            }
        }
        mgr.delegate = delegate
        mgr.desiredAccuracy = kCLLocationAccuracyBest
        mgr.startUpdatingLocation()
        gpsManager = mgr
        gpsDelegate = delegate
    }

    private final class GpsDelegate: NSObject, CLLocationManagerDelegate {
        let onAccuracy: (CLLocationAccuracy) -> Void
        init(onAccuracy: @escaping (CLLocationAccuracy) -> Void) {
            self.onAccuracy = onAccuracy
        }
        func locationManager(_ manager: CLLocationManager,
                             didUpdateLocations locations: [CLLocation]) {
            guard let loc = locations.last else { return }
            onAccuracy(loc.horizontalAccuracy)
        }
    }

    private func installWorldAnchor() {
        guard let arView else { return }
        usingGeoAnchor = false
        if anchorMode != .indoor {
            anchorMode = .world
            telemetry.anchorMode = "world"
        }
        let dx = enuOffsetEast()
        let dz = enuOffsetNorth()
        let base = AnchorEntity(world: SIMD3<Float>(Float(dx), 0, -Float(dz)))
        arView.scene.addAnchor(base)
        installContent(on: base)
    }

    private func installContent(on base: AnchorEntity) {
        baseAnchor = base
        let tweak = AnchorEntity()
        base.addChild(tweak)
        tweakAnchor = tweak
        attachMarker(to: tweak)
        // Defer ghost mesh load until we actually reach the framing
        // stage; saves an avatar load + a USDZ skeletal animation cost
        // on devices that never reach the spot.
    }

    private func attachMarker(to anchor: AnchorEntity) {
        let m = ShotMarkerEntity(arrivalRadiusM: arrivalRadiusM)
        m.onArrival = { [weak self] in
            Task { @MainActor in self?.arriveFraming() }
        }
        anchor.addChild(m)
        marker = m
    }

    private func attachGhostAvatars(to anchor: AnchorEntity) {
        guard ghosts.isEmpty else { return }  // already mounted
        let firstPose = shot.poses.first
        let persons = firstPose?.persons ?? []
        let count = max(firstPose?.personCount ?? 1, persons.count, 1)
        for i in 0..<count {
            let g = GhostAvatarEntity(mode: GhostAvatarEntity.RenderMode.current)
            g.isEnabled = false
            g.position = multiPersonOffset(index: i, total: count,
                                           layout: firstPose?.layout)
            anchor.addChild(g)
            ghosts.append(g)
        }
        Task { [weak self] in
            await self?.bootstrapGhosts(persons: persons, personCount: count)
        }
    }

    /// Multi-person spacing on the local XZ plane. Couples sit ~0.5m
    /// apart, families fan out in an arc.
    ///
    /// IMPORTANT — subject anchor semantics: `tweakAnchor` is the
    /// "subject group center" — `shot.angle.distanceM` is the camera's
    /// recommended distance to *that center*, not to any individual
    /// ghost. We therefore distribute ghosts symmetrically around the
    /// origin so the group's centroid stays at distance `distanceM`
    /// regardless of personCount. Individual ghosts may be slightly
    /// closer or further than `distanceM`; that's correct because the
    /// recommendation is about subject-group framing, not per-person
    /// focus distance.
    private func multiPersonOffset(index: Int, total: Int, layout: Layout?) -> SIMD3<Float> {
        guard total > 1 else { return .zero }
        let spacing: Float = 0.55
        let centered = Float(index) - Float(total - 1) / 2.0
        switch layout {
        case .sideBySide, .line, .single, .none:
            return SIMD3<Float>(centered * spacing, 0, 0)
        case .highLowOffset:
            // Front row vs back row by parity.
            let row = index % 2
            return SIMD3<Float>(centered * spacing, 0, Float(row) * spacing * 0.5)
        case .triangle, .vFormation, .diagonal:
            return SIMD3<Float>(centered * spacing, 0, abs(centered) * spacing * 0.5)
        case .cluster, .custom:
            // Tight grid; even index front, odd back.
            let row = index % 2
            let col = index / 2
            return SIMD3<Float>((Float(col) - 0.5) * spacing, 0, Float(row) * spacing * 0.6)
        case .circle:
            let angle = Float(index) * 2 * .pi / Float(total)
            return SIMD3<Float>(cos(angle) * spacing, 0, sin(angle) * spacing)
        }
    }

    private func bootstrapGhosts(persons: [PersonPose], personCount: Int) async {
        let manifest = await AvatarManifest.shared.load()
        // Make sure the up-front resolver has finished. If it hasn't
        // (network slow), wait briefly — in the worst case we fall
        // through with whatever we managed to resolve.
        if !presetsResolved {
            await resolvePresets()
        }
        for (i, ghost) in ghosts.enumerated() {
            // Per-person tinting in privacy mode keeps multi-person
            // shots distinguishable: in plain "脚印只" mode we'd
            // otherwise have N identical discs and the photographer
            // can't tell whom each spot belongs to.
            ghost.tint(privacyTint(for: i, total: ghosts.count))

            // Privacy mode skips the avatar mesh entirely; the disc +
            // arrow in GhostAvatarEntity stay as the only visible
            // markers.
            guard !privacyMode else { continue }

            let presetId = i < resolvedPresetIds.count
                ? resolvedPresetIds[i]
                : "female_youth_18"
            let mounted = await ghost.loadAndMount(presetId: presetId)
            if !mounted { continue }
            let person = i < persons.count ? persons[i] : nil
            let poseId = person.map { PosePresets.pick(for: $0) }
            await ghost.setPose(poseId: poseId,
                                personCount: personCount,
                                manifest: manifest?.poseToMixamo)
        }
        if let arView, let tweak = tweakAnchor {
            let local = GhostAvatarPlacement.initialLocalTransform(in: arView, shot: shot)
            tweak.transform = local
        }
    }

    private func privacyTint(for index: Int, total: Int) -> UIColor {
        let palette: [UIColor] = [
            .systemTeal, .systemPink, .systemOrange,
            .systemPurple, .systemYellow, .systemGreen,
        ]
        guard total > 1 else { return .systemTeal }
        return palette[index % palette.count]
    }

    func arriveFraming() {
        guard stage == .nav else { return }
        stage = .framing
        marker?.isEnabled = false
        // P1-8.2 — short audio "ding" + heavier success haptic on arrival.
        ARGuideSpeech.speak("到位了")
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        // P2-10.3 — funnel event: arrived.
        Task { [weak self] in
            guard let self else { return }
            let baseURL = (UserDefaults.standard.string(forKey: "apiBaseURL")
                            .flatMap(URL.init)) ?? URL(string: "http://127.0.0.1:8000")!
            let uploader = FeedbackUploader(baseURL: baseURL)
            await uploader.recordArNav(event: "arrived",
                                        payload: ["shot_id": self.shot.id])
        }
        // When handoff-to-ARGuideView is enabled (default), this view
        // stops rendering its own ghost so we don't end up with two
        // avatars on screen during the transition. The view layer
        // observes the stage change and pushes ARGuideView instead.
        let handoff = UserDefaults.standard.object(
            forKey: ARGuideSettingsKeys.handoffToGuide
        ) as? Bool ?? true
        if !handoff {
            if let tweak = tweakAnchor, ghosts.isEmpty {
                // Always attach — bootstrapGhosts decides per-ghost
                // whether to mount the avatar mesh based on privacy
                // mode. The disc + arrow markers are useful even in
                // privacy mode, especially for multi-person shots.
                attachGhostAvatars(to: tweak)
            }
            ghosts.forEach { $0.isEnabled = true }
        }
        telemetry.arrivedAt = Date()
        UINotificationFeedbackGenerator().notificationOccurred(.success)
    }

    /// Apply a screen-space drag delta to the tweak anchor. Updates
    /// dragOffsetM so the HUD can warn when the user has nudged the
    /// shot away from the recommendation by more than 20%.
    func dragGhost(in arView: ARView, from start: CGPoint, to end: CGPoint) {
        guard let tweak = tweakAnchor,
              let delta = GhostAvatarPlacement.groundDelta(in: arView, from: start, to: end)
        else { return }
        tweak.transform.translation += delta
        recomputeDragOffset()
        registerInteraction()
    }

    /// Restore the recommended shot position with a smooth animation
    /// so the user sees the ghost glide back rather than teleporting.
    func resetGhostPosition() {
        guard let arView, let tweak = tweakAnchor else { return }
        let local = GhostAvatarPlacement.initialLocalTransform(in: arView, shot: shot)
        tweak.move(to: local, relativeTo: tweak.parent, duration: 0.4,
                   timingFunction: .easeInOut)
        dragOffsetM = 0
        telemetry.resetCount += 1
        registerInteraction()
    }

    /// Re-measure offset between the ghost's current world position and
    /// where the recommendation says it should be.
    private func recomputeDragOffset() {
        guard let arView, let tweak = tweakAnchor else { return }
        let target = GhostAvatarPlacement.initialLocalTransform(in: arView, shot: shot)
        let cur = tweak.transform.translation
        let want = target.translation
        let dx = cur.x - want.x
        let dz = cur.z - want.z
        dragOffsetM = sqrt(dx * dx + dz * dz)
    }

    /// Fixed 0.5m absolute threshold — relative thresholds got
    /// confusing across long-focal vs selfie distances.
    var dragWarningExceeded: Bool {
        return dragOffsetM > 0.5
    }

    func togglePrivacyMode() {
        privacyMode.toggle()
        UserDefaults.standard.set(privacyMode, forKey: ARGuideSettingsKeys.privacyMode)
        // Privacy on -> hide the mesh on every existing ghost but keep
        // the disc+arrow visible. Privacy off + we have ghosts mounted
        // -> we'd need to reload meshes on the fly. For simplicity we
        // tear down and let arriveFraming rebuild on next attach.
        for g in ghosts {
            g.isEnabled = stage != .nav
            g.setMeshVisible(!privacyMode)
        }
    }

    // MARK: - Shutter integration

    func performShutter() {
        guard !capturing else { return }
        // Snapshot the AR view *before* hiding the overlay so the
        // captured frame still contains the ghost — that's exactly
        // what the trust sheet needs to compare against the real
        // (clean) photo from the camera roll. Lock the trust window
        // start time at this exact moment so when the sheet appears
        // later it pulls the photo that was taken now, not whatever
        // was taken in the last 60s.
        lastShutterAt = Date()
        captureSnapshot()
        capturing = true
        NotificationCenter.default.post(name: .arGuideWillCapture, object: shot)
        // After 300ms re-show the ghost; the actual photo capture lives
        // in the real-shoot screen which posts arGuideDidCapture when
        // it's done. As a safety net we also restore on our own timer
        // in case nobody posts it (e.g. in this navigation-only screen).
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 600_000_000)
            self?.capturing = false
            self?.restoreOverlayAfterCapture()
        }
        telemetry.captureCount += 1
    }

    /// Try `ARView.snapshot` first — when it returns nil (it does on
    /// some iOS 17+ device combinations with people segmentation
    /// enabled) fall back to a direct UIGraphicsImageRenderer pass on
    /// the view's layer. Either path yields a UIImage so the trust
    /// sheet always has *something* to show.
    private func captureSnapshot() {
        guard let arView else { return }
        arView.snapshot(saveToHDR: false) { [weak self, weak arView] image in
            Task { @MainActor in
                guard let self else { return }
                if let image {
                    self.lastARSnapshot = image
                    return
                }
                guard let arView else { return }
                let renderer = UIGraphicsImageRenderer(bounds: arView.bounds)
                let fallback = renderer.image { ctx in
                    arView.drawHierarchy(in: arView.bounds,
                                         afterScreenUpdates: false)
                }
                self.lastARSnapshot = fallback
            }
        }
    }

    private func installShutterObservers() {
        willCaptureObserver = NotificationCenter.default.addObserver(
            forName: .arGuideWillCapture, object: nil, queue: .main,
        ) { [weak self] _ in
            Task { @MainActor in self?.hideOverlayForCapture() }
        }
        didCaptureObserver = NotificationCenter.default.addObserver(
            forName: .arGuideDidCapture, object: nil, queue: .main,
        ) { [weak self] _ in
            Task { @MainActor in self?.restoreOverlayAfterCapture() }
        }
    }

    private func hideOverlayForCapture() {
        capturing = true
        for g in ghosts { g.isEnabled = false }
        marker?.isEnabled = false
        for h in arrivalHaloEntities { h.isEnabled = false }
    }

    private func restoreOverlayAfterCapture() {
        capturing = false
        if (stage == .framing || stage == .params), !privacyMode {
            for g in ghosts { g.isEnabled = true }
            for h in arrivalHaloEntities { h.isEnabled = modelArrived }
        } else if stage == .nav {
            marker?.isEnabled = true
        }
    }

    // MARK: - Idle throttle

    private func registerInteraction() {
        lastInteractionAt = Date()
        // RealityKit's ARView does not expose preferredFramesPerSecond
        // the way SCNView does. The idle-throttle path is therefore a
        // no-op on RK; we keep `lastInteractionAt` updated so we still
        // know if/when the user is idle for telemetry purposes.
    }

    private func startIdleThrottleTimer() {
        idleThrottleTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) {
            [weak self] _ in
            Task { @MainActor in self?.checkIdle() }
        }
    }

    private func checkIdle() {
        guard arView != nil else { return }
        let idle = Date().timeIntervalSince(lastInteractionAt)
        // Same caveat as registerInteraction: RealityKit ARView has no
        // preferredFramesPerSecond. We still surface the idle signal
        // via telemetry so the dashboard can flag long, unintended
        // sessions.
        if idle > 90 {
            telemetry.throttledToLowFps = true
        }
    }

    /// Crude flat-earth ENU offset from start to target (metres).
    private func enuOffsetEast() -> Double {
        guard let lon = target.lon else { return 0 }
        let cos_lat = max(0.05, cos(userStartLat * .pi / 180))
        return (lon - userStartLon) * 111_320.0 * cos_lat
    }
    private func enuOffsetNorth() -> Double {
        guard let lat = target.lat else { return 0 }
        return (lat - userStartLat) * 111_320.0
    }

    // MARK: - Arrival detection

    /// Called by ARSessionDelegate every frame with an estimate of the
    /// closest real human's world position. Multi-person mode: we
    /// compare against every ghost and arrive when the closest one is
    /// within an adaptive radius derived from the ghost's actual
    /// bounding box (a small kid avatar should arrive at a tighter
    /// radius than a tall adult). This ignores which ghost is which —
    /// a couple shoot doesn't need per-person identity, just "two
    /// humans roughly in their spots".
    fileprivate func updatePersonProximity(_ personPos: SIMD3<Float>?,
                                           method: String) {
        guard let personPos, !ghosts.isEmpty else {
            updateModelArrived(false)
            return
        }
        var closest: Float = .infinity
        var closestGhost: GhostAvatarEntity?
        var closestRadius: Float = 0.5
        for ghost in ghosts {
            let g = ghost.position(relativeTo: nil)
            let dx = personPos.x - g.x
            let dz = personPos.z - g.z
            let xz = sqrt(dx * dx + dz * dz)
            if xz < closest {
                closest = xz
                closestGhost = ghost
                closestRadius = arrivalRadius(for: ghost)
            }
        }
        for g in ghosts {
            g.setProximity(g === closestGhost ? closest : .infinity)
        }
        nearestGhost = closestGhost
        telemetry.lastArrivalMethod = method
        updateModelArrived(closest < closestRadius, magnitude: closest)
    }

    /// Adaptive arrival radius: half the ghost's max horizontal extent
    /// + 0.3m slack. Pre-mount (avatar mesh not yet loaded) we use a
    /// **stable** 0.7m rather than the bounds-query fallback of 0.5m,
    /// because the mesh load happens asynchronously and we don't want
    /// the threshold to jump from 0.5 to ~0.85 the moment the avatar
    /// pops in (causing modelArrived to flicker false→true→false).
    private func arrivalRadius(for ghost: GhostAvatarEntity) -> Float {
        let bounds = ghost.visualBounds(relativeTo: nil)
        let halfExtent = max(bounds.extents.x, bounds.extents.z) * 0.5
        if halfExtent.isFinite, halfExtent > 0.05 {
            return halfExtent + 0.3
        }
        // Mesh hasn't mounted yet — use a slightly larger constant so
        // the post-mount radius can only shrink, not grow.
        return 0.7
    }

    private var nearestGhost: GhostAvatarEntity?

    private func updateModelArrived(_ arrived: Bool, magnitude: Float = 0) {
        guard arrived != modelArrived else { return }
        modelArrived = arrived
        arrivalFlipCount += 1
        // Cap the magnitude history at 32 to keep the telemetry payload
        // small — backend only cares about distribution shape.
        if arrivalFlipMagnitudes.count >= 32 {
            arrivalFlipMagnitudes.removeFirst()
        }
        arrivalFlipMagnitudes.append(magnitude)
        telemetry.arrivalFlipCount = arrivalFlipCount
        telemetry.arrivalFlipMagnitudes = arrivalFlipMagnitudes
        if arrived {
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            installArrivalHalo()
        } else {
            for h in arrivalHaloEntities { h.isEnabled = false }
        }
    }

    private func installArrivalHalo() {
        let target = nearestGhost ?? ghosts.first
        guard let target else { return }
        // Reuse a pre-built halo if one is already in the scene; just
        // re-parent to the ghost the human is actually approaching.
        if let existing = arrivalHaloEntities.first {
            existing.removeFromParent()
            target.addChild(existing)
            existing.isEnabled = true
            return
        }
        let mesh = MeshResource.generateBox(width: 1.2, height: 0.005, depth: 1.2,
                                            cornerRadius: 0.6)
        let mat = UnlitMaterial(color: .systemGreen.withAlphaComponent(0.55))
        let halo = ModelEntity(mesh: mesh, materials: [mat])
        // Sit just above the GhostAvatar's disc (which lives at
        // y=0.01 with a thickness of 0.02 -> top at y=0.02). 0.04
        // gives enough clearance to avoid Z-fighting in mixed
        // lighting and on devices with depth-buffer precision issues.
        halo.position.y = 0.04
        target.addChild(halo)
        arrivalHaloEntities = [halo]
    }
}

extension ShotNavigationModel: ARSessionDelegate {
    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let cam = frame.camera.transform
        let camPos = SIMD3<Float>(cam.columns.3.x, cam.columns.3.y, cam.columns.3.z)
        // Detect at least one horizontal plane to drive the
        // groundReady flag — we won't reveal the ghost without it.
        let hasGroundPlane = frame.anchors.contains { anchor in
            (anchor as? ARPlaneAnchor)?.alignment == .horizontal
        }

        // Throttle depth-based human estimation to ~5 Hz. Pixel scans
        // every frame at 60fps are wasteful; arrival is a slow signal.
        let now = CACurrentMediaTime()
        let shouldEstimate = (now - self.lastDepthEstimateAt) > 0.18
        var humanPos: SIMD3<Float>? = nil
        var arrivalMethod = "none"
        if shouldEstimate {
            // ARWorldTracking does NOT emit ARBodyAnchors (only
            // ARBodyTracking does). Use the segmentation matte + depth
            // map to estimate the real human's world position instead.
            let dynamicMaxDepth = max(8, Float(self.shot.angle.distanceM) * 1.5)
            let viewportSize = self.viewportSize
            let orientation = self.viewportOrientation
            humanPos = Self.estimateHumanWorldPosition(
                in: frame,
                maxDepth: dynamicMaxDepth,
                viewportSize: viewportSize,
                orientation: orientation,
                recommendedDistanceM: Float(self.shot.angle.distanceM),
            )
            arrivalMethod = humanPos != nil ? "depth" : "none"
            self.lastDepthEstimateAt = now
        }

        // P1-8.4 — compute compass arrow when the marker is behind the
        // camera or far off-axis (>25° from screen-forward).
        let camForward = SIMD3<Float>(-cam.columns.2.x, -cam.columns.2.y, -cam.columns.2.z)
        let markerWorld = self.marker?.position(relativeTo: nil)
        var compassDeg: Double? = nil
        if let mw = markerWorld {
            let toMarker = SIMD3<Float>(mw.x - camPos.x, 0, mw.z - camPos.z)
            let fwd = SIMD3<Float>(camForward.x, 0, camForward.z)
            let len1 = simd_length(toMarker), len2 = simd_length(fwd)
            if len1 > 0.001 && len2 > 0.001 {
                let dot = simd_dot(toMarker / len1, fwd / len2)
                let cross = toMarker.x * fwd.z - toMarker.z * fwd.x
                let angle = atan2(cross, dot) * 180 / .pi
                if abs(angle) > 25 { compassDeg = Double(angle) }
            }
        }

        Task { @MainActor in
            self.groundReady = hasGroundPlane
            self.compassArrowDeg = compassDeg
            guard let marker = self.marker else { return }
            let target = marker.position(relativeTo: nil)
            let dist = simd_distance(camPos, target)
            self.distanceM = dist
            marker.updateDistance(dist)
            if shouldEstimate {
                self.updatePersonProximity(humanPos, method: arrivalMethod)
            }
        }
    }

    /// Returns the closest real human's world-space position in the
    /// camera's view, or nil when:
    ///   - personSegmentationWithDepth isn't running (older devices)
    ///   - no person pixels are present in the matte
    ///   - depth values are all invalid (e.g. dim scene)
    ///
    /// Implementation:
    ///   1. Sample the segmentation matte at coarse stride to collect
    ///      `(u,v,depth)` person-pixel records.
    ///   2. Cluster them by depth — multiple humans typically sit at
    ///      different depth bands, so a 1-D KMeans-lite on `depth`
    ///      separates them well enough for arrival detection.
    ///   3. Pick the cluster whose median depth is smallest (closest
    ///      to camera) — that's "the model approaching the spot".
    ///   4. Back-project that cluster's centroid through the camera
    ///      intrinsics into world space, using the live
    ///      `displayTransform` so the matte coords stay aligned to
    ///      the actual viewport orientation.
    nonisolated static func estimateHumanWorldPosition(
        in frame: ARFrame,
        maxDepth: Float = 8.0,
        viewportSize: CGSize = .zero,
        orientation: UIInterfaceOrientation = .portrait,
        // Caller passes the shot's recommended distance so this static
        // helper doesn't reach back into instance state. Previously the
        // body referenced `self.shot.angle.distanceM` which the Swift
        // 6 compiler (correctly) rejects on a static function.
        recommendedDistanceM: Float = 3.0,
    ) -> SIMD3<Float>? {
        guard let matte = frame.segmentationBuffer,
              let depth = frame.sceneDepth?.depthMap ?? frame.smoothedSceneDepth?.depthMap
        else { return nil }

        let width = CVPixelBufferGetWidth(matte)
        let height = CVPixelBufferGetHeight(matte)
        let depthW = CVPixelBufferGetWidth(depth)
        let depthH = CVPixelBufferGetHeight(depth)
        guard width > 0, height > 0, depthW > 0, depthH > 0 else { return nil }

        CVPixelBufferLockBaseAddress(matte, .readOnly)
        CVPixelBufferLockBaseAddress(depth, .readOnly)
        defer {
            CVPixelBufferUnlockBaseAddress(matte, .readOnly)
            CVPixelBufferUnlockBaseAddress(depth, .readOnly)
        }
        guard let matteBase = CVPixelBufferGetBaseAddress(matte),
              let depthBase = CVPixelBufferGetBaseAddress(depth)
        else { return nil }

        let matteStride = CVPixelBufferGetBytesPerRow(matte)
        let depthStride = CVPixelBufferGetBytesPerRow(depth)
        let mattePtr = matteBase.assumingMemoryBound(to: UInt8.self)
        _ = depthBase

        // Collect samples.
        let step = max(1, min(width, height) / 48)
        var samples: [(u: Float, v: Float, z: Float)] = []
        samples.reserveCapacity(2048)
        var y = 0
        while y < height {
            let mRow = mattePtr.advanced(by: y * matteStride)
            let depthY = min(depthH - 1, Int(Float(y) * Float(depthH) / Float(height)))
            let dRowBytes = depthBase.advanced(by: depthY * depthStride)
            let dRow = dRowBytes.assumingMemoryBound(to: Float32.self)
            var x = 0
            while x < width {
                if mRow[x] > 128 {
                    let depthX = min(depthW - 1, Int(Float(x) * Float(depthW) / Float(width)))
                    let z = dRow[depthX]
                    if z.isFinite, z > 0.2, z < maxDepth {
                        samples.append((Float(x), Float(y), z))
                    }
                }
                x += step
            }
            y += step
        }
        guard samples.count >= 8 else { return nil }

        // 2-D clustering on (u, z): split first by depth gap (front/
        // back people), then by lateral u-gap (side-by-side people).
        // The closest cluster is the one that should drive arrival.
        let depthSplit = splitByDepthGap(samples, gap: 0.5)
        let frontGroup = depthSplit.front
        // Compute lateral gap threshold from the actual depth +
        // intrinsics so it reflects "two adults' worth of physical
        // gap" instead of a hard pixel ratio. At 3m subject distance
        // a 0.5m human-shoulder gap projects to ~ 0.5 * fx / 3 px.
        let medianFrontZ = frontGroup.isEmpty
            ? recommendedDistanceM
            : frontGroup.map(\.z).sorted()[frontGroup.count / 2]
        let fx = frame.camera.intrinsics[0, 0]
        // Half a human-width gap at the cluster's depth, scaled to
        // the matte resolution (intrinsics are in image-pixel space,
        // matte is smaller).
        let imgWidth = Float(frame.camera.imageResolution.width)
        let gapPixels = (0.5 * fx / max(0.5, medianFrontZ))
            * (Float(width) / imgWidth)
        let lateralSplit = splitByLateralGap(frontGroup,
                                             gapPixels: max(8, gapPixels))
        // `splitByLateralGap` returns [[…]]; `.first` is Array.first
        // and therefore optional. Unwrap explicitly + fall through to
        // the front group or the raw samples if it's empty.
        let firstLateral = lateralSplit.first ?? []
        let cluster: [(u: Float, v: Float, z: Float)]
        if firstLateral.count >= 8 {
            cluster = firstLateral
        } else if frontGroup.count >= 8 {
            cluster = frontGroup
        } else {
            cluster = samples
        }

        // Matte density check: a real human's pixels are densely
        // packed in (u,v); a wall noise cluster spreads thinly across
        // the matte. We compute fill ratio = #samples / (du * dv) and
        // reject anything below 0.0003 sample/px² (tuned empirically).
        // Swift 6 won't infer key paths into named tuple elements in
        // some contexts, so we spell the projections out explicitly.
        let uMin = cluster.map { $0.u }.min() ?? 0
        let uMax = cluster.map { $0.u }.max() ?? 0
        let vMin = cluster.map { $0.v }.min() ?? 0
        let vMax = cluster.map { $0.v }.max() ?? 0
        let bboxArea: Float = max(1, (uMax - uMin) * (vMax - vMin))
        let fillRatio = Float(cluster.count) / bboxArea
        if fillRatio < 0.0003 { return nil }

        var sumU: Float = 0, sumV: Float = 0
        var zs: [Float] = []
        zs.reserveCapacity(cluster.count)
        for s in cluster {
            sumU += s.u; sumV += s.v
            zs.append(s.z)
        }
        let cu = sumU / Float(cluster.count)
        let cv = sumV / Float(cluster.count)
        zs.sort()
        let medianZ = zs[zs.count / 2]

        // Account for orientation — when the device is rotated the
        // matte is still in capture (camera) coords but we want to
        // think in viewport coords. `displayTransform` maps a unit
        // square (capture coords) onto the viewport. We don't need
        // to fully invert it: for back-projection we only need the
        // camera-space ray, which depends on capture coords. So we
        // just keep `cu/cv` in matte/capture space and let the camera
        // intrinsics do their job. `viewportSize/orientation` are
        // accepted for forward compatibility (e.g. drawing crosshairs).
        _ = viewportSize; _ = orientation

        let imgW = Float(frame.camera.imageResolution.width)
        let imgH = Float(frame.camera.imageResolution.height)
        let px = cu / Float(width) * imgW
        let py = cv / Float(height) * imgH
        let K = frame.camera.intrinsics
        // Renamed from fx/fy to avoid clashing with the earlier `fx`
        // (gap-threshold scaling) further up in the same scope.
        let intrFx = K[0, 0], intrFy = K[1, 1]
        let cx = K[2, 0], cy = K[2, 1]
        let xCam = (px - cx) * medianZ / intrFx
        let yCam = (py - cy) * medianZ / intrFy
        let camLocal = SIMD4<Float>(xCam, -yCam, -medianZ, 1)
        let world4 = frame.camera.transform * camLocal
        return SIMD3<Float>(world4.x, world4.y, world4.z)
    }

    /// Sort by depth and split into "front" (closest cluster) and
    /// "back" by the largest gap exceeding `gap`. If no gap is large
    /// enough, "front" is the whole list.
    nonisolated static func splitByDepthGap(
        _ samples: [(u: Float, v: Float, z: Float)],
        gap: Float,
    ) -> (front: [(u: Float, v: Float, z: Float)],
          back: [(u: Float, v: Float, z: Float)]) {
        let sorted = samples.sorted { $0.z < $1.z }
        var splitIndex = sorted.count
        var maxGap: Float = 0
        for i in 1..<sorted.count {
            let g = sorted[i].z - sorted[i - 1].z
            if g > maxGap {
                maxGap = g
                if g > gap { splitIndex = i }
            }
        }
        return (Array(sorted.prefix(splitIndex)),
                Array(sorted.suffix(from: splitIndex)))
    }

    /// Sort the input by `u` and split into clusters wherever the
    /// horizontal gap exceeds `gapPixels` — captures "two humans
    /// standing side by side" with a body-width gap. Pre-filters
    /// outliers (samples whose `u` is more than 3 standard deviations
    /// from the mean) so a single stray pixel can't anchor a phantom
    /// cluster. Returned array is ordered by cluster size (largest
    /// first), so `.first` is the dominant subject.
    nonisolated static func splitByLateralGap(
        _ samples: [(u: Float, v: Float, z: Float)],
        gapPixels: Float,
    ) -> [[(u: Float, v: Float, z: Float)]] {
        guard !samples.isEmpty else { return [] }
        // Outlier rejection: drop samples whose u is > 3σ from mean.
        // The depth segmentation matte occasionally fires on stray
        // pixels at the frame edges (e.g. specular highlights on
        // glass) — without filtering, those pixels get their own
        // cluster and confuse arrival detection.
        let meanU = samples.reduce(Float(0)) { $0 + $1.u } / Float(samples.count)
        let varU = samples.reduce(Float(0)) {
            let d = $1.u - meanU
            return $0 + d * d
        } / Float(samples.count)
        let sigmaU = sqrt(varU)
        let cutoff = max(20, sigmaU * 3)  // never reject inside a 20px window
        let filtered = samples.filter { abs($0.u - meanU) <= cutoff }
        let working = filtered.count >= 8 ? filtered : samples

        let sorted = working.sorted { $0.u < $1.u }
        var clusters: [[(u: Float, v: Float, z: Float)]] = [[]]
        var prevU: Float = sorted.first!.u
        for s in sorted {
            if s.u - prevU > gapPixels {
                clusters.append([])
            }
            clusters[clusters.count - 1].append(s)
            prevU = s.u
        }
        return clusters.sorted { $0.count > $1.count }
    }
}

struct ShotNavigationView: View {
    @StateObject private var model: ShotNavigationModel
    let shot: ShotRecommendation

    @State private var showModelView = false
    @State private var showIntro: Bool = !UserDefaults.standard.bool(
        forKey: ARGuideSettingsKeys.didShowIntro
    )
    /// Set to true once the user reaches the recommended position; we
    /// then push ARGuideView (the dedicated ghost-alignment screen) so
    /// the two views don't both render avatars simultaneously.
    @State private var navigateToGuide = false
    /// Drives the "已到位 ✓" 0.5s flash that runs *before* the actual
    /// handoff push, giving the user a visual confirmation rather than
    /// a jarring screen swap.
    @State private var showArrivalFlash = false
    /// First photo-taken trust sheet: lets the user compare AR preview
    /// vs the actual captured photo so they verify the ghost really
    /// didn't end up in the file.
    @State private var showTrustSheet = false
    @AppStorage(ARGuideSettingsKeys.handoffToGuide)
    private var handoffToGuide: Bool = true

    init(shot: ShotRecommendation,
         target: ShotPosition,
         userLat: Double,
         userLon: Double) {
        _model = StateObject(wrappedValue:
            ShotNavigationModel(shot: shot, target: target,
                                userLat: userLat, userLon: userLon))
        self.shot = shot
    }

    var body: some View {
        Group {
            if !ARWorldTrackingConfiguration.isSupported {
                StaticGuideCard(shot: shot)
            } else {
                arBody
            }
        }
        .onDisappear {
            // Release the camera when SwiftUI tears this view down so
            // the next ARView (e.g. ARGuideView) gets a clean session
            // immediately. Calling pauseSession() at handoff time
            // raced with NavigationLink's transition on slower
            // devices and produced ~200ms of dual-ARSession overlap.
            model.pauseSession()
        }
        .sheet(isPresented: $showIntro, onDismiss: {
            UserDefaults.standard.set(true, forKey: ARGuideSettingsKeys.didShowIntro)
        }) {
            ARGuideIntroSheet(onDismiss: { showIntro = false })
                .presentationDetents([.medium])
        }
        .sheet(isPresented: $showModelView) {
            ModelReferenceSheet(shot: shot)
                .presentationDetents([.medium, .large])
        }
        .sheet(isPresented: $showTrustSheet,
               onDismiss: {
                   // Drop the cached pair so a future manual re-open
                   // (via Settings → "重置首次提示") doesn't show the
                   // wrong photo paired with a stale snapshot from
                   // the last time the user actually shot.
                   model.lastARSnapshot = nil
                   model.lastShutterAt = nil
               }) {
            CaptureTrustSheet(shot: shot,
                              arSnapshot: model.lastARSnapshot,
                              shutterAt: model.lastShutterAt,
                              onDismiss: { showTrustSheet = false })
                .presentationDetents([.medium, .large])
        }
        .background(
            NavigationLink(
                destination: ARGuideView(
                    shot: shot,
                    avatarStyle: AvatarPresets.all.first
                        ?? AvatarPresets.all[0],
                    presetId: model.resolvedPresetIds.first,
                ),
                isActive: $navigateToGuide,
                label: { EmptyView() }
            )
            .hidden()
        )
        .overlay {
            if showArrivalFlash {
                ArrivalFlashOverlay(score: shot.criteriaScore?.composition ?? 3)
                    .id("arrivalFlash")  // stable identity, no re-anim on parent redraws
                    .transition(.opacity.combined(with: .scale))
            }
        }
        .onChange(of: model.stage) { _, newStage in
            guard newStage == .framing, !navigateToGuide else { return }
            triggerArrivalFlashAndMaybeHandoff()
        }
        .onChange(of: model.presetsResolved) { _, ready in
            // If the user reached framing before presets were resolved,
            // we'd be stuck on the navigation screen forever (handoff
            // is gated on resolved presets). Retry the handoff trigger
            // the moment presets land.
            if ready, model.stage != .nav, !navigateToGuide, handoffToGuide {
                triggerArrivalFlashAndMaybeHandoff()
            }
        }
        .onChange(of: model.capturing) { wasCapturing, nowCapturing in
            // Right after the first capture finishes, prompt the user
            // to verify the ghost didn't sneak into the photo. We only
            // do this once per device (didShowTrust persistence).
            if wasCapturing, !nowCapturing,
               !UserDefaults.standard.bool(forKey: ARGuideSettingsKeys.didShowTrust) {
                showTrustSheet = true
                UserDefaults.standard.set(true, forKey: ARGuideSettingsKeys.didShowTrust)
            }
        }
    }

    private var arBody: some View {
        ZStack {
            ARViewContainer(model: model)
                .ignoresSafeArea()
            VStack {
                topBar
                if model.anchorMode == .indoor {
                    indoorNotice
                }
                if !model.groundReady && model.stage == .nav {
                    groundScanHint
                }
                if model.weakGpsBanner && model.stage == .nav {
                    weakGpsHint
                }
                stage1ConsistencyChip
                Spacer()
                if model.stage == .framing || model.stage == .params {
                    ShotFramingOverlay(
                        composition: shot.composition,
                        subjectPositionHint: shot.poses.first?.persons.first?.positionHint,
                    )
                    .frame(maxWidth: .infinity, maxHeight: 240)
                }
                ShotParameterHUD(
                    live: model.liveHUD,
                    target: shot.camera.iphoneApplyPlan,
                    recommendedDistanceM: Float(shot.angle.distanceM),
                )
                .padding(.horizontal)
                if model.stage != .nav {
                    framingActionBar
                }
            }
            if model.capturing {
                Color.black.opacity(0.001)  // tap-blocker; no visual
                    .ignoresSafeArea()
                    .allowsHitTesting(true)
            }
            compassArrowOverlay
        }
    }

    private var topBar: some View {
        HStack {
            Text(stageTitle)
                .font(.subheadline.weight(.semibold))
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(.ultraThinMaterial, in: Capsule())
            Spacer()
            anchorBadge
        }
        .padding(.horizontal)
        .padding(.top, 8)
    }

    private var anchorBadge: some View {
        // Extract label as a plain helper so the @ViewBuilder body
        // contains only View expressions. Free-standing `let` + switch
        // statements at the top of a ViewBuilder closure are rejected
        // under Swift 6 as "buildExpression is unavailable".
        let label: String = {
            switch model.anchorMode {
            case .geo:    return "GeoAnchor"
            case .indoor: return "Indoor"
            case .world:  return "WorldAnchor"
            }
        }()
        return Text(label)
            .font(.caption2)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 8).padding(.vertical, 4)
            .background(.ultraThinMaterial, in: Capsule())
    }

    private var indoorNotice: some View {
        Label("室内位置 · GPS 不可靠，请用拖动微调", systemImage: "building.2.crop.circle")
            .font(.caption.weight(.medium))
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Color.orange.opacity(0.85), in: Capsule())
            .foregroundStyle(.white)
            .padding(.top, 6)
    }

    private var groundScanHint: some View {
        Label("请缓慢移动手机扫描地面", systemImage: "hand.draw")
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(.ultraThinMaterial, in: Capsule())
            .padding(.top, 16)
    }

    private var weakGpsHint: some View {
        Label("GPS 信号弱，AR 位置可能偏 5-10 m。建议走到空旷处。",
              systemImage: "antenna.radiowaves.left.and.right.slash")
            .font(.caption.weight(.medium))
            .multilineTextAlignment(.center)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Color.orange.opacity(0.85), in: Capsule())
            .foregroundStyle(.white)
            .padding(.top, 6)
    }

    @ViewBuilder
    private var compassArrowOverlay: some View {
        if let deg = model.compassArrowDeg, model.stage == .nav {
            // Pin at the screen edge in the direction of the marker.
            ZStack {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 64))
                    .foregroundStyle(.tint)
                    .rotationEffect(.degrees(deg))
                    .shadow(color: .black.opacity(0.35), radius: 6)
            }
            .allowsHitTesting(false)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .padding(.top, 120)
        }
    }

    /// Tiny preview of the Stage-1 card thumbnail to keep the
    /// "所见即所得" promise visually alive in Stage 2.
    private var stage1ConsistencyChip: some View {
        HStack(spacing: 8) {
            AvatarThumbnailView(presetId: model.resolvedPresetIds.first)
                .frame(width: 44, height: 44)
                .clipShape(RoundedRectangle(cornerRadius: 8))
            VStack(alignment: .leading, spacing: 2) {
                Text(shot.title ?? "推荐机位")
                    .font(.caption.weight(.semibold))
                    .lineLimit(1)
                Text("和卡片预览同一虚拟人/同一姿势")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
        }
        .padding(8)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
        .padding(.horizontal)
        .padding(.top, 8)
        .opacity(model.stage == .nav ? 0.7 : 1)
        .scaleEffect(model.stage == .nav ? 0.95 : 1)
        .animation(.spring(response: 0.4), value: model.stage)
    }

    private var framingActionBar: some View {
        VStack(spacing: 8) {
            if model.dragWarningExceeded {
                Text(String(format: "已偏离推荐位置 %.1f m", model.dragOffsetM))
                    .font(.caption2.weight(.medium))
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(Color.orange.opacity(0.85), in: Capsule())
                    .foregroundStyle(.white)
            }
            HStack(spacing: 16) {
                Button {
                    showModelView = true
                } label: {
                    Label("展示给模特", systemImage: "person.wave.2")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.bordered)

                Button {
                    model.resetGhostPosition()
                } label: {
                    Label("重置位置", systemImage: "arrow.uturn.backward")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.bordered)

                Button {
                    model.togglePrivacyMode()
                } label: {
                    Label(model.privacyMode ? "显示虚拟人" : "只显示脚印",
                          systemImage: model.privacyMode ? "eye" : "eye.slash")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.bordered)
            }
            shutterButton
        }
        .padding(.bottom, 24)
    }

    private var shutterButton: some View {
        Button {
            model.performShutter()
        } label: {
            ZStack {
                Circle()
                    .stroke(.white, lineWidth: 4)
                    .frame(width: 72, height: 72)
                Circle()
                    .fill(model.modelArrived ? Color.green : Color.white)
                    .frame(width: 58, height: 58)
            }
        }
        .disabled(model.capturing)
        .accessibilityLabel("拍摄")
    }

    /// Shared "show flash, optionally hand off" routine reused by both
    /// the stage and presetsResolved observers, so whichever condition
    /// finishes second still kicks the transition.
    private func triggerArrivalFlashAndMaybeHandoff() {
        if !showArrivalFlash {
            withAnimation(.spring(response: 0.35)) { showArrivalFlash = true }
        }
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 600_000_000)
            withAnimation { showArrivalFlash = false }
            if handoffToGuide, model.presetsResolved {
                model.telemetry.didHandoff = true
                navigateToGuide = true
                // Pause is now performed in onDisappear so the swap is
                // sequenced cleanly: ARGuideView.onAppear gets the
                // camera as ours releases it.
            }
        }
    }

    private var stageTitle: String {
        switch model.stage {
        case .nav: return String(format: "走位 · 距离 %.1f m", model.distanceM)
        case .framing: return model.modelArrived
            ? "模特已到位 ✓ 可以按下快门"
            : "请引导模特站到虚拟人位置"
        case .params: return "参数对齐 → 拍摄"
        }
    }
}

// MARK: - ARViewContainer

private struct ARViewContainer: UIViewRepresentable {
    let model: ShotNavigationModel

    func makeUIView(context: Context) -> ARView {
        let v = ARView(frame: .zero)
        model.attach(v)
        let pan = UIPanGestureRecognizer(
            target: context.coordinator,
            action: #selector(Coordinator.handlePan(_:)),
        )
        v.addGestureRecognizer(pan)
        context.coordinator.arView = v
        return v
    }

    func updateUIView(_ uiView: ARView, context: Context) {
        // Forward the live viewport size + orientation to the model so
        // the depth-based human estimator can stay aligned to whatever
        // the user is actually seeing.
        let bounds = uiView.bounds.size
        if bounds.width > 0, bounds.height > 0 {
            model.viewportSize = bounds
        }
        if let scene = uiView.window?.windowScene {
            model.viewportOrientation = scene.interfaceOrientation
        }
    }

    func makeCoordinator() -> Coordinator {
        Coordinator(model: model)
    }

    @MainActor
    final class Coordinator: NSObject {
        let model: ShotNavigationModel
        weak var arView: ARView?
        private var dragStart: CGPoint?

        init(model: ShotNavigationModel) {
            self.model = model
        }

        @objc func handlePan(_ gr: UIPanGestureRecognizer) {
            guard let view = arView else { return }
            let p = gr.location(in: view)
            switch gr.state {
            case .began:
                dragStart = p
            case .changed:
                guard let start = dragStart else { return }
                model.dragGhost(in: view, from: start, to: p)
                dragStart = p
            case .ended, .cancelled, .failed:
                dragStart = nil
            default:
                break
            }
        }
    }
}

// MARK: - First-run intro sheet

private struct ARGuideIntroSheet: View {
    let onDismiss: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "sparkles")
                    .font(.title)
                    .foregroundStyle(.tint)
                Text("AR 实拍引导")
                    .font(.title3.weight(.semibold))
            }
            VStack(alignment: .leading, spacing: 10) {
                bullet("虚拟人只在你屏幕上显示，按下快门会自动消失")
                bullet("它不会出现在你拍下来的照片里")
                bullet("可以拖动它来微调位置；点击「重置位置」回到推荐站位")
                bullet("对模特说「站到那个发光圆圈里」即可")
            }
            .font(.subheadline)
            Spacer()
            Button(action: onDismiss) {
                Text("我明白了")
                    .font(.callout.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(20)
    }
    private func bullet(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text("·").bold()
            Text(text)
        }
    }
}

// MARK: - Show-to-model sheet

private struct ModelReferenceSheet: View {
    let shot: ShotRecommendation
    @State private var countdown: Int? = nil

    var body: some View {
        VStack(spacing: 16) {
            Text("把手机递给模特看")
                .font(.headline)
            if let pose = shot.poses.first?.persons.first {
                VStack(alignment: .leading, spacing: 8) {
                    row("姿势", pose.stance ?? pose.upperBody ?? "自然站立")
                    row("手势", pose.hands ?? "自然垂放")
                    row("视线", pose.gaze ?? "看向镜头")
                    row("表情", pose.expression ?? "放松")
                    if let hint = pose.positionHint {
                        row("位置", hint)
                    }
                }
                .font(.subheadline)
                .padding(14)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12))
            }
            if let n = countdown {
                Text("\(n)")
                    .font(.system(size: 64, weight: .bold))
                    .foregroundStyle(.tint)
            } else {
                VStack(spacing: 8) {
                    Button {
                        startCountdown()
                    } label: {
                        Label("3 秒预演倒计时", systemImage: "timer")
                            .font(.callout.weight(.semibold))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 10)
                    }
                    .buttonStyle(.borderedProminent)
                    Text("仅作模特预演 · 不会触发拍摄；正式拍照请回到主画面按快门")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
            }
            Spacer()
        }
        .padding(20)
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top) {
            Text(label).foregroundStyle(.secondary).frame(width: 56, alignment: .leading)
            Text(value).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func startCountdown() {
        countdown = 3
        Task { @MainActor in
            for n in stride(from: 3, through: 1, by: -1) {
                countdown = n
                UIImpactFeedbackGenerator(style: .light).impactOccurred()
                ARGuideSpeech.speak("\(n)")
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
            countdown = 0
            UINotificationFeedbackGenerator().notificationOccurred(.success)
            ARGuideSpeech.speak("拍")
            try? await Task.sleep(nanoseconds: 600_000_000)
            countdown = nil
        }
    }
}

// MARK: - Speech (倒计时 TTS)

/// Wraps AVSpeechSynthesizer so the model can hear the countdown
/// numbers without staring at the screen. Stays silent when the device
/// is muted.
/// Wraps AVSpeechSynthesizer so the model can hear the countdown
/// numbers even when the device is muted. Uses a *temporary*
/// `.playback` activation that is torn down once the countdown ends,
/// so we don't keep the audio session locked open and disrupt the
/// system camera shutter sound or the user's music indefinitely.
@MainActor
final class ARGuideSpeech: NSObject, AVSpeechSynthesizerDelegate {
    static let shared = ARGuideSpeech()

    private let synth: AVSpeechSynthesizer
    /// Outstanding utterances we've enqueued. When this drops back to
    /// zero we deactivate the audio session and let the system route
    /// audio normally again.
    private var pending: Int = 0
    private var didActivate: Bool = false

    override init() {
        self.synth = AVSpeechSynthesizer()
        super.init()
        self.synth.delegate = self
    }

    static func speak(_ text: String) {
        shared.enqueue(text)
    }

    private func enqueue(_ text: String) {
        activateSessionIfNeeded()
        let u = AVSpeechUtterance(string: text)
        u.voice = AVSpeechSynthesisVoice(language: "zh-CN")
            ?? AVSpeechSynthesisVoice(language: "en-US")
        u.rate = AVSpeechUtteranceDefaultSpeechRate
        u.preUtteranceDelay = 0
        pending += 1
        synth.speak(u)
    }

    /// Set `.playback` only for the duration of the countdown.
    /// `mixWithOthers` keeps any background music alive at full
    /// volume — we deliberately do NOT set `duckOthers` because the
    /// previous combo was contradictory and ducked the music, which
    /// surprised users mid-track. Mute-switch override is provided
    /// by `.playback` itself.
    private func activateSessionIfNeeded() {
        guard !didActivate else { return }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback,
                                    mode: .spokenAudio,
                                    options: [.mixWithOthers])
            try session.setActive(true, options: [])
            didActivate = true
        } catch {
            print("[ARGuideSpeech] audio session activate failed:", error)
        }
    }

    private func deactivateSessionIfIdle() {
        guard didActivate, pending == 0 else { return }
        do {
            try AVAudioSession.sharedInstance().setActive(
                false, options: [.notifyOthersOnDeactivation],
            )
            didActivate = false
        } catch {
            print("[ARGuideSpeech] audio session deactivate failed:", error)
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.pending = max(0, self.pending - 1)
            self.deactivateSessionIfIdle()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.pending = max(0, self.pending - 1)
            self.deactivateSessionIfIdle()
        }
    }
}

// MARK: - Avatar thumbnail (consistency chip)

/// Loads `AvatarManifest.shared.payload.presets[id].thumbnail` as a
/// remote image so the Stage-2 consistency chip displays the same
/// digital human face the user saw on the Stage-1 card. Falls back to
/// the SF Symbol placeholder when the preset / network is unavailable.
private struct AvatarThumbnailView: View {
    let presetId: String?

    @State private var url: URL? = nil
    @State private var bundledImage: UIImage? = nil

    var body: some View {
        Group {
            if let bundledImage {
                Image(uiImage: bundledImage).resizable().scaledToFill()
            } else if let url {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .empty:
                        placeholder
                    case .success(let img):
                        img.resizable().scaledToFill()
                    case .failure:
                        placeholder
                    @unknown default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .task(id: presetId) { await resolve() }
    }

    private var placeholder: some View {
        ZStack {
            Color.black.opacity(0.35)
            Image(systemName: "person.crop.rectangle")
                .foregroundStyle(.white)
        }
    }

    private func resolve() async {
        guard let presetId else { url = nil; bundledImage = nil; return }

        // 1) Prefer a bundled thumbnail when one is shipped with the
        //    app — this is host-agnostic and works offline / with any
        //    BYOK API base. Convention: Avatars/<presetId>.png inside
        //    the bundle (mirrors usdz layout).
        if let bundled = UIImage(named: "Avatars/\(presetId)")
            ?? UIImage(named: presetId)
        {
            bundledImage = bundled
            url = nil
            return
        }

        // 2) Fall back to a remote URL. Use the avatar manifest's host
        //    (where the JSON itself was served) rather than the BYOK
        //    `apiBaseURL`, since the thumbnails are static avatar
        //    assets bundled with the manifest, not API responses.
        //    Right now both happen to share `apiBaseURL`, but if we
        //    later split assets to a CDN this view keeps working.
        let payload = await AvatarManifest.shared.load()
        guard let entry = payload?.presets.first(where: { $0.id == presetId })
        else { url = nil; return }
        let host = manifestAssetsBaseURL()
        if let resolved = URL(string: entry.thumbnail, relativeTo: host) {
            url = resolved
        }
    }

    /// The avatar-asset host. Prefers an explicit override
    /// (`avatarAssetsBaseURL`) so a future deploy can move thumbnails
    /// to a CDN; otherwise reuses `apiBaseURL`; finally falls back to
    /// localhost for dev.
    private func manifestAssetsBaseURL() -> URL {
        if let override = UserDefaults.standard.string(forKey: "avatarAssetsBaseURL"),
           let u = URL(string: override) {
            return u
        }
        if let api = UserDefaults.standard.string(forKey: "apiBaseURL"),
           let u = URL(string: api) {
            return u
        }
        return URL(string: "http://127.0.0.1:8000")!
    }
}

// MARK: - Arrival flash overlay

/// Brief "已到位 ✓" badge shown when the user reaches the recommended
/// position. Bridges the visual gap between walking and the ARGuideView
/// handoff, so the screen swap doesn't feel abrupt.
private struct ArrivalFlashOverlay: View {
    /// Composition score (1–5) of the recommendation the user just
    /// reached. Higher score = more particles → more celebratory.
    let score: Int
    @State private var burst: Bool = false

    /// Map composition score 1–5 → 8–24 particle rays so a 5-star
    /// recommendation feels meaningfully different from a 3-star one.
    private var particleCount: Int {
        max(8, min(24, score * 4 + 4))
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.18).ignoresSafeArea()
            // Particle burst — short rays radiating from the badge
            // give the moment a celebratory feel without shipping
            // a real particle system. Pure SwiftUI shapes so it works
            // at 60fps without additional assets.
            ForEach(0..<particleCount, id: \.self) { i in
                let angle = Double(i) / Double(particleCount) * 360
                Capsule()
                    .fill(Color.green.opacity(0.85))
                    .frame(width: 4, height: burst ? 56 : 0)
                    .offset(y: burst ? -64 : -28)
                    .rotationEffect(.degrees(angle))
                    .opacity(burst ? 0 : 1)
            }
            VStack(spacing: 12) {
                ZStack {
                    Circle()
                        .fill(Color.green.opacity(0.95))
                        .frame(width: 80, height: 80)
                        .scaleEffect(burst ? 1.05 : 0.6)
                    Image(systemName: "checkmark")
                        .font(.system(size: 36, weight: .bold))
                        .foregroundStyle(.white)
                        .opacity(burst ? 1 : 0)
                }
                Text("已到位")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.white)
                    .opacity(burst ? 1 : 0)
            }
        }
        .allowsHitTesting(false)
        .onAppear {
            // Wrap in `withTransaction` to make sure even if SwiftUI
            // re-evaluates this view body partway through (e.g. a
            // device-rotation refresh), it doesn't re-animate `burst`
            // back to its initial false→true state. The local @State
            // already gates re-fires, but transaction belt-and-braces
            // it for visual consistency.
            var tx = Transaction()
            tx.animation = .spring(response: 0.45, dampingFraction: 0.65)
            tx.disablesAnimations = false
            withTransaction(tx) { burst = true }
        }
    }
}

// MARK: - Capture trust sheet

/// First-capture trust-building sheet. Shows side-by-side: a still of
/// the AR ghost preview vs the just-captured photo from the Camera Roll
/// so the user verifies the ghost isn't in the file. Encourages
/// long-term confidence in the privacy promise.
private struct CaptureTrustSheet: View {
    let shot: ShotRecommendation
    let arSnapshot: UIImage?
    /// Lock the Photos query to "anything taken at-or-after this
    /// moment" so we never confuse an unrelated earlier shot with
    /// the photo the user just produced.
    let shutterAt: Date?
    let onDismiss: () -> Void

    @State private var realPhoto: UIImage? = nil

    var body: some View {
        VStack(spacing: 16) {
            Text("拍下的照片不会包含虚拟人")
                .font(.title3.weight(.semibold))
            Text("左：刚才屏幕上看到的 AR 引导画面（带虚拟人）。右：实际保存到相册的照片。")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 8)
            HStack(spacing: 12) {
                trustImage(image: arSnapshot,
                           fallbackIcon: "person.crop.rectangle",
                           title: "AR 引导画面",
                           subtitle: "你看到了虚拟人",
                           tint: .blue)
                trustImage(image: realPhoto,
                           fallbackIcon: "photo.on.rectangle",
                           title: "实拍照片",
                           subtitle: "不会出现虚拟人",
                           tint: .green)
            }
            .padding(.vertical, 4)
            Button(action: onDismiss) {
                Text("知道了")
                    .font(.callout.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
            }
            .buttonStyle(.borderedProminent)
            Spacer()
        }
        .padding(20)
        .task { await loadLatestPhoto() }
    }

    private func trustImage(image: UIImage?, fallbackIcon: String,
                            title: String, subtitle: String,
                            tint: Color) -> some View {
        VStack(spacing: 6) {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(tint.opacity(0.12))
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFill()
                        .clipShape(RoundedRectangle(cornerRadius: 12))
                } else {
                    Image(systemName: fallbackIcon)
                        .font(.system(size: 28, weight: .semibold))
                        .foregroundStyle(tint)
                }
            }
            .frame(height: 140)
            .clipped()
            Text(title).font(.caption.weight(.semibold))
            Text(subtitle).font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
    }

    /// Pulls the most recent image from Photos (within 60s) so we can
    /// show the *actual* file the user just produced. Honours
    /// permission gracefully — when denied we fall through to the SF
    /// Symbol fallback so the sheet still tells its story.
    private func loadLatestPhoto() async {
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        let authorized: Bool
        switch status {
        case .authorized, .limited:
            authorized = true
        case .denied, .restricted:
            authorized = false
        default:
            authorized = await withCheckedContinuation { c in
                PHPhotoLibrary.requestAuthorization(for: .readWrite) { s in
                    c.resume(returning: s == .authorized || s == .limited)
                }
            }
        }
        guard authorized else { return }
        // Anchor the lookup window to the actual shutter press
        // instead of "now - 60s" — important when the user takes a
        // moment to peek at the trust sheet, or has rapid-fire
        // captures and we want to show the right pair.
        let cutoff = (shutterAt ?? Date()).addingTimeInterval(-2)
        let opts = PHFetchOptions()
        opts.predicate = NSPredicate(format: "mediaType == %d AND creationDate >= %@",
                                     PHAssetMediaType.image.rawValue,
                                     cutoff as NSDate)
        opts.sortDescriptors = [NSSortDescriptor(key: "creationDate", ascending: false)]
        opts.fetchLimit = 1
        guard let asset = PHAsset.fetchAssets(with: opts).firstObject else { return }
        let manager = PHImageManager.default()
        let reqOpts = PHImageRequestOptions()
        reqOpts.isSynchronous = false
        reqOpts.deliveryMode = .opportunistic
        reqOpts.isNetworkAccessAllowed = true
        let target = CGSize(width: 600, height: 600)
        let img: UIImage? = await withCheckedContinuation { c in
            manager.requestImage(for: asset, targetSize: target,
                                 contentMode: .aspectFill, options: reqOpts) { image, _ in
                c.resume(returning: image)
            }
        }
        if let img { realPhoto = img }
    }
}

// MARK: - Telemetry struct (consumed by FeedbackUploader)

struct ARGuideTelemetry: Sendable {
    var anchorMode: String = "world"
    var presetIds: [String] = []
    var arrivedAt: Date? = nil
    var captureCount: Int = 0
    var resetCount: Int = 0
    var throttledToLowFps: Bool = false
    /// How arrival is being detected on this device — "depth" when
    /// segmentation+sceneDepth are giving us pixels, "none" otherwise.
    var lastArrivalMethod: String = "none"
    /// Counts every flip between arrived ↔ not-arrived, so the backend
    /// can tell whether arrival detection was stable or chattering.
    var arrivalFlipCount: Int = 0
    /// XZ distance (metres) at the moment of each flip. Lets the
    /// backend distinguish "user oscillating right at the threshold"
    /// (small magnitudes) from "user wandered in and out" (large).
    var arrivalFlipMagnitudes: [Float] = []
    /// Set when this view handed off to ARGuideView; lets the backend
    /// stitch the navigation telemetry to the alignment telemetry.
    var didHandoff: Bool = false

    /// Compact 4-bucket histogram of the per-flip XZ magnitudes. The
    /// backend cares about distribution shape ("oscillating right at
    /// the threshold" vs "user wandered far in/out") far more than
    /// per-flip exact values, so we ship a fixed-size payload.
    static func bucketise(_ values: [Float]) -> [String: Int] {
        var b: [String: Int] = [
            "lt_0_3": 0, "0_3_to_0_7": 0, "0_7_to_1_5": 0, "ge_1_5": 0,
        ]
        for v in values {
            if v < 0.3 { b["lt_0_3"]! += 1 }
            else if v < 0.7 { b["0_3_to_0_7"]! += 1 }
            else if v < 1.5 { b["0_7_to_1_5"]! += 1 }
            else { b["ge_1_5"]! += 1 }
        }
        return b
    }

    func snapshot() -> [String: Any] {
        var dict: [String: Any] = [
            "anchor_mode": anchorMode,
            "preset_ids": presetIds,
            "capture_count": captureCount,
            "reset_count": resetCount,
            "throttled_to_low_fps": throttledToLowFps,
            "arrival_method": lastArrivalMethod,
            "arrival_flip_count": arrivalFlipCount,
            "arrival_flip_buckets": Self.bucketise(arrivalFlipMagnitudes),
            "did_handoff": didHandoff,
        ]
        if let a = arrivedAt {
            dict["arrived_at_iso"] = ISO8601DateFormatter().string(from: a)
        }
        return dict
    }
}

extension ShotNavigationModel {
    /// Snapshot of the AR guide state, suitable for piggy-backing onto
    /// a /feedback round-trip after the user takes the photo.
    func telemetrySnapshot() -> [String: Any] {
        var s = telemetry.snapshot()
        s["drag_offset_m"] = Double(dragOffsetM)
        s["privacy_mode"] = privacyMode
        s["model_arrived_at_capture"] = modelArrived
        return s
    }
}
