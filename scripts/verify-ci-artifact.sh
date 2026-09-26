#!/bin/bash
# Downloads a CI run's DMG (a branch build, before any release) and checks the
# app and its pixelswitch helper:  scripts/verify-ci-artifact.sh <run-id>
set -uo pipefail
RUN="$1"
OUT="$(mktemp -d)/artifact-$RUN"
rm -rf "$OUT"; mkdir -p "$OUT"
gh run download "$RUN" -R BespokeWoodcraftStudio/PixelSwitch -n PixelSwitch-macOS -D "$OUT" || exit 1
DMG=$(find "$OUT" -name '*.dmg' | head -1)
echo "DMG: $DMG"
MNT=$(mktemp -d)
hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$DMG" >/dev/null || exit 1
APP="$MNT/PixelSwitch.app"
H="$APP/Contents/Helpers/pixelswitch"
echo "== helper"; ls -l "$H"; file "$H"
echo "== helper signature"; codesign -dvv "$H" 2>&1 | grep -E "^Identifier|^TeamIdentifier|flags=|^Authority=Developer ID Application|^Timestamp"
echo "== helper runs"; "$H" --help 2>&1 | head -3; echo "version: $("$H" --version 2>&1)"
echo "== app deep verify"; codesign --verify --deep --strict --verbose=2 "$APP" 2>&1 | tail -2
echo "== gatekeeper app"; spctl -a -t exec -vv "$APP" 2>&1 | head -3
echo "== gatekeeper dmg"; spctl -a -t open --context context:primary-signature -v "$DMG" 2>&1 | head -2
echo "== staple"; xcrun stapler validate "$DMG" 2>&1 | tail -1
echo "== version"; /usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" -c "Print CFBundleVersion" "$APP/Contents/Info.plist"
hdiutil detach "$MNT" >/dev/null
echo "DMG_PATH=$DMG"
