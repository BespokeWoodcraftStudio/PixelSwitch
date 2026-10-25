"""What a failed usage request says on the account's card, and whether the last reading survives
it (Tests/UnitTests/UsageErrorTests.swift and the words checks of UsageGuardTests.swift), and the
usage response model (Tests/UnitTests/ControlTests.swift fixture, UsageData.swift rules)."""
from __future__ import annotations

import errno
import socket
import unittest
import urllib.error

from pixelswitch.usage_guard import UsageRequestError, paused_message
from pixelswitch.usage_model import (
    ExtraUsage,
    UsageDecodeError,
    UsageResponse,
    UsageWindow,
    parse_iso8601,
    swift_double,
)

E = UsageRequestError

USAGE_JSON = {
    "five_hour": {"utilization": 42.0, "resets_at": "2026-09-25T18:00:00.000000+00:00"},
    "seven_day": {"utilization": 61.0, "resets_at": "2026-09-30T09:00:00+00:00"},
    "extra_usage": {"is_enabled": False, "monthly_limit": None, "used_credits": None, "utilization": None},
    "limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 10, "severity": "normal",
                "resets_at": "2026-09-30T09:00:00+00:00",
                "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": True}],
}


class StatusMappingTests(unittest.TestCase):
    def test_401_or_token_expired_body_is_expired(self):
        self.assertEqual(E.from_status(401, "", None), E("expired"))
        self.assertEqual(E.from_status(400, '{"error":{"type":"token_expired"}}', None), E("expired"))
        self.assertEqual(E.from_status(503, "token_expired", None), E("expired"))  # before the status switch

    def test_429_carries_retry_after_when_it_is_a_number(self):
        self.assertEqual(E.from_status(429, "", "3474"), E("rateLimited", retry_after=3474))
        self.assertEqual(E.from_status(429, "", None), E("rateLimited", retry_after=None))
        self.assertEqual(E.from_status(429, "", "soon"), E("rateLimited", retry_after=None))
        self.assertEqual(E.from_status(429, "", "Wed, 21 Oct 2026 07:28:00 GMT"), E("rateLimited", retry_after=None))

    def test_403_is_no_subscription(self):
        self.assertEqual(E.from_status(403, "nope", None), E("forbidden", body="nope"))

    def test_every_5xx_is_the_servers_trouble(self):
        for status in (500, 502, 503, 504, 529):
            self.assertEqual(E.from_status(status, "", None), E("server", status=status))

    def test_any_other_status_is_unexpected(self):
        for status in (404, 418, 0):
            self.assertEqual(E.from_status(status, "", None), E("unexpected", status=status))


class TransportTests(unittest.TestCase):
    def test_offline_codes(self):
        for number in (errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH, errno.ECONNRESET):
            self.assertEqual(E.from_transport_error(OSError(number, "x")), E("network", offline=True), number)

    def test_other_failures_are_not_offline(self):
        self.assertEqual(E.from_transport_error(TimeoutError()), E("network", offline=False))
        self.assertEqual(E.from_transport_error(socket.gaierror(socket.EAI_NONAME, "no such host")),
                         E("network", offline=False))
        self.assertEqual(E.from_transport_error(ValueError("tls")), E("network", offline=False))

    def test_a_url_error_is_unwrapped(self):
        wrapped = urllib.error.URLError(OSError(errno.ENETUNREACH, "unreachable"))
        self.assertEqual(E.from_transport_error(wrapped), E("network", offline=True))


class BehaviourTests(unittest.TestCase):
    def test_only_server_trouble_and_network_keep_the_last_reading(self):
        self.assertTrue(E("server", status=503).keeps_last_reading)
        self.assertTrue(E("network", offline=True).keeps_last_reading)
        self.assertTrue(E("network").keeps_last_reading)
        for error in (E("expired"), E("forbidden"), E("unexpected", status=404), E.decode_failure("x")):
            self.assertFalse(error.keeps_last_reading, error)

    def test_only_server_trouble_links_to_the_status_page(self):
        self.assertTrue(E("server", status=503).offers_status_page)
        self.assertFalse(E("network", offline=True).offers_status_page)
        self.assertFalse(E("unexpected", status=404).offers_status_page)

    def test_retry_is_offered_unless_only_signing_in_or_waiting_helps(self):
        for error in (E("server", status=503), E("network"), E("unexpected", status=404), E.decode_failure("x")):
            self.assertTrue(error.can_retry, error)
        for error in (E("expired"), E("forbidden"), E("rateLimited", retry_after=60)):
            self.assertFalse(error.can_retry, error)


class MessageTests(unittest.TestCase):
    def test_plain_sentences(self):
        self.assertEqual(E("expired").message(), "Session expired.")
        self.assertEqual(E("network", offline=True).message(),
                         "Can't reach Anthropic. Check your internet connection; PixelSwitch keeps trying.")
        self.assertEqual(E("network", offline=False).message(),
                         "Can't reach Anthropic right now; PixelSwitch keeps trying.")
        self.assertEqual(E("server", status=503).message(),
                         "Anthropic's usage service isn't responding (HTTP 503). Your account is fine; PixelSwitch keeps trying.")
        self.assertEqual(E("unexpected", status=404).message(), "Unexpected reply from Anthropic (HTTP 404).")
        self.assertEqual(E.decode_failure("bad").message(), "Anthropic sent usage data PixelSwitch couldn't read.")
        self.assertEqual(E("forbidden").message(), "No active subscription on this account (OAuth not allowed).")

    def test_a_429_never_reads_api_rate_limited(self):
        text = E("rateLimited", retry_after=3600).message(1_790_000_000)
        self.assertNotIn("API Rate Limited", text)
        self.assertIn("Claude Code itself is not affected", text)
        self.assertTrue(text.startswith("Usage reading paused until "))

    def test_the_pause_message_clamps_the_wait(self):
        # no Retry-After parks 120 s; 0 parks 60 s; 999999 parks 3600 s
        base = 1_790_000_000
        self.assertEqual(E("rateLimited", retry_after=None).message(base), paused_message(base + 120))
        self.assertEqual(E("rateLimited", retry_after=0).message(base), paused_message(base + 60))
        self.assertEqual(E("rateLimited", retry_after=999_999).message(base), paused_message(base + 3600))

    def test_paused_message_format(self):
        import datetime

        utc = datetime.timezone.utc
        self.assertEqual(paused_message(datetime.datetime(2026, 10, 24, 15, 5, tzinfo=utc).timestamp(), utc),
                         "Usage reading paused until 3:05 PM. Claude Code itself is not affected.")
        self.assertEqual(paused_message(datetime.datetime(2026, 10, 24, 0, 30, tzinfo=utc).timestamp(), utc),
                         "Usage reading paused until 12:30 AM. Claude Code itself is not affected.")
        self.assertEqual(paused_message(datetime.datetime(2026, 10, 24, 12, 0, tzinfo=utc).timestamp(), utc),
                         "Usage reading paused until 12:00 PM. Claude Code itself is not affected.")

    def test_str_is_the_message_and_it_can_be_raised(self):
        with self.assertRaises(UsageRequestError) as caught:
            raise E("expired")
        self.assertEqual(str(caught.exception), "Session expired.")
        self.assertNotIn("couldn", str(caught.exception))


class UsageModelTests(unittest.TestCase):
    def test_the_control_tests_fixture(self):
        usage = UsageResponse.from_json(USAGE_JSON)
        self.assertEqual((usage.five_hour.utilization, usage.seven_day.utilization), (42.0, 61.0))
        self.assertEqual(usage.extra_usage, ExtraUsage(False, None, None, None))
        fable = usage.model_weekly_limit("fable")  # case-insensitive
        self.assertEqual(fable.percent, 10)
        self.assertEqual(fable.percent_left, 90)
        self.assertEqual(fable.window, UsageWindow(10, "2026-09-30T09:00:00+00:00"))
        self.assertIsNone(usage.model_weekly_limit("Opus"))

    def test_resets_at_with_and_without_fractional_seconds(self):
        with_fraction = parse_iso8601("2026-09-25T18:00:00.000000+00:00")
        without = parse_iso8601("2026-09-25T18:00:00+00:00")
        self.assertEqual(with_fraction, without)
        self.assertAlmostEqual(parse_iso8601("2026-09-28T18:00:00.177837+00:00")
                               - parse_iso8601("2026-09-28T18:00:00+00:00"), 0.177837, places=3)
        self.assertEqual(parse_iso8601("2026-09-25T18:00:00Z"), without)
        self.assertEqual(parse_iso8601("2026-09-25T20:00:00+02:00"), without)
        self.assertAlmostEqual(parse_iso8601("2026-09-25T18:00:00.5Z") - without, 0.5, places=3)
        self.assertAlmostEqual(parse_iso8601("2026-09-25T18:00:00.123456789Z") - without, 0.123456789, places=3)

    def test_unparseable_reset_is_none(self):
        for bad in (None, "", "tomorrow", "2026-09-25", "2026-09-25 18:00:00Z", "2026-13-25T18:00:00Z",
                    "2026-09-25T25:00:00Z", "2026-09-25T18:00:00+0000", "2026-09-25T18:00:00", 5):
            self.assertIsNone(parse_iso8601(bad), bad)

    def test_a_limits_entry_of_an_unexpected_shape_is_skipped(self):
        body = {"five_hour": {"utilization": 1}, "limits": [
            None, 7, "x", {"kind": 5}, {"percent": "ten"},
            {"kind": "weekly_scoped", "percent": 20, "scope": {"model": {"display_name": "Fable"}}},
            {"kind": "weekly_scoped", "percent": 30, "scope": "bad"},
        ]}
        usage = UsageResponse.from_json(body)
        self.assertEqual(len(usage.limits), 1)
        self.assertEqual(usage.model_weekly_limit("Fable").percent, 20)

    def test_limits_that_is_not_a_list_reads_as_none(self):
        usage = UsageResponse.from_json({"five_hour": {"utilization": 3}, "limits": {"oops": 1}})
        self.assertEqual(usage.limits, ())
        self.assertEqual(usage.five_hour.utilization, 3)
        self.assertIsNone(UsageResponse.from_json({}).limits)

    def test_model_weekly_rules(self):
        def limit(**kw):
            return UsageResponse.from_json({"limits": [dict({"kind": "weekly_scoped", "percent": 5,
                                                              "scope": {"model": {"display_name": "Fable"}}}, **kw)]})

        self.assertIsNotNone(limit().model_weekly_limit("Fable"))
        self.assertIsNotNone(limit(kind="x", group="weekly").model_weekly_limit("Fable"))
        self.assertIsNone(limit(kind="x").model_weekly_limit("Fable"))
        self.assertIsNone(limit(percent=None).model_weekly_limit("Fable"))
        self.assertIsNotNone(UsageResponse.from_json({"limits": [{
            "kind": "weekly_scoped", "percent": 5, "scope": {"model": {"display_name": "  Fable \t"}}}]})
            .model_weekly_limit("Fable"))
        self.assertIsNone(UsageResponse.from_json({"limits": [{
            "kind": "weekly_scoped", "percent": 5, "scope": {"model": {"display_name": "  "}}}]})
            .model_weekly_limit(""))

    def test_a_wrong_type_in_a_window_fails_the_decode(self):
        for body in ([], {"five_hour": 5}, {"five_hour": {"utilization": "5"}}, {"seven_day": {"resets_at": 5}},
                     {"extra_usage": {"is_enabled": "yes"}}, {"seven_day_opus": []}):
            with self.assertRaises(UsageDecodeError, msg=body):
                UsageResponse.from_json(body)

    def test_null_and_missing_fields_decode_to_none(self):
        usage = UsageResponse.from_json({"five_hour": None, "seven_day": {"utilization": None}, "seven_day_opus": None})
        self.assertIsNone(usage.five_hour)
        self.assertEqual(usage.seven_day, UsageWindow(None, None))

    def test_swift_double(self):
        self.assertEqual([swift_double("72.4"), swift_double("3474"), swift_double("-5"), swift_double("1e3")],
                         [72.4, 3474.0, -5.0, 1000.0])
        for bad in ("", "soon", "1_0", "inf", "nan", " 3", "٣", None, "0x10"):
            self.assertIsNone(swift_double(bad), bad)


if __name__ == "__main__":
    unittest.main()
