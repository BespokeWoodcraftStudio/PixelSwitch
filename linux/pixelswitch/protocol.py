"""The remote-control protocol, for the Linux daemon, CLI and MCP server.

Ports PixelSwitch/Control/ControlProtocol.swift, ControlAPI.swift (request shapes,
result builders, error texts) and AccountResolver.swift, with the 2.0 additions of spec
section 4 (two new methods, new optional fields, ``accountsChanged``). Recon:
docs/investigations/2026-10-24-linux-and-split-thresholds/recon/control.md sections 1-2.

JSON-RPC 2.0, one JSON object per line. The protocol version stays 2 (D11): every 2.0
change is an additive optional field or a new method, and ``status.protocolRevision`` is 3.

Encoding rules a second implementation must match (control.md 1.3), enforced by
:func:`dumps`: absent optionals are omitted (never ``null``, except the few nulls the
code builds explicitly); keys sorted, compact, slashes unescaped; integral numbers
without a fraction (``90``, never ``90.0``); dates ISO 8601 UTC at seconds precision
(``2026-09-21T14:13:20Z``); UUID strings uppercase.

Nothing in this module can carry a login token: it builds results from plain inputs
that never include one.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
import uuid as _uuid
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .engine import (
    Defaults,
    leave_at,
    resolve_rule,
    round_half_away,
    window_value,
)
from .usage_model import UsageResponse, is_number

# --- constants ---------------------------------------------------------------------------

PROTOCOL_VERSION = 2
#: Additive revision inside protocol 2 (D11); 3 is 2.0.
PROTOCOL_REVISION = 3
FEATURES = ["priority", "splitThresholds"]
#: A request or response line longer than this closes the connection.
MAX_LINE_BYTES = 1 << 20
#: ``sockaddr_un.sun_path`` is 108 bytes on Linux, including the terminating NUL.
MAX_SOCKET_PATH_BYTES = 107
EVENT_NOTIFICATION_METHOD = "event"

#: ControlMethod.allCases (ControlProtocol.swift:252-272) with the two 2.0 methods
#: placed after ``accounts.setThreshold``. The order is the ``methodNotFound`` candidate order.
METHODS = (
    "status.get",
    "accounts.list",
    "accounts.switch",
    "accounts.addCurrent",
    "accounts.signIn.start",
    "accounts.signIn.status",
    "accounts.signIn.submitCode",
    "accounts.signIn.cancel",
    "accounts.remove",
    "accounts.setLabel",
    "accounts.setThreshold",
    "accounts.setThresholds",
    "accounts.setPriority",
    "accounts.setOrder",
    "usage.get",
    "usage.refresh",
    "settings.get",
    "settings.set",
    "events.subscribe",
    "app.checkForUpdates",
    "app.quit",
)

#: The event types (control.md 1.9, plus ``accountsChanged`` from 2.0).
EVENT_TYPES = (
    "activeAccountChanged",
    "usageUpdated",
    "autoSwitched",
    "signInChanged",
    "error",
    "accountsChanged",
)

# --- JSON text ---------------------------------------------------------------------------


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def loads(text: "str | bytes | bytearray") -> Any:
    """Decode JSON; NaN and Infinity are refused, as Foundation refuses them."""
    return json.loads(text, parse_constant=_reject_constant)


def _dump(value: Any, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True:
        out.append("true")
    elif value is False:
        out.append("false")
    elif isinstance(value, int):
        out.append(str(int(value)))
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a non-finite number cannot be encoded")
        out.append(str(int(value)) if value.is_integer() and abs(value) < 1e15 else repr(value))
    elif isinstance(value, str):
        out.append(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, Mapping):
        out.append("{")
        first = True
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            if not first:
                out.append(",")
            first = False
            out.append(json.dumps(key, ensure_ascii=False))
            out.append(":")
            _dump(value[key], out)
        out.append("}")
    elif isinstance(value, (list, tuple)):
        out.append("[")
        for index, item in enumerate(value):
            if index:
                out.append(",")
            _dump(item, out)
        out.append("]")
    else:
        raise TypeError(f"{type(value).__name__} cannot be encoded as JSON")


def dumps(value: Any) -> str:
    """Compact JSON, keys sorted, slashes unescaped, integral numbers without a fraction
    (``ControlCoding.encoder``: ``.sortedKeys`` and ``.withoutEscapingSlashes``)."""
    out: list[str] = []
    _dump(value, out)
    return "".join(out)


def iso8601_utc(epoch: float) -> str:
    """``2026-09-21T14:13:20Z``: UTC, seconds precision (fractions are dropped, not rounded)."""
    whole = math.floor(epoch)
    days, remainder = divmod(whole, 86400)
    hour, rest = divmod(remainder, 3600)
    minute, second = divmod(rest, 60)
    # civil-from-days (Howard Hinnant), proleptic Gregorian
    shifted = days + 719468
    era = shifted // 146097
    day_of_era = shifted - era * 146097
    year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
    year = year_of_era + era * 400
    day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
    month_part = (5 * day_of_year + 2) // 153
    day = day_of_year - (153 * month_part + 2) // 5 + 1
    month = month_part + 3 if month_part < 10 else month_part - 9
    if month <= 2:
        year += 1
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z"


def uuid_string(identifier: str) -> str:
    """A UUID as Swift's ``uuidString`` writes it (uppercase, hyphenated). An id that is
    not a UUID is returned unchanged (the pure core allows any string id)."""
    try:
        return str(_uuid.UUID(identifier)).upper()
    except (ValueError, AttributeError, TypeError):
        return identifier


# --- errors ---------------------------------------------------------------------------------

#: ControlErrorKind.allCases and their codes (ControlProtocol.swift:188-224).
ERROR_CODES: dict[str, int] = {
    "parse": -32700,
    "invalidRequest": -32600,
    "methodNotFound": -32601,
    "invalidParams": -32602,
    "busy": -32001,
    "notFound": -32002,
    "ambiguous": -32003,
    "invalidValue": -32004,
    "claudeUnavailable": -32005,
    "protocolMismatch": -32006,
    "failed": -32000,
}


def kind_for_code(code: int) -> str:
    """Codes map back to kinds, an unknown one to ``failed``."""
    for kind, known in ERROR_CODES.items():
        if known == code:
            return kind
    return "failed"


class ControlError(Exception):
    """What went wrong, as the CLI and an AI need to tell it apart."""

    def __init__(self, kind: str, message: str, candidates: Sequence[str] = ()) -> None:
        super().__init__(message)
        if kind not in ERROR_CODES:
            raise ValueError(f"unknown error kind {kind!r}")
        self.kind = kind
        self.message = message
        self.candidates = list(candidates)

    @property
    def code(self) -> int:
        return ERROR_CODES[self.kind]

    def rpc(self) -> dict:
        """The JSON-RPC error object: ``data.kind`` is canonical, ``data.candidates`` optional."""
        data: dict[str, Any] = {"kind": self.kind}
        if self.candidates:
            data["candidates"] = list(self.candidates)
        return {"code": self.code, "message": self.message, "data": data}

    @classmethod
    def from_rpc(cls, error: Any) -> "ControlError":
        """The client's mapping (ControlProtocol.swift:243-247): by ``data.kind``, else by code."""
        if not isinstance(error, dict):
            return cls("failed", "PixelSwitch sent an error that could not be read.")
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        kind = data.get("kind")
        if not isinstance(kind, str) or kind not in ERROR_CODES:
            code = error.get("code")
            kind = kind_for_code(code) if isinstance(code, int) and not isinstance(code, bool) else "failed"
        raw = data.get("candidates")
        candidates = [c for c in raw if isinstance(c, str)] if isinstance(raw, list) else []
        message = error.get("message")
        return cls(kind, message if isinstance(message, str) else "", candidates)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, ControlError) and (self.kind, self.message, self.candidates)
                == (other.kind, other.message, other.candidates))

    def __hash__(self) -> int:
        return hash((self.kind, self.message, tuple(self.candidates)))

    def __repr__(self) -> str:
        return f"ControlError({self.kind!r}, {self.message!r}, {self.candidates!r})"


# --- the envelope (control.md 1.4) -------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """A decoded request. ``id`` is an int, a string, or None for a notification (no reply,
    ever, even on error)."""

    id: int | str | None
    method: str
    params: Any
    jsonrpc: str = "2.0"


_INT64 = (-(2**63), 2**63 - 1)


def _decode_id(value: Any) -> int | str | None:
    """``RPCID``: an Int (``1.0`` counts as ``1``), else a String; anything else makes the
    whole request undecodable."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("bad id")
    if isinstance(value, int):
        if not _INT64[0] <= value <= _INT64[1]:
            raise ValueError("bad id")
        return value
    if isinstance(value, float):
        if value.is_integer() and _INT64[0] <= value <= _INT64[1]:
            return int(value)
        raise ValueError("bad id")
    if isinstance(value, str):
        return value
    raise ValueError("bad id")


def decode_request(line: "str | bytes") -> Request:
    """One request line. ``jsonrpc`` (a string) and ``method`` (a string) are required; a
    request that lacks them, or is not a JSON object, gets a parse error. The caller then
    refuses ``jsonrpc != "2.0"`` as ``invalidRequest`` with the request's own id. Unknown
    keys, and unknown parameter keys, are ignored."""
    try:
        obj = loads(line)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        jsonrpc = obj["jsonrpc"]
        method = obj["method"]
        if not isinstance(jsonrpc, str) or not isinstance(method, str):
            raise ValueError("bad envelope")
        request_id = _decode_id(obj.get("id"))
    except (ValueError, KeyError, RecursionError):
        raise ControlError("parse", "That is not a JSON-RPC request.") from None
    return Request(request_id, method, obj.get("params"), jsonrpc)


def request_line(request_id: int | str | None, method: str, params: Any = None) -> str:
    """A request as one line (no newline). ``id`` and ``params`` are left out when None."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    return dumps(message)


def success_line(request_id: int | str | None, result: Any) -> str:
    """``{"id":7,"jsonrpc":"2.0","result":{...}}``. A reply whose id is None omits ``id``."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "result": result}
    if request_id is not None:
        message["id"] = request_id
    return dumps(message)


def failure_line(request_id: int | str | None, error: ControlError) -> str:
    """An error reply: ``error`` and never ``result``. A parse error (id None) omits ``id``."""
    message: dict[str, Any] = {"jsonrpc": "2.0", "error": error.rpc()}
    if request_id is not None:
        message["id"] = request_id
    return dumps(message)


def event_line(event_type: str, at: float, data: Any = None) -> str:
    """A pushed event: a notification line with no ``id`` (control.md 1.9)."""
    params: dict[str, Any] = {"at": iso8601_utc(at), "type": event_type}
    if data is not None:
        params["data"] = data
    return dumps({"jsonrpc": "2.0", "method": EVENT_NOTIFICATION_METHOD, "params": params})


@dataclass(frozen=True)
class Response:
    """A decoded reply line, for the client."""

    id: int | str | None
    has_result: bool
    result: Any
    error: Any


def decode_response(line: "str | bytes") -> Response | None:
    """The reply in ``line``, or None when it is not a JSON object (the client skips it)."""
    try:
        obj = loads(line)
    except (ValueError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    try:
        reply_id = _decode_id(obj.get("id"))
    except ValueError:
        reply_id = None
    return Response(reply_id, "result" in obj, obj.get("result"), obj.get("error"))


def decode_event(line: "str | bytes") -> dict | None:
    """The ``params`` of a pushed event line, or None when ``line`` is not one."""
    try:
        obj = loads(line)
    except (ValueError, RecursionError):
        return None
    if isinstance(obj, dict) and obj.get("method") == EVENT_NOTIFICATION_METHOD and isinstance(obj.get("params"), dict):
        return obj["params"]
    return None


def log_line(method: str, detail: str, outcome: str) -> str:
    """``[control] <method><detail> ok|<kind>|failed``. ``detail`` is ``" <accountUUID>"``,
    ``" reauthenticate <uuid>"``, ``" newAccount"`` or ``" <settingKey>"``: never an email,
    a link or an argument."""
    return f"[control] {method}{detail} {outcome}"


def method_not_found(method: str) -> ControlError:
    return ControlError("methodNotFound", f'There is no method called "{method}".', METHODS)


def unsupported_jsonrpc() -> ControlError:
    return ControlError("invalidRequest", 'jsonrpc must be "2.0".')


# --- request parameters (ControlAPI.swift:212-218 and the Codable structs) ------------------------

THRESHOLD_RANGE_TEXT_LEGACY = "A threshold must be 1–100, 0 for manual only, or null to follow the default."
THRESHOLD_RANGE_TEXT_WINDOW = (
    "A threshold must be 1–100, or null to follow the default. For manual only use accounts.setThreshold with 0."
)
PRIORITY_RANGE_TEXT = "A priority must be a whole number from 1 to 99, or null for none."


def _shape(method: str) -> ControlError:
    return ControlError("invalidParams", f"The parameters for {method} are missing or the wrong shape.")


def _object(method: str, params: Any) -> dict:
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise _shape(method)
    return params


def _required_str(method: str, obj: dict, key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise _shape(method)
    return value


def _optional_str(method: str, obj: dict, key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _shape(method)
    return value


def parse_params(method: str, params: Any) -> dict:
    """The shape check each method's parameter struct does (a missing or wrongly typed
    parameter is ``invalidParams`` "The parameters for <method> are missing or the wrong
    shape."). Returns the arguments by name; unknown keys are ignored.

    Presence matters for the 2.0 methods and is read from the raw object, never from a
    ``Double?``: ``accounts.setThresholds`` returns ``session`` and ``weekly`` only when the
    key was present (a value of ``None`` means "clear"); ``accounts.setPriority`` requires the
    ``priority`` key. Value ranges are checked afterwards by :func:`normalize_window_threshold`,
    :func:`normalize_priority` and :func:`normalize_legacy_threshold`, after the account is
    resolved (as the Mac's handlers do).
    """
    if method in ("accounts.switch", "accounts.remove"):
        return {"account": _required_str(method, _object(method, params), "account")}
    if method == "accounts.signIn.start":
        obj = _object(method, params)
        return {"account": _optional_str(method, obj, "account"), "open": _optional_str(method, obj, "open")}
    if method == "accounts.signIn.submitCode":
        return {"code": _required_str(method, _object(method, params), "code")}
    if method == "accounts.setLabel":
        obj = _object(method, params)
        return {"account": _required_str(method, obj, "account"), "label": _optional_str(method, obj, "label")}
    if method == "accounts.setThreshold":
        obj = _object(method, params)
        value = obj.get("threshold")
        if value is not None and not is_number(value):
            raise _shape(method)
        return {"account": _required_str(method, obj, "account"), "threshold": value}
    if method == "accounts.setThresholds":
        obj = _object(method, params)
        out: dict[str, Any] = {"account": _required_str(method, obj, "account")}
        for key in ("session", "weekly"):
            if key in obj:
                out[key] = obj[key]
        if "session" not in out and "weekly" not in out:
            raise _shape(method)
        return out
    if method == "accounts.setPriority":
        obj = _object(method, params)
        account = _required_str(method, obj, "account")
        if "priority" not in obj:
            raise _shape(method)
        return {"account": account, "priority": obj["priority"]}
    if method == "accounts.setOrder":
        obj = _object(method, params)
        accounts = obj.get("accounts")
        if not isinstance(accounts, list) or not all(isinstance(item, str) for item in accounts):
            raise _shape(method)
        return {"accounts": list(accounts)}
    if method == "usage.get":
        return {"account": _optional_str(method, _object(method, params), "account")}
    if method == "settings.get":
        return {"key": _optional_str(method, _object(method, params), "key")}
    if method == "settings.set":
        obj = _object(method, params)
        key = _required_str(method, obj, "key")
        if "value" not in obj:
            raise _shape(method)
        return {"key": key, "value": obj["value"]}
    return {}


def normalize_legacy_threshold(value: Any) -> float | None:
    """``accounts.setThreshold`` (ControlAPI.swift:149-161): None follows the default; a number
    is rounded to a whole number and must be 0-100 (0 = manual only)."""
    if value is None:
        return None
    if not is_number(value) or not math.isfinite(float(value)):
        raise ControlError("invalidValue", THRESHOLD_RANGE_TEXT_LEGACY)
    whole = round_half_away(float(value))
    if not 0 <= whole <= 100:
        raise ControlError("invalidValue", THRESHOLD_RANGE_TEXT_LEGACY)
    return whole


def normalize_window_threshold(value: Any) -> float | None:
    """``accounts.setThresholds`` (spec section 4): a number sets (rounded half away from
    zero, then 1-100), None clears to follow the default. Out of range, 0 or not a number
    is ``invalidValue``; manual only is ``accounts.setThreshold`` with 0."""
    if value is None:
        return None
    if not is_number(value) or not math.isfinite(float(value)):
        raise ControlError("invalidValue", THRESHOLD_RANGE_TEXT_WINDOW)
    whole = round_half_away(float(value))
    if not 1 <= whole <= 100:
        raise ControlError("invalidValue", THRESHOLD_RANGE_TEXT_WINDOW)
    return whole


def normalize_priority(value: Any) -> int | None:
    """``accounts.setPriority`` (spec section 4): a whole number 1-99 sets, None clears,
    anything else is ``invalidValue``. A JSON number such as ``3.0`` is a whole number."""
    if value is None:
        return None
    if isinstance(value, bool) or not is_number(value):
        raise ControlError("invalidValue", PRIORITY_RANGE_TEXT)
    number = float(value)
    if not math.isfinite(number) or not number.is_integer() or not 1 <= number <= 99:
        raise ControlError("invalidValue", PRIORITY_RANGE_TEXT)
    return int(number)


def normalize_label(label: str | None) -> str | None:
    """``accounts.setLabel`` (ControlAPI.swift:138-147): trimmed (whitespace and newlines);
    blank or None clears; more than 100 characters is ``invalidValue``."""
    if label is None:
        return None
    trimmed = trim_whitespace_and_newlines(label)
    if len(trimmed) > 100:
        raise ControlError("invalidValue", "A label can be at most 100 characters.")
    return trimmed or None


def normalize_open(open_value: str | None) -> str | None:
    """``accounts.signIn.start`` ``open`` (ControlAPI.swift:119): absent, blank or ``none``
    opens nothing (returns None); otherwise the trimmed text (``default`` or a browser name)."""
    if open_value is None:
        return None
    trimmed = _trim_whitespace(open_value)
    return None if trimmed == "" or trimmed.lower() == "none" else trimmed


# --- the account resolver (AccountResolver.swift) -------------------------------------------------


def _is_foundation_whitespace(char: str) -> bool:
    return char == "\t" or unicodedata.category(char) == "Zs"


def _is_foundation_newline(char: str) -> bool:
    return char in "\n\u000b\u000c\r\u0085  "


def _trim(text: str, predicate: Any) -> str:
    start, end = 0, len(text)
    while start < end and predicate(text[start]):
        start += 1
    while end > start and predicate(text[end - 1]):
        end -= 1
    return text[start:end]


def _trim_whitespace(text: str) -> str:
    """Swift ``trimmingCharacters(in: .whitespaces)``."""
    return _trim(text, _is_foundation_whitespace)


def trim_whitespace_and_newlines(text: str) -> str:
    """Swift ``trimmingCharacters(in: .whitespacesAndNewlines)``."""
    return _trim(text, lambda c: _is_foundation_whitespace(c) or _is_foundation_newline(c))


_POSITION = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)


def describe(account: Any) -> str:
    """AccountResolver.swift:40-43. An account named in an error: its email, then its label
    in parentheses if it has one."""
    label = getattr(account, "custom_label", None)
    if label:
        return f"{account.email} ({label})"
    return account.email


def resolve(reference: str, accounts: Sequence[Any]) -> Any:
    """Turns what a person or an AI typed into one account, or a clear error that lists
    the choices (AccountResolver.swift:10-32).

    Tried in order, each level case-insensitive: the account id, the email address, the
    label, then a 1-based position in the priority order. The first level with any match
    wins; two matches at that level is ``ambiguous``. A priority number is never a
    reference (spec section 3). ``accounts`` need ``id``, ``email`` and ``custom_label``.
    """
    ref = trim_whitespace_and_newlines(reference)
    if not ref:
        raise ControlError("invalidValue", "Name an account: its email, label, id or position.")
    wanted = ref.casefold()
    levels = (
        lambda a: a.id.casefold() == wanted or uuid_string(a.id).casefold() == wanted,
        lambda a: a.email.casefold() == wanted,
        lambda a: (getattr(a, "custom_label", None) or "").casefold() == wanted,
    )
    for matches in levels:
        hits = [a for a in accounts if matches(a)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ControlError("ambiguous", f'"{ref}" matches {len(hits)} accounts. Use the email address or id.',
                               [describe(a) for a in hits])
    if _POSITION.match(ref):
        position = int(ref)
        if 1 <= position <= len(accounts):
            return accounts[position - 1]
    raise ControlError("notFound", f'No account matches "{ref}".', [describe(a) for a in accounts])


def resolve_all(references: Iterable[str], accounts: Sequence[Any]) -> list[Any]:
    """Resolves every reference, refusing the whole list if any fails."""
    return [resolve(reference, accounts) for reference in references]


# --- results (ControlProtocol.swift:314-417, ControlAPI.swift ControlSnapshots) --------------------


def _put(out: dict, key: str, value: Any) -> None:
    """Absent optionals are omitted, never sent as null."""
    if value is not None:
        out[key] = value


def build_window_info(utilization: float | None, resets_at: str | None) -> dict | None:
    """A window is omitted unless it has a utilization or a reset (ControlAPI.swift:293-296).
    ``resetsAt`` is the string the usage API sent, passed through exactly."""
    if utilization is None and resets_at is None:
        return None
    out: dict[str, Any] = {}
    _put(out, "utilization", utilization)
    _put(out, "resetsAt", resets_at)
    return out


def build_usage_info(
    usage: UsageResponse | None, sampled_at: float | None = None, error: str | None = None
) -> dict | None:
    """ControlSnapshots.usageInfo (ControlAPI.swift:291-308): None (omit ``usage``) unless there
    is a reading or an error. ``session`` is ``five_hour``, ``weekly`` is ``seven_day`` and
    ``fable`` is the ``limits[]`` entry for Fable."""
    if usage is None and error is None:
        return None
    out: dict[str, Any] = {}
    if usage is not None:
        for key, window in (("session", usage.five_hour), ("weekly", usage.seven_day)):
            if window is not None:
                _put(out, key, build_window_info(window.utilization, window.resets_at))
        fable = usage.model_weekly_limit("Fable")
        if fable is not None:
            _put(out, "fable", build_window_info(fable.percent, fable.resets_at))
        if usage.extra_usage is not None:
            extra: dict[str, Any] = {}
            _put(extra, "enabled", usage.extra_usage.is_enabled)
            _put(extra, "monthlyLimit", usage.extra_usage.monthly_limit)
            _put(extra, "usedCredits", usage.extra_usage.used_credits)
            _put(extra, "utilization", usage.extra_usage.utilization)
            out["extraUsage"] = extra
    if sampled_at is not None:
        out["sampledAt"] = iso8601_utc(sampled_at)
    _put(out, "error", error)
    return out


def effective_thresholds(account: Any, defaults: Defaults) -> tuple[float, float, float]:
    """``(effectiveThreshold, effectiveSessionThreshold, effectiveWeeklyThreshold)``: where
    auto-switch moves off the account while it is active (also the defaults when manual only).
    ``effectiveThreshold`` is the lower of the two (spec section 4, Q1)."""
    rule = resolve_rule(account.switch_threshold, account.session_threshold, account.weekly_threshold, defaults)
    leave = leave_at(rule, defaults)
    return min(leave.session, leave.weekly), leave.session, leave.weekly


def build_account_info(
    account: Any,
    *,
    position: int,
    is_switchable: bool,
    defaults: Defaults,
    usage: dict | None = None,
) -> dict:
    """``AccountInfo`` (ControlProtocol.swift:382-401 plus spec section 4). ``position`` is the
    1-based index in the priority order. ``usage`` is :func:`build_usage_info`'s result."""
    rule = resolve_rule(account.switch_threshold, account.session_threshold, account.weekly_threshold, defaults)
    effective, effective_session, effective_weekly = effective_thresholds(account, defaults)
    info: dict[str, Any] = {
        "id": uuid_string(account.id),
        "email": account.email,
        "isActive": bool(account.is_active),
        "isSwitchable": bool(is_switchable),
        "position": int(position),
        "effectiveThreshold": effective,
        "effectiveSessionThreshold": effective_session,
        "effectiveWeeklyThreshold": effective_weekly,
        "manualOnly": rule.manual_only,
    }
    _put(info, "label", account.custom_label if account.custom_label else None)
    _put(info, "subscription", getattr(account, "display_subscription_type", None))
    _put(info, "threshold", account.switch_threshold)
    _put(info, "sessionThreshold", window_value(account.session_threshold))
    _put(info, "weeklyThreshold", window_value(account.weekly_threshold))
    _put(info, "priority", account.priority)
    _put(info, "usage", usage)
    return info


def build_auto_switch_info(
    *,
    enabled: bool,
    defaults: Defaults,
    on_fable: bool,
    strategy: str,
    drain_early: bool,
    drain_within_hours: float,
) -> dict:
    """``AutoSwitchInfo``. ``defaultThreshold`` stays the base; the two new fields are the
    effective global session and weekly defaults."""
    return {
        "enabled": bool(enabled),
        "defaultThreshold": defaults.base,
        "defaultSessionThreshold": defaults.session,
        "defaultWeeklyThreshold": defaults.weekly,
        "onFable": bool(on_fable),
        "strategy": strategy,
        "drainEarly": bool(drain_early),
        "drainWithinHours": drain_within_hours,
    }


def build_sign_in_info(
    *,
    sign_in_id: str,
    purpose: str,
    state: str,
    started_at: float,
    code_submitted: bool = False,
    account_id: str | None = None,
    email: str | None = None,
    message: str | None = None,
    result_account_id: str | None = None,
    automatic_link: str | None = None,
    manual_link: str | None = None,
    notice: str | None = None,
) -> dict:
    """``SignInInfo``. ``purpose`` is ``newAccount`` or ``reauthenticate``; ``state`` is
    ``starting``, ``waitingForUser``, ``completing``, ``succeeded``, ``failed`` or ``cancelled``."""
    info: dict[str, Any] = {
        "id": sign_in_id,
        "purpose": purpose,
        "state": state,
        "codeSubmitted": bool(code_submitted),
        "startedAt": iso8601_utc(started_at),
    }
    _put(info, "accountId", uuid_string(account_id) if account_id else None)
    _put(info, "email", email)
    _put(info, "message", message)
    _put(info, "resultAccountId", uuid_string(result_account_id) if result_account_id else None)
    _put(info, "automaticLink", automatic_link)
    _put(info, "manualLink", manual_link)
    _put(info, "notice", notice)
    return info


def build_status_info(
    *,
    app_version: str,
    active_account_id: str | None,
    active_account_email: str | None,
    account_count: int,
    claude_available: bool,
    last_refresh: float | None,
    auto_switch: dict,
    sign_in: dict | None = None,
    platform: str = "linux",
    test_mode: bool = False,
) -> dict:
    """``StatusInfo``. ``protocolVersion`` stays 2 so a shipped CLI accepts it;
    ``protocolRevision`` 3 and ``features`` tell a 2.0 CLI what it can call; ``testMode``
    is sent only when true (spec 5.7)."""
    info: dict[str, Any] = {
        "appVersion": app_version,
        "protocolVersion": PROTOCOL_VERSION,
        "protocolRevision": PROTOCOL_REVISION,
        "features": list(FEATURES),
        "platform": platform,
        "accountCount": int(account_count),
        "claudeAvailable": bool(claude_available),
        "autoSwitch": auto_switch,
    }
    _put(info, "activeAccountId", uuid_string(active_account_id) if active_account_id else None)
    _put(info, "activeAccountEmail", active_account_email)
    _put(info, "lastRefresh", iso8601_utc(last_refresh) if last_refresh is not None else None)
    _put(info, "signIn", sign_in)
    if test_mode:
        info["testMode"] = True
    return info


def build_usage_report(
    accounts: Sequence[dict], last_refresh: float | None = None, machine: dict | None = None
) -> dict:
    """``UsageReport``. ``machine`` (cost and activity for this machine) is omitted on Linux:
    the dashboard is not part of the Linux 2.0 (D15)."""
    report: dict[str, Any] = {"accounts": list(accounts)}
    _put(report, "machine", machine)
    _put(report, "lastRefresh", iso8601_utc(last_refresh) if last_refresh is not None else None)
    return report


# --- events (control.md 1.9, spec section 4) -----------------------------------------------------


def active_account_changed_data(account_id: str | None, email: str | None) -> dict:
    """``{accountId: s|null, email: s|null}``: these two are explicit nulls on the wire."""
    return {"accountId": uuid_string(account_id) if account_id else None, "email": email}


def auto_switched_data(
    from_id: str, to_id: str, limit: str, trigger: str, window: str | None = None
) -> dict:
    """``{from, to, limit: windows|fable, trigger, window?}``; ``window`` (``session`` or
    ``weekly``) is new in 2.0 and absent unless a window threshold fired."""
    data: dict[str, Any] = {"from": uuid_string(from_id), "to": uuid_string(to_id), "limit": limit, "trigger": trigger}
    _put(data, "window", window)
    return data


def sign_in_changed_data(sign_in: dict) -> dict:
    return {"signIn": sign_in}


def error_data(message: str) -> dict:
    return {"message": message}


def accounts_changed_data(accounts: Sequence[dict]) -> dict:
    """New in 2.0: sent after any change to the accounts list or to a field ``AccountInfo``
    carries apart from usage."""
    return {"accounts": list(accounts)}
