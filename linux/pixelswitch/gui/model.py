"""What the two faces (the tray and the settings window) show, as plain data and text.

No GTK here: every sentence, number format and menu entry is built from the daemon's own replies
(``accounts.list``, ``status.get``, ``settings.get``), so all of it is unit-tested without a display
(``tests/test_gui_model.py``). The wording is the Mac app's: the account-row help, the limit menus,
"Updated N ago", the messages (they arrive from the daemon in ``usage.error``).

The faces never send ``usage.refresh`` by themselves (spec 5.1): only the user's Refresh or Retry does.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from ..usage_model import parse_iso8601

HYSTERESIS = 1.0
DEFAULT_THRESHOLD = 90.0

#: The Session and Weekly menus (AccountRowThresholds.choices): 10 to 40 in 10s, then 50 to 100 in 5s.
#: A spin button beside a custom value reaches every whole number from 1 to 100.
LIMIT_CHOICES: tuple[int, ...] = (10, 20, 30, 40) + tuple(range(50, 101, 5))
#: The Priority menu offers P1 to P9; the spin button reaches 1 to 99.
PRIORITY_MENU: tuple[int, ...] = tuple(range(1, 10))
PRIORITY_RANGE = (1, 99)
DEFAULT_RANGE = (50, 100)
DRAIN_HOURS_RANGE = (1, 72)
REFRESH_CHOICES: tuple[tuple[int, str], ...] = (
    (15, "15 seconds"), (30, "30 seconds"), (60, "1 minute"), (300, "5 minutes"), (600, "10 minutes"),
)
STRATEGIES: tuple[tuple[str, str], ...] = (
    ("mostRoom", "Most room left"),
    ("myOrder", "Priority (lowest number first)"),
    ("resetsSoonest", "Resets soonest (use quota before it expires)"),
)
STRATEGY_HELP = {
    "mostRoom": "Moves you to the account with the most room left, preferring one that still has Fable to spare.",
    "myOrder": "Moves you to the account with the lowest priority number (Settings → Accounts) that has room. It always starts from the lowest number.",
    "resetsSoonest": "Moves you to the account whose weekly quota resets soonest, so quota about to expire is used before it is lost.",
}

#: The sentences the spec fixes (D3, D6, D9, D10, spec 2.4).
TEXT_PRIORITY_ORDER = "Lower numbers are tried first."
TEXT_MANUAL_ONLY_NOTE = "Manual only accounts are never switched to automatically; you can still switch to them yourself."
TEXT_PRIORITY_NOTE = "Auto-switch follows these numbers only when it chooses by Priority."
TEXT_OVERFLOW = "When no account under its weekly limit can take you, the session limit decides."
TEXT_FABLE = "Fable uses the weekly limit."
TEXT_NOWHERE_TO_GO = "Every account except the one you're using is Manual only, so auto-switch has nowhere to move you."
TEXT_PRIORITY_HELP = "Auto-switch tries accounts with lower numbers first when it chooses by Priority. Accounts with no number come last."
TEXT_RESIGN_MESSAGE = "Session expired. Re-authenticate (↻) to fix."
UPDATE_STALE_SECONDS = 90.0

#: The usage-service sentences a Retry makes sense for (UsageRequestError.canRetry): the wire does not
#: carry the flag, so the face recognises the Mac's own wording, which the daemon sends unchanged.
RETRYABLE_PREFIXES = (
    "Anthropic's usage service isn't responding",
    "Can't reach Anthropic",
    "Unexpected reply from Anthropic",
    "Anthropic sent usage data PixelSwitch couldn't read",
    "Could not save the refreshed sign-in",
    "Could not fetch usage",  # the daemon's own sentence for an unexpected failure (usagecycle: canRetry)
)


# --- numbers and times --------------------------------------------------------------------------------


def round_half_away(value: float) -> int:
    import math

    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def percent_text(value: float | None) -> str:
    return "—" if value is None else f"{round_half_away(value)}%"


def local_time(epoch: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return datetime.fromtimestamp(epoch).strftime(fmt)


def reset_words(iso: str | None, now: float | None = None) -> str:
    """"resets in 3 h 12 min" within a day, else "resets Mon 11:00" (local time); "" when unknown or past."""
    moment = parse_iso8601(iso) if isinstance(iso, str) else None
    if moment is None:
        return ""
    now = time.time() if now is None else now
    delta = moment - now
    if delta <= 0:
        return ""
    minutes = int(delta // 60)
    if minutes < 1:
        return "resets in under a minute"
    if minutes < 60:
        return f"resets in {minutes} min"
    if delta < 24 * 3600:
        hours, rest = divmod(minutes, 60)
        return f"resets in {hours} h {rest} min" if rest else f"resets in {hours} h"
    return "resets " + local_time(moment, "%a %H:%M")


def age_words(seconds: float) -> str:
    """"5 minutes", "2 hours", "3 days": how long ago, for "Updated N ago"."""
    seconds = max(0.0, seconds)
    if seconds < 3600:
        n = max(1, int(seconds // 60))
        return f"{n} minute" + ("" if n == 1 else "s")
    if seconds < 48 * 3600:
        n = int(seconds // 3600)
        return f"{n} hour" + ("" if n == 1 else "s")
    n = int(seconds // 86400)
    return f"{n} days"


def updated_ago(sampled_at: str | None, now: float | None = None) -> str | None:
    """"Updated 5 minutes ago" once a reading is 90 seconds old or more (a fresh one says nothing)."""
    taken = parse_iso8601(sampled_at) if isinstance(sampled_at, str) else None
    if taken is None:
        return None
    age = (time.time() if now is None else now) - taken
    return f"Updated {age_words(age)} ago" if age >= UPDATE_STALE_SECONDS else None


# --- one account (an AccountInfo dict from accounts.list) --------------------------------------------


def window_of(info: Mapping, name: str) -> tuple[float | None, str | None]:
    """``(utilization, resetsAt)`` of ``session``, ``weekly`` or ``fable``."""
    window = (info.get("usage") or {}).get(name) or {}
    return window.get("utilization"), window.get("resetsAt")


def account_title(info: Mapping) -> str:
    """A custom label, else the address."""
    label = info.get("label")
    return label if label else info["email"]


def account_subtitle(info: Mapping) -> str | None:
    """The address, shown under a label."""
    return info["email"] if info.get("label") else None


def priority_text(info: Mapping) -> str | None:
    priority = info.get("priority")
    return f"P{priority}" if isinstance(priority, int) and not isinstance(priority, bool) else None


def message_of(info: Mapping) -> str | None:
    """The account's message: the daemon's ``usage.error`` text, else a plain sentence for an account
    that cannot be switched to until it is signed in again."""
    error = (info.get("usage") or {}).get("error")
    if error:
        return error
    if not info.get("isSwitchable", True):
        return TEXT_RESIGN_MESSAGE
    return None


def needs_resign(info: Mapping) -> bool:
    """Only signing in again helps: the account is not switchable, or its message says so."""
    if not info.get("isSwitchable", True):
        return True
    message = (info.get("usage") or {}).get("error") or ""
    return "Re-authenticate" in message or message.startswith("Session expired")


def can_retry(info: Mapping) -> bool:
    message = (info.get("usage") or {}).get("error") or ""
    return any(message.startswith(prefix) for prefix in RETRYABLE_PREFIXES)


def short_message(text: str, limit: int = 60) -> str:
    """The first sentence, cut to ``limit`` characters with an ellipsis (the tray menu's line under an account)."""
    first = text.strip().split(". ")[0].strip()
    if not first.endswith(".") and first != text.strip():
        first += "."
    if len(first) <= limit:
        return first
    return first[: limit - 1].rstrip() + "…"


def tray_account_text(info: Mapping) -> str:
    """``P1 address · S 42% · W 61%`` (the priority is left out when there is none)."""
    session, _ = window_of(info, "session")
    weekly, _ = window_of(info, "weekly")
    parts = [f"{account_title(info)}", f"S {percent_text(session)}", f"W {percent_text(weekly)}"]
    prefix = priority_text(info)
    text = " · ".join(parts)
    return f"{prefix} {text}" if prefix else text


def tray_label(accounts: Sequence[Mapping]) -> str:
    """The text beside the tray icon: the active account's session percentage ("" when unknown)."""
    for info in accounts:
        if info.get("isActive"):
            session, _ = window_of(info, "session")
            return "" if session is None else percent_text(session)
    return ""


def tray_tooltip(accounts: Sequence[Mapping]) -> str:
    for info in accounts:
        if info.get("isActive"):
            session, _ = window_of(info, "session")
            weekly, _ = window_of(info, "weekly")
            return f"PixelSwitch: {account_title(info)}, session {percent_text(session)}, weekly {percent_text(weekly)}"
    return "PixelSwitch: no active account"


# --- the limits a row shows (SettingsAccountRow, SettingsAccountsTab) -------------------------------------


def own_limit(info: Mapping, window: str) -> float | None:
    """The account's own limit for ``session`` or ``weekly``: its window value, else its both-windows
    value; None while it follows its default and for a Manual only account (the menu then reads Manual only)."""
    if info.get("manualOnly"):
        return None
    value = info.get("sessionThreshold" if window == "session" else "weeklyThreshold")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    both = info.get("threshold")
    if isinstance(both, (int, float)) and not isinstance(both, bool) and both > 0:
        return float(both)
    return None


def defaults_of(status: Mapping | None) -> tuple[float, float]:
    """The two global defaults auto-switch reads: ``(session, weekly)``."""
    auto = (status or {}).get("autoSwitch") or {}
    base = auto.get("defaultThreshold", DEFAULT_THRESHOLD)
    return float(auto.get("defaultSessionThreshold", base)), float(auto.get("defaultWeeklyThreshold", base))


def limit_menu_label(info: Mapping, window: str, defaults: tuple[float, float]) -> str:
    """"Manual only", "Switch at 70%" or "Default (90%)" (the label the menu shows)."""
    if info.get("manualOnly"):
        return "Manual only"
    own = own_limit(info, window)
    if own is not None:
        return f"Switch at {int(round_half_away(own))}%"
    return f"Default ({int(round_half_away(defaults[0 if window == 'session' else 1]))}%)"


def _leave_value(info: Mapping, window: str, defaults: tuple[float, float]) -> float:
    """Where auto-switch moves off the account for ``session`` or ``weekly``: its own limit (as the daemon resolved
    it, when it says), else the snapshot's default for that window."""
    own = own_limit(info, window)
    index = 0 if window == "session" else 1
    if own is None:
        return defaults[index]
    resolved = info.get("effectiveSessionThreshold" if window == "session" else "effectiveWeeklyThreshold", info.get("effectiveThreshold"))
    return float(resolved) if isinstance(resolved, (int, float)) and not isinstance(resolved, bool) else own


def remove_body(info: Mapping, accounts: Sequence[Mapping]) -> str:
    """The Remove confirmation's text. Removing the account Claude Code is using now, while others remain, makes the
    daemon switch Claude Code to the next account by priority (D20): that changes the account every Claude Code on this
    computer uses (on the founder's laptop, the CabDesign farm's), so the dialog says so."""
    if info.get("isActive") and len(accounts) > 1:
        return ("This is the account Claude Code is using now. PixelSwitch will switch Claude Code to the next account by "
                "priority, which changes the account every Claude Code on this computer uses. Its saved login is deleted "
                "from PixelSwitch; signing it back in needs a person at a browser.")
    return ("This deletes the account's saved login from PixelSwitch. Signing it back in needs a person at a browser. "
            "Claude Code's own login is not touched.")


def dbusmenu_label(text: str) -> str:
    """A label as Dbusmenu shows it: an underscore marks an access key and is hidden, so a real one is doubled
    (``side_project`` would otherwise show as ``sideproject``)."""
    return text.replace("_", "__")


def limit_help(info: Mapping, defaults: tuple[float, float]) -> str:
    """What the account's setting does, in numbers (SettingsAccountsTab.thresholdHelp). With equal
    windows it is the one-limit sentence."""
    # An account that follows a default is judged by the defaults of THIS snapshot, not by an effective value that
    # may have been read a moment before the default changed (status.get and accounts.list are separate reads):
    # an account following split defaults (session 90, weekly 50) then still gets the two-window sentence.
    session_leave = _leave_value(info, "session", defaults)
    weekly_leave = _leave_value(info, "weekly", defaults)

    def pct(value: float) -> str:
        return f"{value:g}%"

    if info.get("manualOnly"):
        if defaults[0] == defaults[1]:
            return ("Auto-switch never moves you to this account. You can still switch to it yourself; while you're on it, "
                    f"auto-switch moves you off it at the default threshold ({pct(defaults[0])}).")
        return ("Auto-switch never moves you to this account. You can still switch to it yourself; while you're on it, "
                f"auto-switch moves you off it at the default thresholds ({pct(defaults[0])} of its session or {pct(defaults[1])} of its week).")
    arrive_session = max(0.0, session_leave - HYSTERESIS)
    arrive_weekly = max(0.0, weekly_leave - HYSTERESIS)
    relaxed_weekly = max(0.0, max(weekly_leave, session_leave) - HYSTERESIS)
    if session_leave == weekly_leave:
        return (f"Auto-switch moves you off this account at {pct(session_leave)}, and moves you to it only while it is at "
                f"{pct(arrive_session)} or less.")
    lead = (f"Auto-switch moves you off this account at {pct(session_leave)} of its session or {pct(weekly_leave)} of its week, "
            f"and moves you to it only while it is at {pct(arrive_session)} or less of its session and {pct(arrive_weekly)} or less of its week")
    if relaxed_weekly > arrive_weekly:
        return lead + f" (up to {pct(relaxed_weekly)} of its week when no account under its weekly limit can take you)."
    return lead + "."


def manual_note(info: Mapping, defaults: tuple[float, float]) -> str | None:
    """The line under a Manual only account that is in use (auto-switch on)."""
    if not (info.get("manualOnly") and info.get("isActive")):
        return None
    if defaults[0] == defaults[1]:
        return f"You're using it now. Auto-switch moves you off it at {defaults[0]:g}% and won't move you back to it."
    return (f"You're using it now. Auto-switch moves you off it at {defaults[0]:g}% of its session or {defaults[1]:g}% of its week "
            "and won't move you back to it.")


def nowhere_to_go(accounts: Sequence[Mapping], auto_switch_on: bool) -> bool:
    """Auto-switch on, an account active, and every other account Manual only."""
    if not auto_switch_on or len(accounts) < 2 or not any(a.get("isActive") for a in accounts):
        return False
    return all(a.get("manualOnly") for a in accounts if not a.get("isActive"))


def priority_note_visible(strategy: str, accounts: Sequence[Mapping]) -> bool:
    """The D6 line: numbers exist (or can) but auto-switch does not choose by Priority."""
    return strategy != "myOrder" and bool(accounts)


def reset_note(info: Mapping, now: float | None = None) -> str:
    """"Weekly resets Mon 11:00" and the like, or that it is not known yet."""
    _, resets = window_of(info, "weekly")
    words = reset_words(resets, now)
    return f"Weekly {words}" if words else "Weekly reset not known yet"


def usage_tone(percent: float | None) -> str:
    """For the colour of a usage bar: ``ok``, ``warn`` or ``high``."""
    if percent is None or percent < 75:
        return "ok"
    return "warn" if percent < 90 else "high"


# --- the daemon's state as the faces keep it -----------------------------------------------------------


@dataclass
class Snapshot:
    """Everything the faces draw from. ``connected`` is False while the daemon is not answering."""

    connected: bool = False
    error: str | None = None
    status: dict | None = None
    accounts: list[dict] = field(default_factory=list)
    settings: dict = field(default_factory=dict)
    sign_in: dict | None = None
    #: Epoch seconds when this snapshot was read.
    at: float = field(default_factory=time.time)

    @property
    def active(self) -> dict | None:
        return next((a for a in self.accounts if a.get("isActive")), None)

    @property
    def auto_switch(self) -> dict:
        return (self.status or {}).get("autoSwitch") or {}

    @property
    def auto_switch_on(self) -> bool:
        return bool(self.auto_switch.get("enabled"))

    @property
    def strategy(self) -> str:
        return self.auto_switch.get("strategy", "mostRoom")

    @property
    def defaults(self) -> tuple[float, float]:
        return defaults_of(self.status)

    @property
    def app_version(self) -> str | None:
        return (self.status or {}).get("appVersion")


def snapshot_from_replies(status: Any, accounts: Any, settings: Any = None) -> Snapshot:
    """A connected snapshot from the three replies. Anything malformed is left empty, never raised."""
    snap = Snapshot(connected=True)
    if isinstance(status, dict):
        snap.status = status
        sign_in = status.get("signIn")
        snap.sign_in = sign_in if isinstance(sign_in, dict) else None
    if isinstance(accounts, dict) and isinstance(accounts.get("accounts"), list):
        snap.accounts = [a for a in accounts["accounts"] if isinstance(a, dict) and "email" in a]
    if isinstance(settings, dict) and isinstance(settings.get("settings"), dict):
        snap.settings = dict(settings["settings"])
    return snap


# --- the tray menu as data (rendered by the StatusNotifierItem or AppIndicator backend) ----------------------


@dataclass(frozen=True)
class MenuEntry:
    """One line of the tray menu. ``action`` is what a click does (``switch:<id>``, ``auto-switch``,
    ``refresh``, ``settings``, ``sign-in``, ``quit``, ``start-service``); ``kind`` is ``item``, ``check``
    or ``separator``."""

    kind: str = "item"
    label: str = ""
    action: str | None = None
    enabled: bool = True
    checked: bool = False


SEPARATOR = MenuEntry(kind="separator")


def build_tray_menu(snapshot: Snapshot) -> list[MenuEntry]:
    """The menu of spec 5.1: each account in priority order (``P1 address · S 42% · W 61%``, the active one
    checked, a short form of its message under it), click to switch; Auto-switch on/off; Refresh; Settings;
    Sign in another account; Quit tray."""
    entries: list[MenuEntry] = []
    if not snapshot.connected:
        entries.append(MenuEntry(label="PixelSwitch is not running", enabled=False))
        entries.append(MenuEntry(label="Start PixelSwitch", action="start-service"))
        entries.append(SEPARATOR)
        entries.append(MenuEntry(label="Quit tray", action="quit"))
        return entries
    if snapshot.accounts:
        for info in snapshot.accounts:
            active = bool(info.get("isActive"))
            switchable = bool(info.get("isSwitchable", True))
            entries.append(MenuEntry(
                kind="check", label=tray_account_text(info), checked=active,
                action=None if active or not switchable else f"switch:{info['id']}",
                enabled=active or switchable,
            ))
            message = message_of(info)
            if message:
                entries.append(MenuEntry(label="    " + short_message(message), enabled=False))
    else:
        entries.append(MenuEntry(label="No accounts yet", enabled=False))
    entries.append(SEPARATOR)
    entries.append(MenuEntry(kind="check", label="Auto-switch", action="auto-switch", checked=snapshot.auto_switch_on,
                             enabled=bool(snapshot.status)))
    entries.append(MenuEntry(label="Refresh", action="refresh"))
    entries.append(SEPARATOR)
    entries.append(MenuEntry(label="Settings…", action="settings"))
    entries.append(MenuEntry(label="Sign in another account…", action="sign-in"))
    entries.append(SEPARATOR)
    entries.append(MenuEntry(label="Quit tray", action="quit"))
    return entries
