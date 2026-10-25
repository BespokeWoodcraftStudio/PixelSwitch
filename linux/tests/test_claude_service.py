"""The three requests that carry a login, against the mock API: URLs, headers, bodies, timeouts, every
status mapping, the guard, and the renewal of a saved login with rotation (spec 5.3)."""
from __future__ import annotations

import json
import time
import unittest

from pixelswitch import api as api_module
from pixelswitch.api import ApiBases, Transport, TransportError
from pixelswitch.claude_service import ClaudeService
from pixelswitch.logfile import Log
from pixelswitch.usage_guard import UsageRequestError, UsageRequestGuard

from .lb_support import FakeClock, credential_text
from .mock_api import MockApi, R, usage_body


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.mock = MockApi()
        base = self.mock.start()
        self.addCleanup(self.mock.stop)
        self.bases = ApiBases(usage=base + "/api/oauth/usage", profile=base + "/api/oauth/profile", token=base + "/v1/oauth/token")
        self.clock = FakeClock()
        self.guard = UsageRequestGuard()
        self.log_path = None
        self.service = ClaudeService(Transport(user_agent="PixelSwitch/2.0 (Linux)", test_mode=True), self.bases, self.guard,
                                     Log(None).category("t"), clock=self.clock)


class UrlsAndHeadersTests(ServiceCase):
    def test_production_urls_are_the_macs(self):
        self.assertEqual(api_module.USAGE_URL, "https://api.anthropic.com/api/oauth/usage")
        self.assertEqual(api_module.PROFILE_URL, "https://api.anthropic.com/api/oauth/profile")
        self.assertEqual(api_module.TOKEN_URL, "https://console.anthropic.com/v1/oauth/token")
        self.assertEqual(api_module.OAUTH_CLIENT_ID, "9d1c250a-e61b-44d9-88ed-5944d1962f5e")
        self.assertEqual(api_module.OAUTH_BETA, "oauth-2025-04-20")
        self.assertEqual((api_module.USAGE_TIMEOUT, api_module.PROFILE_TIMEOUT, api_module.TOKEN_TIMEOUT), (60.0, 15.0, 60.0))
        bases = ApiBases()
        self.assertEqual((bases.usage, bases.profile, bases.token),
                         (api_module.USAGE_URL, api_module.PROFILE_URL, api_module.TOKEN_URL))

    def test_the_test_base_is_one_prefix_for_all_three(self):
        bases = ApiBases.from_env({"PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": "http://127.0.0.1:9/"})
        self.assertEqual(bases.usage, "http://127.0.0.1:9/api/oauth/usage")
        self.assertEqual(bases.profile, "http://127.0.0.1:9/api/oauth/profile")
        self.assertEqual(bases.token, "http://127.0.0.1:9/v1/oauth/token")
        self.assertEqual(ApiBases.from_env({"PIXELSWITCH_TEST_API_BASE": "http://127.0.0.1:9"}), ApiBases())

    def test_usage_and_profile_get_the_bearer_and_the_beta_header_the_post_gets_neither(self):
        self.mock.add_account("tok", "uuid-1", "a@x.test")
        self.mock.add_refresh("old-refresh", "new-access", "new-refresh")
        self.service.get_usage_limits("tok")
        self.service.account_identity("tok", None, "acct")
        self.service.refresh_oauth_credentials(credential_text("tok", "old-refresh"))
        usage, profile, token = (self.mock.calls_to(name)[0] for name in ("usage", "profile", "token"))
        for call in (usage, profile):
            self.assertEqual(call.method, "GET")
            self.assertEqual(call.headers["authorization"], "Bearer tok")
            self.assertEqual(call.headers["anthropic-beta"], "oauth-2025-04-20")
            self.assertEqual(call.body, b"")
            self.assertNotIn("content-type", call.headers)
        self.assertEqual(token.method, "POST")
        self.assertEqual(token.headers["content-type"], "application/json")
        self.assertNotIn("anthropic-beta", token.headers)
        self.assertNotIn("authorization", token.headers)
        self.assertEqual(json.loads(token.body), {"grant_type": "refresh_token", "refresh_token": "old-refresh",
                                                  "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e"})
        self.assertNotIn("scope", json.loads(token.body))
        for call in (usage, profile, token):
            self.assertEqual(call.headers["user-agent"], "PixelSwitch/2.0 (Linux)")

    def test_a_redirect_is_never_followed(self):
        self.mock.script("usage", "tok", R(302, "", {"Location": "http://127.0.0.1:1/elsewhere"}))
        with self.assertRaises(UsageRequestError) as raised:
            self.service.get_usage_limits("tok")
        self.assertEqual(raised.exception.kind, "unexpected")
        self.assertEqual(raised.exception.status, 302)
        self.assertEqual(len(self.mock.calls), 1)

    def test_test_mode_refuses_a_real_host_without_a_connection(self):
        service = ClaudeService(Transport(user_agent="x", test_mode=True), ApiBases(), self.guard, Log(None).category("t"))
        with self.assertRaises(UsageRequestError) as raised:
            service.get_usage_limits("tok")
        self.assertEqual(raised.exception.kind, "network")
        self.assertIsNone(service.account_identity("tok", None, "acct"))
        self.assertEqual(service.refresh_oauth_credentials(credential_text("a", "r")).kind, "transient")
        with self.assertRaises(TransportError):
            Transport(user_agent="x", test_mode=True).request("GET", "https://platform.claude.com/x", timeout=1)


class UsageTests(ServiceCase):
    def test_a_good_reading(self):
        self.mock.add_account("tok", "u", "a@x.test", usage_body(42, 61, fable=10))
        usage = self.service.get_usage_limits("tok")
        self.assertEqual(usage.five_hour.utilization, 42)
        self.assertEqual(usage.seven_day.utilization, 61)
        self.assertEqual(usage.model_weekly_limit("Fable").percent, 10)

    def test_status_mapping(self):
        table = [
            (R(401, {"error": "x"}), "expired", None),
            (R(200, {"error": "token_expired"}, {}), None, None),  # a 200 body is not judged
            (R(500, "oops"), "server", None),
            (R(503, "oops"), "server", None),
            (R(403, {"error": {"type": "permission_error"}}), "forbidden", None),
            (R(404, "nope"), "unexpected", None),
            (R(418, "tea"), "unexpected", None),
            (R(400, "token_expired somewhere"), "expired", None),
            (R(429, "slow down", {"Retry-After": "3600"}), "rateLimited", 3600.0),
            (R(429, "slow down", {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), "rateLimited", None),
            (R(429, "slow down"), "rateLimited", None),
        ]
        for response, kind, retry in table:
            self.mock.script("usage", "tok", response)
            if kind is None:
                self.service.get_usage_limits("tok")
                continue
            with self.assertRaises(UsageRequestError) as raised:
                self.service.get_usage_limits("tok")
            self.assertEqual(raised.exception.kind, kind, response)
            if kind == "rateLimited":
                self.assertEqual(raised.exception.retry_after, retry)

    def test_a_200_that_is_not_usage_is_a_decode_error(self):
        for body in ("not json", "[1]", '{"five_hour": 5}', '{"five_hour": {"utilization": "x"}}'):
            self.mock.script("usage", "tok", R(200, body))
            with self.assertRaises(UsageRequestError) as raised:
                self.service.get_usage_limits("tok")
            self.assertEqual(raised.exception.kind, "decode", body)

    def test_an_unreachable_server_is_a_network_error(self):
        service = ClaudeService(Transport(user_agent="x", test_mode=True),
                                ApiBases(usage="http://127.0.0.1:1/api/oauth/usage"), self.guard, Log(None).category("t"))
        with self.assertRaises(UsageRequestError) as raised:
            service.get_usage_limits("tok")
        self.assertEqual(raised.exception.kind, "network")

    def test_the_log_never_carries_the_token_or_the_body(self):
        import os
        import tempfile

        directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, directory, True)
        log = Log(os.path.join(directory, "l.log"))
        service = ClaudeService(Transport(user_agent="x", test_mode=True), self.bases, self.guard, log.category("Claude"))
        self.mock.script("usage", "secret-access-token", R(500, "SECRET-BODY-TEXT"))
        with self.assertRaises(UsageRequestError):
            service.get_usage_limits("secret-access-token")
        service.refresh_oauth_credentials(credential_text("secret-access-token", "secret-refresh-token"))
        log.close()
        with open(os.path.join(directory, "l.log")) as handle:
            text = handle.read()
        for secret in ("secret-access-token", "secret-refresh-token", "SECRET-BODY-TEXT"):
            self.assertNotIn(secret, text)


class ProfileTests(ServiceCase):
    def test_identity(self):
        self.mock.add_account("tok", "uuid-1", "Mixed@X.test")
        identity = self.service.account_identity("tok", None, "acct")
        self.assertEqual((identity.uuid, identity.email), ("uuid-1", "Mixed@X.test"))

    def test_email_address_is_the_fallback_key(self):
        self.mock.script("profile", "tok", R(200, {"account": {"uuid": "u", "email_address": "e@x.test"}}))
        self.assertEqual(self.service.account_identity("tok").email, "e@x.test")

    def test_no_identity_when_the_reply_names_nobody_or_is_not_a_profile(self):
        for body in ({"account": {}}, {"account": "x"}, {"nope": 1}, "junk", [1]):
            self.mock.script("profile", "tok", R(200, body))
            self.assertIsNone(self.service.account_identity("tok", None, "acct"), body)
            self.mock.clear_calls()
            self.guard = UsageRequestGuard()
            self.service.guard = self.guard

    def test_a_401_is_remembered_and_the_token_is_never_sent_again(self):
        self.assertIsNone(self.service.account_identity("unknown", None, "acct"))
        self.assertTrue(self.guard.was_rejected("acct", "unknown"))
        self.assertIsNone(self.service.account_identity("unknown", None, "acct"))
        self.assertIsNone(self.service.account_identity("unknown", None, "other-acct"))
        self.assertEqual(len(self.mock.calls_to("profile")), 1)

    def test_a_401_with_no_known_account_goes_in_the_account_less_list(self):
        self.assertIsNone(self.service.account_identity("unknown", None, None))
        self.assertFalse(self.guard.may_use("unknown"))
        self.assertIsNone(self.service.account_identity("unknown", None, None))
        self.assertEqual(len(self.mock.calls_to("profile")), 1)

    def test_an_expired_token_is_not_sent(self):
        expired_ms = (self.clock.now - 10) * 1000
        self.assertIsNone(self.service.account_identity("tok", expired_ms, "acct"))
        self.assertIsNone(self.service.account_identity("tok", (self.clock.now + 30) * 1000, "acct"))  # within 60 s
        self.assertEqual(self.mock.calls, [])

    def test_a_429_parks_the_token_for_the_account(self):
        self.mock.script("profile", "tok", R(429, "slow", {"Retry-After": "999999"}))
        self.assertIsNone(self.service.account_identity("tok", None, "acct"))
        self.assertFalse(self.guard.may_use("tok", None, self.clock.now))
        self.assertEqual(self.guard.decision("acct", False, _login("tok"), self.clock.now).kind, "parked")
        until = self.guard.decision("acct", False, _login("tok"), self.clock.now).until
        self.assertAlmostEqual(until - self.clock.now, 3600.0, delta=1)  # clamped to the longest park

    def test_a_429_without_an_account_is_not_recorded(self):
        self.mock.script("profile", "tok", R(429, "slow"))
        self.assertIsNone(self.service.account_identity("tok", None, None))
        self.assertTrue(self.guard.may_use("tok", None, self.clock.now))


def _login(token):
    from pixelswitch.logins import Login

    return Login(token, None, None)


class RefreshTests(ServiceCase):
    def refresh(self, access="old-access", refresh="old-refresh", **kw):
        return self.service.refresh_oauth_credentials(credential_text(access, refresh, top={"mcpOAuth": {"s": 1}}, **kw))

    def test_a_rotation_keeps_every_other_key_and_never_rewrites_scopes(self):
        self.mock.add_refresh("old-refresh", "new-access", "new-refresh", expires_in=3600)
        before = self.clock.now
        result = self.refresh()
        self.assertEqual(result.kind, "success")
        document = json.loads(result.credential)
        oauth = document["claudeAiOauth"]
        self.assertEqual(oauth["accessToken"], "new-access")
        self.assertEqual(oauth["refreshToken"], "new-refresh")
        self.assertAlmostEqual(oauth["expiresAt"], (before + 3600) * 1000, delta=2000)
        self.assertEqual(oauth["scopes"], ["user:inference", "user:profile"], "scopes are the login-time value")
        self.assertEqual(oauth["subscriptionType"], "max")
        self.assertEqual(document["mcpOAuth"], {"s": 1})

    def test_no_new_refresh_token_keeps_the_old_one(self):
        self.mock.add_refresh("old-refresh", "new-access", None)
        self.assertEqual(json.loads(self.refresh().credential)["claudeAiOauth"]["refreshToken"], "old-refresh")
        self.mock.add_refresh("old-refresh", "new-access", "")
        self.assertEqual(json.loads(self.refresh().credential)["claudeAiOauth"]["refreshToken"], "old-refresh")

    def test_refresh_token_expires_in_sets_refresh_token_expires_at(self):
        self.mock.add_refresh("old-refresh", "new-access", "new-refresh", refresh_expires_in=7776000)
        before = self.clock.now
        oauth = json.loads(self.refresh().credential)["claudeAiOauth"]
        self.assertAlmostEqual(oauth["refreshTokenExpiresAt"], (before + 7776000) * 1000, delta=2000)

    def test_without_one_a_past_refresh_token_expires_at_is_removed(self):
        self.mock.add_refresh("old-refresh", "new-access", "new-refresh")
        past = credential_text("old-access", "old-refresh", oauth={"refreshTokenExpiresAt": int(time.time() * 1000) - 5000})
        oauth = json.loads(self.service.refresh_oauth_credentials(past).credential)["claudeAiOauth"]
        self.assertNotIn("refreshTokenExpiresAt", oauth)

    def test_without_one_a_future_refresh_token_expires_at_is_kept(self):
        self.mock.add_refresh("old-refresh", "new-access", "new-refresh")
        future = int(time.time() * 1000) + 5_000_000
        text = credential_text("old-access", "old-refresh", oauth={"refreshTokenExpiresAt": future})
        oauth = json.loads(self.service.refresh_oauth_credentials(text).credential)["claudeAiOauth"]
        self.assertEqual(oauth["refreshTokenExpiresAt"], future)

    def test_no_refresh_token_stored_is_rejected_with_no_request(self):
        for text in (credential_text("a", ""), json.dumps({"claudeAiOauth": {"accessToken": "a"}}), "junk", "{}"):
            self.assertEqual(self.service.refresh_oauth_credentials(text).kind, "rejected")
        self.assertEqual(self.mock.calls, [])

    def test_invalid_grant_is_rejected_and_everything_else_is_transient(self):
        self.mock.script("token", "old-refresh", R(400, {"error": "invalid_grant"}))
        self.assertEqual(self.refresh().kind, "rejected")
        for response in (R(400, {"error": "invalid_request"}), R(401, {"error": "invalid_client"}), R(429, "slow"),
                         R(408, "timeout"), R(500, "oops"), R(503, {"error": "overloaded"}), R(400, "not json"),
                         R(400, {"error": ["invalid_grant"]})):
            self.mock.script("token", "old-refresh", response)
            self.assertEqual(self.refresh().kind, "transient", response)

    def test_a_200_that_fails_our_parse_is_transient_not_rejected(self):
        for body in ({"access_token": "x"}, {"expires_in": 5}, {"access_token": 5, "expires_in": 5}, "junk", [1],
                     {"access_token": "  ", "expires_in": 5}, {"access_token": "x", "expires_in": "soon"}):
            self.mock.script("token", "old-refresh", R(200, body))
            self.assertEqual(self.refresh().kind, "transient", body)

    def test_a_network_failure_is_transient(self):
        service = ClaudeService(Transport(user_agent="x", test_mode=True),
                                ApiBases(token="http://127.0.0.1:1/v1/oauth/token"), self.guard, Log(None).category("t"))
        self.assertEqual(service.refresh_oauth_credentials(credential_text("a", "r")).kind, "transient")

    def test_a_spent_refresh_token_is_rejected_the_second_time(self):
        self.mock.add_refresh("old-refresh", "new-access", "new-refresh")
        self.assertEqual(self.refresh().kind, "success")
        self.assertEqual(self.refresh().kind, "rejected")


if __name__ == "__main__":
    unittest.main()
