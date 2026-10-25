"""Every setting the control API and the CLI know, with the values each accepts.

Ports PixelSwitch/Control/SettingsCatalog.swift (kinds, normalisation, error texts) and
the reading and writing rules of SettingsStore.swift, over a plain dict (Linux
``settings.json``). The 22 keys of 1.9 are all here, in the same order, plus the two
2.0 keys ``autoSwitch.sessionThreshold`` and ``autoSwitch.weeklyThreshold`` (placed
right after ``autoSwitch.threshold``, as control.md 4.5 recommends) and the strategy
alias ``priority`` (spec section 4 and D4).

Error texts are byte-identical to the Mac's: ``<key> must be <summary>.`` with the
summaries of SettingsCatalog.swift:66-76.

Keys with no effect on Linux (spec D15): stored and validated exactly as on the Mac,
but nothing acts on them. See :data:`NO_EFFECT_ON_LINUX`.

Pure: no file is read or written here. The daemon loads ``settings.json`` into a dict,
calls :func:`read_setting` / :func:`write_setting`, and saves the dict it gets back.

``launchAtLogin`` (the tray autostart file) and ``updates.automatic`` (the Linux updater) are
stored here too, but the daemon owns their side effects and must read ``launchAtLogin`` back
from the autostart file, as the Mac reads the login item's real state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .engine import (
    DRAIN_HOURS_RANGE,
    FALLBACK_DRAIN_WITHIN_HOURS,
    THRESHOLD_RANGE,
    Defaults,
    Strategy,
    resolve_defaults,
    round_half_away,
)
from .protocol import ControlError, dumps, fold_text, trim_whitespace_and_newlines
from .finite import finite_number
from .usage_model import is_number, swift_double, trim_whitespace

MENU_BAR_MODULES = (
    "account", "sessionBar", "sessionBarPlain", "weeklyBar", "weeklyBarPlain",
    "fableBar", "dailyCost", "sessionReset", "weeklyReset",
)


@dataclass(frozen=True)
class Kind:
    """The shape of a setting's value (SettingKey.Kind)."""

    type: str  # bool | choice | number | list | color | path | threshold
    choices: tuple = ()
    lo: float = 0.0
    hi: float = 0.0
    step: float | None = None


BOOL = Kind("bool")
COLOR = Kind("color")
PATH = Kind("path")


def choice(*values: Any) -> Kind:
    return Kind("choice", choices=tuple(values))


def number(lo: float, hi: float, step: float | None = None) -> Kind:
    return Kind("number", lo=lo, hi=hi, step=step)


def listing(*values: str) -> Kind:
    return Kind("list", choices=tuple(values))


def window_default_kind(lo: float, hi: float) -> Kind:
    """50-100 whole numbers, and ``null`` or ``"default"`` clears to follow ``autoSwitch.threshold``."""
    return Kind("threshold", lo=lo, hi=hi, step=1)


@dataclass(frozen=True)
class SettingKey:
    """One setting. ``name`` is what the CLI and the control API use; ``store_key`` is the
    key in ``settings.json`` (the Mac's UserDefaults name where there is one)."""

    name: str
    store_key: str
    kind: Kind
    default: Any
    #: Spec D15: stored and validated, but nothing on Linux acts on it.
    no_effect_on_linux: bool = False


def _k(name: str, store_key: str, kind: Kind, default: Any, no_effect: bool = False) -> SettingKey:
    return SettingKey(name, store_key, kind, default, no_effect)


#: SettingKey.allCases order (control.md section 2) with the two 2.0 keys after the threshold.
KEYS: tuple[SettingKey, ...] = (
    _k("refreshInterval", "refreshInterval", choice(15, 30, 60, 300, 600), 300),
    _k("transcriptLookbackHours", "transcriptLookbackHours", choice(24, 72, 168, 720, 0), 24, True),
    _k("autoSwitch.enabled", "autoSwitchEnabled", BOOL, False),
    _k("autoSwitch.threshold", "autoSwitchThreshold", number(*THRESHOLD_RANGE, step=1), 90),
    _k("autoSwitch.sessionThreshold", "autoSwitchSessionThreshold", window_default_kind(*THRESHOLD_RANGE), None),
    _k("autoSwitch.weeklyThreshold", "autoSwitchWeeklyThreshold", window_default_kind(*THRESHOLD_RANGE), None),
    _k("autoSwitch.onFable", "autoSwitchOnFable", BOOL, True),
    _k("autoSwitch.strategy", "autoSwitchStrategy", choice(*(s.value for s in Strategy)), Strategy.MOST_ROOM.value),
    _k("autoSwitch.drainEarly", "autoSwitchDrainEarly", BOOL, True),
    _k("autoSwitch.drainWithinHours", "autoSwitchDrainWithinHours", number(*DRAIN_HOURS_RANGE, step=1),
       int(FALLBACK_DRAIN_WITHIN_HOURS)),
    _k("maskEmailAddresses", "maskEmailAddresses", BOOL, False),
    _k("colorCodeAccounts", "colorCodeAccounts", BOOL, True),
    _k("appLanguage", "appLanguage", choice("auto", "en", "zh-Hans", "ja", "de", "fr"), "auto", True),
    _k("launchAtLogin", "launchAtLogin", BOOL, False),
    _k("menuBar.showsHeadIcon", "menuBarShowsHeadIcon", BOOL, True, True),
    _k("menuBar.modules", "menuBarModules", listing(*MENU_BAR_MODULES),
       ["account", "sessionBarPlain", "weeklyBarPlain"], True),
    _k("menuBar.customizesLimitBarColors", "customizesLimitBarColors", BOOL, False, True),
    _k("menuBar.color.session", "sessionLimitBarColor", COLOR, "#34C759", True),
    _k("menuBar.color.weekly", "weeklyLimitBarColor", COLOR, "#0A84FF", True),
    _k("menuBar.color.fable", "fableLimitBarColor", COLOR, "#AF52DE", True),
    _k("menuBar.color.lowRemaining", "lowRemainingLimitBarColor", COLOR, "#FF3B30", True),
    _k("menuBar.lowRemainingWarningThreshold", "lowRemainingWarningThreshold", number(0, 100, None), 30, True),
    _k("claude.binaryPath", "claudeBinaryPathPreference", PATH, ""),
    _k("updates.automatic", "updatesAutomatic", BOOL, True),
)

BY_NAME: dict[str, SettingKey] = {key.name: key for key in KEYS}
NAMES: tuple[str, ...] = tuple(key.name for key in KEYS)

#: The names of the 22 keys of 1.9 (the Mac's list), for parity checks.
KEYS_1_9: tuple[str, ...] = tuple(n for n in NAMES if n not in ("autoSwitch.sessionThreshold", "autoSwitch.weeklyThreshold"))

#: Spec D15: keys that are stored and validated on Linux but have no effect there.
NO_EFFECT_ON_LINUX: frozenset[str] = frozenset(key.name for key in KEYS if key.no_effect_on_linux)

THRESHOLD_KEY = "autoSwitch.threshold"
SESSION_THRESHOLD_KEY = "autoSwitch.sessionThreshold"
WEEKLY_THRESHOLD_KEY = "autoSwitch.weeklyThreshold"
STRATEGY_KEY = "autoSwitch.strategy"
#: Strategy aliases (D4): ``priority`` stores ``myOrder``.
STRATEGY_ALIASES = {"priority": Strategy.MY_ORDER.value}


# --- text ------------------------------------------------------------------------------------


def _format(value: float) -> str:
    """SettingsCatalog.swift:157-159. Whole numbers without a fraction."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def summary(key: SettingKey) -> str:
    """One line for ``pixelswitch settings`` and the MCP tool description (:66-76)."""
    kind = key.kind
    if kind.type == "bool":
        return "true or false"
    if kind.type == "choice":
        return "one of " + ", ".join(dumps(value) for value in kind.choices)
    if kind.type == "number":
        return f"{_format(kind.lo)}–{_format(kind.hi)}" + (", whole numbers" if kind.step == 1 else "")
    if kind.type == "threshold":
        return f'{_format(kind.lo)}–{_format(kind.hi)}, whole numbers, or "default"'
    if kind.type == "list":
        return "a list drawn from " + ", ".join(kind.choices)
    if kind.type == "color":
        return "a colour as #RRGGBB"
    return '"" to auto-detect, or an absolute path'


def invalid(key: SettingKey) -> ControlError:
    """``<key> must be <summary>.`` (SettingsCatalog.swift:131-133)."""
    return ControlError("invalidValue", f"{key.name} must be {summary(key)}.")


def named(name: str) -> SettingKey:
    """Finds a key by its name, case-insensitive, or lists the valid names (:141-145)."""
    trimmed = trim_whitespace(name)
    wanted = fold_text(trimmed)
    for key in KEYS:
        if fold_text(key.name) == wanted:
            return key
    raise ControlError("notFound", f'There is no setting called "{trimmed}".', NAMES)


# --- normalisation (SettingKey.normalize, :81-138) --------------------------------------------


def _as_number(value: Any) -> float | None:
    """:148-152. A JSON number, or numeric text (trimmed)."""
    if is_number(value):
        return finite_number(value)  # a huge integer is not a number here (it overflows a double)
    if isinstance(value, str):
        return swift_double(trim_whitespace(value))
    return None


def _tidy(value: float) -> int | float:
    return int(value) if value.is_integer() and abs(value) < 2**53 else value


def _hex_digit(char: str) -> bool:
    return char in "0123456789abcdefABCDEF"


def normalize(key: SettingKey, value: Any) -> Any:
    """``value`` in the canonical form this setting stores, or ``invalidValue``.

    Accepts what a person types on a command line too: ``true`` / ``on`` / ``yes``, numbers
    as text, a comma-separated list. For ``autoSwitch.sessionThreshold`` and
    ``autoSwitch.weeklyThreshold`` ``None`` (JSON null) or the text ``default`` is returned
    as ``None``: clear to follow ``autoSwitch.threshold``.
    """
    kind = key.kind

    if key.name == STRATEGY_KEY and isinstance(value, str):
        # Trimmed first, as the Mac trims .whitespaces before comparing (SettingsCatalog.swift:108).
        alias = STRATEGY_ALIASES.get(trim_whitespace(value).casefold())
        if alias is not None:
            return alias

    if kind.type == "bool":
        if isinstance(value, bool):
            return value
        number = finite_number(value)
        if number is not None and number in (0.0, 1.0):
            return number == 1.0
        if isinstance(value, str):
            text = value.lower()
            if text in ("true", "on", "yes", "1"):
                return True
            if text in ("false", "off", "no", "0"):
                return False
        raise invalid(key)

    if kind.type == "choice":
        as_number = _as_number(value)
        if as_number is not None:
            for candidate in kind.choices:
                if is_number(candidate) and float(candidate) == as_number:
                    return candidate
        if isinstance(value, str):
            for candidate in kind.choices:
                if isinstance(candidate, str) and fold_text(candidate) == fold_text(value):
                    return candidate
        raise invalid(key)

    if kind.type == "threshold":
        if value is None:
            return None
        if isinstance(value, str) and trim_whitespace(value).casefold() == "default":
            return None
        return _normalize_number(key, kind, value)

    if kind.type == "number":
        return _normalize_number(key, kind, value)

    if kind.type == "list":
        if isinstance(value, list):
            items = []
            for item in value:
                if not isinstance(item, str):
                    raise invalid(key)
                items.append(item)
        elif isinstance(value, str):
            items = [t for t in (trim_whitespace(part) for part in value.split(",")) if t]
        else:
            raise invalid(key)
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            canonical = next((c for c in kind.choices if fold_text(c) == fold_text(item)), None)
            if canonical is None:
                raise invalid(key)
            if canonical not in seen:
                seen.add(canonical)
                result.append(canonical)
        return result

    if kind.type == "color":
        if not isinstance(value, str):
            raise invalid(key)
        text = trim_whitespace(value)
        if text.startswith("#"):
            text = text[1:]
        if len(text) != 6 or not all(_hex_digit(c) for c in text):
            raise invalid(key)
        return "#" + text.upper()

    # path
    if not isinstance(value, str):
        raise invalid(key)
    text = trim_whitespace_and_newlines(value)
    if text == "" or text.lower() == "auto":
        return ""
    if not text.startswith("/"):
        raise invalid(key)
    return text


def _normalize_number(key: SettingKey, kind: Kind, value: Any) -> int | float:
    parsed = _as_number(value)
    if parsed is None or not math.isfinite(parsed):
        raise invalid(key)
    if kind.step:
        parsed = round_half_away(parsed / kind.step) * kind.step
    if not kind.lo <= parsed <= kind.hi:
        raise invalid(key)
    return _tidy(parsed)


def validate_claude_binary_path(path: str, is_executable_file: Callable[[str], bool]) -> None:
    """SettingsStore.swift:128-132: ``claude.binaryPath`` must be empty (auto-detect) or an
    absolute path to an executable file. ``is_executable_file`` is supplied by the daemon."""
    if path != "" and not is_executable_file(path):
        raise ControlError("invalidValue", f"No executable file at {path}.")


# --- reading and writing the stored values (SettingsStore.swift, AutoSwitchSettings) --------------


def _stored_number(stored: Mapping[str, Any], store_key: str) -> float | None:
    return finite_number(stored.get(store_key))


def resolve_global_defaults(stored: Mapping[str, Any]) -> Defaults:
    """Spec 2.1 / 2.2 over ``settings.json``: ``autoSwitchThreshold`` (the base; unset or 0 reads
    90), then ``autoSwitchSessionThreshold`` / ``autoSwitchWeeklyThreshold`` (set only when
    present and a finite non-zero number, else they follow the base)."""
    # A number too large for a double is read as unset (the engine would raise on it).
    def number_or_unset(key: str) -> Any:
        value = stored.get(key)
        return finite_number(value) if is_number(value) else value

    return resolve_defaults(
        number_or_unset("autoSwitchThreshold"),
        number_or_unset("autoSwitchSessionThreshold"),
        number_or_unset("autoSwitchWeeklyThreshold"),
    )


def stored_strategy(stored: Mapping[str, Any]) -> Strategy:
    """AutoSwitchSettings.strategy (:500-502): unset or unrecognised reads as most room left."""
    value = stored.get("autoSwitchStrategy")
    try:
        return Strategy(value)
    except ValueError:
        return Strategy.MOST_ROOM


def stored_drain_within_hours(stored: Mapping[str, Any]) -> float:
    """:510-515. Kept within 1-72; unset or not a finite number reads as 24."""
    value = _stored_number(stored, "autoSwitchDrainWithinHours")
    if value is None:
        return FALLBACK_DRAIN_WITHIN_HOURS
    return min(max(value, DRAIN_HOURS_RANGE[0]), DRAIN_HOURS_RANGE[1])


def _stored_bool(stored: Mapping[str, Any], store_key: str, default: bool) -> bool:
    value = stored.get(store_key)
    return value if isinstance(value, bool) else default


@dataclass(frozen=True)
class AutoSwitchConfig:
    """The auto-switch settings as the engine and the status result need them."""

    enabled: bool
    defaults: Defaults
    on_fable: bool
    strategy: Strategy
    drain_early: bool
    drain_within_hours: float


def read_auto_switch(stored: Mapping[str, Any]) -> AutoSwitchConfig:
    return AutoSwitchConfig(
        enabled=_stored_bool(stored, "autoSwitchEnabled", False),
        defaults=resolve_global_defaults(stored),
        on_fable=_stored_bool(stored, "autoSwitchOnFable", True),
        strategy=stored_strategy(stored),
        drain_early=_stored_bool(stored, "autoSwitchDrainEarly", True),
        drain_within_hours=stored_drain_within_hours(stored),
    )


def read_setting(stored: Mapping[str, Any], key: SettingKey) -> Any:
    """What ``settings.get`` returns for ``key``: the stored value when it is valid, else the
    default. The three threshold keys return the EFFECTIVE number, never null."""
    name = key.name
    if name == THRESHOLD_KEY:
        return _tidy(resolve_global_defaults(stored).base)
    if name == SESSION_THRESHOLD_KEY:
        return _tidy(resolve_global_defaults(stored).session)
    if name == WEEKLY_THRESHOLD_KEY:
        return _tidy(resolve_global_defaults(stored).weekly)
    if name == STRATEGY_KEY:
        return stored_strategy(stored).value
    if name == "autoSwitch.drainWithinHours":
        return _tidy(stored_drain_within_hours(stored))
    value = stored.get(key.store_key)
    kind = key.kind
    if value is None:
        return list(key.default) if isinstance(key.default, list) else key.default
    if kind.type == "bool":
        return value if isinstance(value, bool) else key.default
    if kind.type == "choice":
        try:
            return normalize(key, value)
        except ControlError:
            return key.default
    if kind.type == "number":
        number = finite_number(value)
        return _tidy(number) if number is not None else key.default
    if kind.type == "list":
        try:
            return normalize(key, value)
        except ControlError:
            return list(key.default)
    if kind.type in ("color", "path"):
        return value if isinstance(value, str) else key.default
    return value


def read_all(stored: Mapping[str, Any]) -> dict[str, Any]:
    """``settings.get`` with no key: every setting, by name."""
    return {key.name: read_setting(stored, key) for key in KEYS}


def write_setting(stored: Mapping[str, Any], key: SettingKey, value: Any) -> dict[str, Any]:
    """A copy of ``stored`` with an ALREADY NORMALISED ``value`` written.

    D8: a legacy write of ``autoSwitch.threshold`` means "both windows" and clears the two
    global window keys. A window key written with ``None`` removes its stored key (follow the
    base). Side effects (timers, the autostart file, the updater) belong to the daemon.
    """
    updated = dict(stored)
    if key.name == THRESHOLD_KEY:
        updated.pop("autoSwitchSessionThreshold", None)
        updated.pop("autoSwitchWeeklyThreshold", None)
        updated[key.store_key] = value
    elif key.kind.type == "threshold":
        if value is None:
            updated.pop(key.store_key, None)
        else:
            updated[key.store_key] = value
    else:
        updated[key.store_key] = value
    return updated
