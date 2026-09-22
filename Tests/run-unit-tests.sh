#!/bin/bash
# Compiles the credential-writing helpers, the usage model and the auto-switch engine with Tests/UnitTests/main.swift and runs them.
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
  PixelSwitch/Services/CredentialOwnership.swift \
  PixelSwitch/Models/UsageData.swift \
  PixelSwitch/Models/CostHistoryWindow.swift \
  PixelSwitch/Models/AccountPalette.swift \
  PixelSwitch/Models/Account.swift \
  PixelSwitch/Models/String+Obfuscation.swift \
  PixelSwitch/Services/AutoSwitchEngine.swift \
  Tests/UnitTests/main.swift \
  -o "$OUT/pixelswitch-unit-tests"
"$OUT/pixelswitch-unit-tests"
