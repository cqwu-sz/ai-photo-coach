#!/usr/bin/env bash
# Thin wrapper around gallery-dl for non-Unsplash sources.
# Stops you from accidentally dumping the downloads elsewhere.
#
# Prereqs:
#   pip install gallery-dl
#
# Usage:
#   ./scripts/works_crawler/gallery_dl_wrapper.sh https://www.500px.com/p/<handle>
#   ./scripts/works_crawler/gallery_dl_wrapper.sh https://www.flickr.com/photos/<user>/albums/<id>
#
# Output:
#   scripts/works_crawler/raw/<platform>/<id>.jpg
#   + a sidecar .json (set by gallery-dl --write-metadata)
#
# We deliberately keep `--no-mtime` so files are fresh-dated relative
# to ingestion, not creation, so reviewers can sort by added_at.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <gallery-url> [extra gallery-dl args]" >&2
  exit 2
fi

URL="$1"; shift || true
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HERE/raw"
mkdir -p "$DEST"

exec gallery-dl \
  --destination "$DEST" \
  --write-metadata \
  --no-mtime \
  --filename '{category}/{id|}_{filename}.{extension}' \
  --range 1-50 \
  --sleep 1.5 \
  "$@" \
  "$URL"
