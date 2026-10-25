"""The control protocol: encoding, envelope, error kinds, parameters, the account resolver and the
result builders. Golden vectors ported from Tests/UnitTests/ControlTests.swift
(runControlProtocolTests, runControlResolverTests) and control.md section 1, plus the 2.0
additions of spec section 4."""
from __future__ import annotations

import json
import unittest

from pixelswitch import protocol as p
from pixelswitch.accounts import Account
from pixelswitch.engine import resolve_defaults
from pixelswitch.protocol import ControlError
from pixelswitch.usage_model import UsageResponse

A = Account(id="11111111-1111-1111-1111-111111111111", email="alice@work.com", display_name="Alice",
            subscription_type="max", is_active=True, custom_label="Work")
B = Account(id="22222222-2222-2222-2222-222222222222", email="bob@home.com", display_name="Bob",
            subscription_type="pro", switch_threshold=70)
C = Account(id="33333333-3333-3333-3333-333333333333", email="carol@work.com", display_name="Carol",
            custom_label="Work")

USAGE = {
    "five_hour": {"utilization": 42.0, "resets_at": "2026-09-25T18:00:00.000000+00:00"},
    "seven_day": {"utilization": 61.0, "resets_at": "2026-09-30T09:00:00+00:00"},
    "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None, "utilization": None},
    "limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 10, "severity": "normal",
                "resets_at": "2026-09-30T09:00:00+00:00",
                "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": True}],
}


class EncodingTests(unittest.TestCase):
    def test_keys_sorted_compact_slashes_unescaped(self):
        self.assertEqual(p.dumps({"b": 1, "a": {"d": "x/y", "c": [1, 2]}}), '{"a":{"c":[1,2],"d":"x/y"},"b":1}')

    def test_true_stays_a_boolean_and_one_a_number(self):
        self.assertEqual(p.dumps(True), "true")
        self.assertEqual(p.dumps(1), "1")
        self.assertEqual(p.dumps([True, 1, False, 0]), "[true,1,false,0]")

    def test_integral_numbers_print_without_a_fraction(self):
        self.assertEqual(p.dumps([90.0, 300, 72.5, 0.0, -3.0, 100.0]), "[90,300,72.5,0,-3,100]")
        self.assertEqual(p.dumps({"defaultThreshold": 90.0}), '{"defaultThreshold":90}')

    def test_non_finite_numbers_are_refused(self):
        for value in (float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                p.dumps(value)

    def test_unicode_is_not_escaped_and_controls_are(self):
        self.assertEqual(p.dumps("café ✓"), '"café ✓"')
        self.assertEqual(p.dumps('a"b\\c\n'), '"a\\"b\\\\c\\n"')

    def test_any_json_value_survives_a_round_trip(self):
        value = {"b": True, "n": 2.5, "s": "x", "a": [None, 1], "o": {}}
        self.assertEqual(p.loads(p.dumps(value)), value)

    def test_nan_in_input_is_refused(self):
        with self.assertRaises(ValueError):
            p.loads('{"a": NaN}')

    def test_a_non_string_key_is_refused(self):
        with self.assertRaises(TypeError):
            p.dumps({1: 2})

    def test_dates_are_iso_8601_utc_seconds(self):
        self.assertEqual(p.iso8601_utc(1_790_000_000), "2026-09-21T14:13:20Z")
        self.assertEqual(p.iso8601_utc(1_790_000_000.99), "2026-09-21T14:13:20Z")     # truncated, not rounded
        self.assertEqual(p.iso8601_utc(0), "1970-01-01T00:00:00Z")
        self.assertEqual(p.iso8601_utc(951_782_400), "2000-02-29T00:00:00Z")

    def test_uuids_are_uppercase(self):
        self.assertEqual(p.uuid_string("cb7797e5-5257-402b-80d6-adae1220d368"), "CB7797E5-5257-402B-80D6-ADAE1220D368")
        self.assertEqual(p.uuid_string("not-a-uuid"), "not-a-uuid")

    def test_an_absent_value_is_left_out_not_sent_as_null(self):
        info = p.build_window_info(5, None)
        self.assertEqual(info, {"utilization": 5})
        self.assertIsNone(p.build_window_info(None, None))
        self.assertEqual(p.dumps(p.build_window_info(None, "2026-09-25T18:00:00.000000+00:00")),
                         '{"resetsAt":"2026-09-25T18:00:00.000000+00:00"}')


class EnvelopeTests(unittest.TestCase):
    def test_a_success_reply_has_no_error_member(self):
        self.assertEqual(p.success_line(7, {"ok": True}), '{"id":7,"jsonrpc":"2.0","result":{"ok":true}}')

    def test_a_string_id_and_a_null_result(self):
        self.assertEqual(p.success_line("x", None), '{"id":"x","jsonrpc":"2.0","result":null}')

    def test_an_error_reply_carries_the_code_kind_and_candidates(self):
        line = p.failure_line("x", ControlError("ambiguous", "two", ["a", "b"]))
        self.assertNotIn('"result"', line)
        self.assertIn("-32003", line)
        self.assertIn('"candidates":["a","b"]', line)
        self.assertEqual(line, '{"error":{"code":-32003,"data":{"candidates":["a","b"],"kind":"ambiguous"},'
                               '"message":"two"},"id":"x","jsonrpc":"2.0"}')

    def test_a_parse_error_omits_the_id(self):
        line = p.failure_line(None, ControlError("parse", "That is not a JSON-RPC request."))
        self.assertEqual(line, '{"error":{"code":-32700,"data":{"kind":"parse"},"message":"That is not a JSON-RPC request."},'
                               '"jsonrpc":"2.0"}')

    def test_an_error_survives_the_trip_back_to_the_client(self):
        line = p.failure_line(3, ControlError("ambiguous", "two", ["a", "b"]))
        response = p.decode_response(line)
        self.assertEqual(ControlError.from_rpc(response.error), ControlError("ambiguous", "two", ["a", "b"]))

    def test_every_kind_has_its_own_code(self):
        codes = list(p.ERROR_CODES.values())
        self.assertEqual(len(set(codes)), len(codes))
        self.assertEqual(p.ERROR_CODES["parse"], -32700)
        self.assertEqual(p.ERROR_CODES["invalidRequest"], -32600)
        self.assertEqual(p.ERROR_CODES["methodNotFound"], -32601)
        self.assertEqual(p.ERROR_CODES["invalidParams"], -32602)
        self.assertEqual([p.ERROR_CODES[k] for k in ("busy", "notFound", "ambiguous", "invalidValue",
                                                      "claudeUnavailable", "protocolMismatch", "failed")],
                         [-32001, -32002, -32003, -32004, -32005, -32006, -32000])

    def test_codes_map_back_to_kinds_and_unknown_ones_to_failed(self):
        self.assertEqual(p.kind_for_code(-32001), "busy")
        self.assertEqual(p.kind_for_code(12345), "failed")

    def test_the_client_maps_by_data_kind_else_by_code(self):
        self.assertEqual(ControlError.from_rpc({"code": -32000, "message": "m", "data": {"kind": "busy"}}).kind, "busy")
        self.assertEqual(ControlError.from_rpc({"code": -32002, "message": "m"}).kind, "notFound")
        self.assertEqual(ControlError.from_rpc({"code": 5, "message": "m", "data": {"kind": "nonsense"}}).kind, "failed")
        self.assertEqual(ControlError.from_rpc({"code": -32003, "message": "m",
                                                "data": {"candidates": ["a", 5, "b"]}}).candidates, ["a", "b"])

    def test_unknown_error_kind_is_refused_at_construction(self):
        with self.assertRaises(ValueError):
            ControlError("nope", "x")


class RequestDecodingTests(unittest.TestCase):
    def test_a_normal_request(self):
        r = p.decode_request('{"jsonrpc":"2.0","id":7,"method":"status.get","params":{"a":1}}')
        self.assertEqual((r.id, r.method, r.params, r.jsonrpc), (7, "status.get", {"a": 1}, "2.0"))

    def test_params_are_optional(self):
        self.assertIsNone(p.decode_request('{"jsonrpc":"2.0","id":1,"method":"m"}').params)
        self.assertIsNone(p.decode_request('{"jsonrpc":"2.0","id":1,"method":"m","params":null}').params)

    def test_id_absent_or_null_is_a_notification(self):
        self.assertIsNone(p.decode_request('{"jsonrpc":"2.0","method":"m"}').id)
        self.assertIsNone(p.decode_request('{"jsonrpc":"2.0","id":null,"method":"m"}').id)

    def test_string_id_and_whole_float_id(self):
        self.assertEqual(p.decode_request('{"jsonrpc":"2.0","id":"abc","method":"m"}').id, "abc")
        self.assertEqual(p.decode_request('{"jsonrpc":"2.0","id":1.0,"method":"m"}').id, 1)

    def test_a_request_that_cannot_be_decoded_is_a_parse_error(self):
        bad = [
            "{not json", "", "[]", "5", "null", '"x"',
            '{"id":1,"method":"m"}',                              # jsonrpc is required
            '{"jsonrpc":"2.0","id":1}',                           # method is required
            '{"jsonrpc":2,"id":1,"method":"m"}',
            '{"jsonrpc":"2.0","id":1,"method":5}',
            '{"jsonrpc":"2.0","id":1.5,"method":"m"}',            # a fractional id
            '{"jsonrpc":"2.0","id":true,"method":"m"}',
            '{"jsonrpc":"2.0","id":[1],"method":"m"}',
            '{"jsonrpc":"2.0","id":99999999999999999999,"method":"m"}',
        ]
        for line in bad:
            with self.assertRaises(ControlError, msg=line) as caught:
                p.decode_request(line)
            self.assertEqual(caught.exception.kind, "parse", line)

    def test_a_wrong_jsonrpc_decodes_so_the_caller_can_answer_invalid_request(self):
        r = p.decode_request('{"jsonrpc":"1.0","id":1,"method":"status.get"}')
        self.assertEqual(r.jsonrpc, "1.0")
        error = p.unsupported_jsonrpc()
        self.assertEqual((error.kind, error.message, error.code), ("invalidRequest", 'jsonrpc must be "2.0".', -32600))

    def test_unknown_keys_are_ignored(self):
        r = p.decode_request('{"jsonrpc":"2.0","id":1,"method":"m","extra":[1],"params":{"acount":"1"}}')
        self.assertEqual(r.params, {"acount": "1"})

    def test_bytes_are_accepted(self):
        self.assertEqual(p.decode_request(b'{"jsonrpc":"2.0","id":1,"method":"m"}').id, 1)

    def test_request_line_leaves_out_id_and_params_when_none(self):
        self.assertEqual(p.request_line(1, "status.get"), '{"id":1,"jsonrpc":"2.0","method":"status.get"}')
        self.assertEqual(p.request_line(None, "m", {"a": 1}), '{"jsonrpc":"2.0","method":"m","params":{"a":1}}')

    def test_method_not_found_lists_every_method(self):
        error = p.method_not_found("accounts.teleport")
        self.assertEqual(error.kind, "methodNotFound")
        self.assertEqual(error.message, 'There is no method called "accounts.teleport".')
        self.assertEqual(error.candidates, list(p.METHODS))


class MethodAndEventTests(unittest.TestCase):
    def test_the_method_names(self):
        self.assertEqual(len(p.METHODS), 21)
        self.assertEqual(len(set(p.METHODS)), 21)
        old = ["status.get", "accounts.list", "accounts.switch", "accounts.addCurrent", "accounts.signIn.start",
               "accounts.signIn.status", "accounts.signIn.submitCode", "accounts.signIn.cancel", "accounts.remove",
               "accounts.setLabel", "accounts.setThreshold", "accounts.setOrder", "usage.get", "usage.refresh",
               "settings.get", "settings.set", "events.subscribe", "app.checkForUpdates", "app.quit"]
        self.assertEqual([m for m in p.METHODS if m in old], old)         # the 19 of 1.9, in the Mac's order
        self.assertEqual(sorted(set(p.METHODS) - set(old)), ["accounts.setPriority", "accounts.setThresholds"])

    def test_protocol_stays_two_and_the_revision_is_three(self):
        self.assertEqual((p.PROTOCOL_VERSION, p.PROTOCOL_REVISION), (2, 3))
        self.assertEqual(p.FEATURES, ["priority", "splitThresholds"])

    def test_limits(self):
        self.assertEqual(p.MAX_LINE_BYTES, 1 << 20)
        self.assertEqual(p.MAX_SOCKET_PATH_BYTES, 107)

    def test_an_event_is_a_notification_with_no_id(self):
        line = p.event_line("usageUpdated", 1_790_000_000)
        # Every event carries a data object, as the Mac's does: usageUpdated sends {}.
        self.assertEqual(line, '{"jsonrpc":"2.0","method":"event","params":{"at":"2026-09-21T14:13:20Z","data":{},"type":"usageUpdated"}}')
        self.assertEqual(p.decode_event(line), {"at": "2026-09-21T14:13:20Z", "data": {}, "type": "usageUpdated"})
        self.assertEqual(line, p.event_line("usageUpdated", 1_790_000_000, p.usage_updated_data()))
        self.assertIsNone(p.decode_response(line).id)

    def test_event_data_is_sorted_and_present_only_when_given(self):
        line = p.event_line("autoSwitched", 0, p.auto_switched_data(A.id, B.id, "windows", "threshold", "weekly", relaxed=False))
        params = p.decode_event(line)
        self.assertEqual(params["data"], {"from": A.id.upper(), "to": B.id.upper(), "limit": "windows",
                                          "trigger": "threshold", "window": "weekly", "relaxed": False})
        self.assertNotIn("window", p.auto_switched_data("a", "b", "fable", "threshold", relaxed=False))

    def test_active_account_changed_sends_explicit_nulls(self):
        self.assertEqual(p.dumps(p.active_account_changed_data(None, None)), '{"accountId":null,"email":null}')

    def test_decode_event_ignores_other_lines(self):
        self.assertIsNone(p.decode_event('{"jsonrpc":"2.0","id":1,"result":{}}'))
        self.assertIsNone(p.decode_event("nope"))

    def test_the_log_line_names_methods_and_outcomes_never_an_email(self):
        self.assertEqual(p.log_line("accounts.switch", " 22222222-2222-2222-2222-222222222222", "ok"),
                         "[control] accounts.switch 22222222-2222-2222-2222-222222222222 ok")
        self.assertEqual(p.log_line("settings.set", " autoSwitch.enabled", "invalidValue"),
                         "[control] settings.set autoSwitch.enabled invalidValue")


class ParamsTests(unittest.TestCase):
    def shape(self, method, params):
        with self.assertRaises(ControlError) as caught:
            p.parse_params(method, params)
        self.assertEqual(caught.exception.kind, "invalidParams")
        self.assertEqual(caught.exception.message, f"The parameters for {method} are missing or the wrong shape.")

    def test_account_methods(self):
        self.assertEqual(p.parse_params("accounts.switch", {"account": "1"}), {"account": "1"})
        self.assertEqual(p.parse_params("accounts.remove", {"account": "x", "extra": 1}), {"account": "x"})
        self.shape("accounts.switch", {"acount": "1"})                # a misspelt parameter
        self.shape("accounts.switch", {"account": 1})
        self.shape("accounts.switch", None)
        self.shape("accounts.switch", [])

    def test_optional_account_and_open(self):
        self.assertEqual(p.parse_params("accounts.signIn.start", None), {"account": None, "open": None})
        self.assertEqual(p.parse_params("accounts.signIn.start", {"account": "2", "open": "Safari"}),
                         {"account": "2", "open": "Safari"})
        self.shape("accounts.signIn.start", {"open": 5})
        self.assertEqual(p.parse_params("usage.get", {}), {"account": None})
        self.assertEqual(p.parse_params("settings.get", {"key": "refreshInterval"}), {"key": "refreshInterval"})
        self.shape("settings.get", {"key": []})

    def test_label_and_code(self):
        self.assertEqual(p.parse_params("accounts.setLabel", {"account": "1", "label": None}),
                         {"account": "1", "label": None})
        self.assertEqual(p.parse_params("accounts.setLabel", {"account": "1"}), {"account": "1", "label": None})
        self.shape("accounts.setLabel", {"account": "1", "label": 5})
        self.assertEqual(p.parse_params("accounts.signIn.submitCode", {"code": "abc#def"}), {"code": "abc#def"})
        self.shape("accounts.signIn.submitCode", {})

    def test_set_threshold(self):
        self.assertEqual(p.parse_params("accounts.setThreshold", {"account": "1", "threshold": 72.6}),
                         {"account": "1", "threshold": 72.6})
        self.assertEqual(p.parse_params("accounts.setThreshold", {"account": "1"}), {"account": "1", "threshold": None})
        self.assertEqual(p.parse_params("accounts.setThreshold", {"account": "1", "threshold": None})["threshold"], None)
        self.shape("accounts.setThreshold", {"account": "1", "threshold": "70"})
        self.shape("accounts.setThreshold", {"account": "1", "threshold": True})

    def test_set_order(self):
        self.assertEqual(p.parse_params("accounts.setOrder", {"accounts": ["a", "b"]}), {"accounts": ["a", "b"]})
        self.shape("accounts.setOrder", {"accounts": ["a", 2]})
        self.shape("accounts.setOrder", {"accounts": "a"})
        self.shape("accounts.setOrder", {})

    def test_set_settings(self):
        self.assertEqual(p.parse_params("settings.set", {"key": "k", "value": None}), {"key": "k", "value": None})
        self.shape("settings.set", {"key": "k"})
        self.shape("settings.set", {"value": 1})

    def test_methods_without_parameters_ignore_them(self):
        for method in ("status.get", "accounts.list", "accounts.addCurrent", "accounts.signIn.status",
                       "accounts.signIn.cancel", "usage.refresh", "events.subscribe", "app.checkForUpdates", "app.quit"):
            self.assertEqual(p.parse_params(method, {"anything": 1}), {})
            self.assertEqual(p.parse_params(method, [1, 2]), {})

    # --- the two 2.0 methods: presence is read from the raw object ---

    def test_set_thresholds_presence(self):
        self.assertEqual(p.parse_params("accounts.setThresholds", {"account": "1", "session": 90}),
                         {"account": "1", "session": 90})
        self.assertEqual(p.parse_params("accounts.setThresholds", {"account": "1", "weekly": None}),
                         {"account": "1", "weekly": None})
        both = p.parse_params("accounts.setThresholds", {"account": "1", "session": None, "weekly": 50})
        self.assertEqual(both, {"account": "1", "session": None, "weekly": 50})
        self.assertNotIn("weekly", p.parse_params("accounts.setThresholds", {"account": "1", "session": 1}))

    def test_set_thresholds_needs_a_window(self):
        self.shape("accounts.setThresholds", {"account": "1"})
        self.shape("accounts.setThresholds", {"account": "1", "threshold": 70})
        self.shape("accounts.setThresholds", {"session": 70})

    def test_set_priority_needs_the_key(self):
        self.assertEqual(p.parse_params("accounts.setPriority", {"account": "1", "priority": 3}),
                         {"account": "1", "priority": 3})
        self.assertEqual(p.parse_params("accounts.setPriority", {"account": "1", "priority": None}),
                         {"account": "1", "priority": None})
        self.shape("accounts.setPriority", {"account": "1"})
        self.shape("accounts.setPriority", {"priority": 1})

    # --- value checks (after the account is resolved) ---

    def test_legacy_threshold_values(self):
        n = p.normalize_legacy_threshold
        self.assertIsNone(n(None))
        self.assertEqual([n(72.6), n(0), n(1), n(35), n(0.4), n(0.6), n(100), n(100.4), n(-0.4)],
                         [73, 0, 1, 35, 0, 1, 100, 100, 0])
        for bad in (-1, 101, 100.5, "70", True, float("nan"), float("inf")):
            with self.assertRaises(ControlError, msg=bad) as caught:
                n(bad)
            self.assertEqual(caught.exception.kind, "invalidValue")
            self.assertEqual(caught.exception.message,
                             "A threshold must be 1–100, 0 for manual only, or null to follow the default.")

    def test_window_threshold_values(self):
        n = p.normalize_window_threshold
        self.assertIsNone(n(None))
        self.assertEqual([n(1), n(90), n(100), n(72.5), n(0.5), n(99.5)], [1, 90, 100, 73, 1, 100])
        for bad in (0, 0.4, -5, 100.5, 101, "90", True, [], {}, float("nan")):
            with self.assertRaises(ControlError, msg=bad) as caught:
                n(bad)
            self.assertEqual(caught.exception.kind, "invalidValue")
            self.assertEqual(caught.exception.message,
                             "A threshold must be 1–100, or null to follow the default. "
                             "For manual only use accounts.setThreshold with 0.")

    def test_priority_values(self):
        n = p.normalize_priority
        self.assertIsNone(n(None))
        self.assertEqual([n(1), n(99), n(3), n(3.0)], [1, 99, 3, 3])
        for bad in (0, 100, -1, 1.5, "3", True, False, [], float("nan"), float("inf")):
            with self.assertRaises(ControlError, msg=bad) as caught:
                n(bad)
            self.assertEqual(caught.exception.kind, "invalidValue")
            self.assertEqual(caught.exception.message, "A priority must be a whole number from 1 to 99, or null for none.")

    def test_label_rules(self):
        self.assertEqual(p.normalize_label("  Side  "), "Side")
        self.assertIsNone(p.normalize_label("   "))
        self.assertIsNone(p.normalize_label(None))
        self.assertEqual(p.normalize_label("x" * 100), "x" * 100)
        with self.assertRaises(ControlError) as caught:
            p.normalize_label("x" * 101)
        self.assertEqual((caught.exception.kind, caught.exception.message),
                         ("invalidValue", "A label can be at most 100 characters."))

    def test_sign_in_open_rules(self):
        self.assertIsNone(p.normalize_open(None))
        self.assertIsNone(p.normalize_open(" none "))
        self.assertIsNone(p.normalize_open("NONE"))
        self.assertIsNone(p.normalize_open(""))
        self.assertEqual(p.normalize_open(" default "), "default")
        self.assertEqual(p.normalize_open("Safari"), "Safari")


class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.accounts = [A, B, C]

    def resolved(self, ref, accounts=None):
        try:
            return p.resolve(ref, accounts or self.accounts).email
        except ControlError as error:
            return f"{error.kind}:{len(error.candidates)}"

    def test_an_id_picks_the_account(self):
        self.assertEqual(self.resolved("22222222-2222-2222-2222-222222222222"), "bob@home.com")

    def test_ids_are_case_insensitive(self):
        self.assertEqual(self.resolved("22222222-2222-2222-2222-222222222222".upper()), "bob@home.com")
        lower = type(A)(id="cb7797e5-5257-402b-80d6-adae1220d368", email="l@x.com")
        self.assertEqual(self.resolved("CB7797E5-5257-402B-80D6-ADAE1220D368", [lower]), "l@x.com")

    def test_an_email_case_insensitive_and_trimmed(self):
        self.assertEqual(self.resolved("  BOB@home.com "), "bob@home.com")

    def test_a_label_two_accounts_share_is_ambiguous(self):
        self.assertEqual(self.resolved("work"), "ambiguous:2")
        with self.assertRaises(ControlError) as caught:
            p.resolve("work", self.accounts)
        self.assertEqual(caught.exception.candidates, ["alice@work.com (Work)", "carol@work.com (Work)"])
        self.assertEqual(caught.exception.message, '"work" matches 2 accounts. Use the email address or id.')

    def test_a_position_picks_from_the_priority_order(self):
        self.assertEqual(self.resolved("3"), "carol@work.com")
        self.assertEqual(self.resolved("1"), "alice@work.com")

    def test_no_match_lists_every_account(self):
        self.assertEqual(self.resolved("4"), "notFound:3")
        self.assertEqual(self.resolved("0"), "notFound:3")
        self.assertEqual(self.resolved("nobody"), "notFound:3")
        with self.assertRaises(ControlError) as caught:
            p.resolve("nobody", self.accounts)
        self.assertEqual(caught.exception.message, 'No account matches "nobody".')

    def test_an_empty_name_is_refused(self):
        self.assertEqual(self.resolved(""), "invalidValue:0")
        self.assertEqual(self.resolved("   "), "invalidValue:0")
        with self.assertRaises(ControlError) as caught:
            p.resolve("", self.accounts)
        self.assertEqual(caught.exception.message, "Name an account: its email, label, id or position.")

    def test_a_label_that_looks_like_a_number_wins_over_the_position(self):
        labelled = type(A)(id="X", email="x@y.com", custom_label="2")
        self.assertEqual(p.resolve("2", [A, labelled]).email, "x@y.com")

    def test_describe(self):
        self.assertEqual(p.describe(A), "alice@work.com (Work)")
        self.assertEqual(p.describe(B), "bob@home.com")

    def test_one_bad_name_refuses_the_whole_list(self):
        with self.assertRaises(ControlError):
            p.resolve_all(["1", "nobody"], self.accounts)
        self.assertEqual([a.email for a in p.resolve_all(["3", "bob@home.com"], self.accounts)],
                         ["carol@work.com", "bob@home.com"])

    def test_a_priority_number_is_never_a_reference(self):
        """Spec section 3: references stay id, email, label and position."""
        numbered = [type(A)(id="P", email="p@x.com", priority=1), type(A)(id="Q", email="q@x.com", priority=7)]
        self.assertEqual(p.resolve("1", numbered).email, "p@x.com")   # position 1, which happens to be priority 1
        self.assertEqual(p.resolve("2", numbered).email, "q@x.com")   # position 2, not priority 2
        with self.assertRaises(ControlError):
            p.resolve("7", numbered)                                   # priority 7 is not position 7

    def test_position_text_must_be_ascii_digits(self):
        for ref in ("1.0", "١", "1_0", "1e0"):
            with self.assertRaises(ControlError, msg=ref):
                p.resolve(ref, self.accounts)
        self.assertEqual(p.resolve("+2", self.accounts).email, "bob@home.com")


class BuilderTests(unittest.TestCase):
    D = resolve_defaults(90)

    def info(self, account, **kw):
        kw.setdefault("position", 1)
        kw.setdefault("is_switchable", True)
        kw.setdefault("defaults", self.D)
        return p.build_account_info(account, **kw)

    def test_a_plain_account_omits_every_absent_optional(self):
        plain = type(A)(id="1", email="a@x.com")
        self.assertEqual(p.dumps(self.info(plain)),
                         '{"effectiveSessionThreshold":90,"effectiveThreshold":90,"effectiveWeeklyThreshold":90,'
                         '"email":"a@x.com","id":"1","isActive":false,"isSwitchable":true,"manualOnly":false,"position":1}')

    def test_a_full_account(self):
        full = type(A)(id="11111111-1111-1111-1111-111111111111", email="a@x.com", subscription_type="max",
                       is_active=True, custom_label="Work", switch_threshold=None, session_threshold=90,
                       weekly_threshold=50, priority=2)
        info = self.info(full, position=2)
        self.assertEqual(info["id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual((info["label"], info["subscription"], info["isActive"]), ("Work", "Max", True))
        self.assertEqual((info["sessionThreshold"], info["weeklyThreshold"], info["priority"]), (90, 50, 2))
        self.assertEqual((info["effectiveSessionThreshold"], info["effectiveWeeklyThreshold"]), (90, 50))
        self.assertEqual(info["effectiveThreshold"], 50)            # the lower of the two (Q1)
        self.assertNotIn("threshold", info)                          # no single value of its own
        self.assertEqual(info["position"], 2)

    def test_old_fields_keep_their_meaning(self):
        own = self.info(B)
        self.assertEqual((own["threshold"], own["effectiveThreshold"], own["manualOnly"]), (70, 70, False))
        follow = self.info(A)
        self.assertNotIn("threshold", follow)
        self.assertEqual(follow["effectiveThreshold"], 90)

    def test_manual_only_reports_the_default_as_effective(self):
        manual = type(A)(id="M", email="m@x.com", switch_threshold=0)
        info = self.info(manual, defaults=resolve_defaults(90, session=80, weekly=60))
        self.assertEqual((info["threshold"], info["manualOnly"], info["effectiveThreshold"]), (0, True, 60))
        self.assertEqual((info["effectiveSessionThreshold"], info["effectiveWeeklyThreshold"]), (80, 60))

    def test_window_values_that_are_not_usable_are_absent(self):
        weird = type(A)(id="W", email="w@x.com", session_threshold=0, weekly_threshold=-5)
        info = self.info(weird)
        self.assertNotIn("sessionThreshold", info)
        self.assertNotIn("weeklyThreshold", info)

    def test_label_is_never_empty(self):
        self.assertNotIn("label", self.info(type(A)(id="1", email="a@x.com", custom_label="")))

    def test_usage_info(self):
        usage = UsageResponse.from_json(USAGE)
        info = p.build_usage_info(usage, 1_790_000_100, None)
        self.assertEqual(info["session"], {"utilization": 42.0, "resetsAt": "2026-09-25T18:00:00.000000+00:00"})
        self.assertEqual(info["weekly"]["resetsAt"], "2026-09-30T09:00:00+00:00")     # passed through exactly
        self.assertEqual(info["fable"], {"utilization": 10, "resetsAt": "2026-09-30T09:00:00+00:00"})
        self.assertEqual(info["extraUsage"], {"enabled": False})
        self.assertEqual(info["sampledAt"], "2026-09-21T14:15:00Z")
        self.assertNotIn("error", info)

    def test_usage_info_with_only_an_error(self):
        info = p.build_usage_info(None, None, "Token expired. Switch to refresh.")
        self.assertEqual(info, {"error": "Token expired. Switch to refresh."})
        self.assertIsNone(p.build_usage_info(None, None, None))

    def test_a_usage_with_no_windows_is_an_empty_object(self):
        self.assertEqual(p.build_usage_info(UsageResponse.from_json({}), None, None), {})

    def test_status_info(self):
        auto = p.build_auto_switch_info(enabled=True, defaults=resolve_defaults(90, weekly=50), on_fable=True,
                                        strategy="mostRoom", drain_early=True, drain_within_hours=24)
        status = p.build_status_info(app_version="2.0", active_account_id=A.id, active_account_email=A.email,
                                     account_count=3, claude_available=True, last_refresh=1_790_000_000,
                                     auto_switch=auto)
        self.assertEqual(status["protocolVersion"], 2)
        self.assertEqual(status["protocolRevision"], 3)
        self.assertEqual(status["features"], ["priority", "splitThresholds"])
        self.assertEqual(status["platform"], "linux")
        self.assertEqual(status["lastRefresh"], "2026-09-21T14:13:20Z")
        self.assertNotIn("testMode", status)
        self.assertNotIn("signIn", status)
        self.assertEqual(status["activeAccountId"], A.id)

    def test_test_mode_is_sent_only_when_true(self):
        auto = p.build_auto_switch_info(enabled=False, defaults=self.D, on_fable=True, strategy="mostRoom",
                                        drain_early=True, drain_within_hours=24)
        status = p.build_status_info(app_version="2.0", active_account_id=None, active_account_email=None,
                                     account_count=0, claude_available=False, last_refresh=None, auto_switch=auto,
                                     test_mode=True)
        self.assertIs(status["testMode"], True)
        for absent in ("activeAccountId", "activeAccountEmail", "lastRefresh"):
            self.assertNotIn(absent, status)

    def test_auto_switch_info(self):
        info = p.build_auto_switch_info(enabled=True, defaults=resolve_defaults(98, session=90, weekly=50),
                                        on_fable=False, strategy="myOrder", drain_early=False, drain_within_hours=12)
        self.assertEqual(info, {"enabled": True, "defaultThreshold": 98, "defaultSessionThreshold": 90,
                                "defaultWeeklyThreshold": 50, "onFable": False, "strategy": "myOrder",
                                "drainEarly": False, "drainWithinHours": 12})

    def test_with_only_a_base_stored_both_effective_defaults_read_it(self):
        info = p.build_auto_switch_info(enabled=True, defaults=resolve_defaults(98), on_fable=True,
                                        strategy="mostRoom", drain_early=True, drain_within_hours=24)
        self.assertEqual((info["defaultThreshold"], info["defaultSessionThreshold"], info["defaultWeeklyThreshold"]),
                         (98, 98, 98))

    def test_sign_in_info(self):
        info = p.build_sign_in_info(sign_in_id="S1", purpose="newAccount", state="starting", started_at=1_790_000_000)
        self.assertEqual(info, {"id": "S1", "purpose": "newAccount", "state": "starting", "codeSubmitted": False,
                                "startedAt": "2026-09-21T14:13:20Z"})

    def test_usage_report_omits_machine_on_linux(self):
        report = p.build_usage_report([self.info(A)], 1_790_000_000)
        self.assertNotIn("machine", report)
        self.assertEqual(report["lastRefresh"], "2026-09-21T14:13:20Z")

    def test_no_result_carries_a_token_shaped_key(self):
        usage = UsageResponse.from_json(USAGE)
        info = self.info(A, usage=p.build_usage_info(usage, 1, None))
        text = p.dumps({"status": {}, "accounts": [info]})
        for forbidden in ("token", "credential", "secret", "refresh", "password"):
            for key in _keys(json.loads(text)):
                if key not in ("lastRefresh", "refreshInterval"):
                    self.assertNotIn(forbidden, key.lower(), key)
        self.assertNotIn("sk-ant-", text)


def _keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


if __name__ == "__main__":
    unittest.main()
