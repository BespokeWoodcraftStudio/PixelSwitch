"""logfile (the Mac line format, no emails), testmode (spec 5.7 rule 2) and paths (spec 5.2)."""
from __future__ import annotations

import os
import re
import stat
import unittest

from pixelswitch import testmode
from pixelswitch.logfile import Log, scrub, timestamp
from pixelswitch.paths import Paths, home_from_env

from .lb_support import tmpdir


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class LogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tmpdir(self)
        self.path = os.path.join(self.dir, "state", "pixelswitch.log")

    def test_the_line_format_and_header(self):
        log = Log(self.path, clock=lambda: 1_700_000_000.123)
        log.category("AppState").info("[init] hello")
        log.category("Claude").warning("careful")
        log.category("Claude").error("bad")
        log.close()
        lines = read(self.path).splitlines()
        self.assertRegex(lines[0], r"^====== PixelSwitch launched 2023-11-14T22:13:20\.123Z ======$")
        self.assertEqual(lines[1], "[2023-11-14T22:13:20.123Z] [INFO] [AppState] [init] hello")
        self.assertEqual(lines[2], "[2023-11-14T22:13:20.123Z] [WARN] [Claude] careful")
        self.assertEqual(lines[3], "[2023-11-14T22:13:20.123Z] [ERROR] [Claude] bad")

    def test_the_file_is_0600_in_a_0700_folder(self):
        previous = os.umask(0o022)
        try:
            log = Log(self.path)
        finally:
            os.umask(previous)
        log.close()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)

    def test_a_wider_existing_log_is_tightened(self):
        os.makedirs(os.path.dirname(self.path), mode=0o700)
        with open(self.path, "w") as handle:
            handle.write("old\n")
        os.chmod(self.path, 0o644)
        Log(self.path).close()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_emails_and_tokens_never_reach_the_file(self):
        log = Log(self.path)
        log.category("X").info("account ahmed@example.com has sk-ant-oat01-abcDEF123_-xyz and Bearer abc.def-ghi")
        log.close()
        text = read(self.path)
        self.assertNotIn("ahmed@example.com", text)
        self.assertNotIn("sk-ant-oat01", text)
        self.assertNotIn("abc.def-ghi", text)
        self.assertIn("<email>", text)
        self.assertIn("<token>", text)

    def test_scrub_leaves_ordinary_text_alone(self):
        self.assertEqual(scrub("[fetchUsage] 11111111-AAAA: session=42%"), "[fetchUsage] 11111111-AAAA: session=42%")
        self.assertEqual(scrub("a@b.co c"), "<email> c")

    def test_rotation_keeps_two_prior_files(self):
        log = Log(self.path, max_bytes=300, keep=2)
        for index in range(60):
            log.category("X").info(f"line number {index} " + "x" * 20)
        log.close()
        names = sorted(os.listdir(os.path.dirname(self.path)))
        self.assertEqual(names, ["pixelswitch.log", "pixelswitch.log.1", "pixelswitch.log.2"])
        self.assertIn("rotated at", read(self.path))

    def test_timestamp_is_utc_with_milliseconds(self):
        self.assertRegex(timestamp(0.5), r"^1970-01-01T00:00:00\.500Z$")


class TestModeTests(unittest.TestCase):
    def setUp(self):
        self.real = tmpdir(self)
        self.root = os.path.join(self.real, ".cache", "pixelswitch-dev")
        os.makedirs(os.path.join(self.root, "run1"))

    def env(self, **extra):
        env = {"PIXELSWITCH_TEST_MODE": "1"}
        env.update(extra)
        return env

    def test_not_test_mode_validates_anything(self):
        testmode.validate_start({}, "/anywhere", self.real)
        testmode.validate_start({"PIXELSWITCH_TEST_MODE": "0"}, "/anywhere", self.real)
        self.assertFalse(testmode.enabled({"PIXELSWITCH_TEST_MODE": "true"}))

    def test_home_must_be_under_the_scratch_root(self):
        testmode.validate_start(self.env(), os.path.join(self.root, "run1"), self.real)
        with self.assertRaises(testmode.TestModeError):
            testmode.validate_start(self.env(), self.real, self.real)
        with self.assertRaises(testmode.TestModeError):
            testmode.validate_start(self.env(), self.root, self.real)  # the root itself is not "under" it
        with self.assertRaises(testmode.TestModeError):
            testmode.validate_start(self.env(), "/tmp/elsewhere", self.real)

    def test_a_symlink_out_of_the_scratch_root_is_refused(self):
        outside = os.path.join(self.real, "outside")
        os.makedirs(outside)
        link = os.path.join(self.root, "link")
        os.symlink(outside, link)
        with self.assertRaises(testmode.TestModeError):
            testmode.validate_start(self.env(), link, self.real)

    def test_refused_under_systemd(self):
        with self.assertRaises(testmode.TestModeError) as raised:
            testmode.validate_start(self.env(INVOCATION_ID="abc"), os.path.join(self.root, "run1"), self.real)
        self.assertIn("systemd", str(raised.exception))

    def test_blocked_hosts(self):
        for host in ("anthropic.com", "api.anthropic.com", "console.anthropic.com", "claude.com", "platform.claude.com",
                     "API.Anthropic.COM", "api.anthropic.com."):
            self.assertTrue(testmode.host_is_blocked(host), host)
        for host in ("127.0.0.1", "localhost", "notanthropic.com", "anthropic.com.evil.example", "example.com", None, ""):
            self.assertFalse(testmode.host_is_blocked(host), host)
        with self.assertRaises(testmode.TestModeError):
            testmode.check_url("https://api.anthropic.com/api/oauth/usage")
        testmode.check_url("http://127.0.0.1:9/x")

    def test_overrides_are_honoured_only_in_test_mode(self):
        env = {"PIXELSWITCH_TEST_API_BASE": "http://127.0.0.1:1234/", "PIXELSWITCH_TEST_RELEASE_BASE": "http://127.0.0.1:1/r",
               "PIXELSWITCH_TEST_PUBLIC_KEY": "abc"}
        self.assertIsNone(testmode.api_base(env))
        self.assertIsNone(testmode.release_base(env))
        self.assertIsNone(testmode.public_key(env))
        env["PIXELSWITCH_TEST_MODE"] = "1"
        self.assertEqual(testmode.api_base(env), "http://127.0.0.1:1234")
        self.assertEqual(testmode.release_base(env), "http://127.0.0.1:1/r")
        self.assertEqual(testmode.public_key(env), "abc")

    def test_a_real_api_base_is_refused(self):
        with self.assertRaises(testmode.TestModeError):
            testmode.api_base({"PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": "https://api.anthropic.com"})
        with self.assertRaises(testmode.TestModeError):
            testmode.api_base({"PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_API_BASE": "ftp://127.0.0.1"})


class PathsTests(unittest.TestCase):
    def test_every_path_comes_from_home(self):
        paths = Paths.from_env({"HOME": "/h/u", "XDG_RUNTIME_DIR": "/run/user/1", "XDG_CONFIG_HOME": "/elsewhere",
                                "XDG_DATA_HOME": "/elsewhere", "XDG_STATE_HOME": "/elsewhere"})
        self.assertEqual(paths.credentials, "/h/u/.claude/.credentials.json")
        self.assertEqual(paths.claude_json, "/h/u/.claude.json")
        self.assertEqual(paths.settings, "/h/u/.config/pixelswitch/settings.json")
        self.assertEqual(paths.accounts, "/h/u/.config/pixelswitch/accounts.json")
        self.assertEqual(paths.backups, "/h/u/.local/share/pixelswitch/backups.json")
        self.assertEqual(paths.log, "/h/u/.local/state/pixelswitch/pixelswitch.log")
        self.assertEqual(paths.daemon_lock, "/h/u/.local/state/pixelswitch/daemon.lock")
        self.assertEqual(paths.socket_path_file, "/h/u/.local/state/pixelswitch/socket-path")
        self.assertEqual(paths.socket, "/run/user/1/pixelswitch/control.sock")
        self.assertEqual(paths.autostart_file, "/h/u/.config/autostart/pixelswitch-tray.desktop")
        self.assertEqual(paths.good_marker(31), "/h/u/.local/state/pixelswitch/good-31")

    def test_the_socket_falls_back_to_the_state_folder(self):
        self.assertEqual(Paths.from_env({"HOME": "/h/u"}).socket, "/h/u/.local/state/pixelswitch/control.sock")
        self.assertEqual(Paths.from_env({"HOME": "/h/u", "XDG_RUNTIME_DIR": "relative"}).socket,
                         "/h/u/.local/state/pixelswitch/control.sock")

    def test_claude_config_dir_is_not_honoured(self):
        paths = Paths.from_env({"HOME": "/h/u", "CLAUDE_CONFIG_DIR": "/x", "CLAUDE_SECURESTORAGE_CONFIG_DIR": "/y"})
        self.assertEqual(paths.credentials, "/h/u/.claude/.credentials.json")

    def test_home_must_be_absolute(self):
        self.assertTrue(os.path.isabs(home_from_env({"HOME": "relative"})))
        self.assertEqual(home_from_env({"HOME": "/a/b/"}), "/a/b")


if __name__ == "__main__":
    unittest.main()
