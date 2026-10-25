# PixelSwitch for Linux

This folder is the Linux build of PixelSwitch 2.0: the install, use and MCP sections come first, then the daemon
design and the two findings read from the installed Claude Code.

**Read this before you switch accounts on a shared machine.** On the founder's laptop the CabDesign farm runs
`claude` jobs as the same user, so switching accounts there (by hand or by auto-switch) also changes the account
those jobs use from their next request on. Auto-switch is off after an install. Try switching while the farm is idle.

## Install

Supported: **Ubuntu 24.04 LTS and 26.04** (Python 3.12 or newer, GTK 4.14 or newer, libadwaita 1.5 or newer). It
installs for your own user and never uses sudo. If something is missing it says which packages, and you install
them yourself (the installer prints the exact `sudo apt install ...` line and stops):

```
python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtk-3.0 gir1.2-dbusmenu-glib-0.4
```

On a desktop that shows no tray icons (stock GNOME without the Ubuntu AppIndicator extension) the installer warns
and goes on: the service, the command line and the settings window work without a tray.

**One line.** The first install trusts GitHub over HTTPS:

```
curl -fsSL https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/install-linux.sh | bash
```

**Two steps, checked.** Download the installer from the tag, compare its SHA-256 with the one in that release's
notes, then run it (replace `v2.0` with the tag you want):

```
curl -fsSLO https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.0/install-linux.sh
sha256sum install-linux.sh
bash install-linux.sh
```

Either way the installer then checks what it downloads before it unpacks anything: the size, the SHA-256 and the
Ed25519 signature (with `openssl` 3, against the public key built into the installer; it stops if `openssl` cannot
check Ed25519). It puts the program in `~/.local/share/pixelswitch/app/<version>-<build>/` (with a `current` link),
links `pixelswitch`, `pixelswitchd`, `pixelswitch-tray` and `pixelswitch-settings` into `~/.local/bin`, installs and
starts the systemd user service `pixelswitch.service`, adds the tray to your autostart and a PixelSwitch Settings
entry to the applications menu. **Auto-switch stays off** until you turn it on.

Other options: `--from <tarball> [--signature <file>]` installs a local build (labelled unverified without a
signature), `--no-systemd` and `--no-autostart` leave those out, `--uninstall` removes the program and keeps your
settings and saved logins, `--uninstall --purge` removes those too. The installer keeps a copy of itself at
`~/.local/share/pixelswitch/install-linux.sh` for that, also after the one-line install: the release tarball carries the
installer, so the kept copy is covered by the same signature. Running the installer again repairs or updates an
install; it never moves you back to an older release (it stops and says so; `--from` with that build's tarball is the
way to go back on purpose) and never turns Launch at login back on after you turned it off.

Every delete in the installer goes to `/bin/rm`, so a guard script named `rm` earlier on your `PATH` cannot stop an
install or an uninstall half way. Nothing runs until the whole script has arrived, so a dropped `curl | bash`
download changes nothing.

After an install, `pixelswitch accounts add-current` saves the account Claude Code is signed in as, or use Sign in
another account from the tray menu.

**Updates.** The service checks `latest-linux.json` two minutes after it starts and every 6 hours, verifies the
download the same way, runs the new version's `--self-check`, and only then flips `current` and restarts itself.
If a new version fails to start twice before it has completed one cycle, the rollback check that runs before every
start flips `current` back, and the build it rolled back from is written to `~/.local/state/pixelswitch/bad-builds`
(`{"builds": [32, ...]}`). A build that failed its own self-check is recorded there too. The updater never installs a
recorded build again and still installs any later, higher one; without that file the rolled-back service would find
the same build in the feed two minutes later and flip straight back to it. Turn updates off with
`pixelswitch settings set updates.automatic false`.

## Use

`pixelswitch` is the same command line as the Mac's: `pixelswitch help` lists every command, and the output, `--json`
shapes and exit codes (0 ok, 1 failed, 2 usage, 3 busy, 4 no such account, 5 unreachable) are the Mac's. Differences,
all on purpose and all of them: there is no "This Mac today" cost line in `usage`; `status` prints the daemon's
warnings (one line each); `pixelswitch --version` prints the app version; `update-check` says a newer version, if
there is one, installs by itself, instead of naming the Mac's window; the sign-in link line says "On the computer
running PixelSwitch" instead of "On the Mac running PixelSwitch"; and the `--open-in` hint names Firefox, not Safari.
A script that greps for one of the Mac's sentences needs the Linux one on this machine.

```
pixelswitch accounts                                   every account, in priority order
pixelswitch switch 2                                   switch to account number 2 (an email, label or # works too)
pixelswitch accounts priority ahamade@gmail.com 1      lower numbers are tried first
pixelswitch accounts threshold 2 --session 90 --weekly 50
pixelswitch settings set autoSwitch.strategy priority
pixelswitch settings set autoSwitch.enabled true
```

**The tray** (`pixelswitch-tray`) is a StatusNotifierItem: the active account's session percentage beside the icon,
and a menu with every account in priority order (`P1 address · S 42% · W 61%`, the active one checked, a short form
of its message under it), click to switch, Auto-switch on or off, Refresh, Settings, Sign in another account and Quit
tray. It registers itself with the desktop over D-Bus and exports its menu with Dbusmenu, so it needs no AppIndicator
package (it uses AyatanaAppIndicator3 only when that is installed). It follows the daemon live and restarts itself when
the daemon reports a new version.

**The settings window** (`pixelswitch-settings`, GTK 4 and libadwaita) has Accounts (priority, Session and Weekly
limits, label, both usage windows with their reset times, Fable, "Updated N ago", the account's message, Retry,
Re-sign, Remove), General (auto-switch, the two defaults from 50 to 100, the strategy, Fable, early switching, the
refresh interval), Sign in (Start, both links each with Open and Copy, the code box, status) and About (version,
Update automatically, Check now, the log path). Nothing opens a browser unless you press Open.

The tray and the settings window never send a usage refresh by themselves (only Refresh and Retry do) and never run
the daemon: when it is not running they start it with `systemctl --user start pixelswitch.service` and nothing else.

## MCP

`pixelswitch mcp` is the Mac's MCP server (20 tools, both MCP protocol eras), speaking on standard input and output.
From another machine:

```
{"mcpServers": {"pixelswitch": {"command": "ssh", "args": ["<host>", "/home/<user>/.local/bin/pixelswitch", "mcp"]}}}
```

Use the absolute path: a non-interactive ssh shell may not have `~/.local/bin` on its `PATH`. The tool names, schemas
(types, limits, required keys) and descriptions are the Mac's, with five fixed rewordings where the Mac's sentence is
false on Linux (`mcp.LINUX_TEXT`, and the test compares the Swift text after the same substitution, so any other drift
fails): "that Mac" and "the Mac this server runs on" say "computer"; `get_usage` does not promise "this Mac's cost and
activity today" (D15: Linux sends no cost); `check_for_updates` says "a newer version installs by itself" instead of
"its window opens on that Mac"; and the `open` example is "Firefox", not "Safari". Spec section 4 says "identical";
this is the one place it is not, on purpose: an AI that read "cost today" or "an update window opens" would report a
fault that is not there. `add_current_account` waits up to 120 seconds (the Mac server gives it 30).

## Not in the Linux version

The cost and activity dashboard (so no "This Mac today" line), the widget, menu-bar module customisation (the
`menuBar.*` settings are stored and do nothing), languages other than English, and Gemini and Codex accounts.

## For maintainers: the release files

`linux/build-release.sh` builds `pixelswitch-linux-<version>.tar.gz` (deterministic: the same commit gives the same
bytes; it needs GNU tar) and `install-linux.sh`, and prints the installer's SHA-256, which goes into the release notes.
With `--signing-key` it also writes `latest-linux.json`. CI builds the package unsigned on every run; only the tag-only
`linux-publish` job signs (the signing secret is the environment of that one step) and uploads the tarball and the
installer first and the feed last. `linux-publish` needs the Linux tests (`linux-test`, both Ubuntu versions) as well
as the macOS build, so a red Linux suite stops the signing; the unit-test step also fails if any test was skipped
(only the lock tests on a file system that keeps no birth time may skip). The tarball carries `install-linux.sh`
(written first, then packed), which is what the kept uninstall copy comes from.

**The installer's SHA-256 is checked three times.** `scripts/release.sh` refuses to tag unless `RELEASE_NOTES.md`
contains the SHA-256 of the published `install-linux.sh` (it is `linux/install.sh` with the `SU_PUBLIC_ED_KEY` line set
from `project.yml`, the same edit `build-release.sh` makes, so the hash is known before the tag) and refuses the
`INSTALLER_SHA256_PLACEHOLDER`; the publish job fails before uploading if the notes lack it; and
`scripts/verify-release.sh <tag> [--linux-only]` checks the Linux assets of a tag and that the release body carries
the hash.
Tests: `python3 -m unittest discover -s linux/tests -t linux`; the settings window renders come from
`xvfb-run -a python3 linux/tests/render_settings.py --out DIR`.

## What is in `pixelswitch/` (lane LB modules)

`pixelswitchd` is one background service. It owns everything that talks to Anthropic or writes a login; every
face (CLI, MCP, tray, settings window) is a client of its socket. Standard library only.

| Module | What it is | Public entry points |
|---|---|---|
| `daemon.py` | The process | `main(argv=None, env=None)` (the launcher calls it), `--self-check`, `acquire_daemon_lock`, `build` |
| `coordinator.py` | `AppState` for Linux: accounts, switch, capture, reconcile, audit, renewal, removal, sign-in glue | `Coordinator`, `MutationGate`, `ActionError` |
| `usagecycle.py` | Usage reading behind the guard, the polling cycle, auto-switch | `UsageMixin` (part of `Coordinator`), `AutoSwitchRecord`, `CardError` |
| `control_api.py` | The 21 methods | `ControlApi(coordinator, log).handle(line, on_subscribe)` |
| `server.py` | The socket server | `ControlServer`, `Connection`, `StartError` |
| `claude_login.py` | `~/.claude/.credentials.json` and `~/.claude.json` | `ClaudeLogin.read_credential / write_credential / read_oauth_account / write_oauth_account / refresh_lock` |
| `dirlock.py` | Claude Code's directory locks | `DirLock`, `LockLedger`, `owner_record` |
| `stores.py` | `settings.json`, `accounts.json`, `backups.json`, needs-reauth ids | `SettingsFile`, `AccountsFile`, `BackupStore`, `NeedsReauthFile` |
| `fsio.py` | The one atomic write | `atomic_write`, `ensure_private_dir`, `tighten_permissions` |
| `api.py`, `claude_service.py` | HTTP and the three token-carrying requests | `Transport`, `ApiBases`, `ClaudeService` |
| `claude_cli.py` | Finding and running `claude` | `ClaudeCli`, `process_environment`, `parse_auth_status` |
| `signin.py`, `signin_parse.py`, `browsers.py` | `claude auth login` on pipes | `SignInSession`, `Purpose`, `LinkCapture` |
| `updater.py`, `rollback.py` | Updater I/O, rollback script | `Updater`, `prune_old_versions`, `rollback.run / main` |
| `cli.py`, `mcp.py`, `gui/` | The faces (lane LC): the CLI, the MCP server, the tray and the settings window | `cli.main`, `mcp.run_stdio`, `gui.tray.main`, `gui.settings.main` |
| `logfile.py`, `paths.py`, `testmode.py`, `credtools.py`, `autostart.py` | Log, paths, test mode, credential gates, `launchAtLogin` | see the module docstrings |

Every path comes from `HOME` alone (`XDG_RUNTIME_DIR` is used for the socket only). `CLAUDE_CONFIG_DIR` and
`CLAUDE_SECURESTORAGE_CONFIG_DIR` are not honoured, as on the Mac: the login is `~/.claude/.credentials.json` and
the identity is `~/.claude.json`. Every `claude` the daemon runs has both variables removed from its environment;
if the daemon's own environment sets either, the log says so and `status.get` carries one line in `warnings`.

| File | Where |
|---|---|
| Settings, accounts | `~/.config/pixelswitch/settings.json`, `accounts.json` (accounts always sorted by priority) |
| Saved logins | `~/.local/share/pixelswitch/backups.json`, the Mac keychain item's shape `{uuid: {token, oauthAccount}}` |
| Log | `~/.local/state/pixelswitch/pixelswitch.log` (the Mac's line format, no emails, opened 0600) |
| State | `daemon.lock`, `socket-path`, `needs-reauth.json` (ids only), `held-locks.json`, `good-<build>`, `rollback.json` |
| Socket | `$XDG_RUNTIME_DIR/pixelswitch/control.sock`, else `~/.local/state/pixelswitch/control.sock` |

Every file is written atomically (temp file in the same directory opened `O_CREAT|O_EXCL` at 0600, `fsync`,
`os.replace`, directory `fsync`), keeps its permissions and never gets wider ones, and a symlinked or hard-linked
target is refused. Directories PixelSwitch creates are 0700.

## Claude Code's locks and how it notices a rewrite (confirmed read-only, Claude Code 2.1.310, 2026-10-24)

Read on the laptop with `grep -a`-style scans of `/home/ahmed/.local/share/claude/versions/2.1.310` (a bundled
JavaScript executable). Nothing was run with arguments that change state. Names below are the minified function
names in that build, so they will differ in other builds; the strings and numbers are what matter.

To read it again (read-only, any machine with the same Claude Code): memory-map the version file and print a few
thousand characters around each match of these byte strings, one at a time: `.oauth_refresh.lock` (then the
function that builds the lock options and the one that takes the lock), `ELOCKED` and `Lock file is already being
held` (the bundled `proper-lockfile`), `holder released it without removing` (the takeover reasons), `.storage-write`,
`saveConfigWithLock` (the `~/.claude.json.lock` save) and `.credentials.json` (the store and the mtime comparison).
No command was run with arguments that change state, and no credential file was opened.

### The refresh lock (spec 5.3: confirmed, so it is implemented)

* **Path.** `<claudeDir>/.oauth_refresh.lock`, where `claudeDir` is `~/.claude` (`O0()`). Options (`Anr`):
  `{lockfilePath, realpath: false, stale: 60000, update: 5000}`.
* **Mechanism.** The `proper-lockfile` package is bundled. A lock is a **directory** made by `mkdir`. A contender
  that gets `EEXIST` answers `ELOCKED` ("Lock file is already being held") unless the directory's mtime is older
  than `stale` (60 s), in which case it removes the directory and tries again. The holder keeps the mtime fresh
  with `utimes` every `update` (5 s) and removes the directory with `rmdir` on release.
* **A second, legacy lock.** `<realpath(claudeDir)>.lock` (`yA`, here `~/.claude.lock`), taken after the first
  one with the same options (older Claude Code versions take only this one). If it is busy the new lock is let
  go and an error is raised; any other error on it is logged and ignored (`lri`).
* **Holder record.** `<claudeDir>/.oauth_refresh.lock.owner` (`bF`), JSON checked by a schema (`SF`):
  `pid` (int), `procStart` (optional, at most 64 characters, field 22 of `/proc/<pid>/stat`), `pidDomain`
  (`linux:<machine-id>:<readlink /proc/self/ns/pid>`), `pidSpace` (`<hostname>#<readlink /proc/self/ns/pid>`),
  `lockBirthtimeMs` (the lock directory's birth time), `legacyLockBirthtimeMs` (optional). Removed on release only if
  the pid and birth time are the holder's.
* **Touch interval.** 5 s (`update: 5000`); a holder that has not touched it for 60 s is stale.
* **Takeover rule.** Staleness (60 s untouched) lets any contender remove the directory. Beyond that, a build-gated
  rule (feature `tengu_quiet_marten`, function `EF`) lets a contender remove a lock whose holder record names a
  process in the SAME pid space that is provably gone (and whose lock directory still has the recorded birth
  time and an unchanged mtime). A record from another pid space is never taken over.
* **How Claude Code waits.** `B_n`: up to 5 attempts, 1 to 2 s apart, then `OAuthRefreshLockContendedError`. Under
  the lock it re-reads the credentials (`readAsyncStrict`) before refreshing.

**What PixelSwitch does with it** (`claude_login.refresh_lock`, `dirlock.py`): takes the new lock and then the
legacy one before step 4's final read of the live credential, holds both through the merge read, the write and
the read-back, and releases in a `finally`. It waits at most 35 s for Claude Code (Claude Code's own refresh timeout
is 30 s) and then fails the switch with nothing written ("Claude Code is renewing this login; try again in a
minute."). It holds the lock at most 10 s (checked before the write) and touches it every 5 s while held. It writes
the holder record above (only where the filesystem reports a birth time, via `statx`). **It never removes, breaks
or takes over a lock it did not create**, including a stale one; if one looks abandoned (untouched for over 60 s) the
message says so and Claude Code clears it the next time it refreshes. A small ledger (`held-locks.json`) lets a
restarted daemon remove only a lock IT left behind after a crash (its recorded pid is gone and the lock is stale).
The re-proof of a login that Claude Code renewed in the meantime is a network call, so it happens OUTSIDE the lock:
leave the lock, ask the API, take the lock again (once).

### Two more locks the same build uses (not in the spec; found while reading, and honoured)

* `<claudeDir>/.storage-write` (`dwn`, `{realpath: false, retries: 10, minTimeout 100, maxTimeout 1000, stale: 15000}`)
  wraps every read-modify-write of `.credentials.json`. PixelSwitch takes it (up to 10 s) around its own write of
  that file, so a Claude Code save that starts after that sees the switched file. This narrows the window; it does
  not close it. A Claude Code that had already read the file before the switch, and writes back after it, can still
  put its older copy on disk; PixelSwitch reads the file back after its write and the next cycle's reconcile follows
  whichever login is really there, so such a save is noticed, but it is not prevented.
* `<~/.claude.json>.lock` (`ii`, i.e. `~/.claude.json.lock`, default `proper-lockfile` options: stale 10 s, touched
  every 5 s) wraps every save of `~/.claude.json`, which it does by re-reading the file under the lock. PixelSwitch
  takes it (up to 10 s) around its rewrite of `oauthAccount`.

Order in a switch: refresh lock, then `.storage-write` for the credential write, release it, then
`~/.claude.json.lock` for the identity write, then release everything. That is the order Claude Code itself uses
(it takes `.storage-write` inside the refresh lock), so the two cannot deadlock.

### How a running Claude Code notices an external rewrite of `.credentials.json` (confirmed)

On Linux the credential store is the plain file (`w` in the bundle: `read` is `readFileSync`, `readAsync` is
`readFile`, `write` is `mkdir` plus a 0600 write plus `chmod 384`). The in-memory copy is invalidated by `dA`:
it `stat`s `~/.claude/.credentials.json` and compares `mtimeMs` with the value it saw last; a **different** value
clears the credential cache (`Nh()`) and emits `credentialsChangedOnDisk`; an equal value changes nothing. There
is no inode check and no file watcher. So an atomic rename (a new file, a new mtime) is enough, with one
guard: `claude_login.write_credential` checks that the new file's mtime differs from the old one and moves it on
by a millisecond if the two writes landed in the same clock tick. The write is also read back and compared.

### Not decided by the spec, worth knowing

* The refresh endpoint. Claude Code 2.1.310 refreshes against `https://platform.claude.com/v1/oauth/token` (the string
  `console.anthropic.com/v1/oauth/token` is not in the binary). PixelSwitch uses `console.anthropic.com` as spec 5.3
  says, because that is what the Mac does and it works there today (recon `credentials.md` R5). It is one constant
  (`api.TOKEN_URL`); re-check it on the first real run on the laptop.
* The User-Agent. The Mac sends URLSession's default; Linux sends `PixelSwitch/<version> (Linux)`. Nothing in this
  repo shows that Anthropic keys on it, and no real request was made from any test (spec 5.7).

## Behaviour that differs from the Mac, on purpose

* No first account is created by itself from the live login. The Mac does that on a refresh when it has none; on
  Linux the first account comes from `pixelswitch accounts add-current` or a sign-in (spec 5.5), and removing the
  last account never recreates it (D20).
* `claude auth status` runs for add-current, sign-in and to verify a switch, not on every cycle (reconcile follows
  the profile-proven owner anyway). If `claude` is missing, hangs for 30 s or hides its identity, a switch is
  verified by comparing both files with the saved login instead of failing after the write.
* A failed `~/.claude.json` write after the login was written puts the previous login back.
* Texts that say "Mac" say "computer" ("The claude command could not be found on this computer.").
* `status.get` carries an optional `warnings` list (one line when the daemon's environment sets
  `CLAUDE_CONFIG_DIR` or `CLAUDE_SECURESTORAGE_CONFIG_DIR`). It is additive and optional. (`autoSwitched.data`'s
  `relaxed` is not a difference: the Mac sends it on every switch too, and so does Linux.)
* **Retry is Refresh.** On the Mac a failed account's Retry reads that one account. The control protocol has no
  method that refreshes one account (21 methods, a wire change to add one), so Retry on Linux sends `usage.refresh`,
  which reads every account, the same as the Refresh button. Retry still shows only where the Mac's would (the
  usage-service failures worth trying again), and like Refresh it is a person's click: the faces never send
  `usage.refresh` by themselves.
* The machine-usage line is omitted (D15). Sign-in has no 10-second fallback (spec 5.3): with no link a notice
  appears and the sign-in waits for Cancel or the 15-minute limit.
* Saved-login credentials are compared by their parsed content, not their text, so a formatting difference alone
  never rewrites `backups.json`.

## Test mode and the scratch run (spec 5.7)

`PIXELSWITCH_TEST_MODE=1` makes the daemon refuse to start unless `HOME` is under `~/.cache/pixelswitch-dev/`, its
control socket is too (so an `XDG_RUNTIME_DIR` of `/run/user/<uid>` is refused), and `INVOCATION_ID` is not set; it honours `PIXELSWITCH_TEST_API_BASE` (one base for the usage, profile and token
endpoints), `PIXELSWITCH_TEST_RELEASE_BASE` and `PIXELSWITCH_TEST_PUBLIC_KEY`; it refuses any request to an
`anthropic.com` or `claude.com` host; it will run only a `claude` under the scratch root; it never opens a browser
and never restarts the service; and `status.get` reports `testMode: true`. Outside test mode all of those variables
are ignored.

"`~/.cache/pixelswitch-dev/`" always means the REAL user's home from the password database, never `$HOME`: a test
that sets `HOME` to a scratch folder still creates its scratch folders in the real `~/.cache/pixelswitch-dev/` (test
mode reads the password database, so overriding `HOME` does not move it). Every test that makes one there uses its own
unique subfolder and removes it afterwards, so two test runs at once never share a folder. The installer's tests take
the same route (`install.sh` accepts `PIXELSWITCH_TEST_RELEASE_BASE` and `PIXELSWITCH_TEST_PUBLIC_KEY` only in test
mode with `HOME` there), serving a fake signed release from a local http server.

```
python3 -m unittest discover -s linux/tests -t linux        # from the repository root; Python 3.12 or newer
# on the laptop: copy linux/, Tests/ and project.yml to ~/.cache/pixelswitch-dev/lb-run/ and, from there,
python3 linux/tests/integration_scratch.py                  # the scratch-HOME run (refuses to run anywhere else)
```

`tests/mock_api.py` (the three endpoints with scripted responses), `tests/stub_claude.py` (a stub `claude` that
reads and writes only `$HOME`) and `tests/lb_support.py` (fabricated logins and a wired coordinator) are the fixtures.
Every login in every test is a random string.
