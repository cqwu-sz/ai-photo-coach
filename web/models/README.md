# Web ML models — depth estimation

> **Files NOT in git**: `*.onnx` is gitignored (they're 60-140 MB each
> and exceed GitHub's 100 MB single-file limit). Download them locally
> after clone:
>
> ```bash
> # MiDaS small — 66 MB, default
> curl -L -o web/models/midas_small.onnx \
>   https://huggingface.co/julienkay/sentis-MiDaS/resolve/main/midas_v21_small_256.onnx
>
> # MiDaS LeViT-224 — 136 MB, opt-in for outdoor/building scenes
> curl -L -o web/models/dpt_levit_224.onnx \
>   https://huggingface.co/julienkay/sentis-MiDaS/resolve/main/dpt_levit_224.onnx
> ```
>
> Production deploy: bake them into your container image or serve from
> CDN. Don't try to git-LFS them — they're too large for free LFS quota
> and the cache hit-rate is poor.

The frontend uses ONNX Runtime Web to run a monocular depth model on
captured keyframes when no sensor depth is available. Two models live
here, with hot-swappable defaults:

| File | Backbone | Size | Accuracy | Default |
|---|---|---|---|---|
| `midas_small.onnx` | MiDaS v2.1 Small (MobileNetV2) | 66 MB | baseline | ✅ |
| `dpt_levit_224.onnx` | MiDaS v3.1 LeViT-224 | 136 MB | ~+30% on outdoor / building scenes | opt-in |

## How to switch

In any HTML page that loads `frame_semantics.js`, set the override
*before* loading the script:

```html
<script>
  window.MIDAS_MODEL_URL = "/web/models/dpt_levit_224.onnx";
</script>
<script type="module" src="/web/js/frame_semantics.js"></script>
```

Swap back by removing the line (or setting it to `null`).

## Sources

Both ONNX exports come from
[julienkay/sentis-MiDaS](https://huggingface.co/julienkay/sentis-MiDaS),
re-exported from the original Intel ISL [MiDaS repo](https://github.com/isl-org/MiDaS).

Original models are released under the
[MIT License](https://github.com/isl-org/MiDaS/blob/master/LICENSE).
