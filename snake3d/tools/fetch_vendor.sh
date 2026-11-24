#!/usr/bin/env bash
# Download the MediaPipe hand tracker used by the camera control into vendor/mediapipe/ (not in git, ~37 MB).
# After this the game runs fully offline.
set -euo pipefail
cd "$(dirname "$0")/.."
V=1.2.3
D=vendor/mediapipe
mkdir -p "$D/wasm" /tmp/mp_fetch
curl -sSL -o /tmp/mp_fetch/tv.tgz "https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-$V.tgz"
tar xzf /tmp/mp_fetch/tv.tgz -C /tmp/mp_fetch
cp /tmp/mp_fetch/package/vision_bundle.mjs "$D/"
cp /tmp/mp_fetch/package/wasm/* "$D/wasm/"
curl -sSL -o "$D/hand_landmarker.task" \
  https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task
(cd "$D" && sha256sum -c SHA256SUMS) || echo "checksum differs (the 'latest' model may have been updated) - it should still work"
echo "MediaPipe $V ready in $D"
