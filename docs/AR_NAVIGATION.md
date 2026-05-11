# AR Navigation (W8 — C6)

iOS-only "AR 带我去拍" experience that walks the user from their current
position to an absolute ShotPosition, then helps them frame and dial in
the recommended camera parameters.

## Three stages

```
PickShot → NavMode (走位) → FrameMode (取景) → ParamMode (参数对齐 + 拍摄)
```

Stages advance automatically: `NavMode → FrameMode` triggers when the AR
camera is within 3 m of the marker; `FrameMode → ParamMode` when the user
explicitly taps the shutter (the framing overlay stays visible).

## Files

- `Features/ARGuide/ShotNavigationView.swift` — top-level view + stage
  state machine. Wires up `ARWorldTrackingConfiguration`, picks
  `ARGeoAnchor` vs `ARWorldAnchor`, hosts the overlay sub-views.
- `ShotMarkerEntity.swift` — RealityKit entity: glowing ground disc + a
  floating distance label. Fires `onArrival` when within `arrivalRadiusM`.
- `ShotFramingOverlay.swift` — SwiftUI overlay drawing the recommended
  composition grid (rule of thirds / centred / diagonal) plus a
  yellow dashed circle showing where the subject should stand.
- `ShotParameterHUD.swift` — comparison HUD for live AVCaptureDevice
  values vs the recommended `IphoneApplyPlan`.
- Entry point: `ShotPositionCard` (absolute kind) renders an "AR 带我去拍"
  button that pushes `ShotNavigationView`.

## Anchor strategy

`ARGeoTrackingConfiguration.checkAvailability` is queried at attach time:

- **Available** (iOS 14+, large city + good GPS): use
  `ARGeoAnchor(coordinate:)`. Sub-metre placement, holds across long
  walks. Badge reads "GeoAnchor".
- **Unavailable** (small city, bad GPS, indoor): fall back to
  `ARWorldAnchor` placed at the local-ENU offset between the user's
  starting position and the target. Badge reads "WorldAnchor".

Both paths share the same marker / overlay code.

## Failure modes

- GPS denied → `ARGeoAnchor` impossible; we still render `WorldAnchor`
  but the offset is the user's starting GPS only (no live correction).
- AR session interrupted → `ARSession.didFailWithError` not surfaced
  yet (TODO); the marker simply disappears until tracking resumes.
- Marker behind the user → no compass arrow yet (TODO); user has to
  rotate naturally to find the glowing disc.
