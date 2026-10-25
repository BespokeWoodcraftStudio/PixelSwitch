"""Usage reading, the polling cycle and the auto-switch cycle: the usage half of ``AppState``.

Ports ``AppState.swift`` lines 1301-1740 and 1901-1983 (recon ``usage.md`` sections 3-5 and
``autoswitch.md``), written as a mixin of :class:`pixelswitch.coordinator.Coordinator` so the two
halves of ``AppState`` stay readable. Everything here goes through the usage guard exactly as the
Mac does: every request that carries an access token is gated, a token that got a 401 is never
sent again until it changes, an expired token is never sent, a saved login is renewed before it
is asked, the live login is never refreshed by PixelSwitch.

The cycle (``refresh``): audit saved logins, follow the live login's proven owner, then read the
usage of the active account plus ONE other account (round-robin, the account whose credential just
changed first), 1.5 s apart, and then evaluate auto-switch. The interval is the ``refreshInterval``
setting (default 300 s); a change restarts the timer and does not refresh at once; the cycle never
runs faster than the Mac's: the next one is due one interval after the previous one ENDED.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import engine
from .accounts import Account
from .credtools import gate_is_switchable, is_usable_login
from .logins import Login, login_from_credential
from .protocol import ControlError
from .usage_guard import (
    NoticeCurrent, UsageDecision, UsageRequestError, choose_notice, fingerprint_for_log, fingerprint_of, paused_message,
    parked,
)
from .usage_model import UsageResponse

COOLDOWN = engine.COOLDOWN_SECONDS
SPACING_SECONDS = 1.5

MESSAGE_WAITING = "Waiting for Claude Code to renew this login. It renews the next time a session runs."
MESSAGE_RESIGN = "Session expired. Re-authenticate (↻) to fix."
MESSAGE_BELONGED = "Its saved login belonged to another account and was removed. Re-authenticate (↻) to fix."
MESSAGE_STORE_RETRY = "Could not save the refreshed sign-in; will retry automatically."
MESSAGE_NO_SUBSCRIPTION = "No active subscription on this account (OAuth not allowed)."


@dataclass
class CardError:
    """``AppState.UsageErrorState``: what an account's card says instead of (or beside) a reading."""

    is_expired: bool
    is_rate_limited: bool
    message: str
    #: The usage request was refused with 403: auto-switch leaves such an active account and never moves to it.
    is_no_subscription: bool = False
    can_retry: bool = False
    offers_status_page: bool = False


@dataclass(frozen=True)
class AutoSwitchRecord:
    """One automatic switch that completed (AppState.swift:2146)."""

    from_id: str
    to_id: str
    limit: str
    trigger: str
    window: str | None
    relaxed: bool
    at: float


def _percent(value: float | None) -> str:
    return "no reading" if value is None else f"{value:.0f}%"


def limits_text(pair: engine.LeaveAt, prefix: str = "its") -> str:
    """"its threshold 98%" when both windows leave at the same number (the 1.9 wording, kept for
    log readers), else "its limits 90% session, 50% weekly"."""
    if pair.session == pair.weekly:
        return f"{prefix} threshold {pair.session:.0f}%"
    return f"{prefix} limits {pair.session:.0f}% session, {pair.weekly:.0f}% weekly"


class UsageMixin:
    # The host class (Coordinator) provides: log, clock, sleep, guard, service, backups, login,
    # gate, account_by_id, accounts, active_account, settings accessors and the methods named below.

    # -- state used by the cycle (initialised by Coordinator.__init__) -------------------------------

    def _init_usage_state(self) -> None:
        self.usage: dict[str, UsageResponse] = {}
        self.usage_sampled_at: dict[str, float] = {}
        self.card_errors: dict[str, CardError] = {}
        self.read_soon: set[str] = set()
        self.fetch_cursor = 0
        self.last_cycle_start = 0.0
        self.last_usage_refresh: float | None = None
        self.last_auto_switch_at: float | None = None
        self.last_staying_put_note: str | None = None
        self.last_auto_switch: AutoSwitchRecord | None = None
        self._is_evaluating = False
        self._refresh_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._interval_generation = 0
        self._cycle_thread: threading.Thread | None = None
        self.cycles_completed = 0
        self.on_cycle_complete: Callable[[], None] | None = None

    # -- small helpers --------------------------------------------------------------------------------

    def _pause(self, seconds: float) -> bool:
        """Sleeps up to ``seconds``; False if the daemon is stopping (so a stop never waits 1.5 s)."""
        if self.sleep is not time.sleep:  # a test's fake sleeper
            self.sleep(seconds)
            return not self._stop.is_set()
        return not self._stop.wait(seconds)

    def _usage_login(self, account: Account) -> Login | None:
        """The login a usage request for this account would carry: the live one for the active
        account, the saved one for every other. Looked up by id, so a stale copy still reads the right one."""
        stored = self.account_by_id(account.id)
        is_active = stored.is_active if stored is not None else account.is_active
        if is_active:
            credential = self.login.read_credential()
        else:
            backup = self.backups.get(account.id)
            credential = backup.token if backup is not None else None
        return login_from_credential(credential) if credential else None

    def _is_active_now(self, account: Account) -> bool:
        stored = self.account_by_id(account.id)
        return stored.is_active if stored is not None else account.is_active

    def _current_notice(self, account: Account) -> NoticeCurrent:
        error = self.card_errors.get(account.id)
        if error is None:
            return NoticeCurrent.NOTHING
        if error.is_no_subscription:
            return NoticeCurrent.NO_SUBSCRIPTION
        if error.is_expired:
            return NoticeCurrent.RESIGN
        return NoticeCurrent.TRANSIENT

    def _has_no_subscription(self, account: Account) -> bool:
        error = self.card_errors.get(account.id)
        return error is not None and error.is_no_subscription

    def is_switchable(self, account: Account, backups: dict | None = None) -> bool:
        """The backup must exist and be a usable login (spec D12), the server must not have refused
        its refresh grant, and its card must not say it expired. ``backups`` lets a caller that
        builds many accounts read the store once."""
        if backups is None:
            backup = self.backups.get(account.id)
        else:
            backup = backups.get(account.id)
        card = self.card_errors.get(account.id)
        return gate_is_switchable(backup.token if backup is not None else None,
                                  self.guard.is_needs_reauth(account.id),
                                  card is not None and card.is_expired)

    def _show_notice(self, decision: UsageDecision, account: Account) -> None:
        notice = choose_notice(decision, self._current_notice(account))
        if notice.kind == "keepCurrent":
            return
        if notice.kind == "paused":
            assert notice.until is not None
            self.card_errors[account.id] = CardError(False, True, paused_message(notice.until))
        elif notice.kind == "waitingForClaudeCode":
            self.card_errors[account.id] = CardError(False, True, MESSAGE_WAITING)
        elif notice.kind == "resign":
            self._show_resign(account)

    def _show_resign(self, account: Account, message: str | None = None) -> None:
        """The account needs signing in again: no reading, the re-sign message."""
        self.usage.pop(account.id, None)
        self.usage_sampled_at.pop(account.id, None)
        self.card_errors[account.id] = CardError(True, False, message or MESSAGE_RESIGN)

    def _mark_no_subscription(self, account: Account) -> None:
        """A 403 on the usage request: the account has no active subscription. Clears its reading
        and flags it, so auto-switch leaves it while it is active and never moves you to it."""
        self.usage.pop(account.id, None)
        self.usage_sampled_at.pop(account.id, None)
        self.card_errors[account.id] = CardError(False, False, MESSAGE_NO_SUBSCRIPTION, is_no_subscription=True)

    def _record_usage_failure(self, error: BaseException, account: Account) -> None:
        """A usage request that failed for a reason with no path of its own: say what happened in
        plain words, and keep the last reading when the failure says nothing about the account."""
        failure = error if isinstance(error, UsageRequestError) else None
        if failure is None or not failure.keeps_last_reading:
            self.usage.pop(account.id, None)
            self.usage_sampled_at.pop(account.id, None)
        message = failure.message(self.clock()) if failure is not None else \
            f"Could not fetch usage: {type(error).__name__}"
        self.card_errors[account.id] = CardError(
            False, False, message, can_retry=failure.can_retry if failure is not None else True,
            offers_status_page=failure.offers_status_page if failure is not None else False)

    # -- one reading, through the guard ----------------------------------------------------------------------

    def read_usage(self, account: Account) -> UsageResponse | None:
        """One usage reading for ``account``. Asks the guard first; on "send nothing" the card is
        told why and None comes back. Every 401 is remembered by token, so the same token is never
        sent twice."""
        login = self._usage_login(account)
        if login is None:
            self.log.warning(f"[fetchUsage] No token for {account.id}, skipping")
            return None
        decision = self.guard.decision(account.id, self._is_active_now(account), login, self.clock())
        if decision.kind == "request":
            return self._request_usage(account, login, after_renewal=False)
        if decision.kind == "refreshFirst":
            self.log.info(f"[fetchUsage] {account.id}: saved login (token "
                          f"{fingerprint_for_log(fingerprint_of(login.access_token))}) is expired or was refused; "
                          "renewing it before asking")
            return self._renew_then_request(account)
        self.log.info(f"[fetchUsage] {account.id}: nothing sent ({decision.kind})")
        self._show_notice(decision, account)
        return None

    def _request_usage(self, account: Account, login: Login, after_renewal: bool) -> UsageResponse | None:
        """The one place a usage request is made."""
        try:
            return self.service.get_usage_limits(login.access_token)
        except UsageRequestError as error:
            if error.kind == "expired":
                self.guard.record_rejected(account.id, login.access_token, self.clock())
                self.log.warning(f"[fetchUsage] 401 for {account.id} (token "
                                 f"{fingerprint_for_log(fingerprint_of(login.access_token))}); "
                                 "it will not be sent again until it changes")
                if after_renewal:
                    self._record_usage_failure(error, account)  # a token minted a moment ago was refused
                    return None
                if self._is_active_now(account):
                    # Claude Code owns the live login and renews it itself; say so and keep the last reading.
                    self._show_notice(UsageDecision("waitForClaudeCode"), account)
                    return None
                return self._renew_then_request(account)
            if error.kind == "rateLimited":
                until = self.guard.record_rate_limited(account.id, login.access_token, error.retry_after, self.clock())
                self.log.warning(f"[fetchUsage] {account.id} rate-limited; parked for {until - self.clock():.0f}s")
                self._show_notice(parked(until), account)
                return None
            if error.kind == "forbidden":
                self.log.warning(f"[fetchUsage] {account.id} forbidden (no active subscription?)")
                self._mark_no_subscription(account)
                return None
            self.log.warning(f"[fetchUsage] {account.id}: {error.kind}")
            self._record_usage_failure(error, account)
            return None
        except Exception as error:  # nothing may take the cycle down
            self.log.warning(f"[fetchUsage] {account.id}: {type(error).__name__}")
            self._record_usage_failure(error, account)
            return None

    def _renew_then_request(self, account: Account) -> UsageResponse | None:
        """Renews a saved login in place, then asks once with the new token. Every outcome is
        handled: collapsing a dead grant (or a lost rotation) to a plain None would leave the
        account looking healthy."""
        outcome = self.refresh_backup_in_place(account)
        kind = outcome.kind
        if kind == "refreshed":
            self.log.info(f"[fetchUsage] Silently refreshed backup for {account.id}; asking with the new token")
            new_login = login_from_credential(outcome.detail)
            return self._request_usage(account, new_login, after_renewal=True) if new_login else None
        if kind in ("grantRejected", "rotationLost", "belongsToAnotherAccount"):
            return None  # refresh_backup_in_place flagged the account and put the re-sign message on its card
        if kind in ("sameGrantAsAnotherLogin", "noBackup"):
            self._show_resign(account)  # not the server's verdict, so the account is NOT flagged
            return None
        if kind == "unreachable":
            self._record_usage_failure(UsageRequestError("network", offline=False), account)
            return None
        self.card_errors[account.id] = CardError(False, False, MESSAGE_STORE_RETRY)  # storeUnavailable
        return None

    # -- the polling cycle ---------------------------------------------------------------------------------------

    def fetch_all_account_usage(self) -> None:
        # Ask the guard about every account first. An account it says to leave alone (a dead grant,
        # a 429 park, a cool-down, the live login waiting for Claude Code) is never a target, and
        # its card says why.
        pollable: list[Account] = []
        backups = self.backups.all() or {}
        for account in self.accounts:
            # Spec D12: a saved login that is not a usable login is never asked about; its card
            # says to sign in again and it is not switchable.
            backup = backups.get(account.id)
            if not account.is_active and backup is not None and not is_usable_login(backup.token):
                if self.guard.is_needs_reauth(account.id):
                    self._show_notice(UsageDecision("needsReauth"), account)
                else:
                    self.flag_needs_reauth(account)
                continue
            login = self._usage_login(account)
            if login is None:
                if self.guard.is_needs_reauth(account.id):
                    self._show_notice(UsageDecision("needsReauth"), account)
                else:
                    pollable.append(account)  # read_usage logs "No token" and skips it
                continue
            decision = self.guard.decision(account.id, account.is_active, login, self.clock())
            if decision.kind in ("request", "refreshFirst"):
                pollable.append(account)
            else:
                self._show_notice(decision, account)
        # `decision` clears a flag that had no token when the account turned out to be the active
        # one; keep the saved copy in step.
        self.persist_needs_reauth()

        # This cycle's targets: the active account (when it may be asked) plus one other in
        # round-robin order. An account whose credential just changed goes first. Stale samples
        # for the others are kept.
        others = [a for a in pollable if not a.is_active]
        targets = [a for a in pollable if a.is_active]
        if others:
            fixed = next((a for a in others if a.id in self.read_soon), None)
            if fixed is not None:
                targets.append(fixed)
            else:
                targets.append(others[self.fetch_cursor % len(others)])
                self.fetch_cursor += 1
        self.read_soon.difference_update(a.id for a in targets)
        for target in targets:
            self.card_errors.pop(target.id, None)

        first = True
        for account in targets:
            # Stagger requests: a back-to-back burst reliably gets all but one of them 429'd.
            if not first and not self._pause(SPACING_SECONDS):
                break
            first = False
            usage = self.read_usage(account)
            if usage is not None:
                self.usage[account.id] = usage
                self.usage_sampled_at[account.id] = self.clock()
                self.card_errors.pop(account.id, None)
                session = usage.five_hour.utilization if usage.five_hour and usage.five_hour.utilization is not None else -1
                weekly = usage.seven_day.utilization if usage.seven_day and usage.seven_day.utilization is not None else -1
                self.log.info(f"[fetchUsage] {account.id}: session={session}%, weekly={weekly}%")
        self.persist_needs_reauth()

    def refresh(self, wait_if_busy: bool = False) -> bool:
        """Refresh everything, then decide whether to auto-switch. Returns whether a full refresh ran.

        The two halves are separate: auto-switch is evaluated only when the refresh actually ran.
        When ``refresh_data`` did not run (a sign-in is in progress, another refresh is already
        running) auto-switch must not be evaluated either: it would act on state mid-mutation.
        ``wait_if_busy`` makes a caller that found a refresh running wait for that one to finish."""
        ran = self._refresh_data(wait_if_busy)
        if ran:
            self.evaluate_auto_switch()
        return ran

    def _refresh_data(self, wait_if_busy: bool) -> bool:
        if self.gate.kind == "signin":
            self.log.info("[refresh] Skipping: login in progress")
            return False
        if self._stop.is_set():
            return False
        if not self._refresh_lock.acquire(blocking=False):
            if wait_if_busy:
                with self._refresh_lock:
                    pass
            self.log.info("[refresh] Skipping: refresh already in progress")
            return False
        try:
            self.last_cycle_start = self.clock()
            self.audit_saved_logins()
            self.reconcile_live_login()
            self.fetch_all_account_usage()
            self.last_usage_refresh = self.clock()
        finally:
            self._refresh_lock.release()
        self.cycles_completed += 1
        self.emit("usageUpdated", {})
        if self.on_cycle_complete is not None:
            try:
                self.on_cycle_complete()
            except Exception as error:
                self.log.warning(f"[refresh] Cycle hook failed ({type(error).__name__})")
        return True

    # -- the schedule ----------------------------------------------------------------------------------------------

    def start_cycle_thread(self) -> None:
        """One refresh at once, then every ``refreshInterval`` seconds after the previous one ended."""
        if self._cycle_thread is not None:
            return
        self._cycle_thread = threading.Thread(target=self._cycle_loop, name="usage-cycle", daemon=True)
        self._cycle_thread.start()

    def _cycle_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as error:
                self.log.error(f"[refresh] Cycle failed unexpectedly ({type(error).__name__})")
            generation = self._interval_generation
            due = time.monotonic() + self.refresh_interval()
            self._wake.clear()
            while not self._stop.is_set():
                if self._wake.wait(timeout=min(1.0, max(0.05, due - time.monotonic()))):
                    break  # a refresh was asked for
                if generation != self._interval_generation:
                    generation = self._interval_generation
                    due = time.monotonic() + self.refresh_interval()  # a new interval restarts the timer
                if time.monotonic() >= due:
                    break

    def restart_timer(self) -> None:
        """Settings -> refresh interval changed: the timer restarts and does NOT refresh at once."""
        self._interval_generation += 1

    def request_refresh(self) -> None:
        self._wake.set()

    # -- the card's Retry --------------------------------------------------------------------------------------------

    def retry_usage(self, account: Account) -> None:
        """The card's Retry: one fresh reading for this account now. The active account takes a
        full refresh; any other takes one request, which a rate-limit park still holds back."""
        if self._is_active_now(account):
            self.refresh(wait_if_busy=True)
            return
        usage = self.read_usage(account)
        if usage is None:
            return
        self.usage[account.id] = usage
        self.usage_sampled_at[account.id] = self.clock()
        self.card_errors.pop(account.id, None)
        if not self._refresh_lock.locked():
            self.evaluate_auto_switch()

    # -- auto-switch -----------------------------------------------------------------------------------------------------

    def evaluate_auto_switch(self) -> None:
        """Evaluate whether auto-switch should move the user, and if so where. Called after every
        completed refresh. Safe to call repeatedly.

        Candidates are ranked from whatever samples we hold, then the chosen one is VERIFIED before
        committing, with the same rule that ranked it, in the same mode (strict or relaxed): round-robin
        polling can leave a candidate's sample several cycles old. A sample taken by this very cycle
        counts as verified; otherwise one fresh reading is taken -- a single request per (rare,
        rule-gated, cooldown-gated) switch attempt, not the per-cycle burst the round-robin exists to prevent."""
        config = self.auto_switch_config()
        if not config.enabled or self._is_evaluating or self._stop.is_set():
            return
        active = self.active_account()
        if active is None or self.gate.busy:
            return
        now = self.clock()
        active_unusable = self._has_no_subscription(active)
        # Cooldown: never auto-switch more than once per window, except off an account with no
        # subscription, which nothing can move you back to.
        if engine.cooldown_holds(self.last_auto_switch_at, COOLDOWN, active_unusable, now):
            return

        candidates = [a for a in self.accounts if a.provider == active.provider and a.id != active.id]
        sampled_at = self.usage_sampled_at.get(active.id, 0.0)
        active_sampled_this_cycle = sampled_at >= self.last_cycle_start
        defaults = config.defaults
        drain_within = config.drain_within_hours * 3600
        backups = self.backups.all() or {}
        plan = engine.plan(
            active=active,
            candidates=candidates,
            usage_by_account=dict(self.usage),
            is_switchable=lambda account: self.is_switchable(account, backups),
            is_unusable=self._has_no_subscription,
            active_sampled_this_cycle=active_sampled_this_cycle,
            rule_of=lambda account: self.rule_for(account, defaults),
            defaults=defaults,
            hysteresis=engine.HYSTERESIS,
            watch_fable=config.on_fable,
            strategy=config.strategy,
            drain_early=config.drain_early,
            drain_within=drain_within,
            now=now,
        )
        if plan is None:
            if active_unusable:
                self.log.warning(f"[autoSwitch] Active {active.id} has no active subscription, and no other account "
                                 "has room; staying put")
            else:
                self._note_staying_put(active, active_sampled_this_cycle, config, defaults, now)
            return
        self.last_staying_put_note = None
        active_weekly_reset = engine.weekly_reset(self.usage.get(active.id), engine.Limit.WINDOWS, now)

        self._is_evaluating = True
        try:
            self._act_on_plan(active, plan, config, defaults, active_sampled_this_cycle, active_weekly_reset, drain_within)
        finally:
            self._is_evaluating = False

    def _act_on_plan(self, active: Account, plan: engine.Plan, config, defaults: engine.Defaults,
                     active_sampled_this_cycle: bool, active_weekly_reset: float | None, drain_within: float) -> None:
        limit, trigger, relaxed, ranked = plan.limit, plan.trigger, plan.relaxed, plan.targets
        now = self.clock()
        active_rule = self.rule_for(active, defaults)
        pair_rule = engine.relaxed_rule(active_rule) if relaxed else active_rule
        pair_defaults = engine.relaxed_defaults(defaults) if relaxed else defaults
        leave = engine.leave_at(pair_rule, pair_defaults)
        manual_note = ", manual only: left at the default" if active_rule.manual_only else ""
        relaxed_note = "; no account under its weekly limit can take you, so the session limit decides" if relaxed else ""
        strategy = config.strategy.value
        if trigger == engine.Trigger.NO_SUBSCRIPTION:
            self.log.info(f"[autoSwitch] Rule {trigger.value}, strategy {strategy}: active {active.id} has no active "
                          f"subscription; {len(ranked)} candidate(s){relaxed_note}")
        else:
            usage = self.usage.get(active.id)
            strict = not active_sampled_this_cycle
            if plan.window == "session":
                active_util, on_what = engine.session_reading(usage, now, strict), f"{limit.value} (session window)"
            elif plan.window == "weekly":
                active_util, on_what = engine.weekly_reading(usage, now, strict), f"{limit.value} (weekly window)"
            elif limit == engine.Limit.FABLE:
                active_util, on_what = engine.fable_reading(usage, now, strict), limit.value
            else:
                active_util, on_what = engine.binding_utilization(usage, now, strict), limit.value
            shown = -1 if active_util is None else active_util
            self.log.info(f"[autoSwitch] Rule {trigger.value}, strategy {strategy}: active {active.id} at {shown:.0f}% "
                          f"on {on_what} ({limits_text(leave)}{manual_note}); {len(ranked)} candidate(s){relaxed_note}")

        # At most ONE fresh verification request per evaluation. Later ranked candidates only
        # qualify via samples this cycle already took.
        fresh_budget = 1
        for target in ranked:
            sampled = self.usage_sampled_at.get(target.id)
            if sampled is not None and sampled >= self.last_cycle_start:
                usage = self.usage.get(target.id)  # sampled by this very cycle: that IS a fresh reading
            elif fresh_budget > 0:
                fresh_budget -= 1
                usage = self.read_usage(target)
                if usage is not None:
                    self.usage[target.id] = usage
                    self.usage_sampled_at[target.id] = self.clock()
                    self.card_errors.pop(target.id, None)
            else:
                continue

            # The same rule, with the same per-account limits and in the same mode (strict or
            # relaxed), that ranked it.
            target_rule = self.rule_for(target, defaults)
            verified = engine.eligible_utilization(
                usage, limit, trigger, target_rule, hysteresis=engine.HYSTERESIS,
                active_weekly_reset=active_weekly_reset, drain_within=drain_within, watch_fable=config.on_fable,
                now=self.clock(), relaxed=relaxed)
            if verified is None:
                if target_rule.manual_only:
                    self.log.info(f"[autoSwitch] Candidate {target.id} is manual only now; trying next")
                    continue
                on_limit = _percent(self._limit_value(usage, limit))
                on_windows = _percent(engine.binding_utilization(usage, self.clock()))
                target_leave = engine.leave_at(engine.relaxed_rule(target_rule) if relaxed else target_rule, defaults)
                overflow = " in the weekly overflow pass" if relaxed else ""
                self.log.info(f"[autoSwitch] Candidate {target.id} failed verification for {trigger.value} "
                              f"({limit.value} {on_limit}, windows {on_windows}, {limits_text(target_leave, 'its')})"
                              f"{overflow}; trying next")
                continue

            # Re-check volatile state: the verification above can span a sign-in starting, a
            # refresh beginning, or a manual switch the user just made.
            current = self.active_account()
            if self.gate.busy or self._refresh_lock.locked() or current is None or current.id != active.id:
                self.log.info("[autoSwitch] State changed during verification; standing down")
                return

            value = verified.fable if limit == engine.Limit.FABLE else _max_of(verified.session, verified.weekly)
            self.log.info(f"[autoSwitch] Switching to {target.id} by rule {trigger.value} on {limit.value}, verified "
                          f"at {(-1 if value is None else value):.0f}%{relaxed_note}")
            self.last_auto_switch_at = self.clock()
            self.switch_to(target)
            now_active = self.active_account()
            if now_active is not None and now_active.id == target.id:
                self.last_auto_switch = AutoSwitchRecord(active.id, target.id, limit.value, trigger.value, plan.window,
                                                         relaxed, self.clock())
                self.emit_auto_switched(self.last_auto_switch)
            else:
                self.log.warning(f"[autoSwitch] Switch to {target.id} did not complete")
            return
        if trigger == engine.Trigger.THRESHOLD:
            self.log.info("[autoSwitch] Threshold reached but no candidate verified; staying put")
        elif trigger == engine.Trigger.DRAIN_EARLY:
            self.log.info("[autoSwitch] Early drain found no verified candidate; staying put")
        else:
            self.log.info("[autoSwitch] No subscription on the active account, and no candidate verified; staying put")

    def _limit_value(self, usage: UsageResponse | None, limit: engine.Limit) -> float | None:
        now = self.clock()
        return engine.fable_reading(usage, now) if limit == engine.Limit.FABLE else engine.binding_utilization(usage, now)

    def _note_staying_put(self, active: Account, sampled_this_cycle: bool, config, defaults: engine.Defaults,
                          now: float) -> None:
        """Log, once per change, that the active account has reached a limit but no other account
        has room. Names the window. When the weekly overflow holds and even the relaxed plan found
        nobody (spec 2.3 rule 10), says the account stays until the session limit."""
        rule = self.rule_for(active, defaults)
        leave = engine.leave_at(rule, defaults)
        usage = self.usage.get(active.id)
        strict = not sampled_this_cycle
        reached: list[str] = []
        session = engine.session_reading(usage, now, strict)
        if session is not None and session >= leave.session:
            reached.append(f"{session:.0f}% on the session window")
        weekly = engine.weekly_reading(usage, now, strict)
        if weekly is not None and weekly >= leave.weekly:
            reached.append(f"{weekly:.0f}% on the weekly window")
        if config.on_fable:
            fable = engine.fable_reading(usage, now, strict)
            if fable is not None and fable >= leave.weekly:
                reached.append(f"{fable:.0f}% on fable")
        if not reached:
            self.last_staying_put_note = None
            return
        overflow = engine.overflow_holds(
            active=active, usage_by_account=dict(self.usage), is_unusable=self._has_no_subscription,
            active_sampled_this_cycle=sampled_this_cycle, rule_of=lambda a: self.rule_for(a, defaults),
            defaults=defaults, now=now)
        if overflow:
            note = (f"[autoSwitch] Active {active.id} at {', '.join(reached)} ({limits_text(leave)}), but no account "
                    f"under its weekly limit can take you; staying until the session limit ({leave.session:.0f}%)")
        else:
            note = (f"[autoSwitch] Active {active.id} at {', '.join(reached)} ({limits_text(leave)}), but no other "
                    "account has room under its own limits; staying put")
        if note != self.last_staying_put_note:
            self.log.info(note)
            self.last_staying_put_note = note

    def switch_to(self, account: Account) -> None:
        """The auto-switch path's switch: failures set the error message (and the ``error`` event);
        a busy switch or a missing active account stay quiet, as on the Mac."""
        try:
            self.perform_switch(account)
        except ControlError as error:
            if getattr(error, "silent", False):
                return
            if error.kind in ("failed", "claudeUnavailable"):
                self.set_error(error.message)


def _max_of(*values: float | None) -> float | None:
    present = [v for v in values if v is not None]
    return max(present) if present else None
