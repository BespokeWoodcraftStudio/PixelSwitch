#!/bin/bash
# Verifies a published PixelSwitch release, after `scripts/release.sh` and CI:
#   scripts/verify-release.sh v1.5
# Checks: the release is published (not draft), the live appcast offers it,
# the appcast's length matches the DMG, the appcast's EdDSA signature verifies
# against SUPublicEDKey (so Sparkle will accept the update), and the app in the
# DMG has the right version, a signed helper, passes deep verify, and is
# notarized and stapled. Needs gh, Homebrew OpenSSL 3 (/opt/homebrew/bin/openssl).
set -uo pipefail
TAG="$1"; WANT_VER="${TAG#v}"
R=BespokeWoodcraftStudio/PixelSwitch
cd "$(dirname "$0")/.."
D="$(mktemp -d)/release-$WANT_VER"; mkdir -p "$D"
echo "Working in $D"
echo "== release"; gh release view "$TAG" -R $R --json isDraft,isPrerelease,assets --jq '"draft=\(.isDraft) pre=\(.isPrerelease) assets=\([.assets[].name]|join(","))"'
gh release list -R $R -L 1
echo "== feed"; curl -sL "https://github.com/$R/releases/latest/download/appcast.xml" -o "$D/appcast.xml"
grep -oE 'sparkle:(version|shortVersionString)>[^<]+' "$D/appcast.xml" | head -2
gh release download "$TAG" -R $R -p PixelSwitch.dmg -D "$D" || exit 1
LEN=$(grep -oE 'length="[0-9]+"' "$D/appcast.xml" | grep -oE '[0-9]+' | head -1)
echo "appcast length $LEN, dmg $(stat -f %z "$D/PixelSwitch.dmg")"
PUB=$(grep "SUPublicEDKey" project.yml | sed -E 's/.*: *"?([^"]+)"?.*/\1/')
SIG=$(grep -oE 'sparkle:edSignature="[^"]+"' "$D/appcast.xml" | head -1 | sed -E 's/.*="([^"]+)"/\1/')
{ printf '302a300506032b6570032100' | xxd -r -p; echo "$PUB" | base64 -d; } > "$D/pub.der"
echo "$SIG" | base64 -d > "$D/sig.bin"
/opt/homebrew/bin/openssl pkey -pubin -inform DER -in "$D/pub.der" -out "$D/pub.pem"
echo "== EdDSA"; /opt/homebrew/bin/openssl pkeyutl -verify -pubin -inkey "$D/pub.pem" -rawin -in "$D/PixelSwitch.dmg" -sigfile "$D/sig.bin"
MNT=$(mktemp -d); hdiutil attach -nobrowse -readonly -mountpoint "$MNT" "$D/PixelSwitch.dmg" >/dev/null
APP="$MNT/PixelSwitch.app"
echo "== app"; /usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" -c "Print CFBundleVersion" -c "Print SUScheduledCheckInterval" "$APP/Contents/Info.plist"
codesign -dvv "$APP/Contents/Helpers/pixelswitch" 2>&1 | grep -E "^Identifier|^TeamIdentifier"
codesign --verify --deep --strict "$APP" && echo "deep verify OK"
spctl -a -t exec -vv "$APP" 2>&1 | sed -n 2p
spctl -a -t open --context context:primary-signature -v "$D/PixelSwitch.dmg" 2>&1 | sed -n 2p
xcrun stapler validate "$D/PixelSwitch.dmg" 2>&1 | tail -1
grep -c "Update automatically" "$APP/Contents/Resources/en.lproj/Localizable.strings" 2>/dev/null | sed 's/^/strings with "Update automatically": /'
hdiutil detach "$MNT" >/dev/null
