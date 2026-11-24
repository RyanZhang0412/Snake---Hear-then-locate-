#!/usr/bin/env bash
# Serve the game and open it in the browser (WSL opens the Windows browser).
# Usage: ./run.sh [camera|keyboard|audio]      Ctrl+C to stop.
cd "$(dirname "$0")"
[ -f vendor/mediapipe/hand_landmarker.task ] || { echo "first run: downloading MediaPipe (~37 MB)"; ./tools/fetch_vendor.sh; }
PORT=8000
while ss -ltn 2>/dev/null | grep -q ":$PORT "; do PORT=$((PORT + 1)); done
MODE=${1:-camera}
URL="http://localhost:$PORT/?mode=$MODE"
[ "$MODE" = audio ] && URL="$URL&audio=ws://localhost:8765"
echo "LiDAR Snake 3D -> $URL      (hand direction bench: http://localhost:$PORT/hand3d.html)"
echo "Ctrl+C to stop."
(sleep 1; cmd.exe /c start "" "${URL//&/^&}" >/dev/null 2>&1 || xdg-open "$URL" >/dev/null 2>&1 || open "$URL" 2>/dev/null) &
exec python3 -m http.server "$PORT" --bind 127.0.0.1
