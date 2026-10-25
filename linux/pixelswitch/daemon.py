"""``pixelswitchd``: the one background service that owns everything that talks to Anthropic or
writes a login (spec 5.1). Lane LC's launcher ``bin/pixelswitchd`` calls :func:`main`.

Lifecycle:

* ``os.umask(0o077)`` first, as every PixelSwitch process does.
* Test mode (spec 5.7) is validated before anything is touched: ``PIXELSWITCH_TEST_MODE=1`` needs
  ``HOME`` under ``~/.cache/pixelswitch-dev/`` and no ``INVOCATION_ID`` (not under systemd).
* An exclusive ``flock`` on ``~/.local/state/pixelswitch/daemon.lock`` (path from ``HOME`` only), taken
  BEFORE any store is read or the socket is bound and held for the daemon's whole life. If it is
  held: print "already running" and exit 0, touching nothing. Only while holding it may this process
  unlink a stale socket (or its own leftover Claude Code lock directory).
* Everything the daemon spends (the usage cycle, saved-login renewals, auto-switch, the updater)
  starts only after that. The machine-usage line of the Mac status is omitted on Linux (D15).
* SIGTERM and SIGINT: no new cycle, switch, renewal or sign-in; in-flight renewals finish their POST,
  ownership check and save (at most 80 s); a running sign-in is cancelled as Cancel does; the updater
  abandons a download or self-check at once; then the socket and ``socket-path`` are removed and the
  process exits 0. The whole stop is held inside :data:`STOP_BUDGET_SECONDS` (85 s), under the unit's
  ``TimeoutStopSec=90``.
* ``--self-check``: imports every module, reads settings, accounts and saved logins READ-ONLY, prints
  ``pixelswitchd <version> (build <n>)`` and exits 0. It takes no lock, binds no socket and writes
  nothing, so it runs beside a live daemon.
* After its first complete cycle the daemon writes ``good-<build>`` (the rollback marker, spec 5.4).
"""
from __future__ import annotations

import argparse
import fcntl
import importlib
import os
import pkgutil
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Mapping

PACKAGE_DIR = os.path.dirname(os.path.realpath(__file__))
INSTALL_DIR = os.path.dirname(PACKAGE_DIR)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 78  # EX_CONFIG: test mode refused, or a path that cannot work

USER_AGENT_FORMAT = "PixelSwitch/{version} (Linux)"

#: ``TimeoutStopSec=90`` in the unit; after that systemd sends SIGKILL. The in-flight renewals get at
#: most 80 s (spec 5.1) and the whole stop is held to 85 s.
RENEWAL_WAIT_SECONDS = 80.0
STOP_BUDGET_SECONDS = 85.0
UPDATER_STOP_SECONDS = 5.0


def ensure_own_code_on_path() -> None:
    """Every process puts the realpath of its own package directory's parent first on ``sys.path``,
    so a running process never loads another version's code (spec 5.2)."""
    if INSTALL_DIR not in sys.path:
        sys.path.insert(0, INSTALL_DIR)


# -- the lock -----------------------------------------------------------------------------------------------


def acquire_daemon_lock(lock_path: str) -> int | None:
    """Takes the exclusive lock; returns the open file descriptor (keep it open for life) or None
    if another daemon holds it."""
    from . import fsio

    fsio.ensure_private_dir(os.path.dirname(lock_path))
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    try:
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    return fd


# -- --self-check ----------------------------------------------------------------------------------------------


def import_every_module() -> list[str]:
    """Imports every module of the package; returns the names. Raises on the first that fails."""
    import pixelswitch

    names = []
    for module in pkgutil.iter_modules(pixelswitch.__path__):
        # The GTK faces (lane LC: gui/, tray, settings window) need PyGObject and a display; every
        # other module, the CLI and the MCP server included, is standard library only.
        if module.name.startswith("_") or module.name in ("gui", "tray", "settings_window"):
            continue
        importlib.import_module(f"pixelswitch.{module.name}")
        names.append(module.name)
    return names


def self_check(env: Mapping[str, str], out=None, err=None) -> int:
    """Reads, never writes. Prints ``pixelswitchd <version> (build <n>)`` and returns 0, or says
    what is wrong on ``err`` and returns 1."""
    out = out or sys.stdout
    err = err or sys.stderr
    from . import fsio, testmode
    from .accounts import AccountsFormatError, load_accounts
    from .paths import Paths
    from .version import VersionUnavailable, read_version

    try:
        import_every_module()
        info = read_version()
        paths = Paths.from_env(env)
        testmode.validate_start(env, paths.home)
        for path, label in ((paths.settings, "settings.json"),):
            text = fsio.read_text(path)
            if text is not None and not isinstance(fsio.loads_strict(text), dict):
                raise ValueError(f"{label} is not a JSON object")
        text = fsio.read_text(paths.accounts)
        if text is not None:
            load_accounts(fsio.loads_strict(text))
        data = fsio.read_bytes(paths.backups)
        if data is not None:
            value = fsio.loads_strict(data.decode("utf-8"))
            if not isinstance(value, dict) or not all(
                    isinstance(v, dict) and isinstance(v.get("token"), str) and isinstance(v.get("oauthAccount"), dict)
                    for v in value.values()):
                raise ValueError("backups.json is not in the expected shape")
    except (VersionUnavailable, testmode.TestModeError, AccountsFormatError, ValueError, OSError, ImportError) as error:
        print(f"pixelswitchd self-check failed: {type(error).__name__}: {error}", file=err)
        return EXIT_FAILED
    print(f"pixelswitchd {info.version} (build {info.build})", file=out)
    return EXIT_OK


# -- the daemon ------------------------------------------------------------------------------------------------------


@dataclass
class Daemon:
    """Everything a running daemon holds. Built by :func:`build`, run by :meth:`run`."""

    paths: object
    log: object
    coordinator: object
    server: object
    api: object
    updater: object | None
    lock_fd: int
    shutdown: threading.Event
    version: object
    test_mode: bool

    def request_stop(self, delay: float = 0.0) -> None:
        if delay > 0:
            timer = threading.Timer(delay, self.shutdown.set)
            timer.daemon = True
            timer.start()
        else:
            self.shutdown.set()

    def stop(self, *, server_started: bool = True, clock=time.monotonic) -> None:
        """Shuts down within :data:`STOP_BUDGET_SECONDS`. ``server_started`` False (the control server
        never bound, e.g. another daemon already answers on the socket) leaves the socket and the
        ``socket-path`` pointer alone: they belong to the daemon that is running."""
        began = clock()

        def remaining() -> float:
            return max(0.0, STOP_BUDGET_SECONDS - (clock() - began))

        coordinator, server, updater = self.coordinator, self.server, self.updater
        coordinator.begin_shutdown()
        if updater is not None:
            updater.stop(0.0)  # the flag only: no download, self-check or restart may start from here on
        server.stop_accepting()
        finished = coordinator.finish_shutdown(min(RENEWAL_WAIT_SECONDS, remaining()))
        if updater is not None:
            updater.stop(min(UPDATER_STOP_SECONDS, remaining()))
        if server_started:
            server.stop()
            _remove_socket_pointer(self.paths)
        self.log.category("Daemon").info("[daemon] Stopped" + ("" if finished else " (some work did not finish in time)"))
        self.log.close()
        try:
            os.close(self.lock_fd)
        except OSError:
            pass


def _remove_socket_pointer(paths) -> None:
    try:
        with open(paths.socket_path_file, encoding="utf-8") as handle:
            recorded = handle.read(4096).strip()
        if recorded == paths.socket:
            os.unlink(paths.socket_path_file)
    except OSError:
        pass


def build(env: Mapping[str, str], lock_fd: int, paths, shutdown: threading.Event) -> Daemon:
    """Constructs the stores, the coordinator, the control API and server, and the updater."""
    from . import fsio, testmode
    from .api import ApiBases, Transport
    from .claude_cli import ClaudeCli
    from .claude_login import ClaudeLogin
    from .claude_service import ClaudeService
    from .control_api import ControlApi
    from .coordinator import Coordinator
    from .dirlock import LockLedger
    from .logfile import Log
    from .server import ControlServer
    from .stores import AccountsFile, BackupStore, NeedsReauthFile, SettingsFile
    from .updater import Updater, restart_service
    from .usage_guard import UsageRequestGuard
    from .version import read_version

    version = read_version()
    test_mode = testmode.enabled(env)
    log = Log(paths.log)
    for path in (paths.backups, paths.accounts, paths.settings, paths.needs_reauth):
        fsio.tighten_permissions(path)
    start_log = log.category("Daemon")
    start_log.info(f"[daemon] pixelswitchd {version.version} (build {version.build}) starting"
                   f"{' in TEST MODE' if test_mode else ''}")

    ledger = LockLedger(os.path.join(paths.state_dir, "held-locks.json"))
    login = ClaudeLogin(paths, log.category("Login"), ledger=ledger)
    ledger.reap(log.category("Lock"), login.lock_stale_limits())

    settings = SettingsFile(paths.settings, log.category("Settings"))
    accounts_file = AccountsFile(paths.accounts, log.category("Accounts"))
    backups = BackupStore(paths.backups, log.category("Keychain"))
    needs_file = NeedsReauthFile(paths.needs_reauth, log.category("AppState"))
    user_agent = USER_AGENT_FORMAT.format(version=version.version)
    transport = Transport(user_agent=user_agent, test_mode=test_mode)  # carries tokens: never follows a redirect
    # The updater has its own transport: GitHub answers a release download with redirects, and the
    # one that carries tokens must never follow any. This one has no token and goes to GitHub hosts only.
    update_transport = Transport(user_agent=user_agent, test_mode=test_mode, follow_redirects=True)
    service = ClaudeService(transport, ApiBases.from_env(env), UsageRequestGuard(), log.category("Claude"))
    cli: ClaudeCli
    coordinator_ref: list = []
    cli = ClaudeCli(home=paths.home, base_env=env, log=log.category("Claude"),
                    binary_path_setting=lambda: coordinator_ref[0].binary_path_setting() if coordinator_ref else "",
                    test_mode=test_mode)
    signin_parent = os.path.join(paths.runtime_dir, "pixelswitch") if paths.runtime_dir and os.path.isabs(paths.runtime_dir) \
        else os.path.join(paths.state_dir, "signin")
    coordinator = Coordinator(
        paths=paths, log=log, settings=settings, accounts_file=accounts_file, backups=backups,
        needs_reauth_file=needs_file, login=login, cli=cli, service=service, app_version=version.version,
        test_mode=test_mode, signin_parent=signin_parent)
    coordinator_ref.append(coordinator)
    if cli.env_warning():
        log.category("Claude").warning("[claude] " + cli.env_warning())

    api = ControlApi(coordinator, log.category("Control").info)

    server = ControlServer(
        paths.socket, lambda line, connection: api.handle(line, connection.mark_subscribed),
        log.category("Control").info)
    coordinator.set_listener(lambda event_type, data: server.publish(event_type, data))

    daemon = Daemon(paths, log, coordinator, server, api, None, lock_fd, shutdown, version, test_mode)
    coordinator.on_quit = lambda: daemon.request_stop(0.3)  # terminates 0.3 s after the reply

    release_base = testmode.release_base(env) if test_mode else None
    public_key = testmode.public_key(env) if test_mode else None
    scheduled = (not test_mode) or release_base is not None
    updater_kwargs = {}
    if release_base is not None:
        updater_kwargs["release_base"] = release_base
        updater_kwargs["feed_url"] = release_base + "/latest/download/latest-linux.json"
    if public_key is not None:
        updater_kwargs["public_key"] = public_key

    def idle() -> bool:
        return is_idle(coordinator)

    def restart() -> None:
        if test_mode:
            log.category("Update").info("[update] Test mode: not restarting the service")
            return
        restart_service()

    updater = Updater(
        paths=paths, log=log.category("Update"), running=version, transport=update_transport,
        automatic=lambda: bool(coordinator.setting_value(_automatic_key())), idle=idle, restart=restart,
        scheduled=scheduled, **updater_kwargs)
    daemon.updater = updater
    coordinator.attach_updater(updater)
    coordinator.on_check_for_updates = updater.check_now

    marker_written = threading.Event()

    def cycle_done() -> None:
        """The rollback marker: written once, after the first complete cycle."""
        if marker_written.is_set():
            return
        marker_written.set()
        try:
            fsio.atomic_write(paths.good_marker(version.build), f"{int(time.time())}\n")
            from .updater import prune_old_versions, read_bad_builds

            prune_old_versions(paths.app_dir, log.category("Update"), read_bad_builds(paths))
        except OSError as error:
            log.category("Daemon").warning(f"[daemon] Could not write the good marker ({type(error).__name__})")

    coordinator.on_cycle_complete = cycle_done
    return daemon


def _store_idle(store) -> bool:
    """False while a read or write of the store is in progress (its lock is held)."""
    probe = getattr(store, "is_idle", None)
    if callable(probe):
        return bool(probe())
    lock = getattr(store, "_lock", None)
    if lock is None:
        return True
    if lock.acquire(blocking=False):
        lock.release()
        return True
    return False


def is_idle(coordinator) -> bool:
    """Spec 5.1 and 5.4: a switch (or capture, removal), a sign-in, a saved-login renewal or a write to
    the saved logins, the accounts or the settings blocks the updater's restart."""
    return (not coordinator.gate.busy and coordinator.renewals_in_flight() == 0
            and all(_store_idle(store) for store in (coordinator.backups, coordinator.accounts_file, coordinator.settings)))


def _automatic_key():
    from . import settings_catalog

    return settings_catalog.named("updates.automatic")


def run_daemon(env: Mapping[str, str]) -> int:
    from . import fsio, testmode
    from .paths import Paths
    from .server import StartError

    paths = Paths.from_env(env)
    try:
        testmode.validate_start(env, paths.home)
    except testmode.TestModeError as error:
        print(f"pixelswitchd: {error}", file=sys.stderr)
        return EXIT_CONFIG
    lock_fd = acquire_daemon_lock(paths.daemon_lock)
    if lock_fd is None:
        print("pixelswitchd: already running", file=sys.stderr)
        return EXIT_OK
    shutdown = threading.Event()
    try:
        daemon = build(env, lock_fd, paths, shutdown)
    except Exception as error:
        print(f"pixelswitchd: could not start ({type(error).__name__}: {error})", file=sys.stderr)
        os.close(lock_fd)
        return EXIT_FAILED
    log = daemon.log.category("Daemon")

    def on_signal(signum, _frame) -> None:
        log.info(f"[daemon] Received signal {signum}; shutting down")
        shutdown.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        daemon.server.start()
    except StartError as error:
        log.error(f"[control] Not started: {error}")
        print(f"pixelswitchd: {error}", file=sys.stderr)
        daemon.stop(server_started=False)
        return EXIT_OK if error.already_running else EXIT_FAILED
    try:
        fsio.atomic_write(paths.socket_path_file, paths.socket + "\n")
    except OSError as error:
        log.warning(f"[control] Could not write socket-path ({type(error).__name__})")
    log.info("[control] Listening on the control socket")
    daemon.coordinator.start_cycle_thread()
    daemon.updater.start()
    while not shutdown.wait(0.5):
        pass
    daemon.stop()
    return EXIT_OK


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """The entry point lane LC's launcher calls."""
    ensure_own_code_on_path()
    os.umask(0o077)
    parser = argparse.ArgumentParser(prog="pixelswitchd", description="PixelSwitch background service")
    parser.add_argument("--self-check", action="store_true",
                        help="import everything, read the stores read-only, print the version and exit")
    args = parser.parse_args(argv)
    env = os.environ if env is None else env
    if args.self_check:
        return self_check(env)
    return run_daemon(env)


if __name__ == "__main__":
    sys.exit(main())
