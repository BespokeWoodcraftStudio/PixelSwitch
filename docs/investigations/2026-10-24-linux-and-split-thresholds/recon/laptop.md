# Recon: the Linux test laptop (ahmed-aorus-15p-xd)

Surveyed 2026-10-24, 17:09 to 17:11 PDT (2026-10-25 00:09 UTC), read only, over key-based SSH
(`ssh -o BatchMode=yes ahmed@ahmed-aorus-15p-xd.tail19d9f2.ts.net`). SSH worked first time with no password.

What was NOT done, by design: nothing installed, removed, started or stopped; no `claude` command that
changes state; no Anthropic API call; no screenshot taken; `~/Documents/linux-laptop-login.txt` was not
opened (key auth made it unnecessary, and it is a secret file); `~/cabdesign-farm-account.sh` was not
opened. No token, email, account id or key value appears in this file (key names and types only).
The only `claude` command run was `claude --version`, `claude auth --help` and `claude auth status --json`
(read only; output filtered to the allowed fields).

Every item below is a finding from a command run on the laptop on the date above unless marked "not tested".

---

## 0. The ten facts the spec most needs

1. **Ubuntu 26.04.1 LTS (resolute), kernel 7.0.0-38-generic, GNOME Shell 50.1, Wayland session, Python 3.14.4, PyGObject 3.56.2.** 16 logical CPUs, 30 GiB RAM, 836 GB free.
2. **The AppIndicator typelib is NOT installed.** `gi.require_version('AyatanaAppIndicator3','0.1')` and `('AppIndicator3','0.1')` both fail ("Namespace ... not available"). The runtime library `libayatana-appindicator3-1` (0.5.94-1build1) IS installed; only `gir1.2-ayatanaappindicator3-0.1` is missing. Apt has it (candidate 0.5.94-1build1, `resolute/main`). Installing needs sudo, and **sudo requires a password here** (`sudo -n true` refused).
3. **The tray itself works.** GNOME extension `ubuntu-appindicators@ubuntu.com` is enabled and `State: ACTIVE`; `org.kde.StatusNotifierWatcher` has an owner on the session bus; several StatusNotifier items are already registered (including the Claude desktop app's).
4. **Gtk 3.0, Gtk 4.0, Adw 1 (1.9), Notify 0.7, Secret 1, Handy 1, Dbusmenu 0.4 typelibs all import.** A process can load Gtk 3 OR Gtk 4, never both, and AppIndicator is Gtk 3 only (see section 3.4 for what that forces).
5. **Claude Code 2.1.310, native install, credentials in a PLAIN FILE, not a keyring.** `~/.claude/.credentials.json` (mode 600, 524 bytes) holds one top-level key, `claudeAiOauth`, with `accessToken`, `refreshToken`, `expiresAt` (int, milliseconds), `refreshTokenExpiresAt` (int), `scopes` (list of 6 strings), `subscriptionType`, `rateLimitTier`. Account identity lives separately in `~/.claude.json` under `oauthAccount` (20 keys). `CLAUDE_CONFIG_DIR` is not set anywhere.
6. **The laptop is logged in** (`loggedIn: true`, `authMethod: claude.ai`, `apiProvider: firstParty`, `subscriptionType: max`, rate tier `default_claude_max_20x`) and the access token had about 372 minutes left at survey time.
7. **No `claude` CLI process is running right now**; only the Claude desktop (Electron) app is up, plus the CabDesign farm's node test workers. So nothing was mid-flight on the credential file.
8. **This is a working machine, not an idle test box.** The founder's CabDesign "farm" runs jobs on it (load average 3.6 / 7.1 / 5.4 on 16 cores, two failed farm job scopes in the user systemd manager, `~/farm`, `~/.cabdesign-farm`). A PixelSwitch test that swaps the live `~/.claude/.credentials.json` could land under a farm job. Test with an isolated HOME (section 9).
9. **`~/Projects/PixelSwitch` already exists on the laptop**: a clean `main` checkout at `35f8a99` (the 1.9 handover commit), remote `https://github.com/BespokeWoodcraftStudio/PixelSwitch.git`, and `gh` is logged in with `repo` and `workflow` scopes. There is no `linux/` directory yet. So deploying the Linux build to the laptop is a `git pull`.
10. **No unattended screenshot route works.** `grim`, `gnome-screenshot`, `scrot`, `maim`, `wf-recorder` are all absent; the GNOME Screenshot D-Bus service exists but is not tested and GNOME restricts it; the portals need an on-screen consent click. Visual QA of the tray menu from SSH is therefore limited (section 8).

---

## 1. (a) Python, PyGObject, typelibs, packages

### 1.1 Interpreter

| Item | Result |
|---|---|
| `python3 --version` | `Python 3.14.4` at `/usr/bin/python3` |
| `import gi` | OK, `gi.__version__` = `3.56.2` (package `python3-gi 3.56.2-1`) |
| `python3-venv` / `python3-pip` | installed (`python3-venv 3.14.3-0ubuntu2`, `python3-pip 25.1.1+dfsg-1ubuntu2`); `python3 -m venv` and `ensurepip` both work |
| `uv` | `/home/ahmed/.local/bin/uv`, 0.13.1 |
| Other tools on PATH | `curl wget git(2.53.0) jq make gcc gdbus notify-send dbus-send xdg-open zenity`, Node v24.22.0 (nvm), `gh 2.102.0` |
| Not found | `swift`, `swiftc`, `pipx`, `secret-tool`, `xdotool`, `ydotool`, `wtype`, `wl-copy` |

PyGObject warns "Gtk was imported without specifying a version first" if a module does `from gi.repository import Gtk`
before `require_version`; any code must call `gi.require_version` first or the warning (and a possible wrong
version pick) follows.

### 1.2 Typelibs, each tested in its OWN fresh interpreter

(My first all-in-one test wrongly showed Gtk 4.0 failing; that was only because Gtk 3.0 was already loaded in that
same process. The per-process results below are the true ones.)

| Namespace / version | Result | Notes |
|---|---|---|
| Gtk 3.0 | OK | Gtk 3.24.52 (`gir1.2-gtk-3.0 3.24.52-0ubuntu1`) |
| Gtk 4.0 | OK | Gtk 4.22.4 (`gir1.2-gtk-4.0 4.22.4+ds-0ubuntu0.1`) |
| Adw 1 | OK | libadwaita 1.9 (`gir1.2-adw-1 1.9.1-0ubuntu0.1`); loads together with Gtk 4.0 |
| AyatanaAppIndicator3 0.1 | **FAIL** | "Namespace AyatanaAppIndicator3 not available" |
| AppIndicator3 0.1 | **FAIL** | same |
| Notify 0.7 | OK | `gir1.2-notify-0.7 0.8.8-1`, `libnotify-bin` installed |
| Secret 1 | OK | `gir1.2-secret-1 0.21.7-2build1`, `libsecret-1-0` installed |
| Handy 1 | OK | libhandy, a Gtk 3 widget library |
| Dbusmenu 0.4 | OK | `gir1.2-dbusmenu-glib-0.4` installed (the server side of `com.canonical.dbusmenu`, no Gtk needed) |
| DbusmenuGtk3 0.4 | FAIL | not installed (not needed) |

### 1.3 dpkg results for the requested list

| Package | State |
|---|---|
| python3-gi | installed, 3.56.2-1 |
| gir1.2-gtk-3.0 | installed, 3.24.52-0ubuntu1 |
| gir1.2-gtk-4.0 | installed, 4.22.4+ds-0ubuntu0.1 |
| gir1.2-adw-1 | installed, 1.9.1-0ubuntu0.1 |
| gir1.2-ayatanaappindicator3-0.1 | **NOT installed**; apt candidate 0.5.94-1build1 (resolute/main) |
| libayatana-appindicator3-1 | installed, 0.5.94-1build1 (also provides the `libappindicator3.so.1` symlink) |
| gir1.2-notify-0.7 | installed, 0.8.8-1 |
| libsecret-tools | **NOT installed** (`secret-tool` absent); `libsecret-1-0` and `gir1.2-secret-1` are installed, so libsecret is reachable from Python |
| openssl | installed, 3.5.5-1ubuntu3.7 |
| python3-cryptography | installed, 46.0.5-1ubuntu2.2 |
| python3-dbus | installed, 1.4.0-1build2 |
| python3-gi-cairo | no such package (merged into python3-gi on this release); `import cairo` works |
| gnome-keyring | installed, 50.0-1 |

Also in apt (not installed): `libayatana-appindicator-glib2` and `gir1.2-ayatanaappindicatorglib-2.0` 2.0.1-1build1
(`resolute/universe`), a Glib-only (no Gtk) reimplementation of the same API.

Python modules importable: `requests`, `urllib3`, `yaml`, `dbus`, `PIL`, `cairo`, `gi.repository.Gtk`, `cryptography`.
Not importable: `httpx`, `secretstorage`, `keyring`, `pystray`.

### 1.4 What this means for a stdlib-leaning Python build

- The app can use `urllib.request` or `requests` for HTTP (both there), `cryptography` or `openssl` for Ed25519, Gio/GLib for D-Bus, Notify or `notify-send` for notifications, Secret for the keyring if wanted.
- A tray built on the AppIndicator typelib needs `sudo apt install gir1.2-ayatanaappindicator3-0.1` first (founder's sudo password). Three ways around it are in section 3.4.

---

## 2. (b) GNOME, extensions, session type

### 2.1 Shell and extensions

- `gnome-shell --version`: **GNOME Shell 50.1**. The notification server reports `('gnome-shell', 'GNOME', '50.1', '1.2')`. Shell mode property: `'ubuntu'`.
- `gnome-extensions list --enabled` (with `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`), which equals `gnome-extensions list` (nothing installed-but-disabled):
  - `ding@rastersoft.com`
  - `snapd-prompting@canonical.com`
  - `snapd-search-provider@canonical.com`
  - `tiling-assistant@ubuntu.com`
  - `ubuntu-appindicators@ubuntu.com`
  - `ubuntu-dock@ubuntu.com`
  - `web-search-provider@ubuntu.com`
- `gnome-extensions info ubuntu-appindicators@ubuntu.com`: "Ubuntu AppIndicators ... System update of AppIndicator and KStatusNotifierItem Support extension", path `/usr/share/gnome-shell/extensions/ubuntu-appindicators@ubuntu.com`, **Enabled: Yes, State: ACTIVE**.
- **The AppIndicator/KStatusNotifier extension is enabled and active.**

### 2.2 Session bus

`org.freedesktop.DBus.ListNames` on the session bus (relevant names only):

- `org.kde.StatusNotifierWatcher` (and `NameHasOwner org.kde.StatusNotifierWatcher` returns `(true,)`)
- `org.freedesktop.Notifications` (served by gnome-shell)
- `org.freedesktop.secrets` (owner `:1.1`, served by `gnome-keyring-daemon --foreground --components=pkcs11,secrets`)
- `org.freedesktop.StatusNotifierItem-323235-1` (the Claude desktop app's tray icon)
- `org.gnome.Shell.Screenshot`, `org.gnome.Mutter.ScreenCast`, `org.freedesktop.portal.Desktop`, `org.gnome.Shell`, `org.gtk.Notifications`, `org.gnome.Shell.Notifications`

`StatusNotifierWatcher` `RegisteredStatusNotifierItems` already lists items from `:1.57` under
`/org/ayatana/NotificationItem/` (`software_update_available`, `livepatch`, `unattended_upgrade`, `un_reboot`)
plus the Claude desktop SNI. So the watcher accepts both ayatana-path and plain SNI registrations.

### 2.3 Sessions (`loginctl show-session` for each)

| Session | Type | Class | Seat | Remote | State | Notes |
|---|---|---|---|---|---|---|
| **2** | **wayland** | user | seat0 (tty2) | no | active | the graphical GNOME session; `Desktop=` is empty in loginctl |
| 3 | unspecified | manager | none | no | active | the systemd user manager |
| 20756 | tty | user | none | yes | closing | an SSH login |
| 20973 | tty | user | none | yes | active | this survey's SSH login |

Over SSH, `XDG_SESSION_TYPE=tty` and `XDG_CURRENT_DESKTOP` is empty. **The graphical session is Wayland, not X11**
(also: `Xwayland :0 -rootless` is running, with sockets X0 and X1 in `/tmp/.X11-unix`). The wayland socket is
`/run/user/1000/wayland-0`. Screen lock: `org.gnome.ScreenSaver GetActive` is `(false,)` (screen not locked).

A process started over SSH must export `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` and
`XDG_RUNTIME_DIR=/run/user/1000` to talk to the desktop; this worked for every D-Bus call in this survey.
A GUI started from SSH would additionally need `WAYLAND_DISPLAY=wayland-0` (not tested).

---

## 3. (c) systemd user manager, lingering, autostart; plus the tray design consequences

### 3.1 systemd

- `systemctl --user is-system-running`: **`degraded`**. Cause: two failed transient scopes from the CabDesign farm, `farm-laptop-p3067-exec2-e1.scope` and `farm-laptop-p3180-exec1-e2.scope` (`[systemd-run] /usr/bin/bash /home/ahmed/.cabdesign-farm/jobs/.../farm-job.sh`). Not a PixelSwitch problem, but a unit-health check that insists on "running" would false-alarm here.
- **Lingering: `Linger=yes`** for user ahmed (also `/var/lib/systemd/linger/ahmed` exists). User services survive logout.
- `~/.config/systemd/user/` holds only `default.target.wants/snap.prompting-client.daemon.service` and `timers.target.wants/snap.firmware-updater.firmware-notifier.timer`. There are no hand-written units yet.

### 3.2 Autostart

- `~/.config/autostart` **does not exist** (a PixelSwitch install must create it).
- System-wide `/etc/xdg/autostart` has the usual GNOME entries (gnome-keyring-secrets, at-spi-dbus-bus, update-notifier, and so on).
- `~/.local/share/applications` has `claude-code-url-handler.desktop` and `com.anthropic.Claude.desktop` (the Claude desktop app is installed as `/usr/lib/claude-desktop/claude-desktop`).

### 3.3 Keyring state (relevant if PixelSwitch stores anything via libsecret)

- `org.freedesktop.secrets` exposes collections `/org/freedesktop/secrets/collection/session` and `/org/freedesktop/secrets/collection/login`.
- The default alias collection `Locked` property is `false` (unlocked right now; it is the founder's logged-in graphical session).
- Claude Code itself does NOT use it on Linux (section 5.2).

### 3.4 Tray design consequences (this is the part to read before choosing a UI stack)

1. AppIndicator (Ayatana) is **Gtk 3 only**. Gtk 3 and Gtk 4 cannot live in one process. So if the Linux app uses the AppIndicator typelib for its tray icon, any window in the same process must be Gtk 3 (Handy 1 is available for a modern look). A Gtk 4 / libadwaita window would have to be a second process.
2. Three viable tray routes on this machine:
   - (A) `sudo apt install gir1.2-ayatanaappindicator3-0.1` (one package, in `main`), then `AyatanaAppIndicator3` with a Gtk 3 menu. Needs the founder's sudo password once.
   - (B) Implement the StatusNotifierItem protocol directly over D-Bus (Gio), with `Dbusmenu` (typelib already present) or hand-rolled `com.canonical.dbusmenu` for the menu. No extra package. The watcher is present and accepts plain SNI items (the Claude desktop app proves it).
   - (C) `gir1.2-ayatanaappindicatorglib-2.0` from universe (also an apt install, so also needs sudo).
3. The Linux app should detect a missing tray (no `org.kde.StatusNotifierWatcher` owner, e.g. stock Ubuntu with the extension disabled, or Fedora/stock GNOME) and fall back to a normal window plus notifications; on THIS laptop that fallback will not trigger.
4. `systemctl --user` plus `loginctl enable-linger` are not needed for a tray app: a `~/.config/autostart/*.desktop` entry is enough for a graphical session, and lingering is already on.

---

## 4. (d) openssl, Ed25519, cryptography

- `openssl version`: **OpenSSL 3.5.5 27 Jan 2026** (Library: OpenSSL 3.5.5).
- `openssl list -public-key-algorithms | grep -i ed25519`: `Name: OpenSSL ED25519 algorithm, OID: ED25519, PEM string: ED25519`, plus `ED448`, and implementation `IDs: { 1.3.101.112, ED25519 } @ default`.
- `openssl list -signature-algorithms`: `{ 1.3.101.112, ED25519 } @ default`, `ED25519ph @ default`, `ED25519ctx @ default`. **Ed25519 sign and verify are supported by the default provider.** (`openssl pkeyutl -verify -rawin` is the CLI route; not exercised here.)
- `python3 -c "import cryptography"`: **OK, `cryptography 46.0.5`**, and `from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey` imports fine.
- So a Python update-signature check (the Mac app verifies release signatures; see the Mac recon) can use `cryptography` directly, with `openssl` as a no-Python fallback.

---

## 5. (e) Claude Code on the laptop

### 5.1 Install

| Item | Result |
|---|---|
| `claude --version` | `2.1.310 (Claude Code)` |
| Path | `/home/ahmed/.local/bin/claude`, a symlink to `/home/ahmed/.local/share/claude/versions/2.1.310` (a 291 MB single native binary) |
| Previous version kept | `.../versions/2.1.302` |
| Install method | `installMethod: native`, `autoUpdates: false` in `~/.claude.json` (yet it updated: `~/.claude/.last-update-result.json` says `outcome: success`, `version_from: 2.1.302`, `version_to: 2.1.310`, timestamp `2026-10-24T22:22:14.738Z`, i.e. 15:22 PDT today) |
| Also installed | `agy`, `cursor-agent`/`agent`, `uv`, `uvx` in `~/.local/bin` |

Implication: the `claude` binary can be replaced under PixelSwitch's feet by a native auto-update; do not cache its path
by inode, resolve `~/.local/bin/claude` each time.

### 5.2 Credentials file (KEY STRUCTURE ONLY)

`~/.claude/.credentials.json`: exists, **mode 600**, owner ahmed, **524 bytes**, mtime 2026-10-24 15:22:01 PDT
(written the same minute as the auto-update, about 1 h 47 min before the survey).

```
claudeAiOauth: object (7 keys)
  accessToken:           str
  refreshToken:          str
  expiresAt:             int   (epoch MILLISECONDS; ~372 minutes in the future at survey time)
  refreshTokenExpiresAt: int
  scopes:                list[6] of str
  subscriptionType:      str
  rateLimitTier:         str
```

Non-secret values read:

- `scopes` = `["user:file_upload", "user:inference", "user:mcp_servers", "user:plugins", "user:profile", "user:sessions:claude_code"]` (so `user:profile` is granted, which is what a usage/profile read needs).
- `subscriptionType` = `max`; `rateLimitTier` = `default_claude_max_20x`.

Points for the spec author to compare against the Mac keychain item (not examined in this recon):

- The file has **only** the top-level key `claudeAiOauth`. There is no `mcpOAuth` or other sibling key here.
- `refreshTokenExpiresAt` is present as a distinct key from `expiresAt`. If the Mac's `Credentials` model only decodes `expiresAt`, preserve unknown keys verbatim when rewriting the file.
- File writes by Claude Code are the whole-file kind (mtime moves on refresh). PixelSwitch should write atomically (temp file in the same directory, `fsync`, `rename`) and keep mode 0600.

### 5.3 `~/.claude.json` (KEY STRUCTURE ONLY)

`~/.claude.json` is in `$HOME`, **not** inside `~/.claude/`: mode 600, 73,038 bytes, mtime 15:22:23 PDT today.
It has 58 top-level keys; the ones that matter for account identity and usage:

- `oauthAccount`: object, **20 keys**:

```
accountUuid: str                    emailAddress: str
organizationUuid: str               hasExtraUsageEnabled: bool
billingType: str                    accountCreatedAt: str
subscriptionCreatedAt: str          ccOnboardingFlags: dict
claudeCodeTrialEndsAt: null         claudeCodeTrialDurationDays: null
seatTier: null                      displayName: str
fullName: str                       profileFetchedAt: int
organizationRole: str               workspaceRole: null
organizationName: str               organizationType: str
organizationRateLimitTier: str      userRateLimitTier: null
```

  Non-secret values: `organizationRole` = `admin`, `billingType` = `stripe_subscription`, `hasExtraUsageEnabled` = `false`. Email is present (not printed).
- `userID` (str), `machineID` (str), `hasCompletedOnboarding` (bool, `true`), `numStartups` (int, 2), `firstStartTime` (str), `projects` (dict, 2 entries), `mcpServers` (dict, 1 entry).
- **`cachedUsageUtilization`** (object, 3 keys: `fetchedAtMs: int`, `accountUuid: str`, `utilization: object`). This is Claude Code's own cache of the usage endpoint response. Shape of `utilization`:

```
five_hour:  { utilization: int, resets_at: str, limit_dollars: null, used_dollars: null,
              remaining_dollars: null, locked_reason: null }
seven_day:  { same 6 keys }
seven_day_oauth_apps / seven_day_opus / seven_day_sonnet / seven_day_cowork /
seven_day_omelette: null   (this account: no per-model weekly windows)
iguana_necktie: { same 6 keys, with limit/used/remaining_dollars: int }   (unexplained codename key)
extra_usage: { is_enabled: bool, monthly_limit, used_credits, utilization, currency,
               decimal_places, disabled_reason, user_disabled: bool,
               spend_limit_reached: bool, credits_ever_enabled: bool, daily, weekly }
limits: list    spend: { used{amount_minor,currency,exponent}, limit, percent, severity, enabled, ... }
weekly_scoped_shares: list   member_dashboard_available: bool   seven_day_breakdown: null   pace: null
... plus ~15 other null-valued codename keys
```

  Sample values (usage numbers, not secrets): `five_hour.utilization` = `0`, `five_hour.resets_at` = `2026-10-25T00:10:00.373613+00:00`, `seven_day.utilization` = `0`, `seven_day.resets_at` = `2026-10-31T12:00:00.373633+00:00`; cached 109 minutes before the survey. `utilization` is an **int**; with a value of 0 this sample cannot prove the scale (percent 0..100 vs fraction), but an int type points to percent. `resets_at` is ISO-8601 with microseconds and a `+00:00` offset.
  Relevance to the split-threshold feature: the two windows PixelSwitch needs, `five_hour` and `seven_day`, are exactly the keys in the CLI's own cache, with `utilization` and `resets_at` in each. The cache is a free cross-check for a Linux usage poller (it is also account-keyed by `accountUuid`).
- `ccOnboardingFlags` and the other nested objects were not walked further.

### 5.4 Config directory

- `CLAUDE_CONFIG_DIR` is **not set** in: the SSH environment, the systemd user environment (`systemctl --user show-environment`), `~/.profile`, `~/.bashrc`, `~/.config/environment.d`, `/etc/environment`. No `CLAUDE*` or `ANTHROPIC*` variable is set in the SSH environment at all.
- `claude auth status --json` reports `configDirectory` and `projectsDirectory` (values not recorded here), consistent with the default `~/.claude`.
- Default layout on this machine: `~/.claude/` (dir, 0775, contains `.credentials.json`, `history.jsonl`, `settings.json`, `sessions/`, `projects/`, `state/`, `plugins/`, `backups/`, ...) and `~/.claude.json` (sibling file in `$HOME`).
- `~/.claude/settings.json` has keys `permissions`, `model`, `theme`, `modelSettings` (per-account settings the swap must not clobber).

### 5.5 `claude auth` (read only)

`claude auth --help` lists `login`, `logout`, `status`. `claude auth status --json` was run (read only) and returned a JSON object with keys and types:

```
loggedIn: bool      authMethod: str      apiProvider: str      analyticsDisabled: bool
projectsDirectory: str   configDirectory: str   email: str   orgId: str   orgName: str
subscriptionType: str
```

Filtered values: `loggedIn` = `true`, `authMethod` = `claude.ai`, `apiProvider` = `firstParty`, `subscriptionType` = `max`, email present = yes (not printed). `claude auth status` is a cheap, read-only, local identity check that a Linux port could use to confirm which account is active after a swap.

---

## 6. (f) Running processes

`ps -eo pid,ppid,etime,stat,cmd | grep -i claude` (commands truncated):

- **No interactive `claude` CLI process.** Nothing named `claude` from `~/.local/bin` is running.
- `claude-desktop` (Electron) main process PID 323235, up 6 days 1 h, with its zygote, gpu, network, renderer and utility children and a `cowork-linux-helper -socket /run/user/1000/claude-cowork-vm.sock`. It owns the tray icon `org.freedesktop.StatusNotifierItem-323235-1`. It stores its own state under `~/.config/Claude` (Chromium-style `Cookies`, `Cache`, `Crashpad`); whether it shares the CLI's OAuth tokens was not examined.
- Two `gpg-agent` processes whose home directories are under `/tmp/claude-1000/-home-ahmed-Projects-PixelDirector/...` (leftovers from a PixelDirector Claude session, 6 days 17 h old).
- A `tmux` session `farmclean` (farm cleanup script, 1 h 35 min old), `bwrap` sandboxes (`--ro-bind / / ... --tmpfs /home/ahmed ...`), and several `node` e2e test workers under `/home/ahmed/farm/cabinet-designer/.claude/worktrees/laptop-p3067-exec1-e1/tests/e2e/journeys/...`.

Takeaway: the CabDesign farm launches Claude Code workers on this laptop (logs `~/farm-trial-claude*.log`, `~/cabdesign-farm-account.sh` exist; contents not opened). It likely reads `~/.claude/.credentials.json`. Treat that file as live.

---

## 7. (g) Disk, PATH, shell

- `df -h ~`: `/dev/nvme0n1p2` 937 G total, 54 G used, **836 G available (7% used)**, mounted on `/` (home is on the root filesystem).
- Memory: 30 GiB total, 25 GiB available. CPUs: 16. Uptime 8 days 17 h; load average 3.61 / 7.06 / 5.38. Time zone `America/Los_Angeles`, clock NTP-synchronised.
- Login shell: `/bin/bash`.
- **`~/.local/bin` IS on PATH** in all three contexts tested: non-interactive SSH, `bash -lc`, and `bash -lic`. It is added by `~/.profile` (lines 25 to 26 and 35) and `~/.bashrc` (lines 8, 149, 153), which is why it appears two or three times in the login-shell PATH. `command -v claude` in `bash -lc` gives `/home/ahmed/.local/bin/claude`.
- PATH order in a login shell begins with `/home/ahmed/.cabdesign-safety/bin`, which contains **a wrapper named `rm`** (the "cabdesign-delete-guards" block at the end of `~/.profile`, sourced from `~/.cabdesign-safety/env.sh`). Any PixelSwitch install or uninstall script that shells out to `rm` will hit the guard, not `/bin/rm`. Python `os.remove` and `shutil` are unaffected. Behaviour of the wrapper was not tested.
- A `.desktop` autostart launching `claude` or a PixelSwitch binary in the graphical session does not go through `~/.profile` unless the session was started via a login shell; use absolute paths in `Exec=`.

---

## 8. (h) Screen capture and UI automation from SSH (report only; nothing captured)

| Route | Present? | Verdict |
|---|---|---|
| `grim`, `wf-recorder` | not found | n/a |
| `gnome-screenshot` | **not found** | n/a |
| `scrot`, `maim`, `spectacle`, `flameshot` | not found | n/a |
| ImageMagick `import` (`/usr/bin/import`) | present | X11 only. With `DISPLAY=:0` and `XAUTHORITY=/run/user/1000/.mutter-Xwaylandauth.*` it would grab Xwayland, which on GNOME Wayland shows only X11 clients (native Wayland windows, the panel and tray are not in it). Not tested. |
| `org.gnome.Shell.Screenshot` D-Bus service | name is on the bus; methods `InteractiveScreenshot Screenshot ScreenshotWindow ScreenshotArea PickColor FlashArea SelectArea` | GNOME has restricted this service to allow-listed callers since 41; a call from `gdbus` over SSH is likely refused. **Not tested** (a test would write a file). |
| `org.freedesktop.portal.Screenshot` / `ScreenCast` / `RemoteDesktop` | present on `org.freedesktop.portal.Desktop` | need an on-screen consent click from someone sitting at the laptop; not usable unattended |
| `org.gnome.Mutter.ScreenCast` | name on the bus | PipeWire-based; scriptable with `gst-launch-1.0` / `ffmpeg` but not tested |
| `ffmpeg`, `gst-launch-1.0`, `pw-record` (audio), `xdpyinfo` | present | usable building blocks for a Mutter ScreenCast route |
| `xdotool`, `ydotool`, `wtype` | not found | no synthetic input; clicking the tray menu cannot be automated from SSH this way |
| AT-SPI (`Atspi-2.0.typelib`, `at-spi-dbus-bus`) | typelib present | a possible read-only way to dump the accessibility tree of a PixelSwitch window/menu; not tested |

Practical conclusion: from SSH, verify behaviour by logs, D-Bus introspection (the item showing up in
`RegisteredStatusNotifierItems`, `gdbus introspect` of the SNI and its `com.canonical.dbusmenu` layout via
`GetLayout`), and the files the app writes. For a visual check, ask the founder for one screenshot at the laptop, or
agree a one-time portal consent.

---

## 9. Risks and surprises (ranked)

1. **Live credentials on a shared work machine.** The laptop's Claude login (Max 20x) is used by the founder and apparently by the CabDesign farm. A swap test against the real `~/.claude/.credentials.json` / `~/.claude.json` can log farm workers out or point them at the wrong account. Recommend the test harness run PixelSwitch with `HOME` pointed at a scratch directory (all its paths derive from `$HOME`), or have PixelSwitch honour an explicit `--claude-dir` / `CLAUDE_CONFIG_DIR`-style override for tests; do not ask the founder to log a second account into the live home without telling him.
2. **Missing `gir1.2-ayatanaappindicator3-0.1`, and sudo is password-protected.** An installer cannot apt-install it unattended. Either list it as a one-line founder step (sudo password), or build the SNI-over-D-Bus route (section 3.4 B) so the Linux app needs no extra package. Also decide the toolkit with the Gtk 3 / Gtk 4 exclusivity in mind.
3. **Native auto-update rewrites the `claude` binary and credentials.** The file was rewritten at 15:22 today in the same minute as an update to 2.1.310. The Linux port should re-read the file immediately before every write and treat `refreshToken` rotation as the normal case (the same refresh-token-reuse hazard behind the Mac app's 1.9 "usage 429s were PixelSwitch resending refused logins" finding).
4. **`~/.cabdesign-safety/bin/rm` shadows `rm`** in login shells. Avoid `rm` in install scripts; use Python or `/bin/rm` explicitly (and expect the guard to refuse deletes outside what it allows).
5. **`systemctl --user` reports `degraded`** because of two failed farm scopes. Do not gate on `is-system-running`.
6. **Screenshots are not obtainable unattended** (section 8).
7. **Wayland, not X11.** No `xdotool`; global shortcuts and window positioning by an app are limited on GNOME Wayland (a tray app should not rely on either).
8. **Python 3.14.4 is new**; confirm any pinned dependency supports 3.14 (PyGObject 3.56.2 and cryptography 46.0.5 do, since distro-built for it). `requests` is present but `httpx`/`keyring`/`secretstorage` are not, so do not take hard dependencies on them.
9. **`oauthAccount` holds email, display name and org name in plain text** in `~/.claude.json` (mode 600). Snapshots PixelSwitch keeps per account on Linux must be mode 0600 in a 0700 directory; there is no Keychain equivalent in use by Claude Code on this OS.

## 10. Facts not established (say so rather than guess)

- Whether the laptop's logged-in account is the same account as any account PixelSwitch holds on the Mac (identity values were deliberately not compared).
- The exact `utilization` scale (percent vs fraction): the sampled value is 0.
- Whether `claude auth status` or any CLI command on 2.1.310 writes to `.credentials.json` when merely run: mtime before and after was not captured (the file's mtime 15:22:01 predates the survey; status was run once and mtime was not re-read).
- Whether the Claude desktop app shares the CLI's tokens.
- Whether `org.gnome.Shell.Screenshot` refuses a `gdbus` caller (untested).
- What the `rm` wrapper does.
- Whether the Xwayland capture route yields anything useful for a tray menu (untested).
- What a Gtk 3 `Gtk.Menu` spawned by AppIndicator looks like on GNOME 50 (the typelib is missing, so it could not be tried).

## 11. Command log (all read only)

All run via `ssh -o BatchMode=yes -o ConnectTimeout=15 ahmed@ahmed-aorus-15p-xd.tail19d9f2.ts.net '<cmd>'`:
`hostname`, `cat /etc/os-release`, `uname`, `python3 --version`, `ls .../girepository-1.0`, python3 snippets for
`gi.require_version` per namespace, `dpkg -l` / `dpkg-query -W`, `apt-cache policy` / `apt-cache search`,
`sudo -n true` (refused), `gnome-shell --version`, `gnome-extensions list|info`, `gdbus call/introspect` on the session
bus (`ListNames`, `NameHasOwner`, `GetAll`, `Properties.Get`, `GetServerInformation`), `loginctl list-sessions|show-session|show-user`,
`systemctl --user is-system-running|list-units --failed|show-environment`, `ls`, `stat`, `openssl version|list`, python3 key-structure
walkers over `~/.claude/.credentials.json`, `~/.claude.json` and `~/.claude/settings.json` (types only), `claude --version`,
`claude auth --help`, `claude auth status --json` (filtered), `ps`, `df`, `free`, `uptime`, `bash -lc 'echo $PATH'`,
`command -v` for tools, `git log/status/remote -v` in `~/Projects/PixelSwitch`, `gh auth status` (token hidden).
