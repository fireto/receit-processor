#!/usr/bin/env bash
# Build backend/static/app.css with the Tailwind standalone CLI (no Node needed).
# The binary is downloaded once into .cache/. Pass --watch while editing templates.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=v4.3.3
case "$(uname -m)" in
    x86_64) ARCH=x64 ;;
    aarch64 | arm64) ARCH=arm64 ;;
    *) echo "unsupported architecture $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=macos ;;
    *) echo "unsupported OS $(uname -s)" >&2; exit 1 ;;
esac
BIN=.cache/tailwindcss-$VERSION-$OS-$ARCH
if [ ! -x "$BIN" ]; then
    mkdir -p .cache
    curl -fsSL -o "$BIN.tmp" \
        "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/tailwindcss-$OS-$ARCH"
    chmod +x "$BIN.tmp" && mv "$BIN.tmp" "$BIN"
fi
exec "$BIN" -i backend/assets/app.css -o backend/static/app.css --minify "$@"
