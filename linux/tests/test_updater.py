"""The updater's I/O (spec 5.4) against a local release server with a signed tarball, the rollback
script, and pruning. Nothing here touches systemctl: the restart is a recorder."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tarfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pixelswitch import ed25519
from pixelswitch import rollback as rollback_module
from pixelswitch.api import Transport
from pixelswitch.logfile import Log
from pixelswitch.paths import Paths
from pixelswitch.updater import (
    Updater, flip_symlink, parse_version_dir, prune_old_versions, read_link_name, version_dir_name,
)
from pixelswitch.version import VersionInfo

from .lb_support import FakeClock, read_file, tmpdir, wait_for

SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
PUBLIC = base64.b64encode(ed25519.public_key_from_seed(SEED)).decode()
SELF_CHECK_OK = "import sys\nif '--self-check' in sys.argv:\n    print('pixelswitchd 2.1 (build 32)')\n"
SELF_CHECK_BAD = "import sys\nsys.exit(1)\n"


def tarball(files: dict[str, bytes], extra=None) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if name.startswith("bin/") else 0o644
            archive.addfile(info, io.BytesIO(data))
        if extra:
            extra(archive)
    return buffer.getvalue()


def good_files(self_check=SELF_CHECK_OK, version="2.1", build=32):
    return {"VERSION": f"version={version}\nbuild={build}\n".encode(), "bin/pixelswitchd": self_check.encode(),
            "pixelswitch/__init__.py": b""}


class ReleaseServer:
    def __init__(self):
        self.routes: dict[str, tuple[int, bytes]] = {}
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                status, body = outer.routes.get(self.path, (404, b"missing"))
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/releases"

    def publish(self, data: bytes, version="2.1", build=32, **overrides):
        name = f"PixelSwitch-{version}-linux.tar.gz"
        feed = {"version": version, "build": build, "url": f"{self.base}/download/v{version}/{name}", "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "signature": base64.b64encode(ed25519.sign(SEED, data)).decode()}
        feed.update(overrides)
        self.routes["/releases/latest/download/latest-linux.json"] = (200, json.dumps(feed).encode())
        self.routes[f"/releases/download/v{version}/{name}"] = (200, data)
        return feed

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class UpdaterCase(unittest.TestCase):
    def setUp(self):
        self.home = tmpdir(self)
        self.paths = Paths(self.home, None)
        self.server = ReleaseServer()
        self.addCleanup(self.server.stop)
        self.restarts: list[tuple] = []
        self.idle_answers: list[bool] = []
        self.clock = FakeClock()
        os.makedirs(self.paths.app_dir)
        os.makedirs(os.path.join(self.paths.app_dir, "2.0-31"))
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.0-31")

    def updater(self, **kw):
        def restart():
            self.restarts.append((read_link_name(os.path.join(self.paths.app_dir, "current")),))

        def idle():
            return self.idle_answers.pop(0) if self.idle_answers else True

        options = dict(paths=self.paths, log=Log(None).category("t"), running=VersionInfo("2.0", 31),
                       transport=Transport(user_agent="PixelSwitch/test", test_mode=True), automatic=lambda: True,
                       idle=idle, restart=restart, feed_url=self.server.base + "/latest/download/latest-linux.json",
                       release_base=self.server.base, public_key=PUBLIC, scheduled=False, sleep=self.clock.sleep,
                       clock=self.clock)
        options.update(kw)
        return Updater(**options)

    def current(self):
        return read_link_name(os.path.join(self.paths.app_dir, "current"))

    def previous(self):
        return read_link_name(os.path.join(self.paths.app_dir, "previous"))

    def assert_untouched(self):
        self.assertEqual(self.current(), "2.0-31")
        self.assertEqual(self.restarts, [])
        self.assertEqual(sorted(n for n in os.listdir(self.paths.app_dir) if not n.startswith(".")), ["2.0-31", "current"])
        self.assertEqual([n for n in os.listdir(self.paths.data_dir) if n.startswith(".update")], [], "staging removed")


class HappyPathTests(UpdaterCase):
    def test_download_verify_extract_check_selfcheck_flip_restart(self):
        self.server.publish(tarball(good_files()))
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.version, outcome.build), ("installed", "2.1", 32))
        self.assertEqual(self.current(), "2.1-32")
        self.assertEqual(self.previous(), "2.0-31", "the previous version is kept and named")
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "2.0-31")))
        self.assertEqual(self.restarts, [("2.1-32",)], "the restart came after the flip")
        self.assertTrue(os.path.isfile(os.path.join(self.paths.app_dir, "2.1-32", "bin", "pixelswitchd")))
        for relative in ("", "bin", "bin/pixelswitchd", "pixelswitch", "VERSION"):
            mode = os.stat(os.path.join(self.paths.app_dir, "2.1-32", relative)).st_mode
            self.assertEqual(mode & 0o022, 0, f"{relative or '.'} is writable by group or others")
        self.assertEqual([n for n in os.listdir(self.paths.data_dir) if n.startswith(".update")], [])
        self.assertEqual(self.server.requests[0], "/releases/latest/download/latest-linux.json")

    def test_the_installed_version_is_never_group_or_world_writable_whatever_the_umask(self):
        self.server.publish(tarball(good_files()))
        previous = os.umask(0o000)
        try:
            self.assertEqual(self.updater().check_and_install().kind, "installed")
        finally:
            os.umask(previous)
        for directory, names, files in os.walk(os.path.join(self.paths.app_dir, "2.1-32")):
            for name in names + files:
                self.assertEqual(os.stat(os.path.join(directory, name)).st_mode & 0o022, 0, os.path.join(directory, name))

    def test_an_archive_with_one_top_level_folder_works_too(self):
        files = {f"PixelSwitch-2.1/{name}": data for name, data in good_files().items()}
        self.server.publish(tarball(files))
        self.assertEqual(self.updater().check_and_install().kind, "installed")
        self.assertEqual(self.current(), "2.1-32")

    def test_current_is_flipped_only_when_nothing_is_in_progress(self):
        self.server.publish(tarball(good_files()))
        self.idle_answers = [False, False, False, True]
        seen = []
        updater = self.updater()
        real_sleep = updater._sleep
        updater._sleep = lambda s: (seen.append((self.current(), len(self.restarts))), real_sleep(s))[1]
        outcome = updater.check_and_install()
        self.assertEqual(outcome.kind, "installed")
        self.assertEqual(seen, [("2.0-31", 0)] * 3, "while busy: not flipped, not restarted")
        self.assertEqual(self.restarts, [("2.1-32",)])

    def test_if_it_stays_busy_the_flip_waits_for_the_next_check_without_downloading_again(self):
        self.server.publish(tarball(good_files()))
        self.idle_answers = [False] * 10_000
        outcome = self.updater().check_and_install()
        self.assertEqual(outcome.kind, "waiting")
        self.assertEqual(self.current(), "2.0-31")
        self.assertEqual(self.restarts, [])
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "2.1-32")), "installed on disk")
        downloads = [r for r in self.server.requests if r.endswith(".tar.gz")]
        self.idle_answers = []
        again = self.updater().check_and_install()
        self.assertEqual(again.kind, "installed")
        self.assertEqual([r for r in self.server.requests if r.endswith(".tar.gz")], downloads, "no second download")
        self.assertEqual(self.current(), "2.1-32")

    def test_nothing_newer_is_a_quiet_no_op(self):
        self.server.publish(tarball(good_files(build=31, version="2.0")), version="2.0", build=31)
        self.assertEqual(self.updater().check_and_install().kind, "none")
        self.assert_untouched()

    def test_a_404_feed_is_no_update(self):
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("none", "no feed"))
        self.assert_untouched()

    def test_an_unreachable_server_is_quiet(self):
        updater = self.updater(feed_url="http://127.0.0.1:1/x.json")
        self.assertEqual(updater.check_and_install().kind, "none")
        self.assert_untouched()


class RefusalTests(UpdaterCase):
    def refused(self, reason, kind="failed"):
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), (kind, reason))
        self.assert_untouched()

    def test_a_url_outside_the_release_prefix(self):
        self.server.publish(tarball(good_files()), url="https://evil.example/PixelSwitch.tar.gz")
        self.refused("url")

    def test_an_oversize_feed_size(self):
        self.server.publish(tarball(good_files()), size=60 * 1024 * 1024)
        self.refused("size")

    def test_a_download_larger_than_the_feed_promised_is_cut_off(self):
        data = tarball(good_files())
        self.server.publish(data, size=len(data) - 10)
        self.refused("size")
        self.assertEqual(os.listdir(self.paths.data_dir) if os.path.isdir(self.paths.data_dir) else [], ["app"])

    def test_a_download_smaller_than_promised(self):
        data = tarball(good_files())
        self.server.publish(data, size=len(data) + 10)
        self.refused("size")

    def test_a_tampered_tarball(self):
        data = tarball(good_files())
        feed = self.server.publish(data)
        tampered = bytearray(data)
        tampered[len(tampered) // 2] ^= 0xFF
        name = feed["url"].rsplit("/", 1)[1]
        self.server.routes[f"/releases/download/v2.1/{name}"] = (200, bytes(tampered))
        self.refused("sha256")

    def test_a_tampered_tarball_with_a_matching_sha256_still_fails_the_signature(self):
        data = tarball(good_files())
        evil = tarball(good_files(self_check=SELF_CHECK_OK + "# evil\n"))
        self.server.publish(evil, signature=base64.b64encode(ed25519.sign(SEED, data)).decode())
        self.refused("signature")

    def test_the_wrong_key(self):
        other = base64.b64encode(ed25519.public_key_from_seed(bytes(range(32)))).decode()
        self.server.publish(tarball(good_files()))
        updater = self.updater(public_key=other)
        outcome = updater.check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("failed", "signature"))
        self.assert_untouched()

    def test_a_version_file_that_does_not_match_the_feed(self):
        self.server.publish(tarball(good_files(build=40)))
        self.refused("version-file")

    def test_a_version_file_that_would_be_a_downgrade(self):
        self.server.publish(tarball(good_files(build=31, version="2.1")), build=32)
        self.refused("version-file")

    def test_no_version_file(self):
        files = good_files()
        del files["VERSION"]
        self.server.publish(tarball(files))
        self.refused("version-file")

    def test_path_traversal_extracts_nothing_anywhere(self):
        def evil(archive):
            info = tarfile.TarInfo("../evil.txt")
            info.size = 4
            archive.addfile(info, io.BytesIO(b"evil"))

        self.server.publish(tarball(good_files(), evil))
        self.refused("archive")
        self.assertFalse(os.path.exists(os.path.join(self.paths.data_dir, "evil.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "evil.txt")))

    def test_a_symlink_that_leaves_the_folder(self):
        def link(archive):
            info = tarfile.TarInfo("bin/escape")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)

        self.server.publish(tarball(good_files(), link))
        self.refused("archive")

    def test_a_self_check_that_fails_removes_the_new_version_and_leaves_current_alone(self):
        self.server.publish(tarball(good_files(self_check=SELF_CHECK_BAD)))
        self.refused("self-check")
        self.assertFalse(os.path.exists(os.path.join(self.paths.app_dir, "2.1-32")))

    def test_a_self_check_that_prints_another_version_fails(self):
        self.server.publish(tarball(good_files(self_check="print('pixelswitchd 9.9 (build 99)')\n")))
        self.refused("self-check")

    def test_a_missing_launcher_fails_the_self_check(self):
        files = good_files()
        del files["bin/pixelswitchd"]
        self.server.publish(tarball(files))
        self.refused("self-check")


class ScheduleTests(UpdaterCase):
    def test_a_scheduled_check_respects_the_automatic_setting_and_check_now_does_not(self):
        self.server.publish(tarball(good_files()))
        automatic = [False]
        updater = self.updater(automatic=lambda: automatic[0], scheduled=True, first_delay=0.05, interval=0.05)
        updater.start()
        self.addCleanup(updater.stop)
        import time

        time.sleep(0.4)
        self.assertEqual(self.server.requests, [], "automatic is off: no request at all")
        updater.check_now()
        self.assertTrue(wait_for(lambda: self.current() == "2.1-32", 20))
        self.assertEqual(len(self.restarts), 1)

    def test_with_automatic_on_a_scheduled_check_installs(self):
        self.server.publish(tarball(good_files()))
        updater = self.updater(scheduled=True, first_delay=0.05, interval=3600)
        updater.start()
        self.addCleanup(updater.stop)
        self.assertTrue(wait_for(lambda: self.current() == "2.1-32", 20))

    def test_not_scheduled_nothing_runs_by_itself(self):
        self.server.publish(tarball(good_files()))
        updater = self.updater(scheduled=False)
        updater.start()
        self.addCleanup(updater.stop)
        import time

        time.sleep(0.3)
        self.assertEqual(self.server.requests, [])

    def test_the_default_schedule_is_two_minutes_then_six_hours(self):
        from pixelswitch import updater as module

        self.assertEqual((module.FIRST_CHECK_DELAY, module.CHECK_INTERVAL), (120.0, 6 * 3600.0))

    def test_a_second_check_while_one_runs_is_ignored(self):
        updater = self.updater()
        updater._busy.acquire()
        self.assertEqual(updater.check_and_install().kind, "none")
        updater._busy.release()


class VersionsOnDiskTests(UpdaterCase):
    def make_versions(self, *names):
        for name in names:
            os.makedirs(os.path.join(self.paths.app_dir, name), exist_ok=True)

    def test_names(self):
        self.assertEqual(version_dir_name("2.1", 32), "2.1-32")
        self.assertEqual(parse_version_dir("2.1-32"), ("2.1", 32))
        for bad in ("current", "2.1", "2.1-x", "../2.1-32", "2.1-32-x", ""):
            self.assertIsNone(parse_version_dir(bad), bad)

    def test_prune_keeps_current_previous_and_anything_newer_and_deletes_only_older(self):
        self.make_versions("1.8-28", "1.9-30", "2.0-31", "2.1-32", "2.2-33", "notes")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        removed = prune_old_versions(self.paths.app_dir)
        self.assertEqual(sorted(removed), ["1.8-28", "1.9-30"])
        self.assertEqual(sorted(n for n in os.listdir(self.paths.app_dir)), ["2.0-31", "2.1-32", "2.2-33", "current", "notes", "previous"])

    def test_prune_without_links_touches_nothing(self):
        self.make_versions("1.8-28")
        os.unlink(os.path.join(self.paths.app_dir, "current"))
        self.assertEqual(prune_old_versions(self.paths.app_dir), [])
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "1.8-28")))

    def test_the_flip_is_atomic_and_relative(self):
        link = os.path.join(self.paths.app_dir, "current")
        flip_symlink(link, "2.1-32")
        self.assertEqual(os.readlink(link), "2.1-32")
        self.assertEqual([n for n in os.listdir(self.paths.app_dir) if ".new-" in n], [])


class RollbackTests(unittest.TestCase):
    """The rollback script plays the sequence the spec names against StartLimitBurst=4."""

    def setUp(self):
        self.home = tmpdir(self)
        self.paths = Paths(self.home, None)
        os.makedirs(self.paths.app_dir)
        os.makedirs(self.paths.state_dir)
        for name in ("2.0-31", "2.1-32"):
            os.makedirs(os.path.join(self.paths.app_dir, name))
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.0-31")
        self.starts: list[str] = []

    def cur(self):
        return read_link_name(os.path.join(self.paths.app_dir, "current"))

    def systemd_start(self, writes_marker):
        """One `systemctl start`: ExecStartPre (rollback), then the daemon of whatever current is."""
        message = rollback_module.run(self.home)
        build = self.cur()
        self.starts.append(build)
        if writes_marker(build):
            with open(self.paths.good_marker(int(build.split("-")[1])), "w") as handle:
                handle.write("1")
        return message

    def test_boot_update_two_failures_and_the_rolled_back_start_fit_inside_burst_4(self):
        proven = lambda build: build == "2.0-31"
        self.systemd_start(proven)  # 1. boot start: the old build is proven by its marker
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")  # the update flips current ...
        self.systemd_start(proven)  # 2. ... and restarts: attempt 1 of 2.1-32, it fails (no marker)
        self.systemd_start(proven)  # 3. Restart=on-failure: attempt 2, fails again
        message = self.systemd_start(proven)  # 4. the script rolls back before the third attempt
        self.assertEqual(self.starts, ["2.0-31", "2.1-32", "2.1-32", "2.0-31"])
        self.assertEqual(len(self.starts), 4, "StartLimitBurst=4 allows exactly this many starts")
        self.assertIn("rolled back to 2.0-31", message)
        self.assertEqual(self.cur(), "2.0-31")
        self.assertTrue(os.path.exists(self.paths.good_marker(31)))

    def test_a_new_build_that_writes_its_marker_is_never_rolled_back(self):
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        everything_works = lambda build: True
        for _ in range(6):
            self.systemd_start(everything_works)
        self.assertEqual(set(self.starts), {"2.1-32"})
        self.assertEqual(self.cur(), "2.1-32")

    def test_one_failure_then_success_keeps_the_new_build(self):
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        results = iter([False, True])
        self.systemd_start(lambda build: next(results))
        self.systemd_start(lambda build: next(results))
        self.systemd_start(lambda build: True)
        self.assertEqual(self.starts, ["2.1-32"] * 3)

    def test_no_previous_version_means_no_rollback(self):
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        for _ in range(5):
            self.systemd_start(lambda build: False)
        self.assertEqual(set(self.starts), {"2.1-32"})

    def test_a_previous_that_is_gone_is_not_flipped_to(self):
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "1.9-30")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        for _ in range(4):
            self.systemd_start(lambda build: False)
        self.assertEqual(self.cur(), "2.1-32")

    def test_after_a_rollback_the_counter_starts_over_for_the_old_build(self):
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        for _ in range(3):
            self.systemd_start(lambda build: False)
        self.assertEqual(self.cur(), "2.0-31")
        state = json.loads(read_file(os.path.join(self.paths.state_dir, "rollback.json")))
        self.assertEqual(state, {"build": 31, "attempts": 0})

    def test_it_never_fails_and_never_raises(self):
        self.assertIn("no current version", rollback_module.run(tmpdir(self)))
        with open(os.path.join(self.paths.state_dir, "rollback.json"), "w") as handle:
            handle.write("{broken")
        self.assertIn("attempt 1", rollback_module.run(self.home))
        self.assertEqual(rollback_module.main([]), 0)

    def test_it_is_standard_library_only(self):
        source = read_file(rollback_module.__file__)
        imports = [line.split()[1].split(".")[0] for line in source.splitlines() if line.startswith(("import ", "from "))]
        self.assertTrue(set(imports) <= {"__future__", "json", "os", "re", "sys", "tempfile"}, imports)
        self.assertNotIn("pixelswitch", imports)


if __name__ == "__main__":
    unittest.main()
