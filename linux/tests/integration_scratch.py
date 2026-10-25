#!/usr/bin/env python3
"""Scratch-HOME integration run for the Linux daemon (spec 5.7, every rule).

It starts the REAL ``pixelswitchd`` as a subprocess in test mode, against a mock of the three
Anthropic endpoints and a stub ``claude``, with fabricated logins only, and exercises:

1. add-current, then a clean SIGTERM;
2. usage polling (headers, one request per cycle per account), a 401 that is never resent, a
   saved-login renewal with rotation, a switch between two fabricated accounts (``.credentials.json``
   rewritten with every other key preserved and mode 0600, ``~/.claude.json`` keys and mode preserved,
   no lock or temp file left), the D12 refusals, a sign-in started and cancelled, and the protocol calls
   of ``Tests/integration/remote-control.sh`` that do not need the CLI;
3. the weekly-overflow auto-switch (session 90 / weekly 50, every account past its weekly limit);
4. priority order under the Priority strategy;
5. removing the active account when no fallback works, then a switch with no active account (spec D20 revision 4);
6. a clean SIGTERM each time, nothing left running, the real home untouched.

It refuses to run unless this file lives under ``~/.cache/pixelswitch-dev/`` (copy ``linux/``,
``Tests/`` and ``project.yml`` there first), because every rule of spec 5.7 depends on it:

* ``HOME`` and every ``XDG_*`` are under the scratch tree; ``CLAUDE_CONFIG_DIR``,
  ``CLAUDE_SECURESTORAGE_CONFIG_DIR`` and every ``ANTHROPIC_*`` are not in any environment it builds;
* ``PIXELSWITCH_TEST_MODE=1`` and the mock API base are the only way the daemon is pointed anywhere,
  and it reports ``testMode: true`` before anything else is checked;
* the logins are random strings, ``claude.binaryPath`` is the stub, the real ``~/.claude`` files are
  never read, ``systemctl --user`` is never run, the real ``~/.config`` and ``~/.local`` are never
  written (their signatures are compared before and after), D-Bus is never touched.

Usage: ``python3 linux/tests/integration_scratch.py`` from the copied tree. Exit 0 only if every check passed.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LINUX = os.path.dirname(HERE)
sys.path.insert(0, LINUX)

from pixelswitch import fsio, paths as paths_module, testmode  # noqa: E402
from pixelswitch.stores import AccountsFile  # noqa: E402
from pixelswitch.client import ControlClient  # noqa: E402
from pixelswitch.logfile import Log  # noqa: E402
from pixelswitch.protocol import ControlError  # noqa: E402

from tests.lb_support import Rig, credential_text  # noqa: E402
from tests.mock_api import R, fabricated, usage_body  # noqa: E402

CODE = "import sys; sys.path.insert(0, %r); from pixelswitch.daemon import main; sys.exit(main(sys.argv[1:]))" % LINUX
SCRUBBED_PREFIXES = ("ANTHROPIC_",)
SCRUBBED_NAMES = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR")

passed = 0
failed = 0


def ok(name: str) -> None:
    global passed
    passed += 1
    print(f"PASS  {name}")


def bad(name: str, detail: str = "") -> None:
    global failed
    failed += 1
    print(f"FAIL  {name}{': ' + detail if detail else ''}")


def check(name: str, condition: bool, detail: str = "") -> bool:
    (ok if condition else lambda n: bad(n, detail))(name)
    return bool(condition)


def wait_for(predicate, timeout: float = 30.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(interval)
    try:
        return bool(predicate())
    except Exception:
        return False


def read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def mode(path: str) -> int:
    return os.stat(path).st_mode & 0o7777


# -- the real home must not change (spec 5.7 rule 5) -----------------------------------------------------------


def real_home_signature(real_home: str) -> dict:
    """Existence and mtime of the places a careless run would write. Names and times only: no file is read."""
    places = [".config/pixelswitch", ".config/autostart", ".config/systemd/user", ".local/bin", ".local/share/pixelswitch",
              ".local/state/pixelswitch", ".local/share/applications", ".local/share/icons"]
    out = {}
    for place in places:
        path = os.path.join(real_home, place)
        try:
            status = os.stat(path)
            out[place] = (status.st_mtime_ns, sorted(os.listdir(path)) if os.path.isdir(path) else None)
        except OSError:
            out[place] = None
    return out


# -- one daemon ----------------------------------------------------------------------------------------------------


class Daemon:
    def __init__(self, rig: Rig, xdg_root: str) -> None:
        self.rig = rig
        runtime = os.path.join(xdg_root, "run")
        for name in ("run", "config", "data", "state", "cache"):
            os.makedirs(os.path.join(xdg_root, name), mode=0o700, exist_ok=True)
        self.env = {
            "PATH": "/usr/bin:/bin", "HOME": rig.home, "XDG_RUNTIME_DIR": runtime,
            "XDG_CONFIG_HOME": os.path.join(xdg_root, "config"), "XDG_DATA_HOME": os.path.join(xdg_root, "data"),
            "XDG_STATE_HOME": os.path.join(xdg_root, "state"), "XDG_CACHE_HOME": os.path.join(xdg_root, "cache"),
            "PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": rig.api.base, "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.paths = paths_module.Paths(rig.home, runtime)
        self.process: subprocess.Popen | None = None
        self.client: ControlClient | None = None
        self.events: ControlClient | None = None
        #: The last lines the daemon wrote to stderr when it exited before answering (the reason, e.g. a
        #: socket path longer than 107 bytes), so "the daemon starts" fails with a cause, not a bare FAIL.
        self.start_failure = ""

    def start(self) -> bool:
        assert not any(k in self.env for k in SCRUBBED_NAMES) and not any(k.startswith(SCRUBBED_PREFIXES) for k in self.env)
        self.process = subprocess.Popen([sys.executable, "-c", CODE], env=self.env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not wait_for(lambda: self._answers() or self.process.poll() is not None, 40):
            self.start_failure = "the daemon did not answer within 40 s"
            return False
        if self.process.poll() is not None:
            try:
                _, err = self.process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                err = ""
            lines = [line for line in (err or "").splitlines() if line.strip()]
            self.start_failure = f"exit {self.process.returncode}: " + " | ".join(lines[-3:])
            return False
        self.client = ControlClient(self.paths.socket)
        self.client.connect(launch=False)
        return True

    def _answers(self) -> bool:
        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(1)
            sock.connect(self.paths.socket)
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def call(self, method: str, params=None, timeout: float = 120.0):
        assert self.client is not None
        return self.client.call(method, params, timeout)

    def error_of(self, method: str, params=None) -> ControlError | None:
        try:
            self.call(method, params)
        except ControlError as error:
            return error
        return None

    def subscribe(self) -> ControlClient:
        client = ControlClient(self.paths.socket)
        client.connect(launch=False)
        client.call("events.subscribe")
        self.events = client
        return client

    def stop(self, sig=signal.SIGTERM) -> tuple[int | None, str]:
        if self.process is None:
            return None, ""
        if self.process.poll() is None:
            self.process.send_signal(sig)
        try:
            _, err = self.process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            self.process.kill()
            _, err = self.process.communicate()
        for client in (self.client, self.events):
            if client is not None:
                client.disconnect()
        return self.process.returncode, err

    def kill(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.communicate()
        for client in (self.client, self.events):
            if client is not None:
                client.disconnect()


def read_events(client: ControlClient, timeout: float) -> list[dict]:
    found = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = client.next_event(deadline=min(deadline, time.monotonic() + 0.3))
        if event is not None:
            found.append(event)
    return found


def stub_calls(rig: Rig) -> list[dict]:
    path = os.path.join(rig.home, "stub-calls.log")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines()]


def write_accounts(rig: Rig) -> None:
    from pixelswitch.accounts import sorted_by_priority

    AccountsFile(rig.paths.accounts, Log(None).category("it")).save(sorted_by_priority([s.account for s in rig.seeds]))


def no_stray_files(rig: Rig) -> bool:
    names = os.listdir(rig.paths.claude_dir)
    stray = [n for n in names if n.endswith(".tmp") or n in (".oauth_refresh.lock", ".oauth_refresh.lock.owner", ".storage-write")]
    stray += [n for n in os.listdir(rig.home) if n.endswith(".tmp") or n.endswith(".lock")]
    return not stray


# -- the phases -------------------------------------------------------------------------------------------------------------


def _can_take_lock(path: str) -> bool:
    """Whether ``path`` can be flock'd exclusively and without waiting, as the next daemon would."""
    import fcntl

    try:
        descriptor = os.open(path, os.O_RDWR)
    except OSError:
        return False
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False
    finally:
        os.close(descriptor)


def phase_add_current(work: str) -> None:
    print("\n== 1. add-current, then a clean SIGTERM ==")
    rig = Rig(None, keep_log=False, root=os.path.join(work, "p1"))
    a = rig.seed_account("alice@example.invalid", active=False, saved=False, live_extra_top={"mcpOAuth": {"srv": {"t": "x"}}})
    rig.seeds.clear()
    rig.write_live(a)
    daemon = Daemon(rig, os.path.join(rig.tmp, "xdg"))
    try:
        started = daemon.start()
        if not check("the daemon starts", started, daemon.start_failure):
            return
        status = daemon.call("status.get")
        check("status.get says testMode true, first of all", status.get("testMode") is True, str(status))
        check("status.get: protocol 2, revision 3, platform linux",
              (status["protocolVersion"], status["protocolRevision"], status["platform"]) == (2, 3, "linux"))
        check("no claude yet: claudeAvailable is false in test mode without the stub", status["claudeAvailable"] is False)
        daemon.call("settings.set", {"key": "claude.binaryPath", "value": rig.stub})
        check("claude.binaryPath = the stub", daemon.call("settings.get", {"key": "claude.binaryPath"})["settings"]["claude.binaryPath"] == rig.stub)
        check("the stub makes claudeAvailable true", daemon.call("status.get")["claudeAvailable"] is True)
        added = daemon.call("accounts.addCurrent")["account"]
        check("accounts.addCurrent saves the account Claude Code is signed in as, active",
              (added["email"], added["isActive"], added["isSwitchable"]) == (a.email, True, True), str(added))
        store = read_json(rig.paths.backups)
        entry = store[added["id"]]
        check("backups.json holds {uuid: {token, oauthAccount}} with the whole credential as a string",
              isinstance(entry["token"], str) and json.loads(entry["token"])["claudeAiOauth"]["accessToken"] == a.access
              and entry["oauthAccount"]["emailAddress"] == a.email)
        check("backups.json is 0600 in a 0700 directory", mode(rig.paths.backups) == 0o600 and mode(os.path.dirname(rig.paths.backups)) == 0o700)
        check("accounts.json is 0600", mode(rig.paths.accounts) == 0o600)
        check("adding it again is refused", (daemon.error_of("accounts.addCurrent") or ControlError("x", "")).message == "Account already exists")
        check("claude auth status ran with neither CLAUDE_CONFIG_DIR nor CLAUDE_SECURESTORAGE_CONFIG_DIR",
              all(not c["CLAUDE_CONFIG_DIR"] and not c["CLAUDE_SECURESTORAGE_CONFIG_DIR"] for c in stub_calls(rig)) and stub_calls(rig))
        code, err = daemon.stop()
        check("SIGTERM: exit 0", code == 0, err)
        check("SIGTERM: the socket and socket-path are gone", not os.path.exists(daemon.paths.socket) and not os.path.exists(daemon.paths.socket_path_file))
        check("SIGTERM: nothing is left running", daemon.process.poll() is not None)
        check("SIGTERM: daemon.lock is released (a new flock succeeds)", _can_take_lock(daemon.paths.daemon_lock))
    finally:
        daemon.kill()
        rig.close()


def phase_main(work: str) -> None:
    print("\n== 2. polling, the guard, renewal, switch, D12, sign-in, protocol ==")
    rig = Rig(None, keep_log=False, root=os.path.join(work, "p2"))
    now = time.time()
    a = rig.seed_account("alice@example.invalid", active=True, usage=usage_body(10, 10, now=now), live_extra_top={"mcpOAuth": {"srv": {"live": "token"}}})
    b = rig.seed_account("bob@example.invalid", usage=usage_body(20, 20, now=now))
    c = rig.seed_account("carol@example.invalid", usage=usage_body(30, 30, now=now), hours=-1)  # expired: its renewal rotates
    d = rig.seed_account("dave@example.invalid", usage=usage_body(40, 40, now=now))
    write_accounts(rig)
    fsio.atomic_write(rig.paths.settings, json.dumps({"claudeBinaryPathPreference": rig.stub}))
    c_access, c_refresh = fabricated("oat01"), fabricated("ort01")
    rig.api.add_refresh(c.refresh, c_access, c_refresh, expires_in=3600, refresh_expires_in=7776000)
    rig.api.add_account(c_access, c.uuid, c.email, usage_body(35, 35, now=now))
    daemon = Daemon(rig, os.path.join(rig.tmp, "xdg"))
    try:
        started = daemon.start()
        if not check("the daemon starts with four accounts", started, daemon.start_failure):
            return
        check("status first: testMode true", daemon.call("status.get").get("testMode") is True)
        events = daemon.subscribe()
        accounts = daemon.call("accounts.list")["accounts"]
        check("accounts.list: four accounts, A active", len(accounts) == 4 and [x["email"] for x in accounts if x["isActive"]] == [a.email])

        # -- polling
        check("the first cycle ran by itself and asked for A's usage with the right headers",
              wait_for(lambda: rig.api.calls_to("usage", a.access), 30))
        first = rig.api.calls_to("usage", a.access)[0]
        check("usage GET: Bearer token, anthropic-beta oauth-2025-04-20, a PixelSwitch user agent, no body",
              first.headers.get("authorization") == f"Bearer {a.access}" and first.headers.get("anthropic-beta") == "oauth-2025-04-20"
              and first.headers.get("user-agent", "").startswith("PixelSwitch/") and first.body == b"")
        check("the first cycle read the active account plus ONE other, not all four",
              wait_for(lambda: daemon.call("status.get").get("lastRefresh") is not None, 30)
              and len(rig.api.calls_to("usage")) == 2 and a.access in {c_.token for c_ in rig.api.calls_to("usage")})
        report = daemon.call("usage.get")
        check("usage.get has no machine-usage line on Linux (D15)", "machine" not in report)

        # -- 401 never resent
        rig.api.script("usage", a.access, R(401, {"error": {"type": "authentication_error"}}))
        for _ in range(3):
            daemon.call("usage.refresh")
        sent = len(rig.api.calls_to("usage", a.access))
        check("a 401 on the live login: that token is never sent again (the request count stops at the first 401)",
              sent <= 2, f"{sent} requests")
        before = sent
        for _ in range(2):
            daemon.call("usage.refresh")
        check("... even after more refreshes", len(rig.api.calls_to("usage", a.access)) == before)
        info = [x for x in daemon.call("accounts.list")["accounts"] if x["id"] == a.id.upper()][0]
        check("the card says it is waiting for Claude Code, and nothing was refreshed for the live login",
              "Waiting for Claude Code" in info["usage"].get("error", "") and not rig.api.calls_to("token", a.refresh))
        renewed = fabricated("oat01")
        rig.api.add_account(renewed, a.uuid, a.email, usage_body(11, 12, now=time.time()))
        rig.write_live(a, credential_text(renewed, a.refresh, top={"mcpOAuth": {"srv": {"live": "token"}}}))
        check("when Claude Code renews the login it is read again at once",
              wait_for(lambda: (daemon.call("usage.refresh"), rig.api.calls_to("usage", renewed))[1], 60))
        a.access = renewed
        a.credential = open(rig.paths.credentials).read().strip()

        # -- renewal with rotation
        check("carol's expired saved login is renewed with a token POST (rotation), then asked once with the new token",
              wait_for(lambda: (daemon.call("usage.refresh"), rig.api.calls_to("token", c.refresh))[1], 90))
        post = rig.api.calls_to("token", c.refresh)[0]
        check("token POST: JSON body, Content-Type only, the client id, no scope",
              json.loads(post.body) == {"grant_type": "refresh_token", "refresh_token": c.refresh,
                                        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e"}
              and post.headers.get("content-type") == "application/json" and "authorization" not in post.headers)
        saved_c = json.loads(read_json(rig.paths.backups)[c.id]["token"])["claudeAiOauth"]
        check("the rotated refresh token was saved, with refreshTokenExpiresAt, other keys kept",
              saved_c["refreshToken"] == c_refresh and saved_c["accessToken"] == c_access
              and saved_c["refreshTokenExpiresAt"] > int(time.time() * 1000) + 7_000_000_000 and saved_c["scopes"] == ["user:inference", "user:profile"])
        check("the expired token itself was never sent", not rig.api.calls_to("usage", c.access))

        # -- the switch
        creds_before = rig.live_credential()
        config_before = rig.live_config()
        cfg_mode = mode(rig.paths.claude_json)
        switched = daemon.call("accounts.switch", {"account": "bob@example.invalid"})["account"]
        check("accounts.switch: B is active and switchable", (switched["email"], switched["isActive"]) == (b.email, True))
        live = rig.live_credential()
        check(".credentials.json: B's login, mcpOAuth kept from the live file, mode 0600",
              live["claudeAiOauth"]["accessToken"] == b.access and live["claudeAiOauth"]["refreshToken"] == b.refresh
              and live["mcpOAuth"] == creds_before["mcpOAuth"] and mode(rig.paths.credentials) == 0o600, str(live)[:200])
        config = rig.live_config()
        expected = dict(config_before)
        expected["oauthAccount"] = b.oauth_account
        check("~/.claude.json: oauthAccount is B's, every other key and the file mode kept",
              config == expected and mode(rig.paths.claude_json) == cfg_mode)
        saved_a = json.loads(read_json(rig.paths.backups)[a.id]["token"])["claudeAiOauth"]["accessToken"]
        check("A's newest login was saved under A before it was overwritten", saved_a == renewed)
        check("no Claude Code lock, temp file or leftover is in ~/.claude or ~/", no_stray_files(rig), str(os.listdir(rig.paths.claude_dir)))
        check("claude auth status confirmed the result", any(c_["args"] == ["auth", "status"] for c_ in stub_calls(rig)))
        seen = read_events(events, 3)
        kinds = {e["type"] for e in seen}
        check("events: activeAccountChanged and accountsChanged were pushed", {"activeAccountChanged", "accountsChanged"} <= kinds, str(kinds))

        # -- D12
        store = read_json(rig.paths.backups)
        live_text = open(rig.paths.credentials).read()
        shapes = {"blank accessToken": {"accessToken": "", "refreshToken": "r", "expiresAt": 9_999_999_999_999},
                  "blank refreshToken": {"accessToken": "a", "refreshToken": " ", "expiresAt": 9_999_999_999_999},
                  "null accessToken": {"accessToken": None, "refreshToken": "r", "expiresAt": 9_999_999_999_999},
                  "expiresAt 0": {"accessToken": "a", "refreshToken": "r", "expiresAt": 0}}
        for name, oauth in shapes.items():
            broken = dict(store)
            broken[d.id] = {"token": json.dumps({"claudeAiOauth": oauth}), "oauthAccount": d.oauth_account}
            fsio.atomic_write(rig.paths.backups, json.dumps(broken))
            error = daemon.error_of("accounts.switch", {"account": "dave@example.invalid"})
            check(f"D12: a saved login with a {name} is refused with the re-sign message and nothing is written",
                  error is not None and error.message.startswith("The saved login for dave@example.invalid has expired and could not be renewed.")
                  and open(rig.paths.credentials).read() == live_text)
            info = [x for x in daemon.call("accounts.list")["accounts"] if x["email"] == d.email][0]
            check(f"D12: ... and it is not switchable ({name})", info["isSwitchable"] is False)
        fsio.atomic_write(rig.paths.backups, json.dumps(store))

        # -- remote-control.sh, the part that needs no CLI
        status = daemon.call("status.get")
        check("remote-control.sh 1: status answers, protocol 2", status["protocolVersion"] == 2)
        check("remote-control.sh 2: accounts and usage", len(daemon.call("accounts.list")["accounts"]) == 4 and "accounts" in daemon.call("usage.get"))
        error = daemon.error_of("usage.get", {"account": "nobody-at-all-xyz"})
        check("remote-control.sh 2: an unknown account is notFound (CLI exit 4)", error is not None and error.kind == "notFound")
        original = daemon.call("settings.get", {"key": "autoSwitch.drainWithinHours"})["settings"]["autoSwitch.drainWithinHours"]
        target = 14 if original == 13 else 13
        now_value = daemon.call("settings.set", {"key": "autoSwitch.drainWithinHours", "value": target})["settings"]["autoSwitch.drainWithinHours"]
        daemon.call("settings.set", {"key": "autoSwitch.drainWithinHours", "value": original})
        check("remote-control.sh 3: a setting changes and is put back",
              now_value == target and daemon.call("settings.get", {"key": "autoSwitch.drainWithinHours"})["settings"]["autoSwitch.drainWithinHours"] == original)
        key = "menuBar.lowRemainingWarningThreshold"
        low = daemon.call("settings.get", {"key": key})["settings"][key]
        wanted = 35 if low != 35 else 30
        daemon.call("settings.set", {"key": key, "value": wanted})
        after_set = daemon.call("settings.get", {"key": key})["settings"][key]
        daemon.call("settings.set", {"key": key, "value": low})
        after_restore = daemon.call("settings.get", {"key": key})["settings"][key]
        check("remote-control.sh 3: a menu bar setting round-trips (read back after the set and after the restore)",
              after_set == wanted and after_restore == low, f"set {wanted} read {after_set}; restore {low} read {after_restore}")
        error = daemon.error_of("settings.set", {"key": "refreshInterval", "value": 7})
        check("remote-control.sh 3: refreshInterval 7 is invalidValue (CLI exit 2)", error is not None and error.kind == "invalidValue")
        watcher = daemon.subscribe()
        daemon.call("usage.refresh")
        types = {e["type"] for e in read_events(watcher, 3)}
        check("remote-control.sh 4: watch sees usageUpdated after a refresh", "usageUpdated" in types, str(types))
        started = daemon.call("accounts.signIn.start")["signIn"]
        check("remote-control.sh 5: a sign-in starts", started["state"] in ("starting", "waitingForUser"))
        check("remote-control.sh 5: both links appear; the automatic one finishes on this machine (localhost redirect)",
              wait_for(lambda: daemon.call("accounts.signIn.status")["signIn"].get("automaticLink"), 30)
              and "redirect_uri=http%3A%2F%2Flocalhost" in daemon.call("accounts.signIn.status")["signIn"]["automaticLink"]
              and daemon.call("accounts.signIn.status")["signIn"].get("manualLink"))
        daemon.call("accounts.signIn.cancel")
        check("remote-control.sh 5: the sign-in is cancelled and nothing changed",
              wait_for(lambda: daemon.call("accounts.signIn.status")["signIn"]["state"] == "cancelled", 30)
              and len(daemon.call("accounts.list")["accounts"]) == 4)
        check("the stub was started with BROWSER pointing at the private capture helper and nothing else opened",
              any(c_["BROWSER"] and c_["BROWSER"].endswith("capture-sign-in-link") for c_ in stub_calls(rig) if c_["args"][:2] == ["auth", "login"]))

        # -- a sign-in running when SIGTERM arrives
        daemon.call("accounts.signIn.start")
        wait_for(lambda: daemon.call("accounts.signIn.status")["signIn"]["state"] == "waitingForUser", 30)
        code, err = daemon.stop()
        check("SIGTERM with a sign-in running: exit 0", code == 0, err)
        listing = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
        check("... the stub claude it was running is gone", os.path.join(rig.tmp, "stubbin", "claude") not in listing)
        check("... the socket is gone", not os.path.exists(daemon.paths.socket))
        check("the log is 0600, in the Mac's line format, and carries no email, token or link query",
              mode(daemon.paths.log) == 0o600 and _log_is_clean(daemon.paths.log, [a, b, c, d]))
    finally:
        daemon.kill()
        rig.close()


def _log_is_clean(path: str, seeds) -> bool:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    lines = text.splitlines()
    import re

    shape = re.compile(r"^\[\d{4}-\d\d-\d\dT[\d:.]+Z\] \[(DEBUG|INFO|WARN|ERROR)\] \[[A-Za-z]+\] ")
    if not lines or not lines[0].startswith("====== PixelSwitch launched"):
        return False
    if not all(shape.match(line) or line.startswith("======") for line in lines):
        return False
    secrets = [x for s in seeds for x in (s.email, s.access, s.refresh)] + ["TESTSTATE456", "TESTCHALLENGE123"]
    return not any(secret in text for secret in secrets)


def phase_overflow(work: str) -> None:
    print("\n== 3. the weekly-overflow auto-switch (session 90 / weekly 50) ==")
    rig = Rig(None, keep_log=False, root=os.path.join(work, "p3"))
    now = time.time()
    a = rig.seed_account("alice@example.invalid", active=True, usage=usage_body(95, 60, now=now))
    b = rig.seed_account("bob@example.invalid", usage=usage_body(10, 70, now=now))
    c = rig.seed_account("carol@example.invalid", usage=usage_body(10, 55, now=now))
    write_accounts(rig)
    fsio.atomic_write(rig.paths.settings, json.dumps({"claudeBinaryPathPreference": rig.stub}))
    daemon = Daemon(rig, os.path.join(rig.tmp, "xdg"))
    try:
        started = daemon.start()
        if not check("the daemon starts", started, daemon.start_failure):
            return
        check("status first: testMode true", daemon.call("status.get").get("testMode") is True)
        daemon.call("settings.set", {"key": "autoSwitch.sessionThreshold", "value": 90})
        daemon.call("settings.set", {"key": "autoSwitch.weeklyThreshold", "value": 50})
        for _ in range(3):
            daemon.call("usage.refresh")  # one reading of every account, with auto-switch still off
        check("no switch while auto-switch is off, even at 95%", daemon.call("status.get")["activeAccountEmail"] == a.email)
        watcher = daemon.subscribe()
        daemon.call("settings.set", {"key": "autoSwitch.enabled", "value": True})
        daemon.call("usage.refresh")
        events = [e for e in read_events(watcher, 6) if e["type"] == "autoSwitched"]
        if not check("an autoSwitched event arrived", bool(events), "none"):
            return
        data = events[0]["data"]
        check("it moved off A to C, the account with most room under the raised weekly limit",
              (data["from"], data["to"]) == (a.id, c.id), str(data))
        check("it names the window (session) and says the weekly overflow made the call (relaxed true)",
              data.get("window") == "session" and data.get("relaxed") is True and data["limit"] == "windows" and data["trigger"] == "threshold")
        check("the live credential is C's now", rig.live_credential()["claudeAiOauth"]["accessToken"] == c.access)
        check("A's login was saved before it was left", json.loads(read_json(rig.paths.backups)[a.id]["token"])["claudeAiOauth"]["accessToken"] == a.access)
        check("the log says why", "no account under its weekly limit can take you, so the session limit decides" in open(daemon.paths.log).read())
        code, err = daemon.stop()
        check("SIGTERM: exit 0", code == 0, err)
    finally:
        daemon.kill()
        rig.close()


def phase_priority(work: str) -> None:
    print("\n== 4. priority order ==")
    rig = Rig(None, keep_log=False, root=os.path.join(work, "p4"))
    now = time.time()
    a = rig.seed_account("alice@example.invalid", active=True, usage=usage_body(95, 20, now=now))
    b = rig.seed_account("bob@example.invalid", usage=usage_body(10, 10, now=now))
    c = rig.seed_account("carol@example.invalid", usage=usage_body(30, 30, now=now))
    write_accounts(rig)
    fsio.atomic_write(rig.paths.settings, json.dumps({"claudeBinaryPathPreference": rig.stub}))
    daemon = Daemon(rig, os.path.join(rig.tmp, "xdg"))
    try:
        started = daemon.start()
        if not check("the daemon starts", started, daemon.start_failure):
            return
        check("status first: testMode true", daemon.call("status.get").get("testMode") is True)
        ordered = daemon.call("accounts.setPriority", {"account": "carol@example.invalid", "priority": 1})["accounts"]
        check("setPriority puts carol first (P1)", [x["email"] for x in ordered][0] == c.email and ordered[0]["priority"] == 1)
        ordered = daemon.call("accounts.setPriority", {"account": "bob@example.invalid", "priority": 3})["accounts"]
        check("bob is P3; alice, with none, comes after the numbered ones",
              [x["email"] for x in ordered] == [c.email, b.email, a.email] and ordered[1]["priority"] == 3)
        check("the list stays sorted on disk", [r["email"] for r in read_json(rig.paths.accounts)] == [c.email, b.email, a.email])
        check("a priority number is not an account reference: '99' is notFound",
              (daemon.error_of("accounts.switch", {"account": "99"}) or ControlError("x", "")).kind == "notFound")
        daemon.call("settings.set", {"key": "autoSwitch.strategy", "value": "priority"})
        check("autoSwitch.strategy accepts the alias 'priority' and stores myOrder",
              daemon.call("settings.get", {"key": "autoSwitch.strategy"})["settings"]["autoSwitch.strategy"] == "myOrder"
              and read_json(rig.paths.settings)["autoSwitchStrategy"] == "myOrder")
        for _ in range(3):
            daemon.call("usage.refresh")
        watcher = daemon.subscribe()
        daemon.call("settings.set", {"key": "autoSwitch.enabled", "value": True})
        daemon.call("usage.refresh")
        events = [e for e in read_events(watcher, 6) if e["type"] == "autoSwitched"]
        if not check("an autoSwitched event arrived", bool(events)):
            return
        check("alice left her limit for carol (P1) even though bob (P3) has more room",
              (events[0]["data"]["from"], events[0]["data"]["to"]) == (a.id, c.id))
        check("an ordinary switch still carries relaxed: false (the Mac always sends it)",
              events[0]["data"].get("relaxed") is False, str(events[0]["data"]))
        code, err = daemon.stop()
        check("SIGTERM: exit 0", code == 0, err)
    finally:
        daemon.kill()
        rig.close()


def phase_removal(work: str) -> None:
    print("\n== 5. removing the active account when no fallback works, then switching (spec D20 revision 4) ==")
    rig = Rig(None, keep_log=False, root=os.path.join(work, "p5"))
    now = time.time()
    a = rig.seed_account("alice@example.invalid", active=True, usage=usage_body(10, 10, now=now))
    b = rig.seed_account("bob@example.invalid", usage=usage_body(20, 20, now=now))
    c = rig.seed_account("carol@example.invalid", usage=usage_body(30, 30, now=now))
    write_accounts(rig)
    fsio.atomic_write(rig.paths.settings, json.dumps({"claudeBinaryPathPreference": rig.stub}))
    good = read_json(rig.paths.backups)
    broken = {key: value for key, value in good.items() if key == a.id}   # bob's and carol's saved logins are gone
    fsio.atomic_write(rig.paths.backups, json.dumps(broken))
    daemon = Daemon(rig, os.path.join(rig.tmp, "xdg"))
    try:
        started = daemon.start()
        if not check("the daemon starts", started, daemon.start_failure):
            return
        check("status first: testMode true", daemon.call("status.get").get("testMode") is True)
        watcher = daemon.subscribe()
        daemon.call("usage.refresh")                       # the API proves the live login is alice's
        daemon.call("accounts.remove", {"account": "alice@example.invalid"})
        check("no fallback worked, so no account is active",
              wait_for(lambda: daemon.call("status.get").get("activeAccountEmail") is None, 60))
        events = [e for e in read_events(watcher, 3) if e["type"] == "error"]
        check("the last failure is shown as an error event",
              bool(events) and events[-1]["data"]["message"].startswith("No stored credentials for"), str(events))
        remaining = daemon.call("accounts.list")["accounts"]
        check("two accounts remain and none is active", len(remaining) == 2 and not any(x["isActive"] for x in remaining))
        record = os.path.join(rig.paths.state_dir, "removed-logins.json")
        check("the removed account's login is remembered as fingerprints only, in a 0600 file",
              os.path.exists(record) and mode(record) == 0o600 and a.refresh not in open(record).read()
              and a.access not in open(record).read() and len(read_json(record)) >= 1)
        restored = read_json(rig.paths.backups)
        restored[b.id] = good[b.id]
        fsio.atomic_write(rig.paths.backups, json.dumps(restored))   # bob's saved login is back
        switched = daemon.error_of("accounts.switch", {"account": "bob@example.invalid"})
        check("a switch with no active account is allowed (the live login is the removed account's) and succeeds",
              switched is None, str(switched))
        check("bob is active and Claude Code holds bob's login",
              daemon.call("status.get").get("activeAccountEmail") == b.email
              and rig.live_credential()["claudeAiOauth"]["accessToken"] == b.access)
        check("the record of removed logins is cleared by the switch", not os.path.exists(record))
        code, err = daemon.stop()
        check("SIGTERM: exit 0", code == 0, err)
    finally:
        daemon.kill()
        rig.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scratch-HOME integration run for the Linux daemon")
    parser.add_argument("--keep", action="store_true", help="leave the work folder in place")
    args = parser.parse_args(argv)

    real_home = paths_module.real_home()
    scratch = os.path.realpath(testmode.scratch_root(real_home))
    here = os.path.realpath(LINUX)
    if not here.startswith(scratch + os.sep):
        print(f"Refusing to run: {here} is not under {scratch}/. Copy linux/, Tests/ and project.yml to "
              f"{scratch}/lb-run/ and run it from there (spec 5.7).", file=sys.stderr)
        return 2
    for name in list(os.environ):
        if name in SCRUBBED_NAMES or name.startswith(SCRUBBED_PREFIXES):
            del os.environ[name]
    os.umask(0o077)
    run_root = os.path.dirname(here)
    work = os.path.join(run_root, "work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, mode=0o700)
    signature = real_home_signature(real_home)
    print(f"scratch run under {work}")

    try:
        for phase in (phase_add_current, phase_main, phase_overflow, phase_priority, phase_removal):
            try:
                phase(work)
            except Exception as error:  # a crash in a phase is a failure, and the next phase still runs
                bad(f"{phase.__name__} crashed", f"{type(error).__name__}: {error}")
        check("the real ~/.config and ~/.local places were not touched (no systemd unit, autostart file or install)",
              real_home_signature(real_home) == signature)
        leftover = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True).stdout
        check("no pixelswitchd or stub claude from this run is still running",
              not any(work in line for line in leftover.splitlines() if "ps -axo" not in line and "integration_scratch" not in line))
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
