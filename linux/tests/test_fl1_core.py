"""Lane FL1: the pure-core findings of the LA review (accounts, protocol, settings catalog, logins)
that the daemon fixes ride on. Each test names the Mac code or test it ports."""
from __future__ import annotations

import json
import unicodedata
import unittest
from dataclasses import replace

from pixelswitch import accounts as acc
from pixelswitch import credtools, logins
from pixelswitch import protocol as p
from pixelswitch import settings_catalog as catalog
from pixelswitch.finite import finite_number
from tests.support import acct, defaults_of
from tests.test_accounts import names


def cred(access="a", refresh="r", expires=1_790_000_000_000, top=None):
    oauth = {"accessToken": access, "refreshToken": refresh}
    if expires is not None:
        oauth["expiresAt"] = expires
    document = {"claudeAiOauth": oauth}
    document.update(top or {})
    return json.dumps(document)


class PriorityTests(unittest.TestCase):
    def test_a_stored_number_outside_1_to_99_counts_as_none(self):
        """TwoWindowTests.swift:250-251, AccountOrder.sortedByPriority/validPriority."""
        got = acc.sorted_by_priority([acct("A", priority=0), acct("B", priority=100), acct("C", priority=5)])
        self.assertEqual(names(got), "CAB")
        self.assertEqual(names(acc.sorted_by_priority([acct("Z", priority=-3), acct("N", priority=5)])), "NZ")

    def test_the_account_info_omits_an_out_of_range_priority(self):
        info = p.build_account_info(acct("A", priority=0), position=1, is_switchable=True, defaults=defaults_of())
        self.assertNotIn("priority", info)
        info = p.build_account_info(acct("A", priority=7), position=1, is_switchable=True, defaults=defaults_of())
        self.assertEqual(info["priority"], 7)

    def test_numbers_stop_at_99(self):
        """AccountOrder.numberedInOrder: min(offset + 1, 99)."""
        many = [acct(f"a{i}") for i in range(120)]
        numbered = acc.set_order(many, [a.id for a in many])
        self.assertEqual(numbered[98].priority, 99)
        self.assertEqual(numbered[99].priority, 99)
        self.assertEqual(numbered[119].priority, 99)
        self.assertEqual(numbered[0].priority, 1)

    def test_setting_the_number_an_account_has_already_changes_nothing(self):
        """TwoWindowTests.swift:278-279: not even its recency; a later clock changes nothing."""
        base = acc.setting_priority([acct("A"), acct("B")], "A", 2)
        base = acc.setting_priority(base, "B", 2)
        self.assertEqual(names(base), "BA")
        for _ in range(3):
            self.assertEqual(acc.setting_priority(base, "A", 2), base)
        self.assertEqual(acc.setting_priority([acct("A")], "A", None), [acct("A")])   # none to none

    def test_the_fallback_order(self):
        """TwoWindowTests.swift:318-327 removalOrderTests."""
        accounts = [acct("a", switch=0, priority=1), acct("b", priority=3), acct("c", priority=2), acct("d")]
        self.assertEqual(names(acc.fallback_order(accounts)), "cbda")
        self.assertEqual(acc.fallback(accounts).id, "c")
        self.assertEqual(acc.fallback([acct("m", switch=0), acct("n", switch=0)]).id, "m")
        self.assertEqual(acc.fallback_order([]), [])
        self.assertIsNone(acc.fallback([]))


class StrategyAliasTests(unittest.TestCase):
    def test_the_alias_is_trimmed_first(self):
        """ControlSplitTests.swift:206."""
        key = catalog.named("autoSwitch.strategy")
        for text in (" Priority ", "\tpriority", "PRIORITY", "priority\t"):
            self.assertEqual(catalog.normalize(key, text), "myOrder", repr(text))


class HugeNumberTests(unittest.TestCase):
    HUGE = 10 ** 400

    def test_finite_number_never_raises(self):
        self.assertIsNone(finite_number(self.HUGE))
        self.assertIsNone(finite_number(float("nan")))
        self.assertIsNone(finite_number(True))
        self.assertEqual(finite_number(3), 3.0)

    def test_login_problem_does_not_raise_on_a_huge_expires_at(self):
        text = '{"claudeAiOauth":{"accessToken":"a","refreshToken":"r","expiresAt":' + "9" * 400 + "}}"
        self.assertEqual(logins.login_problem(text), "expiresAt is not a positive number")
        self.assertFalse(logins.usable_login(text))
        self.assertIsNotNone(logins.login_from_credential(text))
        self.assertIsNone(logins.login_from_credential(text).expires_at)
        self.assertTrue(logins.same_oauth_tokens(text, text))

    def test_the_other_float_conversions_do_not_raise(self):
        self.assertIsNone(acc.whole_threshold(self.HUGE))
        with self.assertRaises(p.ControlError):
            p.normalize_legacy_threshold(self.HUGE)
        with self.assertRaises(p.ControlError):
            p.normalize_window_threshold(self.HUGE)
        with self.assertRaises(p.ControlError):
            p.normalize_priority(self.HUGE)
        self.assertEqual(acc.load_accounts([{"id": "A", "email": "a@x.com", "switchThreshold": self.HUGE}])[0].switch_threshold, None)
        with self.assertRaises(p.ControlError):
            catalog.normalize(catalog.named("autoSwitch.threshold"), self.HUGE)
        self.assertEqual(catalog.read_setting({"autoSwitchThreshold": self.HUGE}, catalog.named("autoSwitch.threshold")), 90)

    def test_applying_renewal_expiry_ignores_a_huge_number(self):
        self.assertEqual(credtools.applying_renewal_expiry({"refreshTokenExpiresAt": self.HUGE}, self.HUGE, 1000.0),
                         {"refreshTokenExpiresAt": self.HUGE})


class SurrogateTests(unittest.TestCase):
    def test_an_escaped_lone_surrogate_is_not_a_usable_login(self):
        """Foundation refuses the document (LA review, logins.py:33)."""
        text = '{"claudeAiOauth":{"accessToken":"\\ud800","refreshToken":"r"}}'
        self.assertFalse(logins.usable_login(text))
        self.assertIsNone(logins.loads_object(text))

    def test_a_surrogate_in_the_live_credential_merges_as_target_only(self):
        live = '{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"},"mcpOAuth":{"x":"\\ud800"}}'
        result = logins.credential_for_switch(live, cred("t", "tr"))
        self.assertEqual(result.kind, "targetOnly")
        result.credential.encode("utf-8")


class NanTests(unittest.TestCase):
    def test_nan_anywhere_in_the_credential_is_refused_like_the_mac(self):
        """LA review, tests P2 (mutant L6): not only in expiresAt, where isfinite would catch it anyway."""
        self.assertFalse(logins.usable_login('{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"},"other":NaN}'))
        self.assertFalse(logins.usable_login('{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"},"other":Infinity}'))
        self.assertTrue(logins.usable_login('{"claudeAiOauth":{"accessToken":"a","refreshToken":"r"},"other":1}'))


class LiveLoginChangedTests(unittest.TestCase):
    """The Mac's nine re-read checks, LoginSafetyTests.swift:105-121."""

    def test_the_nine(self):
        a = cred("A1", "R1")
        changed = logins.live_login_changed
        self.assertFalse(changed(a, a))
        self.assertTrue(changed(a, cred("A2", "R1")))
        self.assertTrue(changed(a, cred("A1", "R2")))
        self.assertTrue(changed(a, cred("A2", "R2")))                      # a renewed login is a change
        self.assertFalse(changed(a, cred("A1", "R1", expires=1_800_000_000_000)))
        self.assertFalse(changed(a, cred("A1", "R1", top={"mcpOAuth": {"s": 1}})))   # another MCP login is not
        self.assertTrue(changed(a, None) and changed(None, a))
        self.assertFalse(changed(None, None))
        self.assertTrue(changed(a, "not json"))
        self.assertFalse(changed("x", "x"))
        self.assertTrue(credtools.live_login_changed is logins.live_login_changed)

    def test_blank_tokens_compare_equal_as_strings(self):
        blank = cred("", "")
        self.assertFalse(logins.live_login_changed(blank, cred("", "", expires=5)))
        self.assertTrue(logins.live_login_changed(blank, cred("a", "")))


class GateTests(unittest.TestCase):
    """D12 acceptance: each unusable shape against save, switch and switchable (LoginSafetyTests.swift:67-86)."""

    CASES = {
        "a blank accessToken": cred(access=""), "a blank refreshToken": cred(refresh=""),
        "a missing claudeAiOauth": json.dumps({"mcpOAuth": {}}), "null fields": json.dumps({"claudeAiOauth": {
            "accessToken": None, "refreshToken": None, "expiresAt": None}}),
        "expiresAt 0": cred(expires=0), "a whitespace token": cred(access=" \n\t"),
    }

    def test_every_unusable_login_is_refused_by_all_three(self):
        for name, text in self.CASES.items():
            self.assertFalse(credtools.gate_save(text).allowed, name)
            self.assertIsNotNone(credtools.gate_target_problem(text), name)
            self.assertFalse(credtools.gate_is_switchable(text, False, False), name)

    def test_a_usable_login_passes_all_three(self):
        usable = cred()
        self.assertTrue(credtools.gate_save(usable).allowed)
        self.assertIsNone(credtools.gate_target_problem(usable))
        self.assertTrue(credtools.gate_is_switchable(usable, False, False))
        self.assertFalse(credtools.gate_is_switchable(usable, True, False))
        self.assertFalse(credtools.gate_is_switchable(usable, False, True))


class ProtocolDecodingTests(unittest.TestCase):
    """loads() reads a line the way Foundation's JSONDecoder does (LA review, protocol.py:96)."""

    def test_a_duplicate_key_keeps_the_first_value(self):
        line = '{"jsonrpc":"2.0","id":1,"method":"status.get","method":"app.quit"}'
        self.assertEqual(p.loads(line)["method"], "status.get")
        self.assertEqual(p.loads('{"account":"alice","account":"bob"}'), {"account": "alice"})

    def test_a_number_outside_a_double_is_a_parse_error(self):
        for text in ('{"a":1e999}', '{"a":' + "1" * 400 + "}", '{"a":-1e999}'):
            with self.assertRaises(ValueError):
                p.loads(text)
        self.assertEqual(p.loads('{"a":1e300,"b":3.0,"c":7}'), {"a": 1e300, "b": 3.0, "c": 7})

    def test_nan_and_infinity_are_still_refused(self):
        for text in ('{"a":NaN}', '{"a":Infinity}', '{"a":-Infinity}'):
            with self.assertRaises(ValueError):
                p.loads(text)


class EventShapeTests(unittest.TestCase):
    def test_auto_switched_always_carries_relaxed(self):
        """ControlSplitTests.swift:247-254."""
        weekly = p.auto_switched_data("a", "b", "windows", "threshold", "weekly", relaxed=False)
        self.assertEqual((weekly["window"], weekly["relaxed"]), ("weekly", False))
        drain = p.auto_switched_data("a", "b", "windows", "drainEarly", relaxed=True)
        self.assertNotIn("window", drain)
        self.assertIs(drain["relaxed"], True)
        self.assertNotIn("window", p.auto_switched_data("a", "b", "fable", "threshold", relaxed=False))
        with self.assertRaises(TypeError):
            p.auto_switched_data("a", "b", "windows", "threshold")          # relaxed has no default

    def test_every_event_carries_a_data_object(self):
        for line in (p.event_line("usageUpdated", 0), p.event_line("usageUpdated", 0, None)):
            self.assertEqual(p.decode_event(line)["data"], {})


class ResolverTests(unittest.TestCase):
    def test_canonically_equivalent_spellings_resolve(self):
        nfc, nfd = unicodedata.normalize("NFC", "café"), unicodedata.normalize("NFD", "café")
        self.assertNotEqual(nfc, nfd)
        account = replace(acct("A"), custom_label=nfc)
        self.assertEqual(p.resolve(nfd, [account]).id, "A")

    def test_an_invisible_character_is_ignored(self):
        self.assertEqual(p.resolve("​A@x.com", [acct("A")]).id, "A")

    def test_a_different_email_still_does_not_match(self):
        with self.assertRaises(p.ControlError):
            p.resolve("b@x.com", [acct("A")])

    def test_a_setting_name_matches_the_same_way(self):
        self.assertEqual(catalog.named("auto​Switch.ENABLED").name, "autoSwitch.enabled")


class LabelLengthTests(unittest.TestCase):
    def test_a_label_counts_characters_the_way_a_person_does(self):
        self.assertEqual(p.normalize_label("👍🏽" * 60), "👍🏽" * 60)            # 60 characters, 120 code points
        self.assertEqual(p.grapheme_count("🇨🇦🇺🇸"), 2)
        self.assertEqual(p.grapheme_count("é"), 1)
        self.assertEqual(p.grapheme_count("👨‍👩‍👧"), 1)
        with self.assertRaises(p.ControlError):
            p.normalize_label("x" * 101)
        self.assertEqual(p.normalize_label("x" * 100), "x" * 100)
        with self.assertRaises(p.ControlError):
            p.normalize_label("👍🏽" * 101)


if __name__ == "__main__":
    unittest.main()
