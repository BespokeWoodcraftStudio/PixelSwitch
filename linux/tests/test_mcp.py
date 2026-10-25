"""The MCP server: ControlTests.swift's and ControlSplitTests.swift's MCP golden tests ported (both
eras, the 20 tools, verbatim argument pass-through, isError, structuredContent), a comparison of the
tools and instructions with ``PixelSwitchCLI/MCPServer.swift`` when it is in the checkout, and the
stdio loop over a real socket and a pipe."""
from __future__ import annotations

import io
import os
import re
import unittest

from pixelswitch import mcp, protocol as p
from pixelswitch.client import ClientError, ControlClient
from pixelswitch.mcp import McpServer
from pixelswitch.protocol import ControlError

from .lc_support import REPO_ROOT, DaemonTestCase, status_payload

MODERN_META = '"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}'
EXPECTED_TOOLS = {
    "get_status", "list_accounts", "get_usage", "refresh_usage", "switch_account", "add_current_account", "remove_account",
    "start_sign_in", "get_sign_in_status", "submit_sign_in_code", "cancel_sign_in", "set_account_label", "set_account_threshold",
    "set_account_thresholds", "set_account_priority", "set_account_order", "get_settings", "set_setting", "check_for_updates", "quit_app",
}


class Rig:
    def __init__(self):
        self.calls: list[str] = []

        def call(method, params):
            self.calls.append(f"{method} {p.dumps(params) if params is not None else 'nil'}")
            if method == "accounts.switch" and isinstance(params, dict) and params.get("account") == "busy":
                raise ControlError("busy", "A switch is in progress.")
            return {"ok": True}

        self.server = McpServer("2.0", call)

    def reply(self, text: str):
        out = self.server.handle(text)
        return None if out is None else p.loads(out)


class McpTests(unittest.TestCase):
    def setUp(self):
        self.rig = Rig()
        self.reply = self.rig.reply

    def tools(self, line='{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{' + MODERN_META + "}}"):
        return self.reply(line)["result"]["tools"]

    def tool(self, name):
        return next(t for t in self.tools() if t["name"] == name)

    # -- the handshake, both eras --

    def test_a_legacy_client_gets_the_version_it_asked_for(self):
        r = self.reply('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"claude","version":"9"}}}')
        self.assertEqual(r["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(r["result"]["capabilities"]["tools"], {})
        self.assertEqual(r["result"]["serverInfo"], {"name": "pixelswitch", "version": "2.0"})
        self.assertIn("never by priority number", r["result"]["instructions"])

    def test_an_unknown_legacy_version_gets_the_newest_legacy_one(self):
        r = self.reply('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-01-01"}}')
        self.assertEqual(r["result"]["protocolVersion"], "2025-11-25")

    def test_notifications_get_no_reply(self):
        self.assertIsNone(self.rig.server.handle('{"jsonrpc":"2.0","method":"notifications/initialized"}'))
        self.assertIsNone(self.rig.server.handle('{"jsonrpc":"2.0","id":null,"method":"ping"}'))

    def test_server_discover(self):
        r = self.reply('{"jsonrpc":"2.0","id":"d","method":"server/discover","params":{' + MODERN_META + "}}")
        self.assertEqual(r["id"], "d")
        self.assertEqual(r["result"]["supportedVersions"][0], "2026-07-28")
        self.assertEqual(r["result"]["supportedVersions"], ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"])
        self.assertEqual(r["result"]["resultType"], "complete")
        self.assertEqual(r["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["version"], "2.0")

    def test_server_discover_needs_no_meta(self):
        r = self.reply('{"jsonrpc":"2.0","id":1,"method":"server/discover"}')
        self.assertEqual(r["result"]["resultType"], "complete")

    def test_a_modern_tools_list_has_all_20_tools_and_a_result_type(self):
        r = self.reply('{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{' + MODERN_META + "}}")
        tools = r["result"]["tools"]
        self.assertEqual(len(tools), 20)
        self.assertEqual(r["result"]["resultType"], "complete")
        self.assertEqual({t["name"] for t in tools}, EXPECTED_TOOLS)
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertEqual(t["inputSchema"]["additionalProperties"], False)
            self.assertTrue(t["description"])

    def test_a_legacy_tools_list_has_no_result_type(self):
        r = self.reply('{"jsonrpc":"2.0","id":3,"method":"tools/list"}')
        self.assertNotIn("resultType", r["result"])
        self.assertEqual(len(r["result"]["tools"]), 20)

    def test_an_unsupported_modern_version_gets_the_supported_list(self):
        r = self.reply('{"jsonrpc":"2.0","id":4,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"1900-01-01","io.modelcontextprotocol/clientCapabilities":{}}}}')
        self.assertEqual(r["error"]["code"], -32022)
        self.assertEqual(r["error"]["message"], "Unsupported protocol version")
        self.assertEqual(r["error"]["data"]["requested"], "1900-01-01")
        self.assertEqual(r["error"]["data"]["supported"][0], "2026-07-28")

    def test_a_modern_request_without_client_capabilities_is_invalid_params(self):
        r = self.reply('{"jsonrpc":"2.0","id":5,"method":"tools/list","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}}}')
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(r["error"]["message"], "Missing io.modelcontextprotocol/clientCapabilities in _meta")

    def test_ping_unknown_methods_and_garbage(self):
        self.assertEqual(self.reply('{"jsonrpc":"2.0","id":11,"method":"ping"}')["result"], {})
        modern_ping = self.reply('{"jsonrpc":"2.0","id":11,"method":"ping","params":{' + MODERN_META + "}}")["result"]
        self.assertEqual(modern_ping["resultType"], "complete")
        r = self.reply('{"jsonrpc":"2.0","id":12,"method":"resources/list"}')
        self.assertEqual((r["error"]["code"], r["error"]["message"]), (-32601, "Method not found: resources/list"))
        for garbage in ("garbage", "[1,2]", '{"id":1}', '{"method":5,"id":1}'):
            r = self.reply(garbage)
            self.assertEqual((r["error"]["code"], r["error"]["message"], r["id"]), (-32700, "Parse error", None), garbage)

    # -- tool calls --

    def test_a_tool_call_runs_the_matching_control_method(self):
        r = self.reply('{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"switch_account","arguments":{"account":"bob@home.com"}}}')
        self.assertEqual(self.rig.calls[-1], 'accounts.switch {"account":"bob@home.com"}')
        self.assertIs(r["result"]["isError"], False)
        self.assertEqual(r["result"]["structuredContent"], {"ok": True})
        self.assertEqual(r["result"]["content"], [{"type": "text", "text": '{\n  "ok" : true\n}'}])

    def test_a_refused_call_is_a_tool_error_the_model_can_read(self):
        r = self.reply('{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"switch_account","arguments":{"account":"busy"}}}')
        self.assertIs(r["result"]["isError"], True)
        self.assertEqual(r["result"]["content"][0]["text"], "busy: A switch is in progress.")

    def test_choices_follow_the_error_text(self):
        server = McpServer("2.0", lambda m, params: (_ for _ in ()).throw(ControlError("notFound", "No account matches.", ["a@x.com", "b@x.com"])))
        out = p.loads(server.handle('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"switch_account","arguments":{"account":"z"}}}'))
        self.assertEqual(out["result"]["content"][0]["text"], "notFound: No account matches.\nChoices: a@x.com, b@x.com")

    def test_transport_problems_and_surprises_are_tool_errors(self):
        def unreachable(m, params):
            raise ClientError("unreachable", "nothing is listening at /x")

        def surprise(m, params):
            raise RuntimeError("boom")

        for call, text in ((unreachable, "unreachable: PixelSwitch is not reachable: nothing is listening at /x"), (surprise, "failed: boom")):
            out = p.loads(McpServer("2.0", call).handle('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_status"}}'))
            self.assertEqual((out["result"]["isError"], out["result"]["content"][0]["text"]), (True, text))

    def test_a_missing_required_argument_is_refused_before_calling_the_app(self):
        before = len(self.rig.calls)
        r = self.reply('{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"switch_account","arguments":{}}}')
        self.assertIs(r["result"]["isError"], True)
        self.assertEqual(r["result"]["content"][0]["text"], "invalidParams: switch_account needs account.")
        r = self.reply('{"jsonrpc":"2.0","id":8,"method":"tools/call","params":{"name":"switch_account","arguments":{"account":null}}}')
        self.assertIs(r["result"]["isError"], True)
        self.assertEqual(len(self.rig.calls), before)

    def test_a_tool_with_no_arguments_sends_no_params(self):
        self.reply('{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_status"}}')
        self.assertEqual(self.rig.calls[-1], "status.get nil")

    def test_unknown_tool_is_invalid_params(self):
        r = self.reply('{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"teleport"}}')
        self.assertEqual((r["error"]["code"], r["error"]["message"]), (-32602, "Unknown tool: teleport"))
        r = self.reply('{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{}}')
        self.assertEqual(r["error"]["message"], "Unknown tool: none given")

    def test_a_modern_tool_result_is_decorated(self):
        r = self.reply('{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"get_status",' + MODERN_META + "}}")
        self.assertEqual(r["result"]["resultType"], "complete")
        self.assertEqual(r["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "pixelswitch")

    def test_a_non_object_result_is_wrapped_in_structured_content(self):
        server = McpServer("2.0", lambda m, params: [1, 2])
        out = p.loads(server.handle('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_status"}}'))
        self.assertEqual(out["result"]["structuredContent"], {"value": [1, 2]})

    # -- 2.0 schemas --

    def test_set_account_threshold_takes_0_to_100_and_says_what_0_means(self):
        t = self.tool("set_account_threshold")
        prop = t["inputSchema"]["properties"]["threshold"]
        self.assertEqual((prop["minimum"], prop["maximum"]), (0, 100))
        self.assertIn("manual only", t["description"])
        self.assertIn("manualOnly", self.tool("list_accounts")["description"])
        self.assertIn("manual-only", self.tool("switch_account")["description"])

    def test_set_account_thresholds_schema(self):
        t = self.tool("set_account_thresholds")
        props = t["inputSchema"]["properties"]
        self.assertEqual(t["inputSchema"]["required"], ["account"])
        for key in ("session", "weekly"):
            self.assertEqual(props[key]["type"], ["number", "null"])
            self.assertEqual((props[key]["minimum"], props[key]["maximum"]), (1, 100))

    def test_set_account_priority_schema(self):
        t = self.tool("set_account_priority")
        prop = t["inputSchema"]["properties"]["priority"]
        self.assertEqual(t["inputSchema"]["required"], ["account"])
        self.assertEqual(prop["type"], ["integer", "null"])
        self.assertEqual((prop["minimum"], prop["maximum"]), (1, 99))
        self.assertIn("null clears", t["description"])

    def test_every_account_argument_says_it_is_never_a_priority_number(self):
        with_account = [t for t in self.tools() if "account" in t["inputSchema"]["properties"]]
        silent = [t["name"] for t in with_account if "never a priority number" not in t["inputSchema"]["properties"]["account"]["description"]]
        self.assertGreaterEqual(len(with_account), 8)
        self.assertEqual(silent, [])
        names = {t["name"] for t in with_account}
        self.assertTrue({"get_usage", "start_sign_in"} <= names)

    def test_arguments_are_forwarded_verbatim_nulls_included(self):
        self.reply('{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"set_account_thresholds","arguments":{"account":"2","session":90,"weekly":null}}}')
        self.assertEqual(self.rig.calls[-1], 'accounts.setThresholds {"account":"2","session":90,"weekly":null}')
        self.reply('{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"set_account_priority","arguments":{"account":"2","priority":null}}}')
        self.assertEqual(self.rig.calls[-1], 'accounts.setPriority {"account":"2","priority":null}')
        r = self.reply('{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"set_account_priority","arguments":{"priority":2}}}')
        self.assertIs(r["result"]["isError"], True)
        self.reply('{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"set_setting","arguments":{"key":"autoSwitch.sessionThreshold","value":"default"}}}')
        self.assertEqual(self.rig.calls[-1], 'settings.set {"key":"autoSwitch.sessionThreshold","value":"default"}')
        # A tool whose arguments are optional still passes the (empty) object through.
        self.reply('{"jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"get_usage"}}')
        self.assertEqual(self.rig.calls[-1], "usage.get {}")

    def test_the_2_0_descriptions_tell_the_2_0_story(self):
        self.assertIn("BOTH", self.tool("set_account_threshold")["description"])
        self.assertIn("numbers every account from 1", self.tool("set_account_order")["description"])
        self.assertIn("effectiveSessionThreshold", self.tool("list_accounts")["description"])
        words = self.tool("get_settings")["description"]
        self.assertIn("autoSwitch.sessionThreshold", words)
        self.assertIn('"priority" is accepted', words)
        self.assertIn('or "default" to follow autoSwitch.threshold', words)
        self.assertNotIn("null/", words)

    def test_tool_methods_are_the_control_methods(self):
        for tool in mcp.TOOLS:
            self.assertIn(tool.method, p.METHODS, tool.name)
        self.assertEqual(len(mcp.TOOLS), 20)


# --- the same text as the Swift server ----------------------------------------------------------------------

SWIFT_SOURCE = os.path.join(REPO_ROOT, "PixelSwitchCLI", "MCPServer.swift")


def swift_literals(text: str) -> list[str]:
    out = []
    for match in re.finditer(r'"((?:[^"\\\n]|\\.)*)"', text):
        out.append(re.sub(r"\\(.)", r"\1", match.group(1)))
    return out


@unittest.skipUnless(os.path.exists(SWIFT_SOURCE), "PixelSwitchCLI/MCPServer.swift is not in this checkout")
class SameAsSwiftTests(unittest.TestCase):
    def setUp(self):
        with open(SWIFT_SOURCE, encoding="utf-8") as handle:
            self.source = handle.read()

    def test_every_description_is_the_swift_one(self):
        region = self.source[self.source.index("static let tools"):self.source.index("private static func prop")]
        # The Swift text, after the one fixed list of Linux wordings (mcp.LINUX_TEXT): any other drift still fails.
        swift = {mcp.linux_wording(s) for s in swift_literals(region) if " " in s}
        python: set[str] = set()
        for tool in mcp.TOOLS:
            python.add(tool.description)
            for prop in tool.properties.values():
                if "description" in prop:
                    python.add(prop["description"])
        self.assertEqual(sorted(swift - python), [])
        self.assertEqual(sorted(python - swift), [])

    def test_names_methods_and_required_are_the_swift_ones(self):
        region = self.source[self.source.index("static let tools"):self.source.index("private static func prop")]
        blocks = re.findall(r'Tool\(name: "([a-z_]+)".*?required: (\[[^\]]*\]), method: \.([A-Za-z]+)', region, re.S)
        self.assertEqual(len(blocks), 20)
        by_name = {t.name: t for t in mcp.TOOLS}
        method_names = {"statusGet": "status.get", "accountsList": "accounts.list", "usageGet": "usage.get", "usageRefresh": "usage.refresh",
                        "accountsSwitch": "accounts.switch", "accountsAddCurrent": "accounts.addCurrent", "accountsRemove": "accounts.remove",
                        "signInStart": "accounts.signIn.start", "signInStatus": "accounts.signIn.status", "signInSubmitCode": "accounts.signIn.submitCode",
                        "signInCancel": "accounts.signIn.cancel", "accountsSetLabel": "accounts.setLabel", "accountsSetThreshold": "accounts.setThreshold",
                        "accountsSetThresholds": "accounts.setThresholds", "accountsSetPriority": "accounts.setPriority",
                        "accountsSetOrder": "accounts.setOrder", "settingsGet": "settings.get", "settingsSet": "settings.set",
                        "appCheckForUpdates": "app.checkForUpdates", "appQuit": "app.quit"}
        order = [name for name, _, _ in blocks]
        self.assertEqual(order, [t.name for t in mcp.TOOLS])
        for name, required, method in blocks:
            tool = by_name[name]
            self.assertEqual(tool.method, method_names[method], name)
            self.assertEqual(list(tool.required), re.findall(r'"([^"]+)"', required), name)

    def test_the_instructions_are_the_swift_ones(self):
        start = self.source.index('static let instructions = """') + len('static let instructions = """')
        end = self.source.index('"""', start)
        body = self.source[start:end].strip("\n")
        indent = min(len(line) - len(line.lstrip()) for line in body.split("\n") if line.strip())
        lines = [line[indent:] for line in body.split("\n")]
        joined = "\n".join(lines).replace("\\\n", "").rstrip()
        self.assertEqual(mcp.INSTRUCTIONS, mcp.linux_wording(joined))

    def test_the_linux_wordings_are_all_needed_and_leave_no_mac_behind(self):
        text = " ".join(swift_literals(self.source)) + self.source.replace("\\\n", "")  # literals and the instructions block
        for old, new in mcp.LINUX_TEXT:
            self.assertIn(old, text, f"{old!r} is no longer in the Swift text; drop it from LINUX_TEXT")
            self.assertNotIn(old, mcp.INSTRUCTIONS + " ".join(t.description for t in mcp.TOOLS))
        everything = [mcp.INSTRUCTIONS] + [t.description for t in mcp.TOOLS]
        for tool in mcp.TOOLS:
            everything += [prop["description"] for prop in tool.properties.values() if "description" in prop]
        for line in everything:
            self.assertNotIn("Mac", line)
            self.assertNotIn("Safari", line)
        by_name = {t.name: t for t in mcp.TOOLS}
        self.assertEqual(by_name["check_for_updates"].description,
                         "Ask PixelSwitch to check for an update; a newer version installs by itself.")
        self.assertNotIn("cost", by_name["get_usage"].description)
        self.assertIn('such as "Firefox"', by_name["start_sign_in"].properties["open"]["description"])

    def test_types_limits_and_which_tools_pass_their_arguments_are_the_swift_ones(self):
        region = self.source[self.source.index("static let tools"):self.source.index("private static func prop")]
        blocks = re.split(r"(?=\bTool\(name: \")", region)[1:]
        self.assertEqual(len(blocks), 20)
        kinds = r'(?:prop\(|\.string\()"(string|number|integer|array|null|object|boolean)"'
        for block in blocks:
            name = re.match(r'Tool\(name: "([a-z_]+)"', block).group(1)
            tool = next(t for t in mcp.TOOLS if t.name == name)
            swift_types = re.findall(kinds, block)
            if re.search(r"properties: account\b|account\.merging", block):
                swift_types.append("string")  # the shared account property
            python_types: list[str] = []
            for prop in tool.properties.values():
                declared = prop.get("type")
                python_types += declared if isinstance(declared, list) else ([declared] if declared else [])
                if "items" in prop:
                    python_types.append(prop["items"]["type"])
            self.assertEqual(sorted(swift_types), sorted(python_types), name)
            swift_min = sorted(float(v) for v in re.findall(r'"minimum": \.number\(([0-9.]+)\)', block))
            swift_max = sorted(float(v) for v in re.findall(r'"maximum": \.number\(([0-9.]+)\)', block))
            self.assertEqual(swift_min, sorted(float(p["minimum"]) for p in tool.properties.values() if "minimum" in p), name)
            self.assertEqual(swift_max, sorted(float(p["maximum"]) for p in tool.properties.values() if "maximum" in p), name)
            self.assertEqual("params: pass" in block, tool.passes, name)


# --- the stdio loop -------------------------------------------------------------------------------------


class StdioTests(DaemonTestCase):
    def serve_stdio(self, lines: list[str], daemon_results=None):
        client = ControlClient(env=self.env())
        server = McpServer("2.0", mcp.make_stdio_call(client))
        stdin = io.BytesIO(("\n".join(lines) + "\n").encode())
        stdout = io.BytesIO()
        try:
            self.assertEqual(mcp.serve(server, stdin, stdout), 0)
        finally:
            client.disconnect()
        return [p.loads(l) for l in stdout.getvalue().decode().splitlines()]

    def test_the_handshake_two_listings_and_a_real_call(self):
        self.serve({"accounts.list": {"accounts": []}})
        replies = self.serve_stdio([
            '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"it","version":"1"}}}',
            '{"jsonrpc":"2.0","method":"notifications/initialized"}',
            "",
            '{"jsonrpc":"2.0","id":2,"method":"tools/list"}',
            '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_status"}}',
            '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"list_accounts"}}',
        ])
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-11-25")
        self.assertEqual(len(replies[1]["result"]["tools"]), 20)
        self.assertIs(replies[2]["result"]["isError"], False)
        self.assertEqual(replies[2]["result"]["structuredContent"]["appVersion"], "2.0")
        self.assertEqual(replies[3]["result"]["structuredContent"], {"accounts": []})

    def test_nothing_listening_is_an_unreachable_tool_error_not_a_crash(self):
        replies = self.serve_stdio(['{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_status"}}'])
        self.assertIs(replies[0]["result"]["isError"], True)
        self.assertTrue(replies[0]["result"]["content"][0]["text"].startswith("unreachable: PixelSwitch is not reachable: nothing is listening at"))

    def test_a_daemon_speaking_another_protocol_is_a_tool_error(self):
        self.serve({"status.get": status_payload(protocolVersion=99)})
        replies = self.serve_stdio(['{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"list_accounts"}}'])
        self.assertTrue(replies[0]["result"]["content"][0]["text"].startswith("protocolMismatch: PixelSwitch 2.0 speaks remote-control protocol 99"))

    def test_the_timeouts_are_120_for_refresh_and_switch_and_30_otherwise(self):
        seen = []

        class Spy:
            def connect(self):
                pass

            def call(self, method, params=None, timeout=30.0, **kwargs):
                seen.append((method, timeout))
                return status_payload() if method == "status.get" else {}

        call = mcp.make_stdio_call(Spy())
        for method in ("usage.refresh", "accounts.switch", "accounts.list"):
            call(method, None)
        self.assertEqual(seen, [("status.get", 30.0), ("usage.refresh", 120.0), ("accounts.switch", 120.0), ("accounts.list", 30.0)])

    def test_a_closed_connection_reconnects_once_and_retries(self):
        state = {"calls": 0, "connects": 0}

        class Flaky:
            def connect(self):
                state["connects"] += 1

            def call(self, method, params=None, timeout=30.0, **kwargs):
                if method == "status.get":
                    return status_payload()
                state["calls"] += 1
                if state["calls"] == 1:
                    raise ClientError("closed")
                return {"ok": True}

        call = mcp.make_stdio_call(Flaky())
        self.assertEqual(call("accounts.list", None), {"ok": True})
        self.assertEqual(state, {"calls": 2, "connects": 2})


if __name__ == "__main__":
    unittest.main()
