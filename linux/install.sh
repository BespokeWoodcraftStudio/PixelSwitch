#!/usr/bin/env bash
# PixelSwitch for Linux: installer (spec 5.5). Published with each release as install-linux.sh.
#
#   curl -fsSL https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/install-linux.sh | bash
#
# Installs for the current user only. No sudo, ever; it never installs system packages (it tells you
# which ones are missing). Everything lands under your home folder:
#   ~/.local/share/pixelswitch/app/<version>-<build>/   the program, with a `current` link
#   ~/.local/bin/pixelswitch, pixelswitchd, pixelswitch-tray, pixelswitch-settings
#   ~/.config/systemd/user/pixelswitch.service          the background service (enabled and started)
#   ~/.config/autostart/pixelswitch-tray.desktop        the tray icon at login
#   ~/.local/share/applications/pixelswitch-settings.desktop, ~/.local/share/icons/hicolor/...
#
# Options:
#   --from TARBALL [--signature FILE]   install a local build (labelled unverified without a signature)
#   --uninstall [--purge]               remove the program; keep your saved logins unless --purge
#   --no-systemd                        do not touch the systemd user manager (tests)
#   --no-autostart                      no autostart entry and no tray started (tests)
#   -h, --help
#
# A download is checked before anything is unpacked: its size, its SHA-256 and its Ed25519 signature
# (with openssl 3, against the public key below). If openssl cannot check an Ed25519 signature, the
# installer stops. Auto-switch stays OFF after an install.
#
# For tests only: PIXELSWITCH_INSTALL_SKIP_CHECKS=1 skips the Python and GTK checks;
# PIXELSWITCH_INSTALL_PUBLIC_KEY and PIXELSWITCH_OPENSSL are honoured only together with --from.
set -euo pipefail
umask 077
# bash 5.2 treats & in a replacement as the matched text; a home path must be copied as it is.
shopt -u patsub_replacement 2>/dev/null || true

# SUPublicEDKey from project.yml (build-release.sh writes the project.yml value into install-linux.sh).
SU_PUBLIC_ED_KEY="rk8t6LzU0KnCEzj6+xCXL5XC0CUSfLwtUneaNWX+5/U="
REPOSITORY_URL="https://github.com/BespokeWoodcraftStudio/PixelSwitch"
FEED_URL="$REPOSITORY_URL/releases/latest/download/latest-linux.json"
MAX_TARBALL_BYTES=$((50 * 1024 * 1024))

say()  { printf '%s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf 'PixelSwitch install: %s\n' "$*" >&2; exit 1; }

FROM=""
SIGNATURE_FILE=""
UNINSTALL=0
PURGE=0
NO_SYSTEMD=0
NO_AUTOSTART=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) [ $# -ge 2 ] || die "--from needs a tarball"; FROM="$2"; shift 2 ;;
    --signature) [ $# -ge 2 ] || die "--signature needs a file"; SIGNATURE_FILE="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --no-autostart) NO_AUTOSTART=1; shift ;;
    -h|--help) sed -n '2,/^set -euo pipefail/p' "${BASH_SOURCE[0]:-$0}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done
[ -z "$SIGNATURE_FILE" ] || [ -n "$FROM" ] || die "--signature goes with --from"
[ "$PURGE" = 0 ] || [ "$UNINSTALL" = 1 ] || die "--purge goes with --uninstall"

[ -n "${HOME:-}" ] && [ -d "$HOME" ] || die "HOME is not set to a folder"
[ "$(id -u)" != 0 ] || die "run this as your own user, not as root (it installs into your home folder and never uses sudo)"

DATA="$HOME/.local/share/pixelswitch"
APP="$DATA/app"
BIN="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
AUTOSTART_DIR="$HOME/.config/autostart"
APPLICATIONS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor"
LAUNCHERS="pixelswitch pixelswitchd pixelswitch-tray pixelswitch-settings"

WORK=""
cleanup() { if [ -n "$WORK" ]; then rm -rf "$WORK"; fi; }
trap cleanup EXIT

systemd_user_ready() { command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; }

# A link in ~/.local/bin is ours when it points into a PixelSwitch app folder.
is_ours() { local target; target="$(readlink "$1" 2>/dev/null || true)"; case "$target" in */pixelswitch/app/*) return 0 ;; esac; return 1; }

# -- uninstall ----------------------------------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
  if [ "$NO_SYSTEMD" = 0 ] && systemd_user_ready; then
    systemctl --user disable --now pixelswitch.service >/dev/null 2>&1 || true
    rm -f "$UNIT_DIR/pixelswitch.service"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
  else
    rm -f "$UNIT_DIR/pixelswitch.service"
  fi
  for name in $LAUNCHERS; do
    if [ -L "$BIN/$name" ] && is_ours "$BIN/$name"; then rm -f "$BIN/$name"; fi
  done
  rm -f "$AUTOSTART_DIR/pixelswitch-tray.desktop" "$APPLICATIONS_DIR/pixelswitch-settings.desktop"
  rm -f "$ICONS_DIR/scalable/apps/pixelswitch.svg" "$ICONS_DIR/symbolic/apps/pixelswitch-symbolic.svg"
  rm -rf "$APP"
  rm -f "$DATA/rollback.py" "$DATA/install-linux.sh"
  if [ "$PURGE" = 1 ]; then
    rm -rf "$HOME/.config/pixelswitch" "$HOME/.local/state/pixelswitch" "$DATA"
    say "PixelSwitch is removed, with its settings, accounts and saved logins."
  else
    say "PixelSwitch is removed. Your settings, accounts and saved logins are kept in"
    say "  ~/.config/pixelswitch and ~/.local/share/pixelswitch (backups.json)."
    say "Run this again with --uninstall --purge to delete them too."
  fi
  say "Claude Code's own login (~/.claude) was not touched. If the tray icon is still showing, quit it from its menu."
  exit 0
fi

# -- what this computer needs ----------------------------------------------------------------------------------------
check_python_and_gtk() {
  command -v python3 >/dev/null 2>&1 || die "Python 3 is missing. Install it with: sudo apt install python3 (this installer never uses sudo)."
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || die "PixelSwitch needs Python 3.12 or newer; this computer has $(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])'). Ubuntu 24.04 and 26.04 have it."
  local missing=""
  python3 -c 'import gi' 2>/dev/null || die "PyGObject is missing. Install it with: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk-3.0 gir1.2-dbusmenu-glib-0.4 (this installer never uses sudo)."
  # Gtk 3 and Gtk 4 cannot share a process, so each is checked on its own.
  python3 -c 'import gi; gi.require_version("Gtk", "4.0")' 2>/dev/null || missing="$missing gir1.2-gtk-4.0"
  python3 -c 'import gi; gi.require_version("Adw", "1")' 2>/dev/null || missing="$missing gir1.2-adw-1"
  python3 -c 'import gi; gi.require_version("Gtk", "3.0")' 2>/dev/null || missing="$missing gir1.2-gtk-3.0"
  python3 -c 'import gi; gi.require_version("Dbusmenu", "0.4")' 2>/dev/null || missing="$missing gir1.2-dbusmenu-glib-0.4"
  if [ -n "$missing" ]; then
    die "PixelSwitch needs these packages:$missing. Install them with: sudo apt install$missing (this installer never uses sudo)."
  fi
}

check_tray_watcher() {
  # Only a warning: the service and the command line work without a tray.
  if command -v gdbus >/dev/null 2>&1 && [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    if ! gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
         --method org.freedesktop.DBus.NameHasOwner org.kde.StatusNotifierWatcher 2>/dev/null | grep -q true; then
      warn "this desktop shows no tray icons (nothing owns org.kde.StatusNotifierWatcher). On Ubuntu, enable the AppIndicator extension; the service, the command line and the settings window work without it."
    fi
  else
    warn "no desktop session was found from here, so the tray icon was not checked."
  fi
}

if [ "${PIXELSWITCH_INSTALL_SKIP_CHECKS:-0}" != 1 ]; then
  check_python_and_gtk
  check_tray_watcher
fi
command -v python3 >/dev/null 2>&1 || die "Python 3 is missing."

# -- getting the tarball -------------------------------------------------------------------------------------------------
mkdir -p "$DATA"
chmod 700 "$DATA" 2>/dev/null || true
WORK="$(mktemp -d "$DATA/.install.XXXXXX")"

fetch() {  # fetch URL OUT [MAXBYTES]
  local url="$1" out="$2" max="${3:-}"
  if command -v curl >/dev/null 2>&1; then
    if [ -n "$max" ]; then curl -fsSL --proto '=https' --tlsv1.2 --max-filesize "$max" -o "$out" "$url"
    else curl -fsSL --proto '=https' --tlsv1.2 -o "$out" "$url"; fi
  elif command -v wget >/dev/null 2>&1; then
    wget -q --https-only -O "$out" "$url"
  else
    die "neither curl nor wget is installed (sudo apt install curl), so nothing can be downloaded. You can also pass --from <tarball>."
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

OPENSSL_BIN="openssl"
PUBLIC_KEY="$SU_PUBLIC_ED_KEY"
if [ -n "$FROM" ]; then
  [ -z "${PIXELSWITCH_OPENSSL:-}" ] || OPENSSL_BIN="$PIXELSWITCH_OPENSSL"
  [ -z "${PIXELSWITCH_INSTALL_PUBLIC_KEY:-}" ] || PUBLIC_KEY="$PIXELSWITCH_INSTALL_PUBLIC_KEY"
fi

verify_signature() {  # verify_signature FILE SIGNATURE_BASE64: fails closed
  local file="$1" signature="$2"
  command -v "$OPENSSL_BIN" >/dev/null 2>&1 || die "openssl is missing, so the download's signature cannot be checked. Install it (sudo apt install openssl) and run this again; nothing was installed."
  printf '%s' "$PUBLIC_KEY" | grep -Eq '^[A-Za-z0-9+/]{43}=$' || die "the public key built into this installer is not valid"
  printf -- '-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA%s\n-----END PUBLIC KEY-----\n' "$PUBLIC_KEY" > "$WORK/pub.pem"
  printf '%s' "$signature" | tr -d '[:space:]' | base64 -d > "$WORK/sig.bin" 2>/dev/null || die "the signature is not valid base64"
  [ "$(wc -c < "$WORK/sig.bin" | tr -d ' ')" = 64 ] || die "the signature is not 64 bytes"
  local result
  if ! result="$("$OPENSSL_BIN" pkeyutl -verify -pubin -inkey "$WORK/pub.pem" -rawin -in "$file" -sigfile "$WORK/sig.bin" 2>&1)"; then
    case "$result" in
      *"Signature Verification Failure"*) die "the download's signature does not match PixelSwitch's public key. Nothing was installed." ;;
      *) die "this computer's openssl cannot check an Ed25519 signature ($("$OPENSSL_BIN" version 2>/dev/null || echo unknown)); OpenSSL 3 is needed (Ubuntu 24.04 and 26.04 have it). Nothing was installed." ;;
    esac
  fi
  case "$result" in *"Verified Successfully"*) ;; *) die "the signature could not be confirmed. Nothing was installed." ;; esac
}

TARBALL=""
LABEL=""
FEED_VERSION=""
FEED_BUILD=""
if [ -n "$FROM" ]; then
  [ -f "$FROM" ] || die "no such file: $FROM"
  TARBALL="$(cd "$(dirname "$FROM")" && pwd)/$(basename "$FROM")"
  if [ -n "$SIGNATURE_FILE" ]; then
    [ -f "$SIGNATURE_FILE" ] || die "no such file: $SIGNATURE_FILE"
    if [ "$(wc -c < "$SIGNATURE_FILE" | tr -d ' ')" = 64 ]; then SIGNATURE_TEXT="$(base64 < "$SIGNATURE_FILE" | tr -d '\n')"; else SIGNATURE_TEXT="$(cat "$SIGNATURE_FILE")"; fi
    verify_signature "$TARBALL" "$SIGNATURE_TEXT"
    LABEL="verified signature"
  else
    LABEL="UNVERIFIED local build (no signature was given)"
    warn "installing an unverified local build."
  fi
else
  say "Checking the latest PixelSwitch for Linux..."
  fetch "$FEED_URL" "$WORK/latest-linux.json" 65536 || die "could not download $FEED_URL (is there a Linux release yet?). Nothing was installed."
  FEED_FIELDS="$(python3 -I - "$WORK/latest-linux.json" "$REPOSITORY_URL" "$MAX_TARBALL_BYTES" <<'PY'
import json, re, sys
path, repository, limit = sys.argv[1], sys.argv[2], int(sys.argv[3])
try:
    feed = json.load(open(path, encoding="utf-8"))
    version, build, url = feed["version"], feed["build"], feed["url"]
    size, sha, signature = feed["size"], feed["sha256"], feed["signature"]
    assert isinstance(version, str) and re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}", version)
    assert isinstance(build, int) and not isinstance(build, bool) and 0 <= build < 10**9
    prefix = f"{repository}/releases/download/v{version}/"
    assert isinstance(url, str) and url.startswith(prefix) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", url[len(prefix):])
    assert isinstance(size, int) and not isinstance(size, bool) and 0 < size <= limit
    assert isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha)
    assert isinstance(signature, str) and re.fullmatch(r"[A-Za-z0-9+/]{86}==", signature)
except Exception:
    sys.exit("the release feed is not what PixelSwitch expects")
print(version, build, url, size, sha.lower(), signature)
PY
)" || die "the release feed (latest-linux.json) is not what this installer expects. Nothing was installed."
  read -r FEED_VERSION FEED_BUILD FEED_URL_TARBALL FEED_SIZE FEED_SHA FEED_SIGNATURE <<<"$FEED_FIELDS"
  say "Downloading PixelSwitch $FEED_VERSION (build $FEED_BUILD)..."
  TARBALL="$WORK/pixelswitch-linux.tar.gz"
  fetch "$FEED_URL_TARBALL" "$TARBALL" "$FEED_SIZE" || die "the download failed. Nothing was installed."
  [ "$(wc -c < "$TARBALL" | tr -d ' ')" = "$FEED_SIZE" ] || die "the download is not the size the release promised. Nothing was installed."
  [ "$(sha256_of "$TARBALL")" = "$FEED_SHA" ] || die "the download does not match its SHA-256. Nothing was installed."
  verify_signature "$TARBALL" "$FEED_SIGNATURE"
  LABEL="verified: size, SHA-256 and Ed25519 signature"
fi

# -- unpacking, checking, installing --------------------------------------------------------------------------------------
EXTRACT="$WORK/extracted"
mkdir -p "$EXTRACT"
# Reject names that could leave the folder before unpacking anything.
if tar -tzf "$TARBALL" 2>/dev/null | grep -Eq '(^|/)\.\.(/|$)|^/'; then die "the archive contains a path outside its folder. Nothing was installed."; fi
tar -xzf "$TARBALL" -C "$EXTRACT" --no-same-owner 2>/dev/null || die "the archive could not be unpacked. Nothing was installed."
if [ -f "$EXTRACT/VERSION" ]; then ROOT="$EXTRACT"
else
  set -- "$EXTRACT"/*
  [ $# -eq 1 ] && [ -d "$1" ] && [ ! -L "$1" ] && [ -f "$1/VERSION" ] || die "the archive has no VERSION file at its top. Nothing was installed."
  ROOT="$1"
fi
VERSION=""; BUILD=""
while IFS='=' read -r key value; do
  case "$key" in version) VERSION="$value" ;; build) BUILD="$value" ;; esac
done < "$ROOT/VERSION"
printf '%s' "$VERSION" | grep -Eq '^[0-9]{1,6}\.[0-9]{1,6}$' || die "the archive's VERSION file is not readable. Nothing was installed."
printf '%s' "$BUILD" | grep -Eq '^[0-9]{1,9}$' || die "the archive's VERSION file is not readable. Nothing was installed."
if [ -n "$FEED_VERSION" ] && { [ "$VERSION" != "$FEED_VERSION" ] || [ "$BUILD" != "$FEED_BUILD" ]; }; then
  die "the archive's VERSION does not match the release feed. Nothing was installed."
fi
for needed in pixelswitch/__init__.py bin/pixelswitchd bin/pixelswitch packaging/pixelswitch.service; do
  [ -f "$ROOT/$needed" ] || die "the archive is missing $needed. Nothing was installed."
done
find "$ROOT" -type d -exec chmod 755 {} + ; find "$ROOT" -type f -exec chmod u+rw,go-w {} +
chmod 755 "$ROOT"/bin/*

NAME="$VERSION-$BUILD"
TARGET="$APP/$NAME"
mkdir -p "$APP"
chmod 700 "$APP" 2>/dev/null || true

# The program must start before anything points at it.
SELF_CHECK="$(python3 -I "$ROOT/bin/pixelswitchd" --self-check 2>&1)" || die "the new version failed its self-check ($SELF_CHECK). Nothing was installed."
case "$SELF_CHECK" in "pixelswitchd $VERSION (build $BUILD)"*) ;; *) die "the new version reported '$SELF_CHECK' instead of $VERSION (build $BUILD). Nothing was installed." ;; esac

CURRENT_NAME=""
if [ -L "$APP/current" ]; then CURRENT_NAME="$(basename "$(readlink "$APP/current")")"; fi
# Installing the same build again replaces its folder (the service is restarted below).
if [ -d "$TARGET" ]; then rm -rf "$TARGET"; fi
mv "$ROOT" "$TARGET"

flip() {  # flip LINK NAME: atomically point a link at a relative name
  local link="$1" name="$2"
  ln -sfn "$name" "$link.new.$$"
  mv -Tf "$link.new.$$" "$link" 2>/dev/null || { rm -f "$link"; mv "$link.new.$$" "$link"; }
}
if [ -n "$CURRENT_NAME" ] && [ "$CURRENT_NAME" != "$NAME" ]; then flip "$APP/previous" "$CURRENT_NAME"; fi
flip "$APP/current" "$NAME"

# Launchers in ~/.local/bin (links through `current`, so an update needs no new links).
mkdir -p "$BIN"
for name in $LAUNCHERS; do
  link="$BIN/$name"
  if [ -e "$link" ] || [ -L "$link" ]; then
    if [ -L "$link" ] && is_ours "$link"; then rm -f "$link"
    else die "$link already exists and is not PixelSwitch's. Move it away and run this again. (The program itself is installed in $TARGET.)"; fi
  fi
  ln -s "$APP/current/bin/$name" "$link"
done

# The rollback check lives outside the versioned folders, so a broken new version cannot stop it from running.
cp "$TARGET/pixelswitch/rollback.py" "$DATA/rollback.py.new" && mv -f "$DATA/rollback.py.new" "$DATA/rollback.py"
KEPT_INSTALLER=0
if [ -f "${BASH_SOURCE[0]:-}" ]; then
  cp "${BASH_SOURCE[0]}" "$DATA/install-linux.sh.new" && chmod 755 "$DATA/install-linux.sh.new" && mv -f "$DATA/install-linux.sh.new" "$DATA/install-linux.sh" && KEPT_INSTALLER=1
fi

# Icons and the settings entry in the applications menu.
mkdir -p "$ICONS_DIR/scalable/apps" "$ICONS_DIR/symbolic/apps" "$APPLICATIONS_DIR"
cp "$TARGET/packaging/icons/hicolor/scalable/apps/pixelswitch.svg" "$ICONS_DIR/scalable/apps/pixelswitch.svg"
cp "$TARGET/packaging/icons/hicolor/symbolic/apps/pixelswitch-symbolic.svg" "$ICONS_DIR/symbolic/apps/pixelswitch-symbolic.svg"
write_desktop() {  # write_desktop SOURCE DEST: the Exec path made absolute
  local content; content="$(cat "$1")"
  printf '%s\n' "${content//@HOME@/$HOME}" > "$2.new"
  chmod 644 "$2.new"
  mv -f "$2.new" "$2"
}
write_desktop "$TARGET/packaging/pixelswitch-settings.desktop" "$APPLICATIONS_DIR/pixelswitch-settings.desktop"
if [ "$NO_AUTOSTART" = 0 ]; then
  mkdir -p "$AUTOSTART_DIR"
  write_desktop "$TARGET/packaging/pixelswitch-tray.desktop" "$AUTOSTART_DIR/pixelswitch-tray.desktop"
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1 && [ -f "$ICONS_DIR/index.theme" ]; then
  gtk-update-icon-cache -q -t -f "$ICONS_DIR" >/dev/null 2>&1 || true
fi

# The service.
SERVICE_NOTE=""
if [ "$NO_SYSTEMD" = 1 ]; then
  SERVICE_NOTE="The background service was not set up (--no-systemd)."
elif systemd_user_ready; then
  mkdir -p "$UNIT_DIR"
  cp "$TARGET/packaging/pixelswitch.service" "$UNIT_DIR/pixelswitch.service.new" && mv -f "$UNIT_DIR/pixelswitch.service.new" "$UNIT_DIR/pixelswitch.service"
  systemctl --user daemon-reload
  systemctl --user enable pixelswitch.service >/dev/null 2>&1 || warn "could not enable the service at login."
  systemctl --user restart pixelswitch.service || warn "the service did not start; look with: systemctl --user status pixelswitch.service"
  SERVICE_NOTE="The background service is running (systemctl --user status pixelswitch.service)."
else
  SERVICE_NOTE="systemd's user manager is not available here, so the background service was not set up. Start it yourself with: $BIN/pixelswitchd"
fi

# The tray, when there is a graphical session to show it in.
TRAY_NOTE=""
if [ "$NO_AUTOSTART" = 1 ]; then
  TRAY_NOTE=""
elif [ -n "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
  if command -v pgrep >/dev/null 2>&1 && pgrep -u "$(id -u)" -f "pixelswitch-tray" >/dev/null 2>&1; then
    TRAY_NOTE="The tray icon is already running; it restarts itself on the new version."
  else
    if command -v setsid >/dev/null 2>&1; then setsid "$BIN/pixelswitch-tray" >/dev/null 2>&1 < /dev/null &
    else nohup "$BIN/pixelswitch-tray" >/dev/null 2>&1 < /dev/null & fi
    TRAY_NOTE="The tray icon is starting; it also starts at every login."
  fi
else
  TRAY_NOTE="No graphical session was found, so the tray icon was not started; it starts at your next login."
fi

# -- done ---------------------------------------------------------------------------------------------------------------------------
say ""
say "PixelSwitch $VERSION (build $BUILD) is installed ($LABEL)."
[ -z "$SERVICE_NOTE" ] || say "$SERVICE_NOTE"
[ -z "$TRAY_NOTE" ] || say "$TRAY_NOTE"
case ":$PATH:" in *":$BIN:"*) ;; *) say "Add $BIN to your PATH to run pixelswitch from a terminal." ;; esac
say ""
say "Next step: save the account Claude Code is signed in as with"
say "    pixelswitch accounts add-current"
say "or use Sign in another account from the tray icon's menu."
say ""
say "Auto-switch is OFF. Turn it on in the settings window (PixelSwitch Settings) or with"
say "    pixelswitch settings set autoSwitch.enabled true"
say ""
say "One thing to know: other tools on this computer that use the same Claude Code login (for"
say "example the CabDesign farm's Claude Code workers) use whichever account PixelSwitch has switched to."
say "Switching changes their account too, so try it while they are idle."
say ""
if [ "$KEPT_INSTALLER" = 1 ]; then
  say "To remove it: $DATA/install-linux.sh --uninstall   (add --purge to delete saved logins too)"
else
  say "To remove it: run install-linux.sh again with --uninstall   (add --purge to delete saved logins too)"
fi
