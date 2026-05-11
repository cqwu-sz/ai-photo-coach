# Post-process (W10 — C7)

End-of-flow editing: 8 preset filters + 5-knob beauty pipeline. Pure
on-device on both iOS and Web. No SDXL, no Replicate, no upload.

## iOS

- `Features/PostProcess/FilterEngine.swift` — 8 `CIFilter` chains. Add
  a new preset by extending `FilterPreset` enum + `chain(for:image:)`.
- `Features/PostProcess/BeautyEngine.swift` — Apple Vision face
  landmarks → per-eye `CIBumpDistortion` + global `CIGaussianBlur`
  for skin smoothing + `CIColorControls` for whitening.
- `Features/PostProcess/PostProcessView.swift` — main edit screen.
  Tap-and-hold the preview to compare against the original. Save
  writes a new `PHAsset` (original always preserved).

## Web

- `web/post_process.html` — standalone edit page reachable via
  `/web/post_process.html`.
- `web/js/post_process.js` — Canvas2D pixel ops mirroring the iOS
  presets (brightness / contrast / saturation / temperature / fade /
  vignette). Beauty currently does global skin smoothing via box-blur;
  face-mesh-driven slim/eye work is left for a follow-up using
  MediaPipe FaceLandmarker.

## LUT files

The plan calls for LUT PNGs in `ios/AIPhotoCoach/Resources/LUTs/` and
`web/luts/`. The first iteration ships **CIFilter-only chains** to keep
the binary small; LUTs can be slotted in later by adding a
`CIColorCubeWithColorSpace` step and shipping the PNG.

## Privacy

All operations are local; no network call, no model file > 64 KB
(future LUT PNGs). Saved photos use the system permission prompt the
first time the user taps "保存到相册".
