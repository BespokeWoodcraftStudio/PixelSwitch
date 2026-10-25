"""Credentials: the usable-login rule (spec D12), the switch merge and the ownership parsing
(Services/ClaudeCredentialMerge.swift, Services/CredentialOwnership.swift).

D12 acceptance asks for tests of a blank accessToken, a blank refreshToken, a missing
claudeAiOauth, null fields and expiresAt 0 "each against save, switch and switchable". The store
(save), the switch and isSwitchable are the daemon's; each of them is required to call
``usable_login``, so this file pins the one function all three use for every one of those inputs.
"""
from __future__ import annotations

import json
import unittest

from pixelswitch import logins
from pixelswitch.logins import Login, usable_login


def credential(access="at-1", refresh="rt-1", expires=1_790_000_000_000, **extra):
    oauth = {}
    if access is not ...:
        oauth["accessToken"] = access
    if refresh is not ...:
        oauth["refreshToken"] = refresh
    if expires is not ...:
        oauth["expiresAt"] = expires
    oauth.update(extra)
    return json.dumps({"claudeAiOauth": oauth, "mcpOAuth": {"server": {"token": "m"}}})


class UsableLoginTests(unittest.TestCase):
    def test_a_good_login(self):
        self.assertTrue(usable_login(credential()))
        self.assertIsNone(logins.login_problem(credential()))

    def test_blank_access_token(self):
        for bad in ("", None, 5, True, [], {}, ...):
            self.assertFalse(usable_login(credential(access=bad)), bad)

    def test_blank_refresh_token(self):
        for bad in ("", None, 5, True, [], {}, ...):
            self.assertFalse(usable_login(credential(refresh=bad)), bad)

    def test_missing_or_wrong_claude_ai_oauth(self):
        self.assertFalse(usable_login(json.dumps({"mcpOAuth": {}})))
        self.assertFalse(usable_login(json.dumps({"claudeAiOauth": None})))
        self.assertFalse(usable_login(json.dumps({"claudeAiOauth": "x"})))
        self.assertFalse(usable_login(json.dumps({"claudeAiOauth": []})))
        self.assertFalse(usable_login(json.dumps({"claudeAiOauth": {}})))

    def test_expires_at_zero_negative_null_or_wrong_type(self):
        for bad in (0, 0.0, -1, None, "1790000000000", True, False, [], float("inf")):
            text = credential(expires=bad).replace("Infinity", "1e999")
            self.assertFalse(usable_login(text), bad)

    def test_expires_at_absent_is_allowed(self):
        self.assertTrue(usable_login(credential(expires=...)))

    def test_expires_at_in_the_past_is_still_a_usable_login(self):
        """An expired access token is renewable; only a blank or dead shape is refused."""
        self.assertTrue(usable_login(credential(expires=1)))
        self.assertTrue(usable_login(credential(expires=1.5)))

    def test_not_json_or_not_an_object(self):
        for text in ("", "not json", "[]", "5", "null", '"x"', "{", None):
            self.assertFalse(usable_login(text), text)
        self.assertFalse(usable_login(b"\xff\xfe"))

    def test_nan_is_not_json(self):
        self.assertFalse(usable_login('{"claudeAiOauth":{"accessToken":"a","refreshToken":"r","expiresAt":NaN}}'))

    def test_accepts_bytes_and_dicts(self):
        self.assertTrue(usable_login(credential().encode()))
        self.assertTrue(usable_login(json.loads(credential())))

    def test_other_keys_do_not_matter(self):
        self.assertTrue(usable_login(credential(scopes=["user:inference"], subscriptionType="max")))

    def test_problem_phrases_never_hold_a_value(self):
        for bad in ("", None):
            phrase = logins.login_problem(credential(access=bad, refresh="SECRET-REFRESH"))
            self.assertIn("accessToken", phrase)
            self.assertNotIn("SECRET", phrase)
        phrase = logins.login_problem(credential(access="SECRET-ACCESS", refresh=""))
        self.assertIn("refreshToken", phrase)
        self.assertNotIn("SECRET", phrase)

    def test_claude_code_blanking_a_dead_login(self):
        """R4: Claude Code blanks a dead login on disk; capture must never save that."""
        blank = json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}})
        self.assertFalse(usable_login(blank))


class LoginParsingTests(unittest.TestCase):
    def test_reads_the_ownership_fields(self):
        login = logins.login_from_credential(credential(access="A", refresh="R", expires=1_790_000_000_000))
        self.assertEqual(login, Login("A", "R", 1_790_000_000_000.0))

    def test_empty_refresh_reads_as_none_and_empty_access_is_no_login(self):
        self.assertIsNone(logins.login_from_credential(credential(refresh="")).refresh_token)
        self.assertIsNone(logins.login_from_credential(credential(access="")))
        self.assertIsNone(logins.login_from_credential(json.dumps({"mcpOAuth": {}})))
        self.assertIsNone(logins.login_from_credential("nope"))

    def test_expires_at_is_optional_and_a_number(self):
        self.assertIsNone(logins.login_from_credential(credential(expires=...)).expires_at)
        self.assertIsNone(logins.login_from_credential(credential(expires="soon")).expires_at)
        self.assertEqual(logins.login_from_credential(credential(expires=5)).expires_at, 5.0)

    def test_is_expired_within_a_minute(self):
        now = 1_790_000_000.0
        self.assertTrue(Login("a", None, (now + 59) * 1000).is_expired(now))
        self.assertFalse(Login("a", None, (now + 61) * 1000).is_expired(now))
        self.assertFalse(Login("a", None, None).is_expired(now))

    def test_same_grant(self):
        a, b = Login("A1", "R1"), Login("A2", "R1")
        self.assertTrue(logins.same_grant(a, b))                 # same refresh token
        self.assertTrue(logins.same_grant(Login("A1", "R1"), Login("A1", "R9")))  # same access token
        self.assertFalse(logins.same_grant(Login("A1", "R1"), Login("A2", "R2")))
        self.assertFalse(logins.same_grant(Login("A1", None), Login("A2", None)))   # a miss is unknown, not different

    def test_lineage_and_shared_logins(self):
        saved = {"x": Login("A", "R1"), "y": Login("B", "R1"), "z": Login("C", "R3"), "w": Login("D", "R4"),
                 "v": Login("E", "R4")}
        self.assertEqual(logins.lineage_matches(Login("Q", "R1"), saved), ["x", "y"])
        self.assertEqual(logins.shared_logins(saved), [["v", "w"], ["x", "y"]])
        self.assertEqual(logins.shared_logins({"a": Login("A", "R")}), [])

    def test_details_belong(self):
        d = logins.details_belong
        self.assertTrue(d("u1", "a@x.com", "u1", "other@x.com"))     # the permanent id decides when both have one
        self.assertFalse(d("u1", "a@x.com", "u2", "a@x.com"))
        self.assertTrue(d(None, "A@X.com", "u2", "a@x.COM"))         # else the email, case-insensitive
        self.assertFalse(d(None, None, "u2", "a@x.com"))
        self.assertFalse(d(None, None, None, None))
        self.assertFalse(d("u1", None, None, None))

    def test_same_oauth_tokens_for_re_read_before_write(self):
        base = credential(access="A", refresh="R")
        self.assertTrue(logins.same_oauth_tokens(base, base))
        self.assertTrue(logins.same_oauth_tokens(base, credential(access="A", refresh="R", expires=5)))
        self.assertFalse(logins.same_oauth_tokens(base, credential(access="A2", refresh="R")))
        self.assertFalse(logins.same_oauth_tokens(base, credential(access="A", refresh="R2")))
        self.assertFalse(logins.same_oauth_tokens(base, "garbage"))
        self.assertFalse(logins.same_oauth_tokens(None, base))
        self.assertFalse(logins.same_oauth_tokens("garbage", "garbage"))


class MergeTests(unittest.TestCase):
    LIVE = json.dumps({"claudeAiOauth": {"accessToken": "LIVE-A", "refreshToken": "LIVE-R"},
                       "mcpOAuth": {"srv": {"accessToken": "mcp-new"}}, "other": 1})
    TARGET = json.dumps({"claudeAiOauth": {"accessToken": "T-A", "refreshToken": "T-R", "expiresAt": 9},
                         "mcpOAuth": {"srv": {"accessToken": "mcp-old"}}})

    def test_takes_claude_ai_oauth_from_the_target_and_keeps_every_other_live_key(self):
        result = logins.credential_for_switch(self.LIVE, self.TARGET)
        self.assertEqual(result.kind, "merged")
        merged = json.loads(result.credential)
        self.assertEqual(merged["claudeAiOauth"]["accessToken"], "T-A")
        self.assertEqual(merged["mcpOAuth"]["srv"]["accessToken"], "mcp-new")   # not replaced by the older copy
        self.assertEqual(merged["other"], 1)
        self.assertEqual(result.kept_keys, ("mcpOAuth", "other"))

    def test_output_is_compact_json(self):
        self.assertNotIn(" ", logins.credential_for_switch(self.LIVE, self.TARGET).credential.replace('"Claude', ""))

    def test_non_ascii_survives(self):
        live = json.dumps({"claudeAiOauth": {"accessToken": "a"}, "note": "café →"}, ensure_ascii=False)
        merged = logins.credential_for_switch(live, self.TARGET)
        self.assertEqual(json.loads(merged.credential)["note"], "café →")

    def test_target_without_a_readable_login_is_target_only(self):
        for target in ("garbage", json.dumps({"mcpOAuth": {}}), json.dumps({"claudeAiOauth": "x"})):
            result = logins.credential_for_switch(self.LIVE, target)
            self.assertEqual((result.kind, result.credential), ("targetOnly", target))
            self.assertIn("claudeAiOauth", result.reason)

    def test_missing_or_unreadable_live_is_target_only(self):
        for live in (None, "", "garbage", "[]"):
            result = logins.credential_for_switch(live, self.TARGET)
            self.assertEqual((result.kind, result.credential), ("targetOnly", self.TARGET))

    def test_live_without_a_claude_ai_oauth_key_gains_the_targets(self):
        live = json.dumps({"mcpOAuth": {"s": 1}})
        merged = json.loads(logins.credential_for_switch(live, self.TARGET).credential)
        self.assertEqual(merged["claudeAiOauth"]["accessToken"], "T-A")
        self.assertEqual(merged["mcpOAuth"], {"s": 1})

    def test_the_merge_does_not_change_its_inputs(self):
        live_before, target_before = self.LIVE, self.TARGET
        logins.credential_for_switch(self.LIVE, self.TARGET)
        self.assertEqual((self.LIVE, self.TARGET), (live_before, target_before))

    def test_credential_keys_lists_names_only(self):
        self.assertEqual(list(logins.credential_keys(self.LIVE)), ["claudeAiOauth", "mcpOAuth", "other"])
        self.assertEqual(list(logins.credential_keys("garbage")), [])


if __name__ == "__main__":
    unittest.main()
