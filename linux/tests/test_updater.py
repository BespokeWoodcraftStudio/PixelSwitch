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
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pixelswitch import ed25519
from pixelswitch import rollback as rollback_module
from pixelswitch.api import Transport
from pixelswitch.logfile import Log
from pixelswitch.paths import Paths
from pixelswitch.updater import (
    Updater, bad_builds_path, flip_symlink, parse_version_dir, prune_old_versions, read_bad_builds, read_link_name,
    record_bad_build, version_dir_name,
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
    """A local release server. A route is ``(status, body)`` or ``(status, body, headers)``; a 302 route
    carries a ``Location`` header. ``redirect`` builds a chain, ``slow`` a download that dribbles."""

    def __init__(self):
        self.routes: dict[str, tuple] = {}
        self.slow_routes: dict[str, int] = {}
        self.requests: list[str] = []
        self.received_headers: list[dict[str, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                outer.requests.append(self.path)
                outer.received_headers.append({key.lower(): value for key, value in self.headers.items()})
                if self.path in outer.slow_routes:
                    total = outer.slow_routes[self.path]
                    self.send_response(200)
                    self.send_header("Content-Length", str(total))
                    self.end_headers()
                    try:
                        for _ in range(total // 1024):
                            self.wfile.write(b"x" * 1024)
                            self.wfile.flush()
                            time.sleep(0.05)
                    except OSError:
                        pass
                    return
                route = outer.routes.get(self.path, (404, b"missing"))
                status, body = route[0], route[1]
                self.send_response(status)
                for key, value in (route[2] if len(route) > 2 else {}).items():
                    self.send_header(key, value)
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

    def redirect(self, path: str, *hops: str) -> str:
        """Makes ``path`` answer 302 -> hop 1 -> 302 -> hop 2 ... and the content ends at the LAST hop
        (the route already stored for ``path`` moves there). Returns the last path."""
        final = self.routes.pop(path, (404, b"missing"))
        chain = [path, *hops]
        for here, there in zip(chain, chain[1:]):
            self.routes[here] = (302, b"", {"Location": there})
        self.routes[chain[-1]] = final
        return chain[-1]

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
                       transport=Transport(user_agent="PixelSwitch/test", test_mode=True, follow_redirects=True),
                       automatic=lambda: True,
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


class UpdaterLogCase(UpdaterCase):
    """An updater whose log lines can be read back."""

    def setUp(self):
        super().setUp()
        self.log_path = os.path.join(self.home, "update.log")
        self.log = Log(self.log_path)
        self.addCleanup(self.log.close)

    def updater(self, **kw):
        kw.setdefault("log", self.log.category("Update"))
        return super().updater(**kw)

    def update_lines(self):
        try:
            return [line for line in read_file(self.log_path).splitlines() if "[update]" in line]
        except OSError:
            return []

    def publish_build(self, version, build, **kw):
        files = good_files(self_check=f"import sys\nif '--self-check' in sys.argv:\n    print('pixelswitchd {version} (build {build})')\n",
                           version=version, build=build)
        return self.server.publish(tarball(files), version=version, build=build, **kw)


class RedirectTests(UpdaterLogCase):
    """GitHub answers a release download with redirects (api.py: RELEASE_REDIRECT_HOSTS)."""

    def test_the_feed_and_the_tarball_arrive_through_two_hop_redirect_chains_and_the_update_installs(self):
        feed = self.server.publish(tarball(good_files()))
        name = feed["url"].rsplit("/", 1)[1]
        self.server.redirect("/releases/latest/download/latest-linux.json",
                             "/releases/download/v2.1/latest-linux.json", "/assets/feed-blob")
        self.server.redirect(f"/releases/download/v2.1/{name}", "/assets/hop-a", "/assets/tarball-blob")
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.version, outcome.build), ("installed", "2.1", 32))
        self.assertEqual(self.current(), "2.1-32")
        self.assertEqual(self.restarts, [("2.1-32",)])
        self.assertIn("/assets/tarball-blob", self.server.requests)
        self.assertEqual(self.server.requests.count("/assets/feed-blob"), 1)

    def test_no_authorization_or_cookie_header_is_sent_to_any_hop(self):
        feed = self.server.publish(tarball(good_files()))
        name = feed["url"].rsplit("/", 1)[1]
        self.server.redirect("/releases/latest/download/latest-linux.json", "/releases/download/v2.1/latest-linux.json")
        self.server.redirect(f"/releases/download/v2.1/{name}", "/assets/blob")
        self.assertEqual(self.updater().check_and_install().kind, "installed")
        self.assertGreaterEqual(len(self.server.received_headers), 4)
        for headers in self.server.received_headers:
            for forbidden in ("authorization", "cookie", "proxy-authorization"):
                self.assertNotIn(forbidden, headers)

    def test_a_404_at_the_end_of_the_feed_chain_is_no_update(self):
        self.server.routes["/releases/latest/download/latest-linux.json"] = (
            302, b"", {"Location": "/releases/download/v1.9/latest-linux.json"})  # GitHub: no such asset in that release
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("none", "no feed"))
        self.assert_untouched()
        self.assertEqual(self.update_lines(), [], "quietly")

    def test_a_404_at_the_end_of_the_tarball_chain_is_no_update(self):
        feed = self.server.publish(tarball(good_files()))
        name = feed["url"].rsplit("/", 1)[1]
        del self.server.routes[f"/releases/download/v2.1/{name}"]
        self.server.routes[f"/releases/download/v2.1/{name}"] = (302, b"", {"Location": "/assets/gone"})
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("none", "no download"))
        self.assert_untouched()

    def test_more_than_five_redirects_is_unreachable_and_installs_nothing(self):
        self.server.publish(tarball(good_files()))
        self.server.redirect("/releases/latest/download/latest-linux.json",
                             *[f"/hop/{n}" for n in range(1, 7)])  # six hops
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("none", "unreachable"))
        self.assert_untouched()

    def test_exactly_five_redirects_are_followed(self):
        self.server.publish(tarball(good_files()))
        self.server.redirect("/releases/latest/download/latest-linux.json", *[f"/hop/{n}" for n in range(1, 6)])
        self.assertEqual(self.updater().check_and_install().kind, "installed")

    def test_a_redirect_loop_ends(self):
        self.server.routes["/releases/latest/download/latest-linux.json"] = (
            302, b"", {"Location": "/releases/latest/download/latest-linux.json"})
        outcome = self.updater().check_and_install()
        self.assertEqual(outcome.kind, "none")  # urllib's own loop guard or ours: either way, no update
        self.assertLessEqual(len(self.server.requests), 6)
        self.assert_untouched()

    def test_a_redirect_to_a_blocked_host_is_refused(self):
        self.server.routes["/releases/latest/download/latest-linux.json"] = (
            302, b"", {"Location": "http://api.anthropic.com/steal"})
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("none", "unreachable"))
        self.assert_untouched()

    def test_the_size_cut_off_holds_across_redirects(self):
        data = tarball(good_files())
        feed = self.server.publish(data, size=len(data) - 10)
        name = feed["url"].rsplit("/", 1)[1]
        self.server.redirect(f"/releases/download/v2.1/{name}", "/assets/a", "/assets/b")
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("failed", "size"))
        self.assert_untouched()

    def test_the_token_transport_still_never_follows_a_redirect(self):
        self.server.routes["/r"] = (302, b"", {"Location": "/target"})
        self.server.routes["/target"] = (200, b"hello")
        plain = Transport(user_agent="PixelSwitch/test", test_mode=True)
        reply = plain.request("GET", self.server.base.replace("/releases", "") + "/r", timeout=5)
        self.assertEqual(reply.status, 302)
        self.assertNotIn("/target", self.server.requests)


class BadBuildTests(UpdaterLogCase):
    """A build that failed its self-check, or was rolled back, is never installed again (review LB: process-updater P1)."""

    def systemd_start(self, proven):
        message = rollback_module.run(self.home)
        build = self.current()
        if proven(build):
            with open(self.paths.good_marker(int(build.split("-")[1])), "w") as handle:
                handle.write("1")
        return message

    def play_a_failed_update(self):
        """Boot (proven old build), the update, two failed starts, the rollback."""
        self.systemd_start(lambda build: True)
        self.assertEqual(self.updater().check_and_install().kind, "installed")
        self.assertEqual(self.current(), "2.1-32")
        proven = lambda build: build == "2.0-31"
        self.systemd_start(proven)
        self.systemd_start(proven)
        self.assertIn("rolled back", self.systemd_start(proven))
        self.assertEqual(self.current(), "2.0-31")

    def test_a_rolled_back_build_is_not_installed_again_and_a_later_build_is(self):
        self.server.publish(tarball(good_files()))
        self.play_a_failed_update()
        self.assertEqual(self.restarts, [("2.1-32",)])
        requests_before = len(self.server.requests)
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "2.1-32")), "still on disk, as in the review")
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("none", "rolled back"))
        self.assertEqual(self.current(), "2.0-31", "it did not flip back to the bad build")
        self.assertEqual(self.restarts, [("2.1-32",)], "and asked for no second restart")
        self.assertEqual(len(self.server.requests), requests_before + 1, "only the feed was asked for: no download")
        self.assertFalse(os.path.exists(os.path.join(self.paths.app_dir, "2.1-32")), "the bad version folder is removed")
        # a later, higher build installs
        self.publish_build("2.2", 33)
        outcome = self.updater().check_and_install()
        self.assertEqual((outcome.kind, outcome.build), ("installed", 33))
        self.assertEqual(self.current(), "2.2-33")
        self.assertEqual(self.previous(), "2.0-31")

    def test_the_skip_is_logged_once_per_run_and_the_state_file_is_private(self):
        self.server.publish(tarball(good_files()))
        self.play_a_failed_update()
        updater = self.updater()
        for _ in range(3):
            updater.check_and_install()
        lines = [line for line in self.update_lines() if "rolled back" in line]
        self.assertEqual(len(lines), 1, lines)
        path = bad_builds_path(self.paths)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(read_file(path)), {"builds": [32]})
        self.assertEqual(read_bad_builds(self.paths), {32})

    def test_a_build_that_fails_its_self_check_is_remembered_and_not_downloaded_again(self):
        self.server.publish(tarball(good_files(self_check=SELF_CHECK_BAD)))
        self.assertEqual(self.updater().check_and_install().reason, "self-check")
        self.assertEqual(read_bad_builds(self.paths), {32})
        downloads = len([r for r in self.server.requests if r.endswith(".tar.gz")])
        self.assertEqual(downloads, 1)
        again = self.updater().check_and_install()
        self.assertEqual((again.kind, again.reason), ("none", "rolled back"))
        self.assertEqual(len([r for r in self.server.requests if r.endswith(".tar.gz")]), downloads)
        self.assert_untouched()
        self.publish_build("2.2", 33)
        self.assertEqual(self.updater().check_and_install().kind, "installed")

    def test_a_self_check_that_could_not_be_run_is_not_held_against_the_build(self):
        self.server.publish(tarball(good_files()))
        outcome = self.updater(python="/nonexistent/python3").check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("failed", "self-check"))
        self.assertEqual(read_bad_builds(self.paths), set())
        self.assertFalse(os.path.exists(bad_builds_path(self.paths)))
        self.assertEqual(self.updater().check_and_install().kind, "installed", "tried again, and it works")

    def test_rollback_without_a_previous_version_records_nothing(self):
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.0-31")
        for _ in range(5):
            self.systemd_start(lambda build: False)
        self.assertFalse(os.path.exists(bad_builds_path(self.paths)))

    def test_the_file_is_read_defensively(self):
        os.makedirs(self.paths.state_dir, exist_ok=True)
        path = bad_builds_path(self.paths)
        for text in ("", "{broken", "[]", '{"builds": "x"}', '{"builds": [true, "a", -1, 7]}', "[" * 20000):
            with open(path, "w") as handle:
                handle.write(text)
            self.assertIn(read_bad_builds(self.paths), (set(), {7}), text[:20])
        record_bad_build(self.paths, 40)
        self.assertEqual(read_bad_builds(self.paths), {40})

    def test_prune_also_removes_the_folders_of_bad_builds(self):
        for name in ("2.0-31", "2.1-32", "2.2-33"):
            os.makedirs(os.path.join(self.paths.app_dir, name), exist_ok=True)
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        self.assertEqual(prune_old_versions(self.paths.app_dir), [])
        self.assertEqual(prune_old_versions(self.paths.app_dir, bad_builds={32}), ["2.1-32"])
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "2.2-33")))
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "2.0-31")), "current is never pruned")


class MalformedFeedTests(UpdaterLogCase):
    def feed_body(self, body):
        self.server.routes["/releases/latest/download/latest-linux.json"] = (200, body)

    def test_a_deeply_nested_feed_is_no_update_with_one_log_line(self):
        for body in (b"[" * 16000 + b"]" * 16000, b'{"a":' * 12000 + b"1" + b"}" * 12000, b"[" * 30000):
            self.feed_body(body)
            outcome = self.updater().check_and_install()
            self.assertEqual((outcome.kind, outcome.reason), ("failed", "feed"))
            self.assert_untouched()
        self.assertEqual(len(self.update_lines()), 3, "one line per refused feed")

    def test_other_malformed_feeds_are_the_same(self):
        for body in (b"", b"\xff\xfe\x00", b"null", b"[1,2]", b'{"version": 5}', b"{" * 40, b'{"a": NaN}', b"x" * 70000):
            self.feed_body(body)
            before = len(self.update_lines())
            outcome = self.updater().check_and_install()
            self.assertEqual(outcome.kind, "failed", body[:20])
            self.assertEqual(len(self.update_lines()) - before, 1, body[:20])
            self.assert_untouched()


class StagingAndStopTests(UpdaterLogCase):
    def test_start_removes_unfinished_downloads_left_by_other_processes_only(self):
        data = self.paths.data_dir
        stale = os.path.join(data, ".update-99999-1700000000")
        os.makedirs(os.path.join(stale, "extracted", "bin"))
        with open(os.path.join(stale, "update.tar.gz"), "wb") as handle:
            handle.write(b"x" * 100)
        mine = os.path.join(data, f".update-{os.getpid()}-1700000001")
        os.makedirs(mine)
        elsewhere = os.path.join(self.home, "elsewhere")
        os.makedirs(elsewhere)
        link = os.path.join(data, ".update-88888-1")
        os.symlink(elsewhere, link)
        others = [os.path.join(data, name) for name in (".update-x-1", ".updates-1-1", "update-1-1", ".update-1-1-x")]
        for other in others:
            os.makedirs(other)
        updater = self.updater(scheduled=False)
        updater.start()
        self.addCleanup(updater.stop)
        self.assertFalse(os.path.exists(stale), "another process's staging folder is gone")
        self.assertTrue(os.path.isdir(mine), "this process's own folder is left to its own check")
        self.assertTrue(os.path.islink(link) and os.path.isdir(elsewhere), "a symlink is never followed")
        for other in others:
            self.assertTrue(os.path.isdir(other), other)
        self.assertTrue(os.path.isdir(os.path.join(data, "app")), "the install is untouched")
        self.assertEqual(len([line for line in self.update_lines() if "unfinished download" in line]), 1)

    def test_stop_abandons_a_slow_download_at_once_and_leaves_nothing_behind(self):
        data = tarball(good_files())
        feed = self.server.publish(data)
        name = feed["url"].rsplit("/", 1)[1]
        feed = self.server.publish(data, size=2_000_000)
        del self.server.routes[f"/releases/download/v2.1/{name}"]
        self.server.slow_routes[f"/releases/download/v2.1/{name}"] = 2_000_000  # about 100 s at 20 KiB/s
        updater = self.updater()
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(updater.check_and_install()), daemon=True)
        worker.start()
        self.assertTrue(wait_for(lambda: any(r.endswith(".tar.gz") for r in self.server.requests), 10))
        time.sleep(0.3)
        began = time.monotonic()
        updater.stop(0.0)
        worker.join(10)
        self.assertFalse(worker.is_alive(), "the download was abandoned")
        self.assertLess(time.monotonic() - began, 5.0)
        self.assertEqual(outcomes[0].kind, "none")
        self.assert_untouched()

    def test_stop_kills_a_running_self_check_and_keeps_the_verified_install_for_the_next_start(self):
        sleeper = "import sys,time\nif '--self-check' in sys.argv:\n    time.sleep(60)\n"
        self.server.publish(tarball(good_files(self_check=sleeper)))
        updater = self.updater()
        outcomes = []
        worker = threading.Thread(target=lambda: outcomes.append(updater.check_and_install()), daemon=True)
        worker.start()
        self.assertTrue(wait_for(lambda: os.path.isdir(os.path.join(self.paths.app_dir, "2.1-32")), 10))
        time.sleep(0.5)
        began = time.monotonic()
        updater.stop(0.0)
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertLess(time.monotonic() - began, 5.0)
        self.assertEqual((outcomes[0].kind, outcomes[0].reason), ("none", "stopping"))
        self.assertEqual(self.current(), "2.0-31")
        self.assertEqual(self.restarts, [])
        self.assertEqual(read_bad_builds(self.paths), set(), "a stop is not a failed self-check")
        self.assertTrue(os.path.isdir(os.path.join(self.paths.app_dir, "2.1-32")))

    def test_nothing_is_flipped_once_stop_has_been_called(self):
        self.server.publish(tarball(good_files()))
        updater = self.updater()
        original = updater._idle
        updater._idle = lambda: (updater._stop.set(), True)[1]  # SIGTERM arrives just as the daemon goes idle
        outcome = updater.check_and_install()
        self.assertEqual((outcome.kind, outcome.reason), ("waiting", "stopping"))
        self.assertEqual(self.current(), "2.0-31")
        self.assertEqual(self.restarts, [])
        updater._idle = original


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

    def test_the_sequence_fits_the_start_limit_in_the_packaged_unit_and_records_the_bad_build(self):
        import re as regex

        unit = read_file(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging",
                                      "pixelswitch.service"))
        burst = int(regex.search(r"^StartLimitBurst=(\d+)$", unit, regex.M).group(1))
        proven = lambda build: build == "2.0-31"
        self.systemd_start(proven)  # boot
        flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
        flip_symlink(os.path.join(self.paths.app_dir, "current"), "2.1-32")
        self.systemd_start(proven)  # the update restart: the new build fails (1st failure)
        self.systemd_start(proven)  # Restart=on-failure: fails again (2nd failure)
        message = self.systemd_start(proven)  # ExecStartPre rolls back; the old build runs
        self.assertLessEqual(len(self.starts), burst, f"{self.starts} must fit StartLimitBurst={burst}")
        self.assertEqual(self.starts[-1], "2.0-31")
        self.assertIn("rolled back", message)
        bad = os.path.join(self.paths.state_dir, "bad-builds")
        self.assertEqual(json.loads(read_file(bad)), {"builds": [32]})
        self.assertEqual(os.stat(bad).st_mode & 0o777, 0o600)
        # the old build, once it writes its marker, is proven and never touched again
        for _ in range(3):
            self.systemd_start(proven)
        self.assertEqual(set(self.starts[3:]), {"2.0-31"})

    def test_a_second_rollback_adds_to_the_bad_builds(self):
        for name in ("2.2-33",):
            os.makedirs(os.path.join(self.paths.app_dir, name))
        for bad_build, current_name in ((32, "2.1-32"), (33, "2.2-33")):
            flip_symlink(os.path.join(self.paths.app_dir, "previous"), "2.0-31")
            flip_symlink(os.path.join(self.paths.app_dir, "current"), current_name)
            for _ in range(3):
                self.systemd_start(lambda build: False)
            self.assertEqual(self.cur(), "2.0-31")
        self.assertEqual(json.loads(read_file(os.path.join(self.paths.state_dir, "bad-builds"))), {"builds": [32, 33]})

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
