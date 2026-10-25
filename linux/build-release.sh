#!/usr/bin/env bash
# Builds the PixelSwitch Linux release files (spec 5.4 to 5.6).
#
#   linux/build-release.sh [--out DIR] [--signing-key FILE|env:NAME]
#   linux/build-release.sh --sign-only --out DIR --signing-key FILE|env:NAME
#
# Default: writes into DIR (default: ./dist-linux)
#   pixelswitch-linux-<version>.tar.gz   deterministic: the same commit gives the same bytes
#   install-linux.sh                     a copy of linux/install.sh with the public key from project.yml
# With --signing-key it also writes
#   latest-linux.json                    version, build, url, size, sha256 and the Ed25519 signature
# --sign-only does not build: it signs the tarball already in DIR (the CI publish job, which must
# publish the exact bytes the unsigned package job built) and writes latest-linux.json.
#
# The signing key is the base64 text of the 32-byte Ed25519 seed, the same secret Sparkle signs
# the Mac DMG with (SPARKLE_EDDSA_KEY). Pass it as a file, or as env:NAME to read it from that
# environment variable (nothing is then written to disk except a 0600 temporary key that is
# removed on exit). It is never printed. After signing, the signature is checked against
# SUPublicEDKey from project.yml with the pure-Python verifier, so a wrong key can never publish.
#
# The tarball: tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@$SOURCE_DATE_EPOCH | gzip -n
# (GNU tar; on a Mac use `brew install gnu-tar`, which provides gtar). SOURCE_DATE_EPOCH defaults to
# the commit time of HEAD (or 1 when there is no git history).
set -euo pipefail
export LC_ALL=C
umask 022

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
REPOSITORY_URL="https://github.com/BespokeWoodcraftStudio/PixelSwitch"

die() { echo "build-release: $*" >&2; exit 1; }

STAGE=""
KEY_FILE=""
cleanup() {
  if [ -n "$STAGE" ]; then rm -rf "$STAGE"; fi
  if [ -n "$KEY_FILE" ]; then rm -f "$KEY_FILE"; fi
}
trap cleanup EXIT

OUT="./dist-linux"
SIGNING_KEY=""
SIGN_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --out) [ $# -ge 2 ] || die "--out needs a folder"; OUT="$2"; shift 2 ;;
    --signing-key) [ $# -ge 2 ] || die "--signing-key needs a file or env:NAME"; SIGNING_KEY="$2"; shift 2 ;;
    --sign-only) SIGN_ONLY=1; shift ;;
    -h|--help) sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done
if [ "$SIGN_ONLY" = 1 ] && [ -z "$SIGNING_KEY" ]; then die "--sign-only needs --signing-key"; fi

# -- version, build and public key come from project.yml (as build.yml reads them) ---------------------------
PROJECT="$ROOT/project.yml"
[ -f "$PROJECT" ] || die "project.yml not found at $PROJECT"
VERSION="$(grep -E '^[[:space:]]*MARKETING_VERSION: ' "$PROJECT" | head -1 | awk -F'"' '{print $2}')"
BUILD="$(grep -E '^[[:space:]]*CURRENT_PROJECT_VERSION: ' "$PROJECT" | head -1 | awk -F'"' '{print $2}')"
PUBLIC_KEY="$(grep -E '^[[:space:]]*SUPublicEDKey: ' "$PROJECT" | head -1 | sed -E 's/^[^:]*:[[:space:]]*"?([^"[:space:]]+)"?.*$/\1/')"
printf '%s' "$VERSION" | grep -Eq '^[0-9]{1,6}\.[0-9]{1,6}$' || die "project.yml MARKETING_VERSION is not two whole numbers: '$VERSION'"
printf '%s' "$BUILD" | grep -Eq '^[0-9]{1,9}$' || die "project.yml CURRENT_PROJECT_VERSION is not a whole number: '$BUILD'"
printf '%s' "$PUBLIC_KEY" | grep -Eq '^[A-Za-z0-9+/]{43}=$' || die "project.yml SUPublicEDKey is not a base64 32-byte key"

mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
NAME="pixelswitch-linux-$VERSION"
TARBALL="$OUT/$NAME.tar.gz"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

find_tar() {
  if tar --version 2>/dev/null | grep -q 'GNU tar'; then echo tar; return; fi
  if command -v gtar >/dev/null 2>&1 && gtar --version 2>/dev/null | grep -q 'GNU tar'; then echo gtar; return; fi
  die "GNU tar is needed for a deterministic archive (on a Mac: brew install gnu-tar)"
}

build_tarball() {
  local tar_cmd epoch stage
  tar_cmd="$(find_tar)"
  if [ -n "${SOURCE_DATE_EPOCH:-}" ]; then epoch="$SOURCE_DATE_EPOCH"
  else epoch="$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || true)"; [ -n "$epoch" ] || epoch=1; fi
  printf '%s' "$epoch" | grep -Eq '^[0-9]+$' || die "SOURCE_DATE_EPOCH is not a whole number: '$epoch'"

  STAGE="$(mktemp -d "${TMPDIR:-/tmp}/pixelswitch-build.XXXXXX")"
  stage="$STAGE"
  local top="$stage/$NAME"
  mkdir -p "$top"
  cp -R "$HERE/pixelswitch" "$top/pixelswitch"
  cp -R "$HERE/bin" "$top/bin"
  cp -R "$HERE/packaging" "$top/packaging"
  cp "$HERE/README.md" "$top/README.md"
  [ -f "$ROOT/NOTICE.md" ] && cp "$ROOT/NOTICE.md" "$top/NOTICE.md"
  find "$top" \( -name __pycache__ -o -name '*.pyc' -o -name .DS_Store \) -prune -exec rm -rf {} +
  printf 'version=%s\nbuild=%s\n' "$VERSION" "$BUILD" > "$top/VERSION"

  # Fixed modes, whatever the umask or the checkout said.
  find "$top" -type d -exec chmod 755 {} +
  find "$top" -type f -exec chmod 644 {} +
  chmod 755 "$top"/bin/*

  rm -f "$TARBALL"
  "$tar_cmd" --sort=name --owner=0 --group=0 --numeric-owner --format=gnu --mtime="@$epoch" \
      -cf - -C "$stage" "$NAME" | gzip -n -9 > "$TARBALL"
  echo "wrote $TARBALL ($(wc -c < "$TARBALL" | tr -d ' ') bytes, SOURCE_DATE_EPOCH=$epoch)"
}

write_installer() {
  # A copy of install.sh with SUPublicEDKey from project.yml on its SU_PUBLIC_ED_KEY line.
  local source="$HERE/install.sh" target="$OUT/install-linux.sh"
  grep -q '^SU_PUBLIC_ED_KEY="' "$source" || die "install.sh has no SU_PUBLIC_ED_KEY line"
  awk -v key="$PUBLIC_KEY" '/^SU_PUBLIC_ED_KEY="/ { print "SU_PUBLIC_ED_KEY=\"" key "\""; next } { print }' "$source" > "$target.tmp"
  chmod 755 "$target.tmp"
  mv "$target.tmp" "$target"
  bash -n "$target" || die "install-linux.sh does not parse"
  echo "wrote $target (sha256 $(sha256_of "$target"))"
}

# -- signing --------------------------------------------------------------------------------------------------
sign_file() {   # sign_file FILE -> base64 Ed25519 signature on stdout; the key never leaves this function
  local file="$1" key_text rc
  case "$SIGNING_KEY" in
    env:*) local var="${SIGNING_KEY#env:}"; key_text="${!var:-}"; [ -n "$key_text" ] || die "environment variable $var is empty or unset" ;;
    *) [ -f "$SIGNING_KEY" ] || die "signing key file not found: $SIGNING_KEY"; key_text="$(cat "$SIGNING_KEY")" ;;
  esac
  KEY_FILE="$(mktemp "${TMPDIR:-/tmp}/pixelswitch-key.XXXXXX")"
  chmod 600 "$KEY_FILE"
  local seed_len
  seed_len="$(printf '%s' "$key_text" | tr -d '[:space:]' | base64 -d 2>/dev/null | wc -c | tr -d ' ')"
  case "$seed_len" in
    32|64) ;;   # 64: seed followed by the public half; only the seed is used (as build.yml does)
    *) die "the signing key must decode to 32 bytes (a Sparkle seed); it decoded to $seed_len" ;;
  esac
  {
    printf '\x30\x2e\x02\x01\x00\x30\x05\x06\x03\x2b\x65\x70\x04\x22\x04\x20'   # PKCS8 prefix for an Ed25519 seed
    printf '%s' "$key_text" | tr -d '[:space:]' | base64 -d | head -c 32
  } > "$KEY_FILE"
  local openssl_bin="${OPENSSL:-openssl}"
  set +e
  "$openssl_bin" pkeyutl -sign -rawin -keyform DER -inkey "$KEY_FILE" -in "$file" 2>/dev/null | base64 | tr -d '\n'
  rc=${PIPESTATUS[0]}
  set -e
  rm -f "$KEY_FILE"
  KEY_FILE=""
  [ "$rc" -eq 0 ] || die "openssl could not sign (OpenSSL 3 is needed for Ed25519; set OPENSSL to its path)"
}

verify_with_python() {   # verify_with_python FILE SIGNATURE_B64 -> exit 0 only if SUPublicEDKey verifies it
  python3 -I - "$HERE" "$1" "$2" "$PUBLIC_KEY" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
from pixelswitch import ed25519
path, signature, public = sys.argv[2], sys.argv[3], sys.argv[4]
sys.exit(0 if ed25519.verify_file(path, signature, public, use_openssl=False) else 1)
PY
}

write_feed() {
  [ -f "$TARBALL" ] || die "no tarball at $TARBALL to sign"
  local signature size sha url
  signature="$(sign_file "$TARBALL")"
  [ "$(printf '%s' "$signature" | base64 -d 2>/dev/null | wc -c | tr -d ' ')" = 64 ] || die "the signature is not 64 bytes"
  verify_with_python "$TARBALL" "$signature" || die "the signature does not verify against SUPublicEDKey in project.yml; nothing written"
  size="$(wc -c < "$TARBALL" | tr -d ' ')"
  sha="$(sha256_of "$TARBALL")"
  # The updater only accepts this exact prefix (update_check.validate_feed), whatever repository ran the job.
  url="$REPOSITORY_URL/releases/download/v$VERSION/$NAME.tar.gz"
  printf '{\n  "version": "%s",\n  "build": %s,\n  "url": "%s",\n  "size": %s,\n  "sha256": "%s",\n  "signature": "%s"\n}\n' \
      "$VERSION" "$BUILD" "$url" "$size" "$sha" "$signature" > "$OUT/latest-linux.json.tmp"
  mv "$OUT/latest-linux.json.tmp" "$OUT/latest-linux.json"
  echo "wrote $OUT/latest-linux.json (signature verified against SUPublicEDKey)"
}

if [ "$SIGN_ONLY" = 0 ]; then
  build_tarball
  write_installer
fi
if [ -n "$SIGNING_KEY" ]; then
  write_feed
fi
echo "version=$VERSION build=$BUILD"
