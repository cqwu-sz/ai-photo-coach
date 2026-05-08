#!/usr/bin/env bash
# v7 — Convert ReadyPlayerMe glb avatars + Mixamo glb animations to USDZ
# for the iOS RealityKit AR loader.
#
# Prereqs (install once):
#   pip install usd-core
#   brew install xcode-select  # ships usdz_converter
#   # OR: pip install gltf2usd  (cross-platform fallback)
#
# Usage:
#   bash scripts/glb_to_usdz.sh
#
# Behaviour: idempotent — skips files where the output is newer than
# the input. Logs every step to stdout so CI can grep for failures.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_PRESET="$REPO_ROOT/web/avatars/preset"
WEB_ANIM="$REPO_ROOT/web/avatars/animations"
IOS_AVATARS="$REPO_ROOT/ios/AIPhotoCoach/Resources/Avatars"
IOS_ANIMS="$REPO_ROOT/ios/AIPhotoCoach/Resources/Animations"

mkdir -p "$IOS_AVATARS" "$IOS_ANIMS"

# Pick a converter — Apple's usdzconvert is highest fidelity but Mac-only.
if command -v usdzconvert >/dev/null 2>&1; then
    CONVERTER="usdzconvert"
elif command -v gltf2usd >/dev/null 2>&1; then
    CONVERTER="gltf2usd"
else
    echo "[error] need either 'usdzconvert' (macOS) or 'gltf2usd' (cross-platform)" >&2
    echo "        macOS: install Xcode + 'xcrun usdzconvert'" >&2
    echo "        any  : pip install gltf2usd" >&2
    exit 1
fi
echo "[glb_to_usdz] using converter: $CONVERTER"

convert_one() {
    local in="$1"
    local out="$2"
    if [[ -f "$out" && "$out" -nt "$in" ]]; then
        echo "  [skip] $(basename "$out") up-to-date"
        return
    fi
    echo "  [conv] $(basename "$in") -> $(basename "$out")"
    case "$CONVERTER" in
        usdzconvert)
            usdzconvert "$in" "$out"
            ;;
        gltf2usd)
            gltf2usd -i "$in" -o "$out" --use-euler-rotation --interpolation LINEAR
            ;;
    esac
}

echo "[glb_to_usdz] presets:"
shopt -s nullglob
for glb in "$WEB_PRESET"/*.glb; do
    base=$(basename "$glb" .glb)
    convert_one "$glb" "$IOS_AVATARS/$base.usdz"
done

echo "[glb_to_usdz] animations:"
for glb in "$WEB_ANIM"/*.glb; do
    base=$(basename "$glb" .glb)
    convert_one "$glb" "$IOS_ANIMS/$base.usdz"
done

echo "[glb_to_usdz] done."
