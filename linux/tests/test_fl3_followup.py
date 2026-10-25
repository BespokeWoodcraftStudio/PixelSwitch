"""Lane FL3's small fixes in the pure modules: a huge integer in the engine, a non-finite number in a usage body, the
hex-float forms of ``Double(String)``, and test mode refusing a socket outside the scratch root."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from pixelswitch import cli, engine, testmode
from pixelswitch.usage_model import UsageDecodeError, UsageResponse

HUGE = 10 ** 400  # a valid Python int and a valid JSON number that float() refuses (OverflowError)


class EngineNumberTests(unittest.TestCase):
    def test_a_huge_integer_is_not_a_number_to_the_engine_and_never_raises(self):
        self.assertIsNone(engine._finite_number(HUGE))
        self.assertIsNone(engine._finite_number(-HUGE))
        self.assertEqual(engine.clamped_threshold(HUGE), engine.FALLBACK_THRESHOLD)
        self.assertIsNone(engine.normalized_account_threshold(HUGE))
        self.assertIsNone(engine.window_value(HUGE))
        self.assertFalse(engine.is_manual_only(HUGE))

    def test_ordinary_numbers_are_unchanged(self):
        self.assertEqual(engine._finite_number(72), 72.0)
        self.assertIsNone(engine._finite_number(float("nan")))
        self.assertIsNone(engine._finite_number(float("inf")))
        self.assertIsNone(engine._finite_number(True))
        self.assertIsNone(engine._finite_number("5"))


class UsageBodyNumberTests(unittest.TestCase):
    def test_a_non_finite_or_overflowing_number_fails_the_whole_decode_as_it_does_on_the_mac(self):
        for value in (float("inf"), float("-inf"), float("nan"), HUGE):
            with self.subTest(value=repr(value)[:20]):
                with self.assertRaises(UsageDecodeError):
                    UsageResponse.from_json({"five_hour": {"utilization": value}})
                with self.assertRaises(UsageDecodeError):
                    UsageResponse.from_json({"extra_usage": {"is_enabled": True, "monthly_limit": value}})

    def test_ordinary_values_still_decode(self):
        usage = UsageResponse.from_json({"five_hour": {"utilization": 42.5}, "seven_day": {"utilization": 0}})
        self.assertEqual(usage.five_hour.utilization, 42.5)


class CliHexTests(unittest.TestCase):
    def test_the_cli_reads_c_hex_floats_like_swift(self):
        self.assertEqual([cli.swift_double("0x1E"), cli.swift_double("-0x10"), cli.swift_double("0x1.8p3")], [30.0, -16.0, 12.0])
        self.assertIsNone(cli.swift_double("0x"))
        self.assertIsNone(cli.swift_double("0x1p99999"))
        self.assertEqual(cli.swift_double("72.4"), 72.4)


class TestModeSocketTests(unittest.TestCase):
    def setUp(self):
        self.real = tempfile.mkdtemp(prefix="psfl3")
        self.addCleanup(shutil.rmtree, self.real, True)
        self.root = os.path.join(self.real, ".cache", "pixelswitch-dev")
        self.home = os.path.join(self.root, "run1", "home")
        os.makedirs(self.home)

    def env(self, **extra):
        return {"PIXELSWITCH_TEST_MODE": "1", **extra}

    def test_a_runtime_dir_inside_the_scratch_root_is_fine(self):
        run = os.path.join(self.root, "run1", "run")
        os.makedirs(run)
        testmode.validate_start(self.env(XDG_RUNTIME_DIR=run), self.home, self.real)
        testmode.validate_start(self.env(), self.home, self.real)  # none: the socket falls back under HOME's state folder

    def test_a_runtime_dir_outside_it_is_refused(self):
        for runtime in ("/run/user/1000", self.real, os.path.join(self.real, "run")):
            with self.subTest(runtime=runtime):
                with self.assertRaises(testmode.TestModeError) as raised:
                    testmode.validate_start(self.env(XDG_RUNTIME_DIR=runtime), self.home, self.real)
                self.assertIn("control socket", str(raised.exception))
                self.assertIn("XDG_RUNTIME_DIR", str(raised.exception))

    def test_a_runtime_dir_that_is_a_link_out_of_the_scratch_root_is_refused(self):
        outside = os.path.join(self.real, "outside")
        os.makedirs(outside)
        link = os.path.join(self.root, "run1", "link")
        os.symlink(outside, link)
        with self.assertRaises(testmode.TestModeError):
            testmode.validate_start(self.env(XDG_RUNTIME_DIR=link), self.home, self.real)

    def test_outside_test_mode_nothing_is_checked(self):
        testmode.validate_start({"XDG_RUNTIME_DIR": "/run/user/1000"}, "/anywhere", self.real)


if __name__ == "__main__":
    unittest.main()
