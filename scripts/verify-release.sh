#!/bin/bash
# Verifies a published PixelSwitch release, after `scripts/release.sh` and CI:
#   scripts/verify-release.sh v1.5
# Checks: the release is published (not draft), the live appcast offers it,
# the appcast's length matches the DMG, the appcast's EdDSA signature verifies
# against SUPublicEDKey (so Sparkle will accept the update), and the app in the
# DMG has the right version, a signed helper, passes deep verify, and is
# notarized and stapled. Needs gh, Homebrew OpenSSL 3 (/opt/homebrew/bin/openssl).
#
# Since 2.0 it also checks the Linux assets of the tag (pixelswitch-linux-<version>.tar.gz,
# install-linux.sh, latest-linux.json): present, the feed's version and build match project.yml at the
# tag, size and SHA-256 match, the Ed25519 signature verifies against SUPublicEDKey (with the pure-Python
# verifier in linux/pixelswitch/ed25519.py, since the Mac's /usr/bin/openssl is LibreSSL), and the
# installer carries the same public key. `--linux-only` runs just those (it needs only gh, curl,
# python3 and git, so it also runs on a Linux machine):
#   scripts/verify-release.sh v2.0 [--linux-only]
# The exit status is 1 when a Linux check fails.
set -uo pipefail
TAG="$1"; WANT_VER="${TAG#v}"
LINUX_ONLY=0; [ "${2:-}" = "--linux-only" ] && LINUX_ONLY=1
R=BespokeWoodcraftStudio/PixelSwitch
cd "$(dirname "$0")/.."
D="$(mktemp -d)/release-$WANT_VER"; mkdir -p "$D"
echo "Working in $D"

linux_checks() {
  local failed=0 L="$D/linux"; mkdir -p "$L"
  ok()  { echo "  PASS  $1"; }
  bad() { echo "  FAIL  $1"; failed=1; }
  echo "== linux assets"
  local names; names=$(gh release view "$TAG" -R $R --json assets --jq '[.assets[].name]|join(" ")') || { bad "could not read the release"; return 1; }
  local TARBALL="pixelswitch-linux-$WANT_VER.tar.gz"
  for asset in "$TARBALL" install-linux.sh latest-linux.json; do
    case " $names " in *" $asset "*) ok "asset present: $asset" ;; *) bad "asset missing: $asset" ;; esac
  done
  [ "$failed" = 0 ] || return 1
  gh release download "$TAG" -R $R -p latest-linux.json -p install-linux.sh -p "$TARBALL" -D "$L" --clobber || { bad "download failed"; return 1; }
  # project.yml as it was at the tag (the local checkout may have moved on).
  git show "$TAG:project.yml" > "$L/project.yml" 2>/dev/null || curl -fsSL "https://raw.githubusercontent.com/$R/$TAG/project.yml" -o "$L/project.yml" || { bad "project.yml at $TAG not readable"; return 1; }
  python3 -I - "$L" "$TARBALL" "$WANT_VER" "$PWD/linux" <<'PY' || failed=1
import base64, hashlib, json, os, re, sys
folder, tarball, want, linux = sys.argv[1:5]
sys.path.insert(0, linux)
from pixelswitch import ed25519

def say(ok, text):
    print(("  PASS  " if ok else "  FAIL  ") + text)
    return ok

project = open(os.path.join(folder, "project.yml"), encoding="utf-8").read()
version = re.search(r'^\s*MARKETING_VERSION: "([^"]+)"', project, re.M).group(1)
build = int(re.search(r'^\s*CURRENT_PROJECT_VERSION: "([^"]+)"', project, re.M).group(1))
public = re.search(r'^\s*SUPublicEDKey: (\S+)', project, re.M).group(1)
feed = json.load(open(os.path.join(folder, "latest-linux.json"), encoding="utf-8"))
data = open(os.path.join(folder, tarball), "rb").read()
good = True
good &= say(version == want, f"project.yml at the tag says version {version} (tag {want})")
good &= say(feed.get("version") == version, f"latest-linux.json version {feed.get('version')} matches project.yml {version}")
good &= say(feed.get("build") == build, f"latest-linux.json build {feed.get('build')} matches project.yml {build}")
good &= say(feed.get("size") == len(data), f"size {feed.get('size')} equals the tarball ({len(data)} bytes)")
good &= say(feed.get("sha256") == hashlib.sha256(data).hexdigest(), "SHA-256 matches the tarball")
url = f"https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v{version}/{tarball}"
good &= say(feed.get("url") == url, "the feed's url is the tag's tarball")
signature = feed.get("signature", "")
good &= say(ed25519.verify(data, signature, public, use_openssl=False), "the Ed25519 signature verifies against SUPublicEDKey (pure-Python verifier)")
installer = open(os.path.join(folder, "install-linux.sh"), encoding="utf-8").read()
good &= say(f'\nSU_PUBLIC_ED_KEY="{public}"\n' in installer, "install-linux.sh carries SUPublicEDKey")
print(f"  install-linux.sh sha256 {hashlib.sha256(installer.encode()).hexdigest()} (it must be in the release notes)")
sys.exit(0 if good else 1)
PY
  return $failed
}

if [ "$LINUX_ONLY" = 1 ]; then linux_checks; exit $?; fi
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
# The compiled .strings are UTF-16, so grep finds nothing; read them with plutil.
for phrase in "Update automatically" "has no active subscription, PixelSwitch moves you"; do
  plutil -p "$APP/Contents/Resources/en.lproj/Localizable.strings" | grep -c "$phrase" | sed "s/^/strings with \"$phrase\": /"
done
hdiutil detach "$MNT" >/dev/null
linux_checks || exit 1
