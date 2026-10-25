"""``pixelswitch mcp``: PixelSwitch as an MCP server on stdin and stdout, the Linux port of
PixelSwitchCLI/MCPServer.swift, so Claude on another machine (over ``ssh ... pixelswitch mcp``)
sees each action as a tool.

Speaks both MCP eras, exactly as the Mac server does:

* **Modern** (2026-07-28): stateless; every request carries its version in
  ``params._meta["io.modelcontextprotocol/protocolVersion"]``; ``server/discover`` lists what is
  supported; results carry ``resultType``.
* **Legacy** (2025-11-25 and earlier): an ``initialize`` handshake, then the same ``tools/list``
  and ``tools/call``.

The 20 tools, their schemas and descriptions are the Swift server's, character for character
(``tests/test_mcp.py`` compares them with ``PixelSwitchCLI/MCPServer.swift`` when that file is
present). A tool's ``arguments`` object is forwarded verbatim as the control call's params, so
a tool's property names ARE the control parameter names. The server writes nothing but protocol
messages to stdout; diagnostics go to stderr.

Pure: :class:`McpServer` takes the control call as a function, so it is unit-tested without a
daemon. Standard library only.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import protocol
from .cli import pretty
from .client import ClientError, ControlClient, check_protocol
from .protocol import ControlError

MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SUPPORTED_VERSIONS = [MODERN_VERSION] + LEGACY_VERSIONS
SERVER_NAME = "pixelswitch"

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

INSTRUCTIONS = (
    "Controls PixelSwitch, the Claude Code account switcher on the Mac this server runs on. "
    "list_accounts and get_usage show every account with its session, weekly and Fable usage and reset times; "
    "switch_account changes which Claude login Claude Code uses on that Mac; the set_* tools change priority, "
    "order, thresholds (one for both limits, or separate session and weekly), labels and settings. A priority "
    "number (P1, P2, …) says which account auto-switch tries first when it chooses by Priority; it is never an "
    "account reference. start_sign_in adds or re-signs an account and returns links: a person must "
    "open one in a browser and sign in to Anthropic (an AI cannot finish that step); poll get_sign_in_status until "
    "it reports succeeded, failed or cancelled. Accounts are named by email address, label, id, or position (1, 2, …), never by priority number."
)


@dataclass(frozen=True)
class Tool:
    """One MCP tool and the control method behind it."""

    name: str
    description: str
    properties: Mapping[str, Any]
    required: tuple[str, ...]
    method: str
    #: True: the tool's arguments are the control params (``pass``). False: it sends no params.
    passes: bool

    def params(self, arguments: Mapping[str, Any]) -> Any:
        return dict(arguments) if self.passes else None


def _prop(type_: str, description: str) -> dict:
    return {"type": type_, "description": description}


_ACCOUNT = {"account": _prop("string", "An email, label, id or position (1, 2, …) of the account, never a priority number.")}


def _merged(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict:
    """``account.merging(extra) { a, _ in a }``: the base wins a clash."""
    merged = dict(extra)
    merged.update(base)
    return merged


def _t(name: str, description: str, properties: Mapping[str, Any], required: tuple[str, ...], method: str, passes: bool) -> Tool:
    return Tool(name, description, properties, required, method, passes)


TOOLS: tuple[Tool, ...] = (
    _t("get_status", "PixelSwitch's version, the active account, auto-switch settings and any sign-in in progress.",
       {}, (), "status.get", False),
    _t("list_accounts", "Every account in priority order, with its priority number (absent when none; lower is tried first), its usage, its switch thresholds (manualOnly true: auto-switch never switches to it; effectiveSessionThreshold and effectiveWeeklyThreshold are where auto-switch moves off it while it is active, per window, and effectiveThreshold is the lower of the two), and whether it can be switched to. position is the 1-based place in the list, not the priority number.",
       {}, (), "accounts.list", False),
    _t("get_usage", "Session (5-hour), weekly and Fable usage with reset times for every account, or one; plus this Mac's cost and activity today. Non-active accounts are read in turn, so check sampledAt.",
       {"account": _prop("string", "Optional: only this account (an email, label, id or position, never a priority number).")},
       (), "usage.get", True),
    _t("refresh_usage", "Refresh usage now, then return it like get_usage.", {}, (), "usage.refresh", False),
    _t("switch_account", "Make this account the one Claude Code uses on that Mac. Returns when the switch is done. Works for a manual-only account too; auto-switch then moves off it only at the default threshold.",
       _ACCOUNT, ("account",), "accounts.switch", True),
    _t("add_current_account", "Save the account Claude Code is currently signed in as on that Mac.",
       {}, (), "accounts.addCurrent", False),
    _t("remove_account", "Remove an account and delete its saved login. Signing it back in needs a person at a browser.",
       _ACCOUNT, ("account",), "accounts.remove", True),
    _t("start_sign_in", "Start signing in a new account, or re-signing one (pass account). Returns links: automaticLink finishes by itself in any browser on that Mac; manualLink works on any device and shows a code to pass to submit_sign_in_code. A person must sign in; poll get_sign_in_status.",
       {"account": _prop("string", "Optional: re-sign this account (an email, label, id or position, never a priority number) instead of adding a new one."),
        "open": _prop("string", "Optional: \"default\" to open the link in the default browser on that Mac, or a browser's name such as \"Safari\". Omit to open nothing.")},
       (), "accounts.signIn.start", True),
    _t("get_sign_in_status", "The current or last sign-in: its state (starting, waitingForUser, completing, succeeded, failed, cancelled) and links.",
       {}, (), "accounts.signIn.status", False),
    _t("submit_sign_in_code", "Hand Claude Code the code#state the sign-in page showed on another device.",
       {"code": _prop("string", "The whole code, including the part after #.")}, ("code",), "accounts.signIn.submitCode", True),
    _t("cancel_sign_in", "Cancel the sign-in in progress. Nothing is changed.", {}, (), "accounts.signIn.cancel", False),
    _t("set_account_label", "Give an account a label, or clear it with an empty or null label.",
       _merged(_ACCOUNT, {"label": {"type": ["string", "null"], "description": "The label; empty or null clears it."}}),
       ("account",), "accounts.setLabel", True),
    _t("set_account_threshold", "Set the usage percentage (1–100) at which auto-switch moves off this account, for BOTH the 5-hour session and the weekly limit (it clears any separate session or weekly value; use set_account_thresholds for those). Another account is switched to only while it is under its own threshold by at least a point. 0 makes it manual only: auto-switch never switches to it; a person or switch_account still can, and while it is active auto-switch moves off it at the default threshold. Null or omitted: follow the default threshold. 100 uses the account until it is empty.",
       _merged(_ACCOUNT, {"threshold": {"type": ["number", "null"], "minimum": 0, "maximum": 100}}),
       ("account",), "accounts.setThreshold", True),
    _t("set_account_thresholds", "Set the usage percentage (1–100) at which auto-switch moves off this account for the 5-hour session limit and the weekly limit separately, for example session 90 and weekly 50. A key you leave out is left as it is; null makes that limit follow the default again. Fable follows the weekly limit. For manual only use set_account_threshold with 0; null on a manual-only account changes nothing.",
       _merged(_ACCOUNT, {
           "session": {"type": ["number", "null"], "minimum": 1, "maximum": 100,
                       "description": "The 5-hour session limit; null follows the default; omit to leave it."},
           "weekly": {"type": ["number", "null"], "minimum": 1, "maximum": 100,
                      "description": "The 7-day weekly limit (Fable too); null follows the default; omit to leave it."},
       }), ("account",), "accounts.setThresholds", True),
    _t("set_account_priority", "Give an account a priority number from 1 to 99; lower is tried first when auto-switch chooses by Priority (settings autoSwitch.strategy \"priority\"). Gaps are fine (1, 4, 9). Accounts with no number come after every numbered one. null clears the account's number. The reply is every account in the new order.",
       _merged(_ACCOUNT, {
           "priority": {"type": ["integer", "null"], "minimum": 1, "maximum": 99, "description": "1–99, lower first; null clears it."},
       }), ("account",), "accounts.setPriority", True),
    _t("set_account_order", "Set the whole order: list every account exactly once, first choice first. It numbers every account from 1 in that order (priority 1, 2, 3, …), replacing any priority numbers set before. Auto-switching by Priority follows it.",
       {"accounts": {"type": "array", "items": {"type": "string"}, "description": "Every account, in order."}},
       ("accounts",), "accounts.setOrder", True),
    _t("get_settings", "Every setting and its value, or one (key). Keys include autoSwitch.enabled, autoSwitch.threshold (both limits; setting it clears the two below), autoSwitch.sessionThreshold and autoSwitch.weeklyThreshold (50–100, or \"default\" to follow autoSwitch.threshold), autoSwitch.strategy (mostRoom, myOrder, resetsSoonest; \"priority\" is accepted for myOrder and means: lowest priority number first), autoSwitch.drainEarly, autoSwitch.drainWithinHours, refreshInterval, launchAtLogin, menuBar.modules, updates.automatic (install updates by itself).",
       {"key": _prop("string", "Optional: one setting.")}, (), "settings.get", True),
    _t("set_setting", "Change one setting. The value is checked; an invalid one is refused with the allowed values.",
       {"key": _prop("string", "The setting's name, as get_settings lists it."),
        "value": {"description": "The new value: true/false, a number, a string, or a list, as the setting takes."}},
       ("key", "value"), "settings.set", True),
    _t("check_for_updates", "Ask PixelSwitch to check for an update (its window opens on that Mac).",
       {}, (), "app.checkForUpdates", False),
    _t("quit_app", "Quit PixelSwitch on that Mac. The next tool call starts it again.", {}, (), "app.quit", False),
)

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


def tool_list() -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": {
                "type": "object",
                "properties": dict(tool.properties),
                "required": list(tool.required),
                "additionalProperties": False,
            },
        }
        for tool in TOOLS
    ]


# --- one message ------------------------------------------------------------------------------------


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _object(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _result_line(request_id: Any, result: Any) -> str:
    return protocol.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error_line(request_id: Any, code: int, message: str, data: Any = None) -> str:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return protocol.dumps({"jsonrpc": "2.0", "id": request_id, "error": error})


def _tool_result(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class McpServer:
    def __init__(self, server_version: str, call: Callable[[str, Any], Any]) -> None:
        self._server_version = server_version
        self._call = call

    @property
    def _server_info(self) -> dict:
        return {"name": SERVER_NAME, "version": self._server_version}

    def handle(self, line: str) -> str | None:
        """The reply line for one incoming line, or None (notifications get none)."""
        try:
            message = protocol.loads(line)
        except (ValueError, RecursionError):
            message = None
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            return _error_line(None, -32700, "Parse error")
        method = message["method"]
        request_id = message.get("id")
        if request_id is None:
            return None  # a notification, e.g. notifications/initialized
        params = _object(message.get("params"))
        meta = _object(params.get("_meta"))
        requested = _string(meta.get(META_VERSION))
        modern = requested is not None

        if requested is not None and method != "server/discover":
            if requested not in SUPPORTED_VERSIONS:
                return _error_line(request_id, -32022, "Unsupported protocol version",
                                   {"supported": list(SUPPORTED_VERSIONS), "requested": requested})
            if META_CAPABILITIES not in meta:
                return _error_line(request_id, -32602, f"Missing {META_CAPABILITIES} in _meta")

        if method == "initialize":
            asked = _string(params.get("protocolVersion")) or ""
            chosen = asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0]
            return _result_line(request_id, {
                "protocolVersion": chosen,
                "capabilities": {"tools": {}},
                "serverInfo": self._server_info,
                "instructions": INSTRUCTIONS,
            })
        if method == "server/discover":
            return _result_line(request_id, {
                "resultType": "complete",
                "supportedVersions": list(SUPPORTED_VERSIONS),
                "capabilities": {"tools": {}},
                "_meta": {META_SERVER_INFO: self._server_info},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result_line(request_id, self._decorate({}, modern))
        if method == "tools/list":
            return _result_line(request_id, self._decorate({"tools": tool_list()}, modern))
        if method == "tools/call":
            name = _string(params.get("name"))
            tool = TOOLS_BY_NAME.get(name) if name is not None else None
            if tool is None:
                return _error_line(request_id, -32602, f"Unknown tool: {name if name is not None else 'none given'}")
            arguments = _object(params.get("arguments"))
            return _result_line(request_id, self._decorate(self.run(tool, arguments), modern))
        return _error_line(request_id, -32601, f"Method not found: {method}")

    def run(self, tool: Tool, arguments: Mapping[str, Any]) -> dict:
        """Runs a tool. A failure is a tool result with ``isError``, so the model sees why (busy, no
        such account, invalid value) and can act on it."""
        for key in tool.required:
            if arguments.get(key) is None:
                return _tool_result(f"invalidParams: {tool.name} needs {key}.", True)
        try:
            result = self._call(tool.method, tool.params(arguments))
        except ControlError as error:
            text = f"{error.kind}: {error.message}"
            if error.candidates:
                text += "\nChoices: " + ", ".join(error.candidates)
            return _tool_result(text, True)
        except ClientError as error:
            return _tool_result(f"unreachable: {error.description}", True)
        except Exception as error:  # a tool must never end the session
            return _tool_result(f"failed: {error}", True)
        return {
            "content": [{"type": "text", "text": pretty(result)}],
            "structuredContent": result if isinstance(result, dict) else {"value": result},
            "isError": False,
        }

    def _decorate(self, result: dict, modern: bool) -> dict:
        if not modern:
            return result
        decorated = dict(result)
        decorated["resultType"] = "complete"
        decorated["_meta"] = {META_SERVER_INFO: self._server_info}
        return decorated


# --- stdio ------------------------------------------------------------------------------------------------


def make_stdio_call(client: ControlClient) -> Callable[[str, Any], Any]:
    """The control call the stdio server uses: connects (and starts the service) on first use,
    checks the protocol once, gives ``usage.refresh`` and ``accounts.switch`` 120 s and everything
    else 30 s (``accounts.addCurrent`` gets 120 s too, from the client's slow floor), and on a
    closed connection reconnects once and retries."""
    state = {"connected": False}

    def call(method: str, params: Any) -> Any:
        if not state["connected"]:
            client.connect()
            status = client.call("status.get", None, 30.0)
            check_protocol(status if isinstance(status, dict) else {})
            state["connected"] = True
        try:
            return client.call(method, params, 120.0 if method in ("usage.refresh", "accounts.switch") else 30.0)
        except ClientError as error:
            # The daemon may have restarted: reconnect once and retry.
            state["connected"] = False
            if error.kind == "closed":
                client.connect()
                state["connected"] = True
                return client.call(method, params, 120.0)
            raise

    return call


def serve(server: McpServer, stdin, stdout) -> int:
    """Reads newline-delimited JSON-RPC from ``stdin`` (a binary stream) and writes replies to ``stdout``."""
    for raw in stdin:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line.strip():
            continue
        reply = server.handle(line)
        if reply is not None:
            stdout.write((reply + "\n").encode("utf-8"))
            stdout.flush()
    return 0


def run_stdio(server_version: str, env: Mapping[str, str] | None = None) -> int:
    client = ControlClient(env=env)
    server = McpServer(server_version, make_stdio_call(client))
    try:
        return serve(server, sys.stdin.buffer, sys.stdout.buffer)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
