"""One-shot script to convert OpenAI CLIP ViT-B/32 image encoder to CoreML.

Run this on a Mac (CoreMLTools is x86_64 / arm64 darwin only).

    pip install coremltools open_clip_torch torch torchvision pillow
    python scripts/convert_clip_to_coreml.py

It writes ios/AIPhotoCoach/Resources/CLIPImageEncoder.mlpackage
which the iOS app loads via CLIPEmbedder.
"""
from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    try:
        import torch  # type: ignore
        import open_clip  # type: ignore
        import coremltools as ct  # type: ignore
    except ImportError as exc:
        print(f"Missing dep: {exc}. See module docstring.")
        return 1

    out_dir = Path(__file__).resolve().parent.parent / "ios" / "AIPhotoCoach" / "Resources"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "CLIPImageEncoder.mlpackage"

    print("Loading open_clip ViT-B-32 (laion2b_s34b_b79k)...")
    model, _, preprocess = open_clip.create_model_and_transforms(
        "ViT-B-32", pretrained="laion2b_s34b_b79k"
    )
    model.eval()

    class ImageEncoder(torch.nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base

        def forward(self, x):
            feat = self.base.encode_image(x)
            return feat / feat.norm(dim=-1, keepdim=True)

    wrapper = ImageEncoder(model)
    example = torch.randn(1, 3, 224, 224)
    traced = torch.jit.trace(wrapper, example)

    print("Converting to CoreML (.mlpackage)...")
    mlmodel = ct.convert(
        traced,
        inputs=[ct.ImageType(name="image", shape=example.shape, scale=1.0 / 255.0)],
        convert_to="mlprogram",
        compute_units=ct.ComputeUnit.CPU_AND_NE,
    )
    mlmodel.short_description = "CLIP ViT-B/32 image encoder, L2-normalized output"
    mlmodel.save(str(out))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
