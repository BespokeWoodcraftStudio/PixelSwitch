#!/bin/bash
# Compiles the credential-writing helpers with Tests/UnitTests/main.swift and runs them.
# Needs only the Swift compiler (Command Line Tools are enough; no Xcode).
# PIXELSWITCH_KEYCHAIN_TESTS=1 adds a round trip against /usr/bin/security on a throwaway item.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT
swiftc -swift-version 6 \
  PixelSwitch/Services/ClaudeTokenWriter.swift \
  PixelSwitch/Services/ClaudeCredentialsFile.swift \
  PixelSwitch/Services/ClaudeCredentialMerge.swift \
  Tests/UnitTests/main.swift \
  -o "$OUT/pixelswitch-unit-tests"
"$OUT/pixelswitch-unit-tests"
