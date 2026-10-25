#!/bin/bash
# Renders the Settings pieces (Tools/screenshots/make_settings_renders.swift) in
# English, German, French, Japanese and Chinese into <output folder>.
#
# The program is wrapped in a small .app next to the app's five string tables,
# because SwiftUI reads translations from the main bundle: without the wrapper
# every language would draw as English. Nothing here touches a real login or a
# real reading. Needs only the Swift compiler and a Mac with a window server.
#
#   bash Tools/screenshots/run_settings_renders.sh <output folder> [language ...]
set -euo pipefail
cd "$(dirname "$0")/../.."
OUT="${1:?usage: run_settings_renders.sh <output folder> [language ...]}"
shift || true
LANGS=("$@")
[ "${#LANGS[@]}" -gt 0 ] || LANGS=(en de fr ja zh-Hans)
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/PixelRenders.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -swift-version 6 -target arm64-apple-macos14.0 -parse-as-library \
  -o "$APP/Contents/MacOS/pixelrenders" \
  PixelSwitch/Views/SettingsAccountRow.swift PixelSwitch/Views/SettingsThresholdDefaults.swift \
  PixelSwitch/Views/Components/Badge.swift PixelSwitch/Models/AppStyle.swift \
  PixelSwitch/Models/Account.swift PixelSwitch/Models/AccountOrder.swift \
  PixelSwitch/Services/AutoSwitchEngine.swift PixelSwitch/Models/UsageData.swift \
  PixelSwitch/Models/String+Obfuscation.swift PixelSwitch/Services/L10n.swift \
  Tools/screenshots/AssetColorStubs.swift Tools/screenshots/make_settings_renders.swift
for lproj in PixelSwitch/*.lproj; do cp -R "$lproj" "$APP/Contents/Resources/"; done
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.example.pixelrenders</string>
<key>CFBundleExecutable</key><string>pixelrenders</string>
<key>CFBundleName</key><string>PixelRenders</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleDevelopmentRegion</key><string>en</string>
<key>CFBundleLocalizations</key><array><string>en</string><string>de</string><string>fr</string><string>ja</string><string>zh-Hans</string></array>
<key>LSUIElement</key><true/>
</dict></plist>
PLIST
for language in "${LANGS[@]}"; do
  "$APP/Contents/MacOS/pixelrenders" "$OUT" "$language"
done
