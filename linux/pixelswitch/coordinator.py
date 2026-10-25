"""The coordinator: ``AppState`` for the Linux daemon (accounts, saved logins, the switch, capture,
reconcile, the launch audit, renewal, removal, sign-in) on top of the pure core of lane LA.

Each method cites the Swift it ports (``PixelSwitch/AppState.swift`` and ``ClaudeService.swift``,
lines as merged in the 2.0 base). The usage cycle, the usage reading and the auto-switch decision
live in :class:`pixelswitch.usagecycle.UsageMixin`, a mixin of this class.

Concurrency. The Mac runs ``AppState`` on the main actor, so its read-modify-write sequences only
interleave at an ``await``. Here several threads call in (the usage cycle, one thread per control
connection, the sign-in thread, the removal fallback), so the discipline is explicit:

* ONE mutex for credential mutation, :class:`MutationGate`, shared by a manual switch, an
  auto-switch, a sign-in, add-current and removal. Whoever cannot take it gets ``busy`` (a request)
  or skips the work (a background step), exactly as ``isSwitching`` / ``isLoggingIn`` do on the Mac.
* Reconcile and the launch audit prove ownership over the network WITHOUT the gate, then take it
  (without waiting) for the synchronous part and re-check what they proved, as the Mac re-checks
  after an ``await``.
* Saved-login renewal joins one in-flight renewal per account, and every write to the saved logins
  goes through the one locked store-save function of :class:`stores.BackupStore`.

Authority over who is live is Anthropic's profile endpoint, never ``~/.claude.json`` (which running
sessions rewrite from memory); lineage (the same refresh token) only ever REFUSES, never proves
(``credentials.md`` section 2, 6). Every log line names accounts by id, never by email.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable

from . import autostart, engine, protocol, settings_catalog as catalog
from . import signin_parse
from .accounts import (
    ABSENT, Account, setting_priority, set_order as numbered_set_order, sorted_by_priority,
    with_legacy_threshold, with_window_thresholds,
)
from .claude_cli import AuthStatus, ClaudeCli, ClaudeError
from .claude_login import ClaudeLogin, LiveLoginBusy, LockLost, LoginWriteError
from .claude_service import ClaudeService, Identity
from .credtools import gate_target_problem, is_usable_login, live_login_changed, unusable_reason
from .logfile import CategoryLog, Log
from .logins import Login, credential_for_switch, details_belong, lineage_matches, login_from_credential, same_grant, shared_logins
from .paths import Paths
from .protocol import ControlError
from .signin import Launch, Purpose, SignInSession
from .stores import AccountBackup, AccountsFile, BackupStore, NeedsReauthFile, SettingsFile
from .usage_guard import (
    GRANT_REJECTED, NO_BACKUP, ROTATION_LOST, STORE_UNAVAILABLE, UNREACHABLE, BackupRefreshOutcome, UsageRequestGuard,
    belongs_to_another_account, refreshed,
)
from .usagecycle import MESSAGE_BELONGED, CardError, UsageMixin

#: Seconds between 2001-01-01 (Swift's reference date) and 1970-01-01. ``Account.lastUsed`` is
#: stored in the Mac's encoding so an account file is the same shape on both platforms.
REFERENCE_DATE_OFFSET = 978307200.0

BUSY_MESSAGE = "A switch or sign-in is in progress. Try again in a moment."
LIVE_CHANGED_MESSAGE = ("Claude Code renewed its login while PixelSwitch was switching. Nothing was changed. "
                        "Try again in a moment.")
SHUTTING_DOWN_MESSAGE = "PixelSwitch is shutting down."


class ActionError(ControlError):
    """Why an action could not be done (``AppState.ActionError``): a ``ControlError`` that also says
    whether the auto-switch path should stay quiet about it (a missing active account)."""

    def __init__(self, kind: str, message: str, silent: bool = False) -> None:
        super().__init__(kind, message)
        self.silent = silent


class SwitchFailure(Exception):
    """A switch could not be completed; ``message`` is what the person sees."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MutationGate:
    """The one mutex for credential mutation. ``try_begin`` never waits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._kind: str | None = None

    @property
    def kind(self) -> str | None:
        return self._kind

    @property
    def busy(self) -> bool:
        return self._kind is not None

    def try_begin(self, kind: str) -> bool:
        with self._lock:
            if self._kind is not None:
                return False
            self._kind = kind
            return True

    def end(self, kind: str) -> None:
        """Ends ``kind`` if it is the one held (idempotent: ending twice, or ending another kind, does nothing)."""
        with self._lock:
            if self._kind == kind:
                self._kind = None


@dataclass
class _Renewal:
    event: threading.Event
    outcome: BackupRefreshOutcome | None = None


def fallback_order(remaining: list[Account]) -> list[Account]:
    """AccountOrder.fallbackOrder: where to go when the active account is removed, best first:
    the accounts that are not Manual only in priority order, then the Manual only ones."""
    ordered = sorted_by_priority(remaining)
    return [a for a in ordered if not a.manual_only] + [a for a in ordered if a.manual_only]


def is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


class Coordinator(UsageMixin):
    def __init__(
        self,
        *,
        paths: Paths,
        log: Log,
        settings: SettingsFile,
        accounts_file: AccountsFile,
        backups: BackupStore,
        needs_reauth_file: NeedsReauthFile,
        login: ClaudeLogin,
        cli: ClaudeCli,
        service: ClaudeService,
        app_version: str,
        test_mode: bool = False,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        signin_parent: str | None = None,
        post_switch_pause: float = 0.0,
    ) -> None:
        self.paths = paths
        self._log_root = log
        self.log: CategoryLog = log.category("AppState")
        self._signin_log: CategoryLog = log.category("SignIn")
        self.settings = settings
        self.accounts_file = accounts_file
        self.backups = backups
        self.needs_file = needs_reauth_file
        self.login = login
        self.cli = cli
        self.service = service
        self.guard: UsageRequestGuard = service.guard
        self.app_version = app_version
        self.test_mode = test_mode
        self.clock = clock
        self.sleep = sleep
        self.gate = MutationGate()
        self.signin_parent = signin_parent or os.path.join(paths.state_dir, "signin")
        self._post_switch_pause = post_switch_pause
        self._state_lock = threading.RLock()
        self._renewal_lock = threading.Lock()
        self._renewals: dict[str, _Renewal] = {}
        self._did_audit = False
        self._error_message: str | None = None
        self._last_error_emitted: str | None = None
        self._listener: Callable[[str, dict], None] | None = None
        self._current_sign_in: SignInSession | None = None
        self._sign_in_open: dict[str, str | None] = {}
        self._last_sign_in_info: dict | None = None
        self._updater: Any = None
        self.shutting_down = False
        self.on_quit: Callable[[], None] = lambda: None
        self.on_check_for_updates: Callable[[], None] = lambda: None
        self._init_usage_state()
        self._accounts: list[Account] = []
        self._active: Account | None = None
        self._last_active_id: str | None = None
        self._load_accounts()
        self._restore_needs_reauth()

    # =====================================================================================
    # Events
    # =====================================================================================

    def set_listener(self, listener: Callable[[str, dict], None] | None) -> None:
        """The control server's hook: called with (event type, data) for every event."""
        self._listener = listener

    def emit(self, event_type: str, data: dict) -> None:
        listener = self._listener
        if listener is not None:
            try:
                listener(event_type, data)
            except Exception as error:
                self.log.warning(f"[event] Listener failed ({type(error).__name__})")

    def emit_auto_switched(self, record) -> None:
        data = protocol.auto_switched_data(record.from_id, record.to_id, record.limit, record.trigger, record.window)
        if record.relaxed:
            data["relaxed"] = True
        self.emit("autoSwitched", data)

    def emit_accounts_changed(self) -> None:
        """After any change to the accounts list or to a field ``AccountInfo`` carries apart from usage."""
        self.emit("accountsChanged", protocol.accounts_changed_data(self.account_infos()))

    def set_error(self, message: str | None) -> None:
        """AppState.errorMessage: the one-line footer. A new non-empty message that differs from the
        last one becomes an ``error`` event."""
        self._error_message = message
        if message and message != self._last_error_emitted:
            self._last_error_emitted = message
            self.emit("error", protocol.error_data(message))
        if message is None:
            self._last_error_emitted = None

    @property
    def error_message(self) -> str | None:
        return self._error_message

    # =====================================================================================
    # Accounts: load, install, save
    # =====================================================================================

    def _load_accounts(self) -> None:
        loaded = self.accounts_file.load()
        active = next((a for a in loaded if a.is_active), None)
        self._accounts = [replace(a, is_active=(active is not None and a.id == active.id)) for a in loaded]
        self._active = next((a for a in self._accounts if a.is_active), None)
        self._last_active_id = self._active.id if self._active else None
        self.log.info(f"[init] Loaded {len(self._accounts)} accounts, active: {self._active.id if self._active else 'none'}")

    @property
    def accounts(self) -> list[Account]:
        with self._state_lock:
            return list(self._accounts)

    def account_by_id(self, account_id: str) -> Account | None:
        with self._state_lock:
            return next((a for a in self._accounts if a.id == account_id), None)

    def active_account(self) -> Account | None:
        """AppState.activeAccount: kept as its own copy, so a removed active account is still the
        one a fallback switch leaves (credentials.md 8.2 option A)."""
        with self._state_lock:
            return self._active

    def _save_accounts(self) -> None:
        try:
            self.accounts_file.save(self._accounts)
        except OSError as error:
            self.log.error(f"[saveAccounts] Could not save accounts.json ({type(error).__name__})")

    def _install_accounts(self, accounts: list[Account], *, active: "Account | None | bool" = True,
                          announce: bool = True) -> None:
        """Replaces the list (already in the order it should have) and saves. ``active=True`` keeps
        ``_active`` pointing at the fresh copy of the same account; an Account or None sets it."""
        with self._state_lock:
            self._accounts = list(accounts)
            if active is True:
                if self._active is not None:
                    fresh = next((a for a in self._accounts if a.id == self._active.id), None)
                    if fresh is not None:
                        self._active = fresh
            else:
                self._active = active or None
            self._save_accounts()
            new_id = self._active.id if self._active else None
            changed = new_id != self._last_active_id
            self._last_active_id = new_id
            email = self._active.email if self._active else None
        if changed:
            self.emit("activeAccountChanged", protocol.active_account_changed_data(new_id, email))
        if announce:
            self.emit_accounts_changed()

    def _mark_active(self, account_id: str | None, *, last_used: bool = False) -> Account | None:
        """Every account's ``is_active`` follows ``account_id``; ``_active`` becomes that account."""
        with self._state_lock:
            stamp = self.clock() - REFERENCE_DATE_OFFSET
            updated = [replace(a, is_active=(a.id == account_id), last_used=stamp if (last_used and a.id == account_id)
                               else a.last_used) for a in self._accounts]
            chosen = next((a for a in updated if a.id == account_id), None)
        self._install_accounts(updated, active=chosen)
        return chosen

    def adopt_active(self, account: Account) -> None:
        """Marks ``account`` as the active one in PixelSwitch's own records. It now reads its live
        login, which is not the saved one the guard may have flagged; forget what was earned on the old token."""
        self._mark_active(account.id)
        self.note_credential_changed(account)

    # =====================================================================================
    # Settings
    # =====================================================================================

    def auto_switch_config(self) -> catalog.AutoSwitchConfig:
        return catalog.read_auto_switch(self.settings.snapshot())

    def rule_for(self, account: Account, defaults: engine.Defaults | None = None) -> engine.Rule:
        """The account's resolved rule (spec 2.2), read by id so a stale copy still gets the saved values."""
        stored = self.account_by_id(account.id) or account
        defaults = defaults or self.auto_switch_config().defaults
        return engine.resolve_rule(stored.switch_threshold, stored.session_threshold, stored.weekly_threshold, defaults)

    def refresh_interval(self) -> float:
        value = catalog.read_setting(self.settings.snapshot(), catalog.named("refreshInterval"))
        return float(value)

    def binary_path_setting(self) -> str:
        value = self.settings.snapshot().get("claudeBinaryPathPreference", "")
        return value if isinstance(value, str) else ""

    def setting_value(self, key: catalog.SettingKey) -> Any:
        if key.name == "launchAtLogin":
            return autostart.is_enabled(self.paths)
        return catalog.read_setting(self.settings.snapshot(), key)

    def set_setting(self, key: catalog.SettingKey, value: Any) -> None:
        """Writes an already normalised value and applies its side effect (SettingsStore.set)."""
        name = key.name
        if name == "launchAtLogin":
            try:
                autostart.set_enabled(self.paths, bool(value))
            except OSError as error:
                raise ControlError("failed", f"PixelSwitch could not change Launch at login: "
                                             f"{error.strerror or type(error).__name__}") from None
            return
        if name == "claude.binaryPath":
            catalog.validate_claude_binary_path(value, is_executable_file)
        if name == "updates.automatic" and self._updater is None:
            raise ControlError("failed", "The updater is not running yet. Try again in a moment.")
        stored = catalog.write_setting(self.settings.snapshot(), key, value)
        try:
            self.settings.replace(stored)
        except OSError as error:
            raise ControlError("failed", f"PixelSwitch could not save the setting: "
                                         f"{error.strerror or type(error).__name__}") from None
        if name == "refreshInterval":
            self.restart_timer()
        elif name == "claude.binaryPath":
            self.request_refresh()
        elif name == "updates.automatic":
            self._updater.set_automatic(bool(value))
        if name in (catalog.THRESHOLD_KEY, catalog.SESSION_THRESHOLD_KEY, catalog.WEEKLY_THRESHOLD_KEY):
            self.emit_accounts_changed()

    def attach_updater(self, updater: Any) -> None:
        self._updater = updater

    # =====================================================================================
    # Reading, for the control API
    # =====================================================================================

    def claude_available(self) -> bool:
        return self.cli.available()

    def account_info(self, account: Account, position: int, backups: dict | None = None,
                     defaults: engine.Defaults | None = None) -> dict:
        defaults = defaults or self.auto_switch_config().defaults
        card = self.card_errors.get(account.id)
        usage = protocol.build_usage_info(self.usage.get(account.id), self.usage_sampled_at.get(account.id),
                                          card.message if card is not None else None)
        return protocol.build_account_info(account, position=position, is_switchable=self.is_switchable(account, backups),
                                           defaults=defaults, usage=usage)

    def account_infos(self) -> list[dict]:
        backups = self.backups.all() or {}
        defaults = self.auto_switch_config().defaults
        return [self.account_info(a, i, backups, defaults) for i, a in enumerate(self.accounts, start=1)]

    def info_for(self, account_id: str) -> dict | None:
        accounts = self.accounts
        for index, account in enumerate(accounts, start=1):
            if account.id == account_id:
                return self.account_info(account, index)
        return None

    def auto_switch_info(self) -> dict:
        config = self.auto_switch_config()
        return protocol.build_auto_switch_info(
            enabled=config.enabled, defaults=config.defaults, on_fable=config.on_fable, strategy=config.strategy.value,
            drain_early=config.drain_early, drain_within_hours=config.drain_within_hours)

    def sign_in_info(self) -> dict | None:
        session = self._current_sign_in
        return self.session_info(session) if session is not None else None

    def status_info(self) -> dict:
        active = next((a for a in self.accounts if a.is_active), None)
        info = protocol.build_status_info(
            app_version=self.app_version, active_account_id=active.id if active else None,
            active_account_email=active.email if active else None, account_count=len(self.accounts),
            claude_available=self.claude_available(), last_refresh=self.last_usage_refresh,
            auto_switch=self.auto_switch_info(), sign_in=self.sign_in_info(), platform="linux", test_mode=self.test_mode)
        warning = self.cli.env_warning()
        if warning:
            info["warnings"] = [warning]
        return info

    # =====================================================================================
    # Login ownership (AppState.swift:1049-1125)
    # =====================================================================================

    def saved_logins(self) -> dict[str, Login] | None:
        """Claude Code accounts' saved logins, keyed by account id; None if the store is unreadable."""
        backups = self.backups.all()
        if backups is None:
            return None
        claude_ids = {a.id for a in self.accounts if a.provider == "Claude Code"}
        logins: dict[str, Login] = {}
        for account_id, backup in backups.items():
            if account_id in claude_ids:
                found = login_from_credential(backup.token)
                if found is not None:
                    logins[account_id] = found
        return logins

    def account_matching(self, identity: Identity) -> Account | None:
        """The one Claude Code account an API identity names: by the permanent account id
        (``accountUuid`` in that account's saved identity) and by email. None if neither names
        exactly one account, or if they disagree."""
        claude = [a for a in self.accounts if a.provider == "Claude Code"]
        backups = self.backups.all() or {}
        by_uuid = by_email = None
        if identity.uuid is not None:
            hits = [a for a in claude if (backups.get(a.id) is not None
                                          and backups[a.id].oauth_account.get("accountUuid") == identity.uuid)]
            if len(hits) == 1:
                by_uuid = hits[0]
        if identity.email is not None:
            hits = [a for a in claude if a.email.casefold() == identity.email.casefold()]
            if len(hits) == 1:
                by_email = hits[0]
        if by_uuid is not None and by_email is not None and by_uuid.id != by_email.id:
            return None
        return by_uuid or by_email

    def proven_owner(self, credential: str, believed_account: str | None) -> tuple[Account, Identity] | None:
        """The account the credential's login belongs to, per Anthropic's API, with the API's
        identity for it; None if the API cannot say (expired token, network) or names no known account."""
        found = login_from_credential(credential)
        if found is None:
            return None
        identity = self.service.account_identity(found.access_token, found.expires_at, believed_account)
        if identity is None:
            return None
        owner = self.account_matching(identity)
        return (owner, identity) if owner is not None else None

    @staticmethod
    def details_of(details: dict | None, identity: Identity | None, owner_email: str | None = None) -> bool:
        """Whether saved identity details describe the owner the API named."""
        if details is None:
            return False
        uuid_value, email = details.get("accountUuid"), details.get("emailAddress")
        return details_belong(uuid_value if isinstance(uuid_value, str) else None,
                              email if isinstance(email, str) else None,
                              identity.uuid if identity else None,
                              identity.email if identity else owner_email)

    # =====================================================================================
    # needs-reauth, credential changes (AppState.swift:1688-1736)
    # =====================================================================================

    def note_credential_changed(self, account: Account, read_next: bool = True) -> None:
        """Called wherever an account's stored or live credential changes. Forgets what was earned
        on the old token, drops the card's old complaint and gets the account read on the next cycle."""
        found = self._usage_login(account)
        self.guard.credential_changed(account.id, found.access_token if found else None)
        self.persist_needs_reauth()
        if read_next:
            self.card_errors.pop(account.id, None)
            self.read_soon.add(account.id)

    def flag_needs_reauth(self, account: Account, belonged_to_another: bool = False) -> None:
        """The server refused this account's refresh grant (or its saved login is lost or was
        someone else's): nothing is sent for it until it signs in again."""
        found = self._usage_login(account)
        self.guard.mark_needs_reauth(account.id, found.access_token if found else None)
        self.persist_needs_reauth()
        self.log.warning(f"[needsReauth] {account.id} must sign in again; no usage, profile or refresh requests "
                         "until its login changes")
        self._show_resign(account, MESSAGE_BELONGED if belonged_to_another else None)

    def persist_needs_reauth(self) -> None:
        try:
            self.needs_file.save({str(item) for item in self.guard.needs_reauth_accounts})
        except OSError as error:
            self.log.error(f"[needsReauth] Could not save the needs-reauth file ({type(error).__name__})")

    def _restore_needs_reauth(self) -> None:
        ids = self.needs_file.load()
        known = {i for i in ids if any(a.id == i for a in self._accounts)}
        for account_id in known:
            # Tie the flag to the dead token the account holds now, so any other token clears it.
            backup = self.backups.get(account_id)
            found = login_from_credential(backup.token) if backup is not None else None
            self.guard.mark_needs_reauth(account_id, found.access_token if found else None)
        if known != ids:
            try:
                self.needs_file.save(known)
            except OSError:
                pass
        if known:
            self.log.info(f"[init] {len(known)} account(s) need to sign in again")

    # =====================================================================================
    # Capture (ClaudeService.captureCurrentCredentials, :638-682)
    # =====================================================================================

    def capture_current_credentials(self, account: Account) -> bool:
        """Saves the live login and identity details as ``account``'s backup, but only if the API
        confirms the login is ``account``'s and ~/.claude.json describes that same account (running
        sessions rewrite that file from memory). If the API cannot answer, the file must at least
        name ``account``."""
        self.log.info(f"[capture] Capturing credentials for account {account.id}...")
        token = self.login.read_credential()
        if token is None:
            self.log.error("[capture] Failed: no login found in ~/.claude/.credentials.json")
            return False
        oauth_account = self.login.read_oauth_account()
        if oauth_account is None:
            self.log.error("[capture] Failed: no oauthAccount found in ~/.claude.json")
            return False
        file_uuid = oauth_account.get("accountUuid") if isinstance(oauth_account.get("accountUuid"), str) else None
        file_email = oauth_account.get("emailAddress") if isinstance(oauth_account.get("emailAddress"), str) else None
        live = login_from_credential(token)
        identity = self.service.account_identity(live.access_token, live.expires_at, account.id) if live else None
        if live is not None and identity is not None:
            if identity.email is None or identity.email.casefold() != account.email.casefold():
                self.log.error(f"[capture] REFUSED: the live login belongs to another account, not {account.id}")
                return False
            if not details_belong(file_uuid, file_email, identity.uuid, identity.email):
                self.log.error("[capture] REFUSED: ~/.claude.json describes another account, not the login's owner")
                return False
        else:
            self.log.warning("[capture] Could not confirm the login's owner with the API; checking ~/.claude.json instead")
            if file_email is None or file_email.casefold() != account.email.casefold():
                self.log.error(f"[capture] REFUSED: ~/.claude.json does not name {account.id}")
                return False
            # Without the API there is no proof whose login this is, and running sessions can leave
            # the wrong account in ~/.claude.json. So never let it replace a DIFFERENT login
            # already saved for this account.
            existing = self.backups.get(account.id)
            existing_login = login_from_credential(existing.token) if existing is not None else None
            if existing_login is not None and live is not None and not same_grant(existing_login, live):
                self.log.error("[capture] REFUSED: without the API's confirmation, not replacing the saved login "
                               "with a different one")
                return False
        self.log.info(f"[capture] Login and identity confirmed for {account.id}; saving backup...")
        result = self.backups.save(token, oauth_account, account.id)
        self.log.info(f"[capture] Save result: {result}")
        return result

    # =====================================================================================
    # Add current account (AppState.performAddCurrentAccount, :271-330)
    # =====================================================================================

    def _shadowed_message(self, status: AuthStatus) -> str:
        method = status.shadowing_auth_method or "unknown"
        return (f"Claude CLI is authenticating via {method} instead of a stored claude.ai login, so it reports no "
                "account. Unset ANTHROPIC_AUTH_TOKEN / CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_PROFILE and remove any "
                "apiKeyHelper, then try again.")

    def add_current_account(self) -> str:
        """Saves the account Claude Code is signed in as and returns its id. Raises ``ActionError``
        with the messages the Mac shows."""
        if self.shutting_down:
            raise ActionError("failed", SHUTTING_DOWN_MESSAGE)
        if not self.gate.try_begin("capture"):
            raise ActionError("busy", BUSY_MESSAGE)
        try:
            self.log.info("[addAccount] Starting add current account flow...")
            if not self.claude_available():
                self.log.error("[addAccount] Aborted: Claude CLI not found")
                raise ActionError("claudeUnavailable", "Claude CLI not found")
            try:
                status = self.cli.auth_status()
            except ClaudeError as error:
                self.log.error(f"[addAccount] Error: {error.kind}")
                raise ActionError("failed", error.message) from None
            if not status.logged_in:
                self.log.error("[addAccount] Aborted: not logged in")
                raise ActionError("failed", "Not logged in to Claude. Run 'claude auth login' first.")
            if not status.email:
                self.log.error(f"[addAccount] Aborted: CLI reports authMethod={status.auth_method} without an account identity")
                raise ActionError("failed", self._shadowed_message(status))
            if any(a.email == status.email for a in self.accounts):
                self.log.warning("[addAccount] Aborted: duplicate account")
                raise ActionError("failed", "Account already exists")
            first = not self.accounts
            account = Account(id=str(uuid.uuid4()).upper(), email=status.email, display_name=status.org_name or status.email,
                              org_name=status.org_name, subscription_type=status.subscription_type, is_active=first)
            self.log.info(f"[addAccount] Created account model, id={account.id}")
            if not self.capture_current_credentials(account):
                self.log.error("[addAccount] Token capture failed!")
                raise ActionError("failed", "Could not capture the current Claude Code login")
            updated = sorted_by_priority([*self.accounts, account])
            self._install_accounts(updated, active=account if first else True)
            self.log.info(f"[addAccount] Account saved. Total accounts: {len(updated)}")
            return account.id
        finally:
            self.gate.end("capture")

    # =====================================================================================
    # Sign-in (AppState.swift:332-669)
    # =====================================================================================

    def start_sign_in(self, purpose: Purpose, open_with: str | None = None) -> SignInSession:
        """Starts a sign-in and returns its session at once. The current account is backed up first
        (before ``claude auth login`` can overwrite it), then the CLI runs. Raises ``ActionError``
        (``busy`` or ``claudeUnavailable``)."""
        if self.shutting_down:
            raise ActionError("failed", SHUTTING_DOWN_MESSAGE)
        current = self._current_sign_in
        decision = signin_parse.gate_decide(
            self.claude_available(), self.gate.kind in ("switch", "capture", "remove"), self.gate.kind == "signin",
            None if current is None else current.is_finished)
        if decision == "claudeUnavailable":
            raise ActionError("claudeUnavailable", "The claude command could not be found on this computer.")
        if decision == "busy" or not self.gate.try_begin("signin"):
            raise ActionError("busy", BUSY_MESSAGE)
        self.set_error(None)
        path = self.cli.path()
        assert path is not None
        launch = Launch(path, self.cli.environment(path))
        session = SignInSession(
            purpose, launch, self.signin_parent, log=self._signin_log.info,
            complete=self._complete_sign_in, on_finish=self._sign_in_finished, on_change=self._sign_in_changed)
        self._current_sign_in = session
        self._sign_in_open[session.id] = open_with
        self._sign_in_changed(session)

        def begin() -> None:
            self._back_up_before_sign_in(purpose)
            session.start()

        threading.Thread(target=begin, name="signin-backup", daemon=True).start()
        return session

    def current_sign_in(self) -> SignInSession | None:
        return self._current_sign_in

    def dismiss_sign_in(self) -> None:
        if self._current_sign_in is not None and self._current_sign_in.is_finished:
            self._current_sign_in = None

    def session_info(self, session: SignInSession) -> dict:
        return protocol.build_sign_in_info(
            sign_in_id=session.id, purpose=session.purpose.kind, state=session.state, started_at=session.started_at,
            code_submitted=session.code_submitted, account_id=session.purpose.account_id, email=session.purpose.email,
            message=session.message, result_account_id=session.result_account_id,
            automatic_link=session.automatic_link, manual_link=session.manual_link, notice=session.notice)

    def _sign_in_changed(self, session: SignInSession) -> None:
        info = self.session_info(session)
        if info != self._last_sign_in_info:
            self._last_sign_in_info = info
            self.emit("signInChanged", protocol.sign_in_changed_data(info))
        open_with = self._sign_in_open.get(session.id)
        link = session.automatic_link
        if link is not None and session.id in self._sign_in_open and open_with is not None:
            self._sign_in_open[session.id] = None  # once
            self._open_link(link, open_with)

    def _open_link(self, link: str, open_with: str) -> None:
        from . import browsers

        if self.test_mode:
            self.log.info("[signIn] Test mode: not opening a browser")
            return
        browser = None if open_with.casefold() == "default" else browsers.find_browser(self.paths.home, open_with)
        if open_with.casefold() != "default" and browser is None:
            return
        if not browsers.open_url(self.paths.home, link, browser):
            self.log.warning("[signIn] Could not open the sign-in link")

    def browser_names(self) -> list[str]:
        from . import browsers

        return [b.name for b in browsers.installed_browsers(self.paths.home)]

    def check_browser(self, open_with: str) -> None:
        """accounts.signIn.start ``open``: ``default`` or an installed browser's name."""
        if open_with.casefold() == "default" or self.test_mode:
            return
        from . import browsers

        if browsers.find_browser(self.paths.home, open_with) is None:
            raise ControlError("invalidValue", f'No browser called "{open_with}" is installed on this computer.',
                               ["default", *self.browser_names()])

    def _back_up_before_sign_in(self, purpose: Purpose) -> None:
        """Step 1: back up the current account (token + oauthAccount) before the login overwrites them."""
        current = self.active_account()
        if purpose.kind == "newAccount":
            if current is not None:
                self._signin_log.info("[loginNewAccount] Step 1: Backing up the current account...")
                backed = self.capture_current_credentials(current)
                self._signin_log.info(f"[loginNewAccount] Step 1: Backup result: {backed}")
                if backed:
                    self.note_credential_changed(current)
            else:
                self._signin_log.info("[loginNewAccount] Step 1: No active account, skipping backup")
        elif current is not None and current.id != purpose.account_id:
            self._signin_log.info("[reauth] Backing up the current account before login...")
            if self.capture_current_credentials(current):
                self.note_credential_changed(current)

    def _complete_sign_in(self, session: SignInSession) -> tuple[str, str | None, str | None]:
        """Runs after Claude Code exited 0. Returns (final state, message, account id)."""
        self.sleep(1.0)  # the Mac gives its keychain a second to settle; kept until proven unnecessary
        if session.purpose.kind == "newAccount":
            return self._finish_new_account_sign_in()
        return self._finish_reauthentication(session.purpose.account_id or "", session.purpose.email or "")

    def _finish_new_account_sign_in(self) -> tuple[str, str | None, str | None]:
        log = self._signin_log
        try:
            status = self.cli.auth_status()
        except ClaudeError as error:
            self.set_error(error.message)
            self.gate.end("signin")
            return "failed", error.message, None
        kind, detail = signin_parse.classify_new_account(status, self.accounts)
        if kind == "notLoggedIn":
            message = "Login did not complete"
            self.set_error(message)
            self.gate.end("signin")
            return "failed", message, None
        if kind == "noIdentity":
            message = self._shadowed_message(status)
            self.set_error(message)
            self.gate.end("signin")
            return "failed", message, None
        if kind == "existing":
            existing_id = str(detail)
            log.info("[loginNewAccount] Step 4: Account already exists, refreshing backup and marking it active")
            target = self.account_by_id(existing_id)
            if target is None:
                self.gate.end("signin")
                return "failed", "Login did not complete", None
            captured = self.capture_current_credentials(target)
            if captured:
                self.note_credential_changed(target)
            if self.account_by_id(existing_id) is None:  # the capture took a while; the list may have changed
                message = "That account was removed during the sign-in, so nothing was saved."
                self.set_error(message)
                self.gate.end("signin")
                return "failed", message, None
            self._mark_active(existing_id, last_used=True)
            self.last_auto_switch_at = self.clock()  # a login is a deliberate account choice
            self.gate.end("signin")
            if captured:
                self.set_error("Account already exists - credentials refreshed")
                return "succeeded", None, existing_id
            log.error("[loginNewAccount] Step 4: Backup capture FAILED for existing account")
            message = "Could not capture credentials"
            self.set_error(message)
            return "failed", message, None
        email = str(detail)
        account = Account(id=str(uuid.uuid4()).upper(), email=email, display_name=status.org_name or email,
                          org_name=status.org_name, subscription_type=status.subscription_type, is_active=True)
        log.info(f"[loginNewAccount] Step 5: Created account, id={account.id}")
        if not self.capture_current_credentials(account):
            message = "Could not capture credentials"
            self.set_error(message)
            log.error("[loginNewAccount] Step 5: Capture failed!")
            self.gate.end("signin")
            return "failed", message, None
        updated = [replace(a, is_active=False) for a in self.accounts] + [account]
        self._install_accounts(sorted_by_priority(updated), active=account)
        self.last_auto_switch_at = self.clock()
        log.info(f"[loginNewAccount] Step 6: New account active. Total: {len(updated)}")
        # refresh() skips while a sign-in holds the gate; the session is still `completing`, which
        # keeps a second sign-in out meanwhile.
        self.gate.end("signin")
        self.refresh()
        return "succeeded", None, account.id

    def _finish_reauthentication(self, account_id: str, expected_email: str) -> tuple[str, str | None, str | None]:
        log = self._signin_log
        try:
            status = self.cli.auth_status()
        except ClaudeError as error:
            self.set_error(error.message)
            self.gate.end("signin")
            return "failed", error.message, None
        kind, signed_in_as = signin_parse.classify_reauthentication(status, expected_email)
        if kind == "notLoggedIn":
            message = "Login did not complete"
        elif kind == "noIdentity":
            message = self._shadowed_message(status)
        elif kind == "wrongAccount":
            message = f"Logged in as {signed_in_as}, but expected {expected_email}. Credentials not updated."
        else:
            message = ""
        if message:
            self.set_error(message)
            self.gate.end("signin")
            return "failed", message, None
        account = self.account_by_id(account_id)
        if account is None:
            message = "That account was removed during the sign-in, so nothing was saved."
            self.set_error(message)
            self.gate.end("signin")
            return "failed", message, None
        captured = self.capture_current_credentials(account)
        log.info(f"[reauth] Token capture result: {captured}")
        if captured:
            self.note_credential_changed(account)  # forget the old login's park, refusal, cool-down and flag
        # Update metadata. Done even when the capture failed -- the CLI really is on this account
        # now -- but a failed capture must be surfaced, not folded into "completed".
        updated = [replace(a, org_name=status.org_name, subscription_type=status.subscription_type, is_active=True)
                   if a.id == account.id else replace(a, is_active=False) for a in self.accounts]
        chosen = next((a for a in updated if a.id == account.id), None)
        self._install_accounts(updated, active=chosen)
        self.last_auto_switch_at = self.clock()
        self.gate.end("signin")
        self.refresh()
        if captured:
            log.info("[reauth] ===== Re-authentication completed =====")
            return "succeeded", None, account.id
        message = "Could not capture credentials"
        self.set_error(message)  # after refresh(): the Mac's refresh clears the message
        log.error("[reauth] ===== Re-authentication finished, but the backup capture FAILED =====")
        return "failed", message, None

    def _sign_in_finished(self, session: SignInSession) -> None:
        """Runs once per sign-in, on every path: success, failure, cancel, timeout."""
        self.gate.end("signin")
        self._sign_in_open.pop(session.id, None)
        if session.state == "failed" and session.message:
            self.set_error(session.message)
        elif session.state == "cancelled":
            self._signin_log.info("[signIn] Cancelled; nothing was changed")

    def submit_sign_in_code(self, code: str) -> dict:
        session = self._current_sign_in
        if session is None or session.is_finished:
            raise ControlError("notFound", "No sign-in is running.")
        if session.state != "waitingForUser" or session.code_submitted:
            raise ControlError("invalidValue", "This sign-in is not waiting for a code.")
        if not session.submit_code(code):
            raise ControlError("invalidValue", "That isn't the whole code. Copy all of it, including the part after #.")
        return self.session_info(session)

    def cancel_sign_in(self) -> dict:
        session = self._current_sign_in
        if session is None or session.is_finished:
            raise ControlError("notFound", "No sign-in is running.")
        if session.state == "completing":
            raise ControlError("busy", "The account is being saved and cannot be cancelled now.")
        session.cancel()
        return self.session_info(session)

    # =====================================================================================
    # Removal (AppState.removeAccount, spec D20)
    # =====================================================================================

    def remove_account(self, account_id: str) -> None:
        """Removes the account and its saved login. Removing the account Claude Code is using also
        moves Claude Code to another one (credentials.md 8, option A): nothing is marked active until
        its switch has succeeded; if a switch fails the next account in ``fallback_order`` is tried;
        if none works, no account is active. Removing the last account leaves no active account and
        never recreates it from the live login."""
        if self.shutting_down:
            raise ActionError("failed", SHUTTING_DOWN_MESSAGE)
        account = self.account_by_id(account_id)
        if account is None:
            raise ControlError("notFound", "That account no longer exists.")
        if not self.gate.try_begin("remove"):
            raise ActionError("busy", BUSY_MESSAGE)
        fallbacks: list[Account] = []
        try:
            self.log.info(f"[removeAccount] Removing account {account.id}")
            active = self.active_account()
            was_active = account.is_active or (active is not None and active.id == account.id)
            self.backups.remove(account.id)
            remaining = [a for a in self.accounts if a.id != account.id]
            # Drop every per-account cache too, or a re-added account inherits the removed one's
            # readings, error banner and rate-limit park.
            self.usage.pop(account.id, None)
            self.usage_sampled_at.pop(account.id, None)
            self.card_errors.pop(account.id, None)
            self.guard.forget(account.id)
            self.read_soon.discard(account.id)
            self.persist_needs_reauth()
            if was_active:
                # No remaining account is marked active until its switch succeeds. `_active` stays
                # on the removed account for now: perform_switch needs a current account to leave,
                # and the "already live" shortcut must not see the target as current.
                remaining = [replace(a, is_active=False) for a in remaining]
                fallbacks = fallback_order(remaining)
                self._install_accounts(remaining, active=(True if fallbacks else None))
                if not fallbacks:
                    self.log.info("[removeAccount] Removed the last account; no account is active")
                else:
                    self.log.info(f"[removeAccount] Removed active account; switching to the first of {len(fallbacks)} remaining")
            else:
                self._install_accounts(remaining)
            self.log.info(f"[removeAccount] Done. Remaining accounts: {len(remaining)}")
        finally:
            self.gate.end("remove")
        if fallbacks:
            threading.Thread(target=self._switch_after_removal, args=(account.id, [a.id for a in fallbacks]),
                             name="switch-after-removal", daemon=True).start()

    def _switch_after_removal(self, removed_id: str, candidates: list[str]) -> None:
        """Switches Claude Code to the first of ``candidates`` that works. A busy switch or sign-in
        is waited out (a few seconds); any other failure moves on to the next candidate. When none
        works, no account is active."""
        for candidate in candidates:
            target = self.account_by_id(candidate)
            if target is None:
                continue
            attempts = 0
            while not self.shutting_down:
                try:
                    self.perform_switch(target)
                    current = self.active_account()
                    if current is not None and current.id == target.id:
                        return  # success, or "already live" (which adopts the account)
                    break
                except ActionError as error:
                    if error.kind == "busy" and attempts < 5:
                        attempts += 1
                        self.sleep(1.0)
                        continue
                    self.log.warning(f"[removeAccount] Switch to {candidate} after removing {removed_id} failed; "
                                     "trying the next account")
                    break
                except Exception as error:
                    self.log.warning(f"[removeAccount] Switch to {candidate} failed unexpectedly ({type(error).__name__})")
                    break
        # Only if nothing else made another account active meanwhile.
        current = self.active_account()
        if current is not None and current.id == removed_id and not self.shutting_down:
            self._mark_active(None)
            self.log.warning("[removeAccount] No remaining account could be switched to; no account is active")

    # =====================================================================================
    # Labels, thresholds, priority, order (the setters the control API calls)
    # =====================================================================================

    def _replace_account(self, account_id: str, change: Callable[[Account], Account], *, label: str) -> bool:
        with self._state_lock:
            current = next((a for a in self._accounts if a.id == account_id), None)
            if current is None:
                return False
            changed = change(current)
            if changed == current:
                return True
            updated = [changed if a.id == account_id else a for a in self._accounts]
        self._install_accounts(updated)
        self.log.info(f"[{label}] {account_id}")
        return True

    def set_label(self, label: str | None, account_id: str) -> None:
        self._replace_account(account_id, lambda a: replace(a, custom_label=label), label="updateAccountLabel")

    def set_threshold(self, threshold: float | None, account_id: str) -> None:
        """The legacy write (D8): both windows, clearing the account's window values even when the
        both-windows value is unchanged."""
        self._replace_account(account_id, lambda a: with_legacy_threshold(a, threshold), label="thresholds")

    def set_thresholds(self, account_id: str, session: Any = ABSENT, weekly: Any = ABSENT) -> None:
        self._replace_account(account_id, lambda a: with_window_thresholds(a, session=session, weekly=weekly),
                              label="thresholds")

    def set_priority(self, account_id: str, priority: int | None) -> bool:
        with self._state_lock:
            updated = setting_priority(self._accounts, account_id, priority)
        if updated is None:
            self.log.warning(f"[setPriority] Refused for {account_id}: a priority is a whole number from 1 to 99, or none")
            return False
        if updated != self.accounts:
            self._install_accounts(updated)
            self.log.info(f"[setPriority] {account_id}: {'none' if priority is None else f'P{priority}'}")
        return True

    def set_account_order(self, ordered_ids: list[str]) -> bool:
        """Puts the accounts in the order given and numbers every account from 1 in that order,
        replacing typed numbers (D3). False, and nothing changed, unless every account is named once."""
        with self._state_lock:
            numbered = numbered_set_order(self._accounts, ordered_ids)
        if numbered is None:
            self.log.warning(f"[setAccountOrder] Refused: the order does not name each of the {len(self.accounts)} "
                             "accounts exactly once")
            return False
        if numbered != self.accounts:
            self._install_accounts(numbered)
            self.log.info("[order] Accounts reordered and numbered from 1")
        return True

    # =====================================================================================
    # Switching (AppState.performSwitch :962-1047, ClaudeService.switchAccount :464-609)
    # =====================================================================================

    def perform_switch(self, account: Account) -> None:
        """Switches to ``account`` and returns once the switch has finished (or was not needed).
        Raises ``ActionError``; the messages are the ones the Mac shows."""
        current_active = self.active_account()
        if current_active is None:
            self.log.info("[switchTo] No switch needed (no active account)")
            raise ActionError("failed", "No account is active yet, so there is nothing to switch from. "
                                        "Add the current account first.", silent=True)
        if self.shutting_down:
            raise ActionError("failed", SHUTTING_DOWN_MESSAGE)
        # One credential mutation at a time: a switch already in flight or a running login must
        # finish before another switch may touch ~/.claude/.credentials.json and ~/.claude.json.
        if not self.gate.try_begin("switch"):
            self.log.warning("[switchTo] Skipped: another switch or a login is in progress")
            raise ActionError("busy", BUSY_MESSAGE)
        try:
            self._perform_switch_locked(account, current_active)
        finally:
            self.gate.end("switch")

    def _perform_switch_locked(self, account: Account, current_active: Account) -> None:
        # Whose login is live, proven by Anthropic's API from the token (never from ~/.claude.json,
        # which running sessions rewrite). If it already is the target's, there is nothing to switch.
        live_credential = self.login.read_credential()
        live = self.proven_owner(live_credential, current_active.id) if live_credential else None
        live_owner = live[0] if live else None
        live_identity = live[1] if live else None
        if (live_owner is not None and live_owner.id == account.id) or (live_owner is None and current_active.id == account.id):
            self.log.info(f"[switchTo] No switch needed: {account.id}'s login is already live")
            if live_owner is not None and live_owner.id == account.id and current_active.id != account.id:
                self.adopt_active(account)
            return

        self.log.info(f"[switchTo] ===== Switching from {(live_owner or current_active).id} to {account.id} =====")
        # Resolve the target's backup ONCE, check that its login really is the target's (renewing
        # it first if it has expired), and hand it down.
        target_backup = self._validated_target_backup(account)

        # Any switch, deliberate or automatic, restarts the auto-switch cooldown: a user who
        # knowingly picks an account sitting at 95% must not be auto-switched away seconds later.
        self.last_auto_switch_at = self.clock()
        try:
            shadowed_by = self._switch_account(live_owner, live_identity, live_credential, account, target_backup)
        except SwitchFailure as failure:
            self.log.error(f"[switchTo] Switch failed: {failure.message}")
            raise ActionError("failed", failure.message) from None

        self._mark_active(account.id, last_used=True)
        # The switch wrote the target's saved login as the live one, and saved the old live login
        # under its owner: both credentials changed.
        target_now = self.account_by_id(account.id) or account
        self.note_credential_changed(target_now)
        if live_owner is not None and live_owner.id != account.id:
            owner_now = self.account_by_id(live_owner.id)
            if owner_now is not None:
                self.note_credential_changed(owner_now)
        self.refresh()
        if shadowed_by:  # refresh() clears the message on the Mac, so the warning comes last
            self.set_error(f"Switched to {account.email}, but the Claude CLI is authenticating via {shadowed_by} "
                           "instead of the stored login, so it will not use this account.")
        self.log.info("[switchTo] ===== Switch completed =====")

    # -- the target's backup (:1115-1211) ------------------------------------------------------------------

    def _validated_target_backup(self, account: Account) -> AccountBackup:
        """The target's backup, if its login provably is the target's. An expired login is renewed
        first (with the usage refresh's own guards) so the API can be asked; if the API still cannot
        say, only a login that is also saved under another account is refused."""
        email = account.email
        resign = f"The saved login for {email} has expired and could not be renewed. Re-authenticate {email} to fix it."
        kind, backup = self.backups.lookup(account.id)
        if kind == BackupStore.MISSING:
            raise ActionError("failed", f"No stored credentials for {email}. Use re-authenticate to fix.")
        if kind == BackupStore.UNAVAILABLE or backup is None:
            raise ActionError("failed", "Credential storage is temporarily unavailable. Try again shortly.")
        # Spec D12: a saved login that is not a usable login is never written to Claude Code's live
        # store; it fails with the re-sign message instead.
        reason = gate_target_problem(backup.token)
        if reason is not None:
            self.log.error(f"[switchTo] ABORT: {account.id}'s saved login is not a usable login ({reason})")
            raise ActionError("failed", resign)
        login = login_from_credential(backup.token)
        if login is None:
            raise ActionError("failed", resign)
        state: dict[str, Any] = {"backup": backup, "login": login, "identity": None}

        def renew_and_identify() -> None:
            """Renews the saved login, then asks the API about the renewed one."""
            outcome = self.refresh_backup_in_place(account)
            if outcome.kind == "refreshed":
                current = self.backups.get(account.id)
                if current is not None:
                    state["backup"] = current
                renewed = login_from_credential(outcome.detail or "")
                if renewed is not None:
                    state["login"] = renewed
                    state["identity"] = self.service.account_identity(renewed.access_token, renewed.expires_at, account.id)
                return
            if outcome.kind in ("storeUnavailable", "unreachable"):
                # A blip (network, rate limit): nothing was spent, so go ahead as before; Claude Code
                # renews it on first use.
                self.log.warning(f"[switchTo] Could not renew {account.id}'s expired login right now; switching anyway")
                return
            if outcome.kind == "belongsToAnotherAccount":
                raise ActionError("failed", f"The login saved for {email} belongs to {outcome.detail}. "
                                            f"Re-authenticate {email} to fix it.")
            raise ActionError("failed", resign)  # grantRejected, sameGrantAsAnotherLogin, noBackup, rotationLost

        # Ask the guard BEFORE the profile lookup: an expired or refused login is renewed first,
        # never sent. The lookup is the call that used to send dead tokens on every switch attempt.
        decision = self.guard.decision(account.id, False, login, self.clock())
        if decision.kind == "needsReauth":
            raise ActionError("failed", resign)
        if decision.kind == "refreshFirst":
            renew_and_identify()
        elif decision.kind == "request":
            state["identity"] = self.service.account_identity(login.access_token, login.expires_at, account.id)
            # A 401 just now means the token was refused: renew it and ask again. The guard has
            # already put a cool-down on it, so `decision` would answer "cooling down"; ask what it
            # recorded instead.
            if state["identity"] is None and self.guard.was_rejected(account.id, login.access_token):
                renew_and_identify()
        else:  # parked, coolingDown, waitForClaudeCode
            # Not asking the usage or profile endpoint now. An expired or refused login can still be
            # renewed (a POST to the token endpoint, outside both limits).
            if login.is_expired(self.clock()) or self.guard.was_rejected(account.id, login.access_token):
                renew_and_identify()
            else:
                self.log.info(f"[switchTo] Not asking the API about {account.id}'s saved login right now; checking lineage only")

        backup, login, identity = state["backup"], state["login"], state["identity"]
        if identity is not None:
            owner = self.account_matching(identity)
            if owner is None or owner.id != account.id:
                owner_name = (owner.email if owner else None) or identity.email or "another account"
                raise ActionError("failed", f"The login saved for {email} belongs to {owner_name}. "
                                            f"Re-authenticate {email} to fix it.")
            # The token is the target's; its saved identity details must be too, or the switch
            # would pair this login with another account's details.
            if not self.details_of(backup.oauth_account, identity):
                raise ActionError("failed", f"The saved details for {email} belong to another account. "
                                            f"Re-authenticate {email} to fix it.")
            return backup
        others = {k: v for k, v in (self.saved_logins() or {}).items() if k != account.id}
        if lineage_matches(login, others):
            raise ActionError("failed", f"The login saved for {email} is also saved under another account. "
                                        f"Re-authenticate {email} to fix it.")
        return backup

    # -- the switch itself ------------------------------------------------------------------------------------

    def _save_leaving_login(self, owner: Account, credential: str, owner_identity: Identity | None) -> bool:
        """Saves the live login under its proven owner, with identity details only if they describe
        the owner (running sessions can leave another account's in ~/.claude.json)."""
        file_identity = self.login.read_oauth_account()
        existing = self.backups.get(owner.id)
        backup_identity = existing.oauth_account if existing is not None else None

        def belongs(details: dict | None) -> bool:
            return self.details_of(details, owner_identity, owner.email)

        identity = file_identity if belongs(file_identity) else (backup_identity if belongs(backup_identity) else None)
        if identity is None:
            self.log.warning("[switchAccount] Step 1: No identity details on record that match the owner; skipping backup")
            return False
        saved = self.backups.save(credential, identity, owner.id)
        self.log.info(f"[switchAccount] Step 1: Backup saved for its owner: {saved}")
        return saved

    def _reprove(self, owner: Account | None, owner_identity: Identity | None, credential: str | None
                 ) -> str | None:
        """Claude Code renewed the login after it was proven: ask the API about the new one
        (OUTSIDE the refresh lock, which is held for 10 s at most). The new credential counts as
        proven only if the API confirms it still belongs to the owner."""
        found = login_from_credential(credential) if credential else None
        if owner is None or found is None or credential is None:
            return None
        identity = self.service.account_identity(found.access_token, found.expires_at, owner.id)
        if identity is not None and details_belong(identity.uuid, identity.email,
                                                   owner_identity.uuid if owner_identity else None, owner.email):
            return credential
        return None

    def _switch_account(self, live_owner: Account | None, live_identity: Identity | None,
                        verified_credential: str | None, target: Account, target_backup: AccountBackup) -> str | None:
        """Steps 4 to 10 of credentials.md section 3 on Linux. Returns ``shadowed_by`` (None when
        the CLI confirmed the target), raises :class:`SwitchFailure`.

        Under Claude Code's refresh lock (taken BEFORE step 4's final read of the live credential,
        held through the merge read, the write and the read-back, released in a ``finally``):
        re-read the live credential; if its ``claudeAiOauth`` differs from the proven one, leave the
        lock, re-prove once (a network call, which must not be made while holding the lock), and
        try again; then save the leaving login under its proven owner, re-read the live credential
        immediately before writing (re-read-before-write, lock or not), write ``.credentials.json``
        merged, write ``oauthAccount``, and read both back."""
        reason = gate_target_problem(target_backup.token)
        if reason is not None:
            self.log.error(f"[switchAccount] ABORT: the target's saved login is not a usable login ({reason}); "
                           "nothing was written")
            raise SwitchFailure(f"The saved login for {target.email} can't be used. "
                                f"Re-authenticate {target.email} to fix it.")
        self.log.info(f"[switchAccount] Switching to {target.id}; live login belongs to "
                      f"{live_owner.id if live_owner else 'an unproven account'}")
        verified = verified_credential
        attempt = 0
        while True:
            attempt += 1
            reprove: str | None = None
            written = False
            try:
                with self.login.refresh_lock() as held:
                    # 4. Back up the live login under the account it belongs to.
                    self.log.info("[switchAccount] Step 1: Backing up the live login...")
                    seen = self.login.read_credential()
                    seen_login = login_from_credential(seen) if seen else None
                    verified_login = login_from_credential(verified) if verified else None
                    proven = False
                    if live_owner is not None and verified_login is not None and seen_login is not None:
                        if same_grant(verified_login, seen_login):
                            proven = True
                        elif attempt == 1:
                            reprove = seen  # the CLI renewed the login during the checks: prove the new one
                    if reprove is None:
                        saved = False
                        if live_owner is not None and seen is not None and proven:
                            saved = self._save_leaving_login(live_owner, seen, live_identity)
                        else:
                            self.log.warning("[switchAccount] Step 1: The live login's owner is not proven (or it changed "
                                             "since it was checked); skipping the backup so no account's backup is "
                                             "overwritten with another's login")
                        step_ok = saved or not is_usable_login(seen)
                        # 5. Re-read the live login immediately before writing (spec 5.3). If Claude
                        # Code renewed it since step 4 looked, the copy saved for its owner holds a
                        # refresh token the server has rotated: write nothing.
                        held.check()
                        live_now = self.login.read_credential()
                        changed = live_login_changed(seen, live_now)
                        if changed and attempt == 1:
                            self.log.warning("[switchAccount] Step 3: The live login changed since it was backed up; "
                                             "backing it up again before writing")
                            reprove = live_now
                        elif changed or (attempt > 1 and live_owner is not None and not step_ok):
                            self.log.error("[switchAccount] Step 3: ABORT: the live login is still changing or could "
                                           "not be saved; nothing was written")
                            raise SwitchFailure(LIVE_CHANGED_MESSAGE)
                        else:
                            self._write_target(held, live_now, target, target_backup)
                            written = True
            except LiveLoginBusy as busy:
                raise SwitchFailure(busy.message) from None
            except LockLost:
                self.log.error("[switchAccount] The Claude Code lock was lost or held too long; nothing more was written")
                raise SwitchFailure("PixelSwitch could not finish within the time it may hold Claude Code's lock. "
                                    "Try again in a moment.") from None
            if written:
                break
            verified = self._reprove(live_owner, live_identity, reprove)
        return self._verify_switch(target, target_backup)

    def _write_target(self, held, live_now: str | None, target: Account, target_backup: AccountBackup) -> None:
        merge = credential_for_switch(live_now, target_backup.token)
        if merge.kind == "merged":
            kept = ", ".join(merge.kept_keys) or "(nothing else)"
            self.log.info(f"[switchAccount] Step 3: claudeAiOauth from the target backup; kept {kept} from the live credential")
        else:
            self.log.warning(f"[switchAccount] Step 3: Writing the target backup verbatim ({merge.reason}); "
                             "other keys come from the backup")
        held.check()
        try:
            self.login.write_credential(merge.credential)
        except LoginWriteError as error:
            self.log.error(f"[switchAccount] Step 3: Failed to write the login: {error}")
            raise SwitchFailure(f"Failed to write the Claude Code login. {error}") from None
        if not self.login.write_oauth_account(target_backup.oauth_account):
            self.log.error("[switchAccount] Step 3: Failed to write oauthAccount to ~/.claude.json!")
            self._roll_back_credential(live_now, merge.credential)
            raise SwitchFailure("Failed to write oauthAccount to ~/.claude.json")
        self.log.info("[switchAccount] Step 3: Both login and oauthAccount written")

    def _roll_back_credential(self, previous: str | None, written: str) -> None:
        """The identity write failed after the login was written: put the login back, if it is still
        exactly what we wrote, so Claude Code is not left on one account with another's identity."""
        if previous is None:
            return
        try:
            if self.login.read_credential() == written.strip():
                self.login.write_credential(previous)
                self.log.warning("[switchAccount] Put the previous login back after the identity write failed")
        except (LoginWriteError, OSError):
            self.log.error("[switchAccount] Could not put the previous login back")

    def _verify_switch(self, target: Account, target_backup: AccountBackup) -> str | None:
        """Step 9. The credentials are already written, so a CLI that does not answer must not
        strand the switch half-done: check the stores directly."""
        self.log.info("[switchAccount] Step 4: Verifying with `claude auth status`...")
        try:
            status = self.cli.auth_status()
        except ClaudeError as error:
            if error.kind in ("timedOut", "unavailable"):
                self.log.warning("[switchAccount] Step 4: `claude auth status` did not answer; verifying against the "
                                 "credential store instead")
                if not self._on_disk_match(target, target_backup):
                    raise SwitchFailure("Account switch verification failed") from None
                self.log.info("[switchAccount] Step 4: Switch verified against the credential store (CLI did not answer)")
                return None
            raise SwitchFailure(error.message) from None
        if not status.logged_in:
            self.log.error("[switchAccount] Step 4: Not logged in after switch!")
            raise SwitchFailure("Account switch verification failed")
        if status.email:
            if status.email != target.email:
                self.log.error(f"[switchAccount] Step 4: Logged in as another account instead of {target.id}")
                raise SwitchFailure(f"Switch failed: expected {target.email} but got {status.email}. "
                                    "Try removing and re-adding the account.")
            self.log.info("[switchAccount] Step 4: Switch verified: the CLI reports the target account")
            return None
        # No `email` in the status output: the CLI is logged in but resolves to a credential source
        # that outranks the stored claude.ai login. That says nothing about whether our swap worked,
        # so verify against the credentials we just wrote (issue #18).
        shadowed = status.shadowing_auth_method or "unknown"
        self.log.warning(f"[switchAccount] Step 4: CLI reports authMethod={shadowed} and omits the account identity; "
                         "verifying against the credential store instead")
        if not self._on_disk_match(target, target_backup):
            raise SwitchFailure("Account switch verification failed")
        self.log.info(f"[switchAccount] Step 4: Switch verified against the credential store (CLI identity hidden by {shadowed})")
        return shadowed

    def _on_disk_match(self, target: Account, backup: AccountBackup) -> bool:
        """Ground truth that does not depend on ``claude auth status``: both halves of a switch --
        the login and the ``~/.claude.json`` identity -- must hold the target account."""
        live = login_from_credential(self.login.read_credential() or "")
        expected = login_from_credential(backup.token)
        if live is None or expected is None or live.access_token != expected.access_token:
            self.log.error("[switchAccount] The live login does not match the target account's backup")
            return False
        identity = self.login.read_oauth_account()
        if identity is None or identity.get("emailAddress") != target.email:
            self.log.error("[switchAccount] ~/.claude.json identity does not match the target account")
            return False
        return True

    # =====================================================================================
    # Reconcile and audit (AppState.swift:1223-1299)
    # =====================================================================================

    def reconcile_live_login(self) -> None:
        """Keeps PixelSwitch tied to the live login rather than to ~/.claude.json: follows the
        login's proven owner, restores that owner's identity in ~/.claude.json if a session left
        another account's there, and keeps the owner's backup current, so switching back restores
        the newest login instead of one whose refresh token the CLI has since rotated."""
        if self.gate.busy:
            return
        credential = self.login.read_credential()
        if credential is None:
            return
        active = self.active_account()
        proven = self.proven_owner(credential, active.id if active else None)
        if proven is None:
            return
        owner, owner_identity = proven
        # The API call took time: act only if no switch or login started meanwhile and the live
        # login is still the one that was checked. The synchronous part runs under the gate.
        if not self.gate.try_begin("reconcile"):
            self.log.info("[reconcile] A switch or sign-in started; skipping this cycle")
            return
        try:
            if self.login.read_credential() != credential:
                self.log.info("[reconcile] The login changed while its owner was being checked; skipping this cycle")
                return
            current = self.active_account()
            if current is None or current.id != owner.id:
                self.log.warning(f"[reconcile] The live login belongs to {owner.id}, not "
                                 f"{current.id if current else 'none'}; following the login")
                self.adopt_active(owner)
            file_identity = self.login.read_oauth_account()
            file_belongs = self.details_of(file_identity, owner_identity)
            saved = self.backups.get(owner.id)
            backup_belongs = saved is not None and self.details_of(saved.oauth_account, owner_identity)
            if not file_belongs and backup_belongs and saved is not None:
                self.log.warning(f"[reconcile] ~/.claude.json named another account while {owner.id}'s login is live; "
                                 "restoring its identity")
                self.login.write_oauth_account(saved.oauth_account)
            if saved is not None and not _same_credential(saved.token, credential):
                identity = file_identity if file_belongs else (saved.oauth_account if backup_belongs else None)
                if identity is not None:
                    old = login_from_credential(saved.token)
                    new = login_from_credential(credential)
                    token_changed = (old.access_token if old else None) != (new.access_token if new else None)
                    if self.backups.save(credential, identity, owner.id):
                        self.log.info(f"[reconcile] {owner.id}'s backup updated from the live login"
                                      f"{'' if token_changed else ' (other parts of the credential changed; the Claude token did not)'}")
                        # Compare the Claude token, not the whole credential: the file also holds
                        # MCP logins, which change without the Claude token changing.
                        if token_changed:
                            self.note_credential_changed(owner)
        finally:
            self.gate.end("reconcile")

    def audit_saved_logins(self) -> None:
        """Checks saved logins against Anthropic's API and removes any that provably belongs to a
        DIFFERENT known account, flagging that account for re-authentication (on 2026-09-21
        account A's login was saved under B, and switching to B then used A's token). Every saved
        login is checked once per launch; after that, only logins saved under two accounts."""
        if self.gate.busy:
            return
        saved = self.saved_logins()
        if saved is None:
            return
        shared = {i for group in shared_logins(saved) for i in group}
        to_check = {k: v for k, v in saved.items() if k in shared} if self._did_audit else saved
        if not to_check:
            return
        self._did_audit = True
        for account_id, found in sorted(to_check.items()):
            account = self.account_by_id(account_id)
            if account is None or self.guard.is_needs_reauth(account.id):
                continue
            identity = self.service.account_identity(found.access_token, found.expires_at, account.id)
            owner = self.account_matching(identity) if identity is not None else None
            if owner is None or owner.id == account.id:
                continue
            # Act only if nothing moved during the call and this account still holds that same login.
            if not self.gate.try_begin("audit"):
                continue
            try:
                current = self.backups.get(account_id)
                current_login = login_from_credential(current.token) if current is not None else None
                if current_login is None or not same_grant(current_login, found):
                    continue
                if self.backups.remove(account_id):
                    self.log.error(f"[audit] {account.id}'s saved login belongs to {owner.id}; removed it")
                    self.usage.pop(account.id, None)
                    self.card_errors[account.id] = CardError(True, False, MESSAGE_BELONGED)
            finally:
                self.gate.end("audit")

    # =====================================================================================
    # Renewal of saved logins (AppState.swift:1789-1899)
    # =====================================================================================

    def refresh_backup_in_place(self, account: Account) -> BackupRefreshOutcome:
        """Renews a saved login in place, joining a renewal already running for the same account
        instead of starting a second one (two renewals from the same refresh token would race: the
        loser gets ``invalid_grant``, and a server that detects the reuse may revoke the grant)."""
        with self._renewal_lock:
            running = self._renewals.get(account.id)
            if running is None:
                running = _Renewal(threading.Event())
                self._renewals[account.id] = running
                starter = True
            else:
                starter = False
        if not starter:
            running.event.wait()
            return running.outcome or UNREACHABLE
        try:
            outcome = self._renew_backup_in_place(account)
        except Exception as error:
            self.log.error(f"[refreshBackup] Renewal of {account.id} failed unexpectedly ({type(error).__name__})")
            outcome = UNREACHABLE
        finally:
            with self._renewal_lock:
                self._renewals.pop(account.id, None)
        running.outcome = outcome
        running.event.set()
        # Settled once, by the call that started the renewal. What the server said decides what the
        # account is allowed to do next.
        if outcome.kind == "refreshed":
            self.note_credential_changed(account, read_next=False)
        elif outcome.requires_reauth:
            self.flag_needs_reauth(account, belonged_to_another=outcome.kind == "belongsToAnotherAccount")
        return outcome

    def renewals_in_flight(self) -> int:
        with self._renewal_lock:
            return len(self._renewals)

    def _renew_backup_in_place(self, account: Account) -> BackupRefreshOutcome:
        kind, backup = self.backups.lookup(account.id)
        if kind == BackupStore.MISSING:
            self.log.warning(f"[refreshBackup] No stored backup for {account.id}")
            return NO_BACKUP
        if kind == BackupStore.UNAVAILABLE or backup is None:
            self.log.warning(f"[refreshBackup] Store unreadable for {account.id}; trying again next cycle")
            return STORE_UNAVAILABLE
        # A saved login that is not a usable login (spec D12) has nothing to renew with: the
        # account must sign in again.
        reason = unusable_reason(backup.token)
        if reason is not None:
            self.log.warning(f"[refreshBackup] {account.id}'s saved login is not a usable login ({reason}); "
                             "it must sign in again")
            return GRANT_REJECTED

        # Never spend a refresh token that is not provably this account's own. If the same grant is
        # live or saved under another account, refreshing it here rotates that account's refresh
        # token and can sign its running sessions out.
        found = login_from_credential(backup.token)
        if found is not None:
            live_credential = self.login.read_credential()
            live = login_from_credential(live_credential) if live_credential else None
            others = {k: v for k, v in (self.saved_logins() or {}).items() if k != account.id}
            is_live = live is not None and same_grant(live, found)
            refusal = BackupRefreshOutcome.local_refusal(is_live, bool(lineage_matches(found, others)))
            if refusal is not None:
                self.log.error(f"[refreshBackup] {account.id}'s saved login is the same grant as "
                               f"{'the live login' if is_live else 'another account'}'s; not refreshing it")
                return refusal

        # Prove the store is writable BEFORE spending the refresh token: the endpoint can rotate
        # it, and a rotation that cannot be persisted kills the account (old token dead
        # server-side, new one lost, manual re-login the only way back).
        if not self.backups.save(backup.token, backup.oauth_account, account.id):
            self.log.error(f"[refreshBackup] Store not writable; skipping refresh for {account.id} so its refresh token stays valid")
            return STORE_UNAVAILABLE

        result = self.service.refresh_oauth_credentials(backup.token)
        if result.kind == "success" and result.credential is not None:
            refreshed_credential = result.credential
            # A renewed token is fresh, so the API can say whose it is. A login that belongs to
            # another account must not stay under this one.
            renewed = login_from_credential(refreshed_credential)
            identity = self.service.account_identity(renewed.access_token, renewed.expires_at, account.id) if renewed else None
            owner = self.account_matching(identity) if identity is not None else None
            if owner is not None and owner.id != account.id:
                self.log.error(f"[refreshBackup] {account.id}'s saved login belongs to {owner.id}; removing it")
                self.backups.remove(account.id)
                return belongs_to_another_account(owner.email)
            if self.backups.save(refreshed_credential, backup.oauth_account, account.id):
                return refreshed(refreshed_credential)
            self.sleep(1.0)  # the probe passed moments ago, so this is likely a blip: one retry
            if self.backups.save(refreshed_credential, backup.oauth_account, account.id):
                return refreshed(refreshed_credential)
            self.log.error(f"[refreshBackup] Rotation succeeded but the store write failed twice for {account.id}; "
                           "the account needs re-authentication")
            return ROTATION_LOST
        if result.kind == "rejected":
            self.log.warning(f"[refreshBackup] Refresh grant rejected for {account.id}; the refresh token is dead")
            return GRANT_REJECTED
        self.log.warning(f"[refreshBackup] Refresh attempt for {account.id} failed transiently (network/server); will retry")
        return UNREACHABLE

    # =====================================================================================
    # Control-API entry points that are not plain state
    # =====================================================================================

    def switch_account_by_id(self, account_id: str) -> None:
        account = self.account_by_id(account_id)
        if account is None:
            raise ControlError("notFound", "That account no longer exists.")
        self.perform_switch(account)

    def refresh_usage(self) -> None:
        """usage.refresh: waits for a refresh already running instead of starting a second one."""
        self.refresh(wait_if_busy=True)

    def request_check_for_updates(self) -> None:
        self.on_check_for_updates()

    def request_quit(self) -> None:
        self.on_quit()

    # =====================================================================================
    # Shutdown (spec 5.1)
    # =====================================================================================

    def begin_shutdown(self) -> None:
        """SIGTERM or SIGINT: no new cycle, switch, renewal or sign-in; a running sign-in is
        cancelled as Cancel does."""
        self.shutting_down = True
        self._stop.set()
        self._wake.set()
        session = self._current_sign_in
        if session is not None and not session.is_finished and session.state != "completing":
            session.cancel()

    def finish_shutdown(self, timeout: float = 80.0) -> bool:
        """Waits for in-flight renewals and a running switch (bounded), then for the cycle thread.
        Returns whether everything finished in time."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.renewals_in_flight() == 0 and not self.gate.busy:
                break
            time.sleep(0.05)
        thread = self._cycle_thread
        if thread is not None:
            thread.join(max(0.0, deadline - time.monotonic()))
        session = self._current_sign_in
        if session is not None:
            session.join(max(0.0, min(8.0, deadline - time.monotonic())))
        return self.renewals_in_flight() == 0 and not self.gate.busy and (thread is None or not thread.is_alive())


def _same_credential(a: str, b: str) -> bool:
    """Whether two credential texts hold the same data (formatting is not a difference)."""
    if a == b:
        return True
    from .logins import loads_object

    left, right = loads_object(a), loads_object(b)
    return left is not None and left == right
