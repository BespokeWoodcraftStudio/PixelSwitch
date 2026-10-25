"""Fixtures for the daemon tests (lane LB): a scratch HOME, the mock API, the stub ``claude``, fabricated
logins and a fully wired :class:`Coordinator` that never sleeps for real.

Every login here is a fabricated random string. Nothing reads or writes the real ``~/.claude``.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import stat
import tempfile
import threading
import time
import unittest
import uuid
from dataclasses import dataclass, field

from pixelswitch import fsio
from pixelswitch.accounts import Account, dump_accounts, sorted_by_priority
from pixelswitch.api import ApiBases, Transport
from pixelswitch.claude_cli import ClaudeCli
from pixelswitch.claude_login import ClaudeLogin
from pixelswitch.claude_service import ClaudeService
from pixelswitch.coordinator import Coordinator
from pixelswitch.dirlock import LockLedger
from pixelswitch.logfile import Log
from pixelswitch.paths import Paths
from pixelswitch.stores import AccountsFile, BackupStore, NeedsReauthFile, SettingsFile
from pixelswitch.usage_guard import UsageRequestGuard

from . import stub_claude
from .mock_api import MockApi, fabricated, usage_body


def read_file(path: str, mode: str = "r"):
    with open(path, mode) as handle:
        return handle.read()


def read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


class FakeClock:
    """Starts at the real time and moves only when told (or when the code under test sleeps)."""

    def __init__(self) -> None:
        self.now = time.time()
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def credential_text(access: str, refresh: str, expires_ms: float | None = None, *, subscription: str = "max",
                    top: dict | None = None, oauth: dict | None = None, hours: float = 8) -> str:
    """A credential JSON as Claude Code writes it: ``claudeAiOauth`` plus any other top-level keys."""
    body = {
        "accessToken": access, "refreshToken": refresh,
        "expiresAt": int(expires_ms if expires_ms is not None else (time.time() + hours * 3600) * 1000),
        "refreshTokenExpiresAt": int((time.time() + 90 * 86400) * 1000),
        "scopes": ["user:inference", "user:profile"], "subscriptionType": subscription,
        "rateLimitTier": "default_claude_max_20x",
    }
    body.update(oauth or {})
    document = {"claudeAiOauth": body}
    document.update(top or {})
    return json.dumps(document, separators=(",", ":"))


@dataclass
class Seed:
    """One fabricated account."""

    id: str
    email: str
    uuid: str
    access: str
    refresh: str
    credential: str
    oauth_account: dict
    account: Account
    extra: dict = field(default_factory=dict)


class Rig:
    """A scratch HOME with a mock API, a stub claude and a wired coordinator."""

    def __init__(self, case: unittest.TestCase | None = None, *, keep_log: bool = True) -> None:
        self.tmp = tempfile.mkdtemp(prefix="pswitch-lb-")
        if case is not None:
            case.addCleanup(self.close)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home, mode=0o700)
        self.paths = Paths(self.home, None)
        self.clock = FakeClock()
        self.api = MockApi()
        base = self.api.start()
        self.bases = ApiBases(usage=base + "/api/oauth/usage", profile=base + "/api/oauth/profile",
                              token=base + "/v1/oauth/token")
        self.stub = stub_claude.install(os.path.join(self.tmp, "stubbin"))
        self.log = Log(self.paths.log) if keep_log else Log(None)
        self.seeds: list[Seed] = []
        self.events: list[tuple[str, dict]] = []
        self.coordinator: Coordinator | None = None
        self._lock = threading.Lock()
        self.settings_seed: dict = {"claudeBinaryPathPreference": self.stub}
        fsio.ensure_private_dir(self.paths.claude_dir)

    # -- seeding ------------------------------------------------------------------------------------------

    def seed_account(self, email: str, *, active: bool = False, usage: dict | None = None, priority: int | None = None,
                     switch: float | None = None, session: float | None = None, weekly: float | None = None,
                     hours: float = 8, label: str | None = None, subscription: str = "max", saved: bool = True,
                     live_extra_top: dict | None = None, display: str | None = None) -> Seed:
        account_id = str(uuid.uuid4()).upper()
        account_uuid = str(uuid.uuid4())
        access, refresh = fabricated("oat01"), fabricated("ort01")
        credential = credential_text(access, refresh, subscription=subscription, hours=hours, top=live_extra_top)
        oauth_account = {"accountUuid": account_uuid, "emailAddress": email, "organizationUuid": "org-" + account_id[:8],
                         "organizationName": display or email, "displayName": email.split("@")[0]}
        self.api.add_account(access, account_uuid, email, usage if usage is not None else usage_body(10, 10, now=self.clock.now))
        account = Account(id=account_id, email=email, display_name=display or email, is_active=active,
                          switch_threshold=switch, session_threshold=session, weekly_threshold=weekly,
                          priority=priority, custom_label=label, subscription_type=subscription)
        seed = Seed(account_id, email, account_uuid, access, refresh, credential, oauth_account, account)
        self.seeds.append(seed)
        if saved:
            BackupStore(self.paths.backups, self.log.category("seed")).save(credential, oauth_account, account_id)
        if active:
            self.write_live(seed)
        return seed

    def write_live(self, seed: Seed, credential: str | None = None) -> None:
        """Makes ``seed`` the login Claude Code holds: ``.credentials.json`` and ``~/.claude.json``."""
        fsio.atomic_write(self.paths.credentials, credential if credential is not None else seed.credential)
        try:
            config = read_json(self.paths.claude_json)
        except (OSError, ValueError):
            config = {"numStartups": 3, "projects": {"/x": {}}, "theme": "dark"}
        config["oauthAccount"] = seed.oauth_account
        fsio.atomic_write(self.paths.claude_json, json.dumps(config, indent=2))

    # -- building ---------------------------------------------------------------------------------------------

    def build(self, **overrides) -> Coordinator:
        accounts = sorted_by_priority([s.account for s in self.seeds])
        if accounts:
            AccountsFile(self.paths.accounts, self.log.category("seed")).save(accounts)
        if self.settings_seed:
            fsio.atomic_write(self.paths.settings, json.dumps(self.settings_seed))
        ledger = LockLedger(os.path.join(self.paths.state_dir, "held-locks.json"))
        login = ClaudeLogin(self.paths, self.log.category("Login"), ledger=ledger, clock=self.clock, sleep=self.clock.sleep)
        settings = SettingsFile(self.paths.settings, self.log.category("Settings"))
        holder: list = []
        cli = ClaudeCli(home=self.home, base_env={"PATH": "/usr/bin:/bin"}, log=self.log.category("Claude"),
                        binary_path_setting=lambda: holder[0].binary_path_setting() if holder else "")
        service = ClaudeService(Transport(user_agent="PixelSwitch/test", test_mode=True), self.bases, UsageRequestGuard(),
                                self.log.category("Claude"), clock=self.clock)
        coordinator = Coordinator(
            paths=self.paths, log=self.log, settings=settings,
            accounts_file=AccountsFile(self.paths.accounts, self.log.category("Accounts")),
            backups=BackupStore(self.paths.backups, self.log.category("Keychain")),
            needs_reauth_file=NeedsReauthFile(self.paths.needs_reauth, self.log.category("AppState")),
            login=login, cli=cli, service=service, app_version="2.0", test_mode=False, clock=self.clock,
            sleep=self.clock.sleep, signin_parent=os.path.join(self.tmp, "signin"), **overrides)
        holder.append(coordinator)
        coordinator.set_listener(lambda event_type, data: self.events.append((event_type, data)))
        self.coordinator = coordinator
        return coordinator

    def events_of(self, event_type: str) -> list[dict]:
        return [data for kind, data in self.events if kind == event_type]

    # -- reading back ---------------------------------------------------------------------------------------------

    def live_credential(self) -> dict:
        return read_json(self.paths.credentials)

    def live_config(self) -> dict:
        return read_json(self.paths.claude_json)

    def backup_of(self, seed: Seed):
        return BackupStore(self.paths.backups, self.log.category("check")).get(seed.id)

    def mode_of(self, path: str) -> int:
        return stat.S_IMODE(os.stat(path).st_mode)

    def log_text(self) -> str:
        try:
            return read_file(self.paths.log)
        except OSError:
            return ""

    def close(self) -> None:
        self.api.stop()
        self.log.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


def tmpdir(case: unittest.TestCase) -> str:
    path = tempfile.mkdtemp(prefix="pswitch-lb-")
    case.addCleanup(shutil.rmtree, path, True)
    return path


def wait_for(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())
