"""``pixelswitch``, the command-line tool: the Linux port of PixelSwitchCLI/CLIParser.swift and
CLIRunner.swift with the 2.0 additions (separate window thresholds, priority numbers).

Same commands, flags, output strings, ``--json`` shapes, exit codes and timeouts as the Mac tool
(recon control.md 3.1 and 3.2), over lane LA's :class:`~pixelswitch.client.ControlClient`. The
differences are the ones the spec names (D15 and section 5):

* no "This Mac today" line in ``usage`` (the daemon sends no machine usage);
* the daemon's optional ``warnings`` list in ``status`` is printed one line each;
* ``update-check`` and the sign-in links say "this computer" where the Mac tool says "the Mac";
* ``pixelswitch --version`` prints the app version.

Standard library only. Output goes through ``Runner.out`` / ``Runner.err`` (unbuffered, so
``watch`` over ssh is not held back in a buffer), and every pure formatter is a static method of
:class:`Output` so the formats are unit-tested.

Exit codes: 0 ok, 1 failed, 2 usage, 3 busy, 4 no such account, 5 PixelSwitch unreachable.
"""
from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from . import protocol
from .client import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNREACHABLE,
    EXIT_USAGE,
    ClientError,
    ControlClient,
    check_protocol,
    exit_code_for,
    has_feature,
    require_feature,
)
from .protocol import ControlError
from .usage_model import parse_iso8601

DEFAULT_TIMEOUT = 30.0
FEATURE_PRIORITY = "priority"
FEATURE_SPLIT = "splitThresholds"


# --- rounding and numbers the way Swift does them -----------------------------------------------


def round_half_away(value: float) -> float:
    """``Double.rounded()``: halves go away from zero (Python's ``round`` goes to even)."""
    return math.copysign(math.floor(abs(value) + 0.5), value)


_SWIFT_DOUBLE = re.compile(
    r"[+-]?(?:[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?|inf|infinity|nan)\Z",
    re.IGNORECASE | re.ASCII,
)
_SWIFT_INT = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)


def swift_double(text: str) -> float | None:
    """``Double(text)``: no spaces, no underscores (Python's ``float`` allows both)."""
    if _SWIFT_DOUBLE.match(text) is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def swift_int(text: str) -> int | None:
    return int(text) if _SWIFT_INT.match(text) else None


# --- the parser -------------------------------------------------------------------------------


class CliUsageError(Exception):
    """A usage problem: printed as ``error: <message>``, exit 2."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Command:
    """One parsed command. ``kind`` is the Swift enum case; ``args`` its payload, in order:

    ``help`` ``version`` ``status`` ``listAccounts`` ``addCurrent`` ``signInStatus`` ``signInCancel``
    ``refresh`` ``watch`` ``updateCheck`` ``quit`` ``mcp``: none.
    ``switchAccount`` ``(account,)``; ``signIn`` ``(account|None, open|None, wait)`` where ``open`` is
    the ``open`` parameter (``"default"`` or a browser's name); ``signInCode`` ``(code,)``;
    ``remove`` ``(account,)``; ``label`` ``(account, text|None)``; ``threshold`` ``(account,
    value|None)`` (0 = manual only); ``windowThresholds`` ``(account, session, weekly)`` where each
    window is ``None`` (not named), ``FOLLOW`` or a float; ``priority`` ``(account, int|None)``;
    ``order`` ``(accounts...)`` as one tuple; ``usage`` ``(account|None,)``; ``settingsGet``
    ``(key|None,)``; ``settingsSet`` ``(key, text)``.
    """

    kind: str
    args: tuple = ()


#: ``--session default`` / ``--weekly default``: the window follows its default again.
FOLLOW = "follow"


@dataclass(frozen=True)
class Invocation:
    command: Command
    json: bool = False
    timeout: float = DEFAULT_TIMEOUT


def parse(arguments: Sequence[str]) -> Invocation:
    """``CLIParser.parse``. Raises :class:`CliUsageError`."""
    as_json = False
    timeout = DEFAULT_TIMEOUT
    words: list[str] = []
    index = 0
    arguments = list(arguments)
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--json":
            as_json = True
        elif argument == "--timeout":
            index += 1
            seconds = swift_double(arguments[index]) if index < len(arguments) else None
            if seconds is None or not seconds > 0:
                raise CliUsageError("--timeout needs a number of seconds.")
            timeout = seconds
        elif argument in ("-h", "--help"):
            words = ["help"]
            break
        elif argument == "--version":
            words = ["version"]
            break
        else:
            words.append(argument)
        index += 1
    return Invocation(_command(words), as_json, timeout)


def _none(words: Sequence[str], usage: str) -> None:
    if words:
        raise CliUsageError(f"Usage: pixelswitch {usage}")


def _one(words: Sequence[str], usage: str) -> str:
    if len(words) != 1:
        raise CliUsageError(f"Usage: pixelswitch {usage}")
    return words[0]


def _command(words: list[str]) -> Command:
    if not words:
        return Command("help")
    first, rest = words[0], words[1:]
    if first == "help":
        return Command("help")
    if first == "version":
        return Command("version")
    if first == "status":
        _none(rest, "status")
        return Command("status")
    if first == "switch":
        return Command("switchAccount", (_one(rest, "switch <account>"),))
    if first == "accounts":
        return _accounts(rest)
    if first == "usage":
        if len(rest) > 1:
            raise CliUsageError("Usage: pixelswitch usage [<account>]")
        return Command("usage", (rest[0] if rest else None,))
    if first == "refresh":
        _none(rest, "refresh")
        return Command("refresh")
    if first == "watch":
        _none(rest, "watch")
        return Command("watch")
    if first == "settings":
        if rest[:1] == ["set"]:
            if len(rest) < 3:
                raise CliUsageError("Usage: pixelswitch settings set <key> <value>")
            return Command("settingsSet", (rest[1], " ".join(rest[2:])))
        if len(rest) > 1:
            raise CliUsageError("Usage: pixelswitch settings [<key>]")
        return Command("settingsGet", (rest[0] if rest else None,))
    if first == "update-check":
        _none(rest, "update-check")
        return Command("updateCheck")
    if first == "quit":
        _none(rest, "quit")
        return Command("quit")
    if first == "mcp":
        _none(rest, "mcp")
        return Command("mcp")
    raise CliUsageError(f'Unknown command "{first}". Run `pixelswitch help`.')


def _accounts(words: list[str]) -> Command:
    if not words:
        return Command("listAccounts")
    sub, rest = words[0], words[1:]
    if sub == "list":
        _none(rest, "accounts list")
        return Command("listAccounts")
    if sub == "add-current":
        _none(rest, "accounts add-current")
        return Command("addCurrent")
    if sub == "sign-in":
        head = rest[0] if rest else None
        if head == "code":
            return Command("signInCode", (_one(rest[1:], "accounts sign-in code <code>"),))
        if head == "cancel":
            _none(rest[1:], "accounts sign-in cancel")
            return Command("signInCancel")
        if head == "status":
            _none(rest[1:], "accounts sign-in status")
            return Command("signInStatus")
        open_with, wait, leftover = _sign_in_options(rest)
        if leftover:
            raise CliUsageError("Usage: pixelswitch accounts sign-in [--open | --open-in <browser>] [--wait]")
        return Command("signIn", (None, open_with, wait))
    if sub == "reauth":
        open_with, wait, leftover = _sign_in_options(rest)
        if len(leftover) != 1:
            raise CliUsageError("Usage: pixelswitch accounts reauth <account> [--open | --open-in <browser>] [--wait]")
        return Command("signIn", (leftover[0], open_with, wait))
    if sub == "remove":
        confirmed = "--yes" in rest
        names = [word for word in rest if word != "--yes"]
        if len(names) != 1:
            raise CliUsageError("Usage: pixelswitch accounts remove <account> --yes")
        if not confirmed:
            raise CliUsageError(
                f"Removing an account deletes its saved login. Add --yes to confirm: pixelswitch accounts remove {names[0]} --yes"
            )
        return Command("remove", (names[0],))
    if sub == "label":
        if len(rest) < 2:
            raise CliUsageError("Usage: pixelswitch accounts label <account> <text> | --clear")
        text = " ".join(rest[1:])
        return Command("label", (rest[0], None if text == "--clear" else text))
    if sub == "threshold":
        if any(word.startswith("--") for word in rest):
            return _window_thresholds(rest)
        if len(rest) != 2:
            raise CliUsageError(
                "Usage: pixelswitch accounts threshold <account> <1-100|default|manual>  or  --session <1-100|default> --weekly <1-100|default>"
            )
        if rest[1].lower() == "default":
            return Command("threshold", (rest[0], None))
        # Manual only is 0. "off", "never" and "disable" are deliberately not aliases: they read
        # just as well as "never switch away".
        if rest[1].lower() == "manual":
            return Command("threshold", (rest[0], 0.0))
        value = swift_double(rest[1].replace("%", ""))
        if value is None:
            raise CliUsageError('The threshold must be a number from 1 to 100, "default", or "manual" (0 means manual too).')
        return Command("threshold", (rest[0], value))
    if sub == "priority":
        if len(rest) != 2:
            raise CliUsageError("Usage: pixelswitch accounts priority <account> <1-99|none>")
        if rest[1].lower() == "none":
            return Command("priority", (rest[0], None))
        # A priority is a whole number. The "P" some screens show is not typed here.
        value = swift_int(rest[1])
        if value is None or not 1 <= value <= 99:
            raise CliUsageError('The priority must be a whole number from 1 to 99, or "none". Lower numbers are tried first.')
        return Command("priority", (rest[0], value))
    if sub == "order":
        if not rest:
            raise CliUsageError("Usage: pixelswitch accounts order <account> <account> ...")
        return Command("order", tuple(rest))
    raise CliUsageError(f'Unknown command "accounts {sub}". Run `pixelswitch help`.')


def _window_thresholds(words: list[str]) -> Command:
    usage = "Usage: pixelswitch accounts threshold <account> [--session <1-100|default>] [--weekly <1-100|default>]"
    account: str | None = None
    session: Any = None
    weekly: Any = None
    index = 0
    while index < len(words):
        word = words[index]
        if word in ("--session", "--weekly"):
            index += 1
            if index >= len(words):
                raise CliUsageError(f'{word} needs 1-100 or "default".')
            value = _window_value(words[index], word)
            if word == "--session":
                session = value
            else:
                weekly = value
        else:
            if word.startswith("--") or account is not None:
                raise CliUsageError(usage)
            account = word
        index += 1
    if account is None or (session is None and weekly is None):
        raise CliUsageError(usage)
    return Command("windowThresholds", (account, session, weekly))


def _window_value(text: str, flag: str) -> Any:
    if text.lower() == "default":
        return FOLLOW
    number = swift_double(text.replace("%", ""))
    if text.lower() == "manual" or number == 0:
        raise CliUsageError(
            "Manual only is for the whole account, not one window. Use: pixelswitch accounts threshold <account> manual"
        )
    if number is None or not math.isfinite(number) or not 1.0 <= round_half_away(number) <= 100.0:
        raise CliUsageError(f'{flag} needs a number from 1 to 100, or "default".')
    return round_half_away(number)


def _sign_in_options(words: Sequence[str]) -> tuple[str | None, bool, list[str]]:
    open_with: str | None = None
    wait = False
    leftover: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word == "--open":
            open_with = "default"
        elif word == "--open-in":
            index += 1
            if index >= len(words):
                raise CliUsageError("--open-in needs a browser's name, like Firefox.")
            open_with = words[index]
        elif word == "--wait":
            wait = True
        else:
            leftover.append(word)
        index += 1
    return open_with, wait, leftover


HELP_TEXT = """pixelswitch: control PixelSwitch from the command line, or from an AI.

Accounts
  pixelswitch status                             What is active, auto-switch, any sign-in
  pixelswitch accounts                           Every account, in priority order
  pixelswitch switch <account>                   Switch to an account
  pixelswitch accounts add-current               Save the account Claude Code is signed in as
  pixelswitch accounts sign-in [--open | --open-in <browser>] [--wait]
                                                 Sign in a new account (prints the links)
  pixelswitch accounts reauth <account> [--open | --open-in <browser>] [--wait]
  pixelswitch accounts sign-in status | cancel
  pixelswitch accounts sign-in code <code>       The code the page shows, from another device
  pixelswitch accounts remove <account> --yes    Deletes the account's saved login
  pixelswitch accounts label <account> <text> | --clear
  pixelswitch accounts threshold <account> <1-100|default|manual>
                                                 Both windows at once. manual (or 0): never switched to automatically
  pixelswitch accounts threshold <account> [--session <1-100|default>] [--weekly <1-100|default>]
                                                 One window or both: the 5-hour limit, the 7-day limit
  pixelswitch accounts priority <account> <1-99|none>
                                                 Lower numbers are tried first (shown as P1, P2, ...)
  pixelswitch accounts order <account> <account> ...   The full order; numbers every account from 1

Usage
  pixelswitch usage [<account>]                  Session, weekly and Fable use
  pixelswitch refresh                            Refresh now
  pixelswitch watch                              Live events, one JSON object per line

Settings and the app
  pixelswitch settings [<key>]                   Every setting, or one
  pixelswitch settings set <key> <value>
  pixelswitch update-check
  pixelswitch quit
  pixelswitch mcp                                Run as an MCP server on stdin/stdout

<account> is an email address, a label, an id, or a position (the # column: 1, 2, ...).
It is never a priority number (P1, P2, ...): set those with `accounts priority`.
Add --json for exact JSON, --timeout <seconds> to wait longer.
Exit codes: 0 ok, 1 failed, 2 usage, 3 busy, 4 no such account, 5 PixelSwitch unreachable."""

#: The settings' names in the order the Settings window shows them (CLIRunner.swift SettingKeyNames).
#: ``tests`` check it equals ``settings_catalog.NAMES``.
SETTING_KEY_NAMES = (
    "refreshInterval", "transcriptLookbackHours",
    "autoSwitch.enabled", "autoSwitch.threshold", "autoSwitch.sessionThreshold", "autoSwitch.weeklyThreshold",
    "autoSwitch.onFable", "autoSwitch.strategy", "autoSwitch.drainEarly", "autoSwitch.drainWithinHours",
    "maskEmailAddresses", "colorCodeAccounts", "appLanguage", "launchAtLogin",
    "menuBar.showsHeadIcon", "menuBar.modules", "menuBar.customizesLimitBarColors",
    "menuBar.color.session", "menuBar.color.weekly", "menuBar.color.fable", "menuBar.color.lowRemaining",
    "menuBar.lowRemainingWarningThreshold", "claude.binaryPath", "updates.automatic",
)


# --- decoding what the daemon sent (Swift's Codable, by hand) ------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional(value: Any, check: Callable[[Any], bool]) -> bool:
    return value is None or check(value)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def decode_status(value: Any) -> dict | None:
    """The status reply as a dict, or None when a required field is missing or mistyped (the Swift
    ``try? decode(StatusInfo.self)``)."""
    if not isinstance(value, dict):
        return None
    auto = value.get("autoSwitch")
    if not isinstance(auto, dict):
        return None
    ok = (
        _is_str(value.get("appVersion")) and _is_int(value.get("protocolVersion"))
        and _optional(value.get("activeAccountId"), _is_str) and _optional(value.get("activeAccountEmail"), _is_str)
        and _is_int(value.get("accountCount")) and _is_bool(value.get("claudeAvailable"))
        and _optional(value.get("lastRefresh"), _is_str)
        and _is_bool(auto.get("enabled")) and _is_number(auto.get("defaultThreshold"))
        and _is_bool(auto.get("onFable")) and _is_str(auto.get("strategy")) and _is_bool(auto.get("drainEarly"))
        and _is_number(auto.get("drainWithinHours"))
        and _optional(auto.get("defaultSessionThreshold"), _is_number)
        and _optional(auto.get("defaultWeeklyThreshold"), _is_number)
        and (value.get("signIn") is None or decode_sign_in(value.get("signIn")) is not None)
        and _optional(value.get("features"), lambda f: isinstance(f, list) and all(_is_str(x) for x in f))
    )
    return value if ok else None


def decode_sign_in(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    ok = (
        _is_str(value.get("id")) and _is_str(value.get("purpose")) and _is_str(value.get("state"))
        and _is_bool(value.get("codeSubmitted")) and _is_str(value.get("startedAt"))
        and all(_optional(value.get(k), _is_str) for k in
                ("accountId", "email", "message", "resultAccountId", "automaticLink", "manualLink", "notice"))
    )
    return value if ok else None


def decode_account(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    ok = (
        _is_str(value.get("id")) and _is_str(value.get("email"))
        and _optional(value.get("label"), _is_str) and _optional(value.get("subscription"), _is_str)
        and _is_bool(value.get("isActive")) and _is_bool(value.get("isSwitchable")) and _is_int(value.get("position"))
        and _optional(value.get("threshold"), _is_number) and _is_number(value.get("effectiveThreshold"))
        and _is_bool(value.get("manualOnly")) and _optional(value.get("usage"), lambda u: isinstance(u, dict))
        and _optional(value.get("priority"), _is_int)
        and all(_optional(value.get(k), _is_number)
                for k in ("sessionThreshold", "weeklyThreshold", "effectiveSessionThreshold", "effectiveWeeklyThreshold"))
    )
    return value if ok else None


def decode_accounts(value: Any) -> list[dict]:
    """``CLIRunner.accounts``: a list that fails to decode is the empty list (the CLI then says
    "No accounts yet", as the Mac tool does)."""
    if not isinstance(value, list):
        return []
    decoded = [decode_account(item) for item in value]
    return [] if any(item is None for item in decoded) else decoded  # type: ignore[misc]


def decode_report(value: Any) -> dict | None:
    """``usage`` reply: ``accounts`` (required) and ``lastRefresh``; ``machine`` is optional here
    because the Linux daemon sends none (D15)."""
    if not isinstance(value, dict) or not isinstance(value.get("accounts"), list):
        return None
    accounts = decode_accounts(value["accounts"])
    if len(accounts) != len(value["accounts"]):
        return None
    return value


# --- readable output (pure) -------------------------------------------------------------------------


class Output:
    @staticmethod
    def percent(value: float | None) -> str:
        if value is None:
            return "—"
        return f"{int(round_half_away(value))}%"

    @staticmethod
    def name(account: Mapping | None) -> str:
        if account is None:
            return "the account"
        label = account.get("label")
        if label is not None:
            return f"{account['email']} ({label})"
        return account["email"]

    @staticmethod
    def time(epoch: float | None) -> str:
        if epoch is None:
            return "—"
        return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def reset(iso: str | None) -> str:
        moment = parse_iso8601(iso) if iso is not None else None
        return "" if moment is None else f" (resets {Output.time(moment)})"

    @staticmethod
    def strategy_words(raw: str) -> str:
        if raw == "myOrder":
            return "priority"
        if raw == "resetsSoonest":
            return "resets soonest"
        return "most room left"

    @staticmethod
    def status(status: Mapping | None) -> str:
        if status is None:
            return "PixelSwitch answered, but not in a form this pixelswitch understands."
        lines = [f"PixelSwitch {status['appVersion']} (remote-control protocol {status['protocolVersion']})"]
        lines.append(f"Active:        {status.get('activeAccountEmail') or 'none'}")
        lines.append(f"Accounts:      {status['accountCount']}")
        a = status["autoSwitch"]
        if a["enabled"]:
            auto = f"on, default {Output.default_words(a)}, next by {Output.strategy_words(a['strategy'])}"
            if not a["onFable"]:
                auto += ", Fable ignored"
            if a["strategy"] == "resetsSoonest":
                auto += f", switches early within {int(a['drainWithinHours'])} h" if a["drainEarly"] else ", no early switching"
        else:
            auto = "off"
        lines.append(f"Auto-switch:   {auto}")
        lines.append(f"Claude CLI:    {'found' if status['claudeAvailable'] else 'not found'}")
        last = status.get("lastRefresh")
        lines.append(f"Last refresh:  {Output.time(parse_iso8601(last) if last is not None else None)}")
        if status.get("signIn") is not None:
            lines.append(f"Sign-in:       {Output.state_words(status['signIn'])}")
        warnings = status.get("warnings")
        if isinstance(warnings, list):
            for warning in warnings:
                if isinstance(warning, str) and warning:
                    lines.append(f"Warning:       {warning}")
        return "\n".join(lines)

    @staticmethod
    def default_words(a: Mapping) -> str:
        """"90%" when the two windows share one default, else "5h 90% / 7d 50%". The windows' own
        defaults win even when equal and different from the base; an app that sends neither (1.9)
        has only the base."""
        session, weekly = a.get("defaultSessionThreshold"), a.get("defaultWeeklyThreshold")
        if session is None or weekly is None:
            return Output.percent(a["defaultThreshold"])
        return Output.percent(session) if session == weekly else Output.windows_words(session, weekly)

    @staticmethod
    def windows_words(session: float, weekly: float) -> str:
        return f"5h {Output.percent(session)} / 7d {Output.percent(weekly)}"

    @staticmethod
    def priority_cell(account: Mapping) -> str:
        priority = account.get("priority")
        return f"P{priority}" if priority is not None else "—"

    @staticmethod
    def has_split(account: Mapping) -> bool:
        session, weekly = account.get("effectiveSessionThreshold"), account.get("effectiveWeeklyThreshold")
        return session is not None and weekly is not None and session != weekly

    @staticmethod
    def accounts(accounts: Sequence[Mapping]) -> str:
        if not accounts:
            return "No accounts yet. Add one: pixelswitch accounts add-current"
        rows = [["", "#", "Account", "Session", "Weekly", "Fable", "Switches at", "Priority"]]
        for account in accounts:
            usage = account.get("usage") or {}
            rows.append([
                "*" if account["isActive"] else ("" if account["isSwitchable"] else "!"),
                str(account["position"]),
                Output.name(account),
                Output.percent((usage.get("session") or {}).get("utilization")),
                Output.percent((usage.get("weekly") or {}).get("utilization")),
                Output.percent((usage.get("fable") or {}).get("utilization")),
                Output.switches_at(account),
                Output.priority_cell(account),
            ])
        text = Output.table(rows)
        text += "\n* active   ! cannot be switched to until it is re-signed (pixelswitch accounts reauth <account>)"
        if any(account["manualOnly"] for account in accounts):
            text += "\nmanual only: auto-switch never moves you to it; switch to it yourself with pixelswitch switch <account>"
        return text

    @staticmethod
    def _follows(account: Mapping) -> bool:
        return account.get("threshold") is None and account.get("sessionThreshold") is None and account.get("weeklyThreshold") is None

    @staticmethod
    def switches_at(account: Mapping) -> str:
        """The "Switches at" cell: where auto-switch moves off the account."""
        if Output.has_split(account):
            limit = Output.windows_words(
                account.get("effectiveSessionThreshold", account["effectiveThreshold"]),
                account.get("effectiveWeeklyThreshold", account["effectiveThreshold"]),
            )
        else:
            limit = Output.percent(account["effectiveThreshold"])
        if account["manualOnly"]:
            return limit + " (manual only)"
        return limit + (" (default)" if Output._follows(account) else "")

    @staticmethod
    def leave_words(account: Mapping) -> str:
        """Where auto-switch moves off the account, in words: "90%", or "90% of its session or 50% of its week"."""
        if Output.has_split(account):
            return (f"{Output.percent(account['effectiveSessionThreshold'])} of its session or "
                    f"{Output.percent(account['effectiveWeeklyThreshold'])} of its week")
        return Output.percent(account["effectiveThreshold"])

    PRIORITY_NOTE = "Auto-switch follows these numbers only when it chooses by Priority."

    @staticmethod
    def priority_set(accounts: Sequence[Mapping], strategy: str) -> str:
        text = Output.accounts(accounts)
        if strategy != "myOrder":
            text += "\n" + Output.PRIORITY_NOTE + " To use them: pixelswitch settings set autoSwitch.strategy priority"
        return text

    @staticmethod
    def order_set(accounts: Sequence[Mapping], numbered: bool) -> str:
        text = Output.accounts(accounts)
        if numbered:
            text += ("\nEvery account is now numbered from 1 in this order, replacing any priority numbers you set. "
                     "Lower numbers are tried first.")
        return text

    @staticmethod
    def threshold_set(account: Mapping | None) -> str:
        if account is None:
            return "Threshold set."
        if account["manualOnly"]:
            text = (f"{account['email']} is now manual only: auto-switch never switches to it. "
                    "You can still switch to it yourself.")
            if account["isActive"]:
                text += f" You're using it now: auto-switch moves you off it at {Output.leave_words(account)} and won't move you back to it."
            return text
        if Output.has_split(account):
            return f"{account['email']} now switches at {Output.leave_words(account)}."
        if Output._follows(account):
            return f"{account['email']} now follows the default threshold ({Output.percent(account['effectiveThreshold'])})."
        return f"{account['email']} now switches at {Output.percent(account['effectiveThreshold'])}."

    @staticmethod
    def switched(account: Mapping | None) -> str:
        text = f"Switched to {Output.name(account)}."
        if account is not None and account["manualOnly"]:
            text += f" It is manual only: auto-switch moves you off it at {Output.leave_words(account)} and won't move you back to it."
        return text

    @staticmethod
    def usage(report: Mapping | None) -> str:
        """The usage text. The Mac's last line ("This Mac today: ...") is left out: the Linux daemon
        has no cost dashboard (spec D15)."""
        if report is None:
            return "No usage yet."
        lines: list[str] = []
        for account in report["accounts"]:
            lines.append(f"{account['position']}. {Output.name(account)}{'  [active]' if account['isActive'] else ''}")
            usage = account.get("usage")
            if usage is not None:
                session, weekly, fable = usage.get("session") or {}, usage.get("weekly") or {}, usage.get("fable")
                lines.append(f"   Session {Output.percent(session.get('utilization'))}{Output.reset(session.get('resetsAt'))}")
                lines.append(f"   Weekly  {Output.percent(weekly.get('utilization'))}{Output.reset(weekly.get('resetsAt'))}")
                if fable is not None:
                    lines.append(f"   Fable   {Output.percent(fable.get('utilization'))}{Output.reset(fable.get('resetsAt'))}")
                if usage.get("error") is not None:
                    lines.append(f"   Note: {usage['error']}")
                sampled = parse_iso8601(usage.get("sampledAt")) if usage.get("sampledAt") is not None else None
                if sampled is not None:
                    lines.append(f"   Read at {Output.time(sampled)}")
            else:
                lines.append("   No reading yet.")
        return "\n".join(lines)

    @staticmethod
    def state_words(info: Mapping) -> str:
        state = info["state"]
        if state == "starting":
            return "getting the sign-in link"
        if state == "waitingForUser":
            return "checking the code" if info["codeSubmitted"] else "waiting for someone to sign in in a browser"
        if state == "completing":
            return "saving the account"
        if state == "succeeded":
            return "signed in"
        if state == "failed":
            return f"failed: {info.get('message') or 'unknown reason'}"
        if state == "cancelled":
            return "cancelled"
        return state

    @staticmethod
    def sign_in(info: Mapping) -> str:
        what = f"Re-sign {info.get('email') or 'account'}" if info["purpose"] == "reauthenticate" else "New account"
        lines = [f"{what}: {Output.state_words(info)}"]
        if info.get("automaticLink") is not None:
            lines.append("On the computer running PixelSwitch, open this in any browser (it finishes by itself):")
            lines.append(f"  {info['automaticLink']}")
        if info.get("manualLink") is not None:
            lines.append("On another device, open this; the page then shows a code:")
            lines.append(f"  {info['manualLink']}")
            lines.append("  then run: pixelswitch accounts sign-in code <code>")
        if info.get("notice") is not None:
            lines.append(info["notice"])
        return "\n".join(lines)

    @staticmethod
    def settings(value: Any) -> str:
        if not isinstance(value, dict):
            return ""
        order = list(SETTING_KEY_NAMES)

        def rank(key: str) -> tuple[int, str]:
            return (order.index(key) if key in order else 1 << 62, key)

        return "\n".join(f"{key} = {protocol.dumps(value[key])}" for key in sorted(value, key=rank))

    @staticmethod
    def table(rows: Sequence[Sequence[str]]) -> str:
        columns = len(rows[0]) if rows else 0
        widths = [max(len(row[column]) for row in rows) for column in range(columns)]
        return "\n".join(
            "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).strip(" \t")
            for row in rows
        )


# --- JSON text for --json (Foundation's .prettyPrinted, so the two tools print the same) --------------


def pretty(value: Any, depth: int = 0) -> str:
    """Indented JSON, keys sorted, slashes unescaped, ``"key" : value`` and empty containers as
    ``[\\n\\n  ]``, which is how Foundation's ``JSONEncoder`` writes them."""
    pad = "  " * depth
    if isinstance(value, Mapping):
        if not value:
            return "{\n\n" + pad + "}"
        keys = sorted(value)
        body = ",\n".join(f"{pad}  {json.dumps(key, ensure_ascii=False)} : {pretty(value[key], depth + 1)}" for key in keys)
        return "{\n" + body + "\n" + pad + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[\n\n" + pad + "]"
        body = ",\n".join(f"{pad}  {pretty(item, depth + 1)}" for item in value)
        return "[\n" + body + "\n" + pad + "]"
    return protocol.dumps(value)


def value_from(text: str) -> Any:
    """What a person typed after ``settings set <key>``: JSON when it reads as JSON (``true``, ``90``,
    ``["a","b"]``, ``"text"``), otherwise plain text."""
    try:
        return protocol.loads(text)
    except (ValueError, RecursionError):
        return text


def sign_in_of(result: Any) -> dict:
    """``CLIRunner.signIn``: the ``signIn`` of a reply."""
    value = result.get("signIn") if isinstance(result, dict) else None
    if value is None:
        raise ControlError("notFound", "No sign-in has been started.")
    info = decode_sign_in(value)
    if info is None:
        raise ControlError("failed", "PixelSwitch answered, but not in a form this pixelswitch understands.")
    return info


def _account_of(result: Any, key: str = "account") -> dict | None:
    value = result.get(key) if isinstance(result, dict) else None
    return decode_account(value) if value is not None else None


# --- the runner -------------------------------------------------------------------------------------


def _write_stdout(text: str) -> None:
    try:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        raise SystemExit(EXIT_FAILED) from None


def _write_stderr(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


@dataclass
class Runner:
    """Runs one parsed command against the daemon and prints the result."""

    client: ControlClient
    out: Callable[[str], None] = _write_stdout
    err: Callable[[str], None] = _write_stderr
    #: How often ``--wait`` checks a sign-in, in seconds.
    poll_interval: float = 1.0
    sleep: Callable[[float], None] = time.sleep
    version_text: Callable[[], str] = field(default=lambda: "unknown")

    def run(self, invocation: Invocation) -> int:
        command = invocation.command
        if command.kind == "help":
            self.out(HELP_TEXT)
            return EXIT_OK
        if command.kind == "version":
            self.out(f"pixelswitch {self.version_text()} (PixelSwitch remote-control protocol {protocol.PROTOCOL_VERSION})")
            return EXIT_OK
        try:
            self.client.connect()
            status = self.check_protocol(invocation.timeout)
            return self._execute(invocation, status)
        except ControlError as error:
            self.err(f"error: {error.message}")
            if error.candidates:
                self.err("  choices: " + ", ".join(error.candidates))
            return exit_code_for(error)
        except ClientError as error:
            self.err(f"error: {error.description}")
            return EXIT_UNREACHABLE if error.kind == "unreachable" else EXIT_FAILED
        except KeyboardInterrupt:
            return 130
        except (OSError, ValueError) as error:
            self.err(f"error: {getattr(error, 'strerror', None) or error}")
            return EXIT_FAILED

    def check_protocol(self, timeout: float) -> dict:
        """Refuses a daemon that speaks another protocol version. Returns what it said about itself,
        so a command can check what that daemon can do."""
        result = self.client.call("status.get", None, timeout)
        status = decode_status(result)
        check_protocol(result if isinstance(result, dict) else {})
        if status is None:
            raise ControlError("failed", "PixelSwitch answered, but not in a form this pixelswitch understands.")
        return status

    def _execute(self, invocation: Invocation, app: dict) -> int:
        timeout = invocation.timeout
        as_json = invocation.json
        command = invocation.command
        args = command.args

        def call(method: str, params: Any = None) -> Any:
            # accounts.switch, accounts.addCurrent and usage.refresh wait at least 120 s (the client's slow floor).
            return self.client.call(method, params, timeout)

        def show(result: Any, text: Callable[[], str]) -> None:
            self.out(pretty(result) if as_json else text())

        kind = command.kind
        if kind == "status":
            result = call("status.get")
            show(result, lambda: Output.status(decode_status(result)))
        elif kind == "listAccounts":
            result = call("accounts.list")
            show(result, lambda: Output.accounts(decode_accounts(result.get("accounts") if isinstance(result, dict) else None)))
        elif kind == "switchAccount":
            result = call("accounts.switch", {"account": args[0]})
            show(result, lambda: Output.switched(_account_of(result)))
        elif kind == "addCurrent":
            result = call("accounts.addCurrent")
            show(result, lambda: f"Saved {Output.name(_account_of(result))}.")
        elif kind == "signIn":
            return self._sign_in(args, timeout, as_json)
        elif kind == "signInStatus":
            result = call("accounts.signIn.status")

            def sign_in_text() -> str:
                info = decode_sign_in(result.get("signIn")) if isinstance(result, dict) else None
                return Output.sign_in(info) if info is not None else "No sign-in has been started."

            show(result, sign_in_text)
        elif kind == "signInCode":
            result = call("accounts.signIn.submitCode", {"code": args[0]})
            show(result, lambda: "Code handed to Claude Code; it is checking it now.")
        elif kind == "signInCancel":
            result = call("accounts.signIn.cancel")
            show(result, lambda: "Sign-in cancelled.")
        elif kind == "remove":
            result = call("accounts.remove", {"account": args[0]})
            show(result, lambda: f"Removed {Output.name(_account_of(result, 'removed'))}.")
        elif kind == "label":
            result = call("accounts.setLabel", {"account": args[0], "label": args[1]})
            show(result, lambda: "Label cleared." if args[1] is None else f"Label set: {Output.name(_account_of(result))}.")
        elif kind == "threshold":
            result = call("accounts.setThreshold", {"account": args[0], "threshold": args[1]})
            show(result, lambda: Output.threshold_set(_account_of(result)))
        elif kind == "windowThresholds":
            require_feature(app, FEATURE_SPLIT)
            params: dict[str, Any] = {"account": args[0]}
            for key, value in (("session", args[1]), ("weekly", args[2])):
                if value is not None:
                    params[key] = None if value == FOLLOW else value
            result = call("accounts.setThresholds", params)
            show(result, lambda: Output.threshold_set(_account_of(result)))
        elif kind == "priority":
            require_feature(app, FEATURE_PRIORITY)
            result = call("accounts.setPriority", {"account": args[0], "priority": args[1]})
            show(result, lambda: Output.priority_set(
                decode_accounts(result.get("accounts") if isinstance(result, dict) else None), app["autoSwitch"]["strategy"]))
        elif kind == "order":
            result = call("accounts.setOrder", {"accounts": list(args)})
            numbered = has_feature(app, FEATURE_PRIORITY)
            show(result, lambda: Output.order_set(
                decode_accounts(result.get("accounts") if isinstance(result, dict) else None), numbered))
        elif kind == "usage":
            result = call("usage.get", {"account": args[0]} if args[0] is not None else None)
            show(result, lambda: Output.usage(decode_report(result)))
        elif kind == "refresh":
            result = call("usage.refresh")
            show(result, lambda: Output.usage(decode_report(result)))
        elif kind == "watch":
            call("events.subscribe")
            if not as_json:
                self.err("Watching PixelSwitch. Press Control-C to stop.")
            while True:
                params = self.client.next_event(3600.0)
                if params is None:
                    continue
                self.out(protocol.dumps(params))
        elif kind == "settingsGet":
            result = call("settings.get", {"key": args[0]} if args[0] is not None else None)
            show(result, lambda: Output.settings(result.get("settings") if isinstance(result, dict) else None))
        elif kind == "settingsSet":
            result = call("settings.set", {"key": args[0], "value": value_from(args[1])})
            show(result, lambda: Output.settings(result.get("settings") if isinstance(result, dict) else None))
        elif kind == "updateCheck":
            result = call("app.checkForUpdates")
            show(result, lambda: "PixelSwitch is checking for updates; a newer version, if there is one, installs by itself.")
        elif kind == "quit":
            result = call("app.quit")
            show(result, lambda: "PixelSwitch is quitting.")
        return EXIT_OK

    def _sign_in(self, args: tuple, timeout: float, as_json: bool) -> int:
        account, open_with, wait = args
        params: dict[str, Any] = {}
        if account is not None:
            params["account"] = account
        if open_with is not None:
            params["open"] = open_with
        info = sign_in_of(self.client.call("accounts.signIn.start", params, timeout))
        # The links arrive a moment after the command starts; wait for them.
        link_deadline = time.monotonic() + 15
        while info["state"] == "starting" and time.monotonic() < link_deadline:
            self.sleep(0.5)
            info = sign_in_of(self.client.call("accounts.signIn.status", None, timeout))
        if not wait:
            self.out(pretty(info) if as_json else Output.sign_in(info))
            return EXIT_OK
        if not as_json:
            self.out(Output.sign_in(info))
        last_state = info["state"]
        wait_deadline = time.monotonic() + 16 * 60
        while info["state"] not in ("succeeded", "failed", "cancelled") and time.monotonic() < wait_deadline:
            self.sleep(self.poll_interval)
            info = sign_in_of(self.client.call("accounts.signIn.status", None, timeout))
            if info["state"] != last_state and not as_json:
                self.out("… " + Output.state_words(info))
            last_state = info["state"]
        if as_json:
            self.out(pretty(info))
        return EXIT_OK if info["state"] == "succeeded" else EXIT_FAILED


def app_version_text() -> str:
    from . import version as version_module

    try:
        return version_module.read_version().version
    except version_module.VersionUnavailable:
        return "unknown"


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """The entry point the ``pixelswitch`` launcher calls."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        invocation = parse(arguments)
    except CliUsageError as error:
        _write_stderr(f"error: {error.message}")
        return EXIT_USAGE
    if invocation.command.kind == "mcp":
        from . import mcp

        return mcp.run_stdio(app_version_text(), env=env)
    runner = Runner(ControlClient(env=env), version_text=app_version_text)
    return runner.run(invocation)
