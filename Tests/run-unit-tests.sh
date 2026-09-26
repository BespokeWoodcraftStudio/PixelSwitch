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
  PixelSwitch/Models/BrandColor.swift \
  PixelSwitch/Models/AccountPalette.swift \
  PixelSwitch/Models/Account.swift \
  PixelSwitch/Models/AccountOrder.swift \
  PixelSwitch/Models/String+Obfuscation.swift \
  PixelSwitch/Models/EmailDisplay.swift \
  PixelSwitch/Services/AutoSwitchEngine.swift \
  Tests/UnitTests/AutoSwitchRulesTests.swift \
  PixelSwitch/Services/SignInOutputParser.swift \
  PixelSwitch/Services/L10n.swift \
  PixelSwitch/Services/SignInProcess.swift \
  PixelSwitch/Services/SignInSession.swift \
  PixelSwitch/Services/SignInRules.swift \
  PixelSwitch/Services/ClaudeProcessEnvironment.swift \
  PixelSwitch/Services/SignInLinkCapture.swift \
  Tests/UnitTests/SignInTests.swift \
  PixelSwitch/Control/ControlProtocol.swift \
  PixelSwitch/Models/MenuBarModule.swift \
  PixelSwitch/Control/AccountResolver.swift \
  PixelSwitch/Control/SettingsCatalog.swift \
  PixelSwitch/Control/ControlAPI.swift \
  PixelSwitch/Control/ControlServer.swift \
  PixelSwitchCLI/ControlClient.swift \
  PixelSwitchCLI/CLIParser.swift \
  PixelSwitchCLI/CLIRunner.swift \
  Tests/UnitTests/ControlTests.swift \
  Tests/UnitTests/main.swift \
  -o "$OUT/pixelswitch-unit-tests"
"$OUT/pixelswitch-unit-tests"
