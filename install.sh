#!/usr/bin/env bash
# install.sh — link the plugin into Omarchy and build the images.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$HOME/.config/omarchy/plugins/io.github.cristosolar.waassistant"

for dep in docker curl python3 claude; do
  command -v "$dep" >/dev/null || { echo "missing dependency: $dep" >&2; exit 1; }
done

mkdir -p "$(dirname "$PLUGIN_DIR")"
ln -sfn "$ROOT" "$PLUGIN_DIR"
chmod +x "$ROOT"/bin/*

if ! docker image inspect omarchy-wuzapi >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  git clone --depth 1 https://github.com/asternic/wuzapi "$tmp/wuzapi"
  docker build -t omarchy-wuzapi "$tmp/wuzapi"
  rm -rf "$tmp"
fi

docker build -t omarchy-wa-sink -f "$ROOT/docker/sink.Dockerfile" "$ROOT"

# `omarchy bar put` is idempotent: it leaves the widget where it is if it's
# already on the bar, so re-running install.sh is safe and won't duplicate it.
if command -v omarchy-bar >/dev/null; then
  omarchy-bar put io.github.cristosolar.waassistant || true
fi

echo "Installed. Next:"
echo "  ./bin/wa-up"
echo "  ./bin/wa-pair <your number>"
echo "  omarchy-restart-shell"
