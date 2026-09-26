#!/bin/bash
# Type-checks the app target with swiftc, without Xcode (this Mac has only the
# Command Line Tools; full builds, signing and notarization run in CI).
# Also type-checks the command-line tool (PixelSwitchCLI/ plus the shared
# PixelSwitch/Control/ControlProtocol.swift) when PixelSwitchCLI/ exists.
# The widget is skipped: its #Preview macros need Xcode's PreviewsMacros plugin.
set -euo pipefail
cd "$(dirname "$0")/.."
SPARKLE_VERSION="2.9.0"
CACHE="$HOME/Library/Caches/pixelswitch-dev/sparkle-$SPARKLE_VERSION"
if [ ! -d "$CACHE/Sparkle.framework" ]; then
    mkdir -p "$CACHE"
    curl -sfL -o "$CACHE/sparkle.tar.xz" \
        "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VERSION/Sparkle-$SPARKLE_VERSION.tar.xz"
    tar -xf "$CACHE/sparkle.tar.xz" -C "$CACHE"
fi
SDK=$(xcrun --show-sdk-path)
COMMON=(-typecheck -swift-version 6 -target arm64-apple-macos14.0 -sdk "$SDK")

APP_SOURCES=()
while IFS= read -r f; do APP_SOURCES+=("$f"); done < <(find PixelSwitch Shared -name '*.swift' | sort)
swiftc "${COMMON[@]}" -F "$CACHE" "${APP_SOURCES[@]}"
echo "app: type-check OK (${#APP_SOURCES[@]} files)"

if [ -d PixelSwitchCLI ]; then
    CLI_SOURCES=()
    while IFS= read -r f; do CLI_SOURCES+=("$f"); done < <(find PixelSwitchCLI -name '*.swift' | sort)
    CLI_SOURCES+=(PixelSwitch/Control/ControlProtocol.swift)
    swiftc "${COMMON[@]}" "${CLI_SOURCES[@]}"
    echo "cli: type-check OK (${#CLI_SOURCES[@]} files)"
fi
