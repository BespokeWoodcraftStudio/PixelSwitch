"""The usage response model, ported from PixelSwitch/Models/UsageData.swift.

Dates are POSIX seconds (``float``) throughout the core; ``parse_iso8601`` is
the one place a reset string becomes one.

Decoding mirrors the Swift ``Codable`` rules: every field is optional, a wrong
type anywhere in the windows or ``extra_usage`` fails the whole decode
(``UsageDecodeError``, Swift's ``.decode`` error), while ``limits`` is decoded
leniently (UsageData.swift:31-47 and 119-146): an entry of an unexpected shape
is skipped and a ``limits`` that is not a list reads as empty.
"""
from __future__ import annotations

import calendar
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

FABLE_MODEL_NAME = "Fable"

FIVE_HOUR_SECONDS = 5 * 60 * 60
SEVEN_DAY_SECONDS = 7 * 24 * 60 * 60

_ISO = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})\Z",
    re.ASCII,
)


class UsageDecodeError(ValueError):
    """The body was not usage data PixelSwitch can read."""


def is_number(value: Any) -> bool:
    """A JSON number: an int or float that is not a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def trim_whitespace(text: str) -> str:
    """Swift ``trimmingCharacters(in: .whitespaces)``: Zs characters and tab."""
    def is_space(char: str) -> bool:
        return char == "\t" or unicodedata.category(char) == "Zs"

    start, end = 0, len(text)
    while start < end and is_space(text[start]):
        start += 1
    while end > start and is_space(text[end - 1]):
        end -= 1
    return text[start:end]


_PLAIN_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z", re.ASCII)


def swift_double(text: str | None) -> float | None:
    """Swift ``Double(String)`` for the texts a person or a header can carry.

    Plain decimal and exponent forms only (``72.4``, ``1e3``, ``-5``); no
    underscores, no non-ASCII digits, no ``inf`` / ``nan`` words. The caller
    trims whitespace first when Swift's caller does.
    """
    if not isinstance(text, str) or _PLAIN_NUMBER.match(text) is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_iso8601(text: str | None) -> float | None:
    """POSIX seconds for an ISO 8601 internet date-time, or None.

    UsageData.swift:157-164: tried with fractional seconds, then without; an
    unparseable value is nil. Accepts 0 to 9 fractional digits and ``Z`` or
    ``+HH:MM`` offsets (the colon is required, as ``.withInternetDateTime``
    requires it).
    """
    if not isinstance(text, str):
        return None
    match = _ISO.match(text)
    if match is None:
        return None
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    fraction = match.group(7)
    zone = match.group(8)
    try:
        moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None
    offset = 0
    if zone != "Z":
        sign = 1 if zone[0] == "+" else -1
        zone_hours, zone_minutes = int(zone[1:3]), int(zone[4:6])
        if zone_hours > 23 or zone_minutes > 59:
            return None
        offset = sign * (zone_hours * 3600 + zone_minutes * 60)
    seconds = float(calendar.timegm(moment.timetuple()) - offset)
    if fraction:
        seconds += int(fraction) / (10 ** len(fraction))
    return seconds


def _opt_number(obj: dict, key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if not is_number(value):
        raise UsageDecodeError(f"{key} is not a number")
    return float(value)


def _opt_string(obj: dict, key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise UsageDecodeError(f"{key} is not a string")
    return value


@dataclass(frozen=True)
class UsageWindow:
    """UsageData.swift:148-155. ``utilization`` is a percent in 0 to 100."""

    utilization: float | None = None
    resets_at: str | None = None

    @property
    def resets_at_date(self) -> float | None:
        return parse_iso8601(self.resets_at)

    @classmethod
    def from_json(cls, obj: Any) -> "UsageWindow | None":
        if obj is None:
            return None
        if not isinstance(obj, dict):
            raise UsageDecodeError("a usage window is not an object")
        return cls(_opt_number(obj, "utilization"), _opt_string(obj, "resets_at"))


@dataclass(frozen=True)
class UsageLimit:
    """One entry of the ``limits`` list (UsageData.swift:64-115)."""

    kind: str | None = None
    group: str | None = None
    percent: float | None = None
    severity: str | None = None
    resets_at: str | None = None
    model_id: str | None = None
    model_display_name: str | None = None
    is_active: bool | None = None

    @property
    def model_name(self) -> str | None:
        """The model's display name, trimmed, when it is non-empty (``:95-99``)."""
        if self.model_display_name is None:
            return None
        name = trim_whitespace(self.model_display_name)
        return name or None

    @property
    def is_model_weekly(self) -> bool:
        """A weekly allowance for one model, with a reading to show (``:102-104``)."""
        return (
            (self.kind == "weekly_scoped" or self.group == "weekly")
            and self.model_name is not None
            and self.percent is not None
        )

    @property
    def percent_left(self) -> float | None:
        if self.percent is None:
            return None
        return min(max(100 - self.percent, 0), 100)

    @property
    def window(self) -> UsageWindow:
        """The same reading as a window (``:112-114``)."""
        return UsageWindow(self.percent, self.resets_at)

    @classmethod
    def from_json(cls, obj: Any) -> "UsageLimit | None":
        """The entry, or None when its shape does not fit (the lenient skip)."""
        if not isinstance(obj, dict):
            return None
        try:
            scope = obj.get("scope")
            model_id = model_name = None
            if scope is not None:
                if not isinstance(scope, dict):
                    return None
                model = scope.get("model")
                if model is not None:
                    if not isinstance(model, dict):
                        return None
                    model_id = _opt_string(model, "id")
                    model_name = _opt_string(model, "display_name")
            active = obj.get("is_active")
            if active is not None and not isinstance(active, bool):
                return None
            return cls(
                kind=_opt_string(obj, "kind"),
                group=_opt_string(obj, "group"),
                percent=_opt_number(obj, "percent"),
                severity=_opt_string(obj, "severity"),
                resets_at=_opt_string(obj, "resets_at"),
                model_id=model_id,
                model_display_name=model_name,
                is_active=active,
            )
        except UsageDecodeError:
            return None


@dataclass(frozen=True)
class ExtraUsage:
    is_enabled: bool | None = None
    monthly_limit: float | None = None
    used_credits: float | None = None
    utilization: float | None = None

    @classmethod
    def from_json(cls, obj: Any) -> "ExtraUsage | None":
        if obj is None:
            return None
        if not isinstance(obj, dict):
            raise UsageDecodeError("extra_usage is not an object")
        enabled = obj.get("is_enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise UsageDecodeError("is_enabled is not a bool")
        return cls(
            enabled,
            _opt_number(obj, "monthly_limit"),
            _opt_number(obj, "used_credits"),
            _opt_number(obj, "utilization"),
        )


@dataclass(frozen=True)
class UsageResponse:
    """The ``GET /api/oauth/usage`` body (UsageData.swift:5-58)."""

    five_hour: UsageWindow | None = None
    seven_day: UsageWindow | None = None
    seven_day_oauth_apps: UsageWindow | None = None
    seven_day_opus: UsageWindow | None = None
    seven_day_sonnet: UsageWindow | None = None
    seven_day_cowork: UsageWindow | None = None
    iguana_necktie: UsageWindow | None = None
    extra_usage: ExtraUsage | None = None
    limits: tuple[UsageLimit, ...] | None = None

    @classmethod
    def from_json(cls, obj: Any) -> "UsageResponse":
        if not isinstance(obj, dict):
            raise UsageDecodeError("the usage body is not an object")
        raw_limits = obj.get("limits")
        limits: tuple[UsageLimit, ...] | None
        if raw_limits is None:
            limits = None
        elif isinstance(raw_limits, list):
            limits = tuple(
                entry for entry in (UsageLimit.from_json(item) for item in raw_limits) if entry is not None
            )
        else:
            limits = ()  # a limits that is not a list reads as none
        return cls(
            five_hour=UsageWindow.from_json(obj.get("five_hour")),
            seven_day=UsageWindow.from_json(obj.get("seven_day")),
            seven_day_oauth_apps=UsageWindow.from_json(obj.get("seven_day_oauth_apps")),
            seven_day_opus=UsageWindow.from_json(obj.get("seven_day_opus")),
            seven_day_sonnet=UsageWindow.from_json(obj.get("seven_day_sonnet")),
            seven_day_cowork=UsageWindow.from_json(obj.get("seven_day_cowork")),
            iguana_necktie=UsageWindow.from_json(obj.get("iguana_necktie")),
            extra_usage=ExtraUsage.from_json(obj.get("extra_usage")),
            limits=limits,
        )

    @property
    def model_weekly_limits(self) -> tuple[UsageLimit, ...]:
        """Per-model weekly allowances (today only Fable), in the API's order."""
        return tuple(entry for entry in (self.limits or ()) if entry.is_model_weekly)

    def model_weekly_limit(self, name: str) -> UsageLimit | None:
        """The weekly allowance for one model, matched by display name, case-insensitively."""
        wanted = name.casefold()
        for entry in self.model_weekly_limits:
            model = entry.model_name
            if model is not None and model.casefold() == wanted:
                return entry
        return None


def window_elapsed_percent(window: UsageWindow, window_seconds: float, now: float) -> float | None:
    """How much of the window has elapsed, 0 to 100 (UsageData.swift:218-222)."""
    reset = window.resets_at_date
    if window_seconds <= 0 or reset is None:
        return None
    elapsed = 1.0 - ((reset - now) / window_seconds)
    return min(max(elapsed, 0.0), 1.0) * 100
