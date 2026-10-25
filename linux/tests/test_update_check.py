"""The updater's checks (spec 5.4): the feed, the download, the VERSION file and the archive.
Unit tests for an older build, a mismatched VERSION, a wrong url prefix, an oversize download and
path traversal (and the other ways an archive can try to leave its folder)."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
import unittest

from pixelswitch import ed25519, update_check as uc
from pixelswitch.update_check import Feed, UpdateError

RUNNING_BUILD = 30
SEED = bytes.fromhex("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb")
PUBLIC_B64 = base64.b64encode(ed25519.public_key_from_seed(SEED)).decode()
PREFIX = "https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.0/"


def good_feed(**overrides):
    feed = {
        "version": "2.0", "build": 31, "url": PREFIX + "PixelSwitch-linux-x86_64.tar.gz",
        "size": 1234, "sha256": "ab" * 32, "signature": base64.b64encode(bytes(64)).decode(),
    }
    feed.update(overrides)
    return feed


def refused(test, feed, reason, **kwargs):
    with test.assertRaises(UpdateError) as caught:
        uc.validate_feed(feed, RUNNING_BUILD, **kwargs)
    test.assertEqual(caught.exception.reason, reason, feed)
    return caught.exception


class FeedParsingTests(unittest.TestCase):
    def test_a_good_document(self):
        self.assertEqual(uc.parse_feed(json.dumps(good_feed()))["version"], "2.0")
        self.assertEqual(uc.parse_feed(json.dumps(good_feed()).encode())["build"], 31)
        self.assertEqual(uc.parse_feed({"a": 1}), {"a": 1})

    def test_bad_documents(self):
        for text in ("", "not json", "[1]", "5", "null", b"\xff\xfe", '{"a":'):
            with self.assertRaises(UpdateError, msg=text) as caught:
                uc.parse_feed(text)
            self.assertEqual(caught.exception.reason, "feed")

    def test_an_oversize_document_is_refused(self):
        with self.assertRaises(UpdateError) as caught:
            uc.parse_feed('{"pad":"' + "x" * uc.MAX_FEED_BYTES + '"}')
        self.assertEqual(caught.exception.reason, "feed")


class ValidateFeedTests(unittest.TestCase):
    def test_a_good_feed(self):
        feed = uc.validate_feed(good_feed(), RUNNING_BUILD)
        self.assertIsInstance(feed, Feed)
        self.assertEqual((feed.version, feed.build, feed.size), ("2.0", 31, 1234))
        self.assertEqual(feed.sha256, "ab" * 32)
        self.assertEqual(len(feed.signature_bytes), 64)

    def test_an_older_or_equal_build_is_no_update(self):
        refused(self, good_feed(build=30), "older")
        refused(self, good_feed(build=29), "older")
        refused(self, good_feed(build=0), "older")
        uc.validate_feed(good_feed(build=31), RUNNING_BUILD)

    def test_a_malformed_version(self):
        for bad in ("2", "2.0.1", "v2.0", "2.x", "", " 2.0", "2.0 ", "2..0", "-1.0", "2.-1", None, 2.0, ["2.0"], "٢.٠",
                    "1234567.0"):
            refused(self, good_feed(version=bad, url=PREFIX + "a.tar.gz"), "version")

    def test_a_malformed_build(self):
        for bad in ("31", 31.0, True, None, -5, [31]):
            refused(self, good_feed(build=bad), "build")

    def test_the_url_must_start_with_the_release_download_prefix_for_that_version(self):
        base = "https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases"
        for bad in (
            "http://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.0/a.tar.gz",     # not https
            "https://evil.example/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.0/a.tar.gz",
            "https://github.com/Evil/PixelSwitch/releases/download/v2.0/a.tar.gz",
            "https://github.com/BespokeWoodcraftStudio/Other/releases/download/v2.0/a.tar.gz",
            base + "/download/v1.9/a.tar.gz",                                                            # another version
            base + "/download/v2.1/a.tar.gz",
            base + "/download/2.0/a.tar.gz",
            base + "/latest/download/a.tar.gz",
            base + "/download/v2.0/",                                                                    # no file name
            base + "/download/v2.0",
            base + "/download/v2.0/../v1.0/a.tar.gz",                                                    # traversal
            base + "/download/v2.0/sub/a.tar.gz",
            base + "/download/v2.0/a.tar.gz?x=1",
            base + "/download/v2.0/a.tar.gz#frag",
            base + "/download/v2.0/a b.tar.gz",
            base + "/download/v2.0/‮a.tar.gz",
            "https://github.com@evil.example/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.0/a.tar.gz",
            "",
            None,
            5,
        ):
            refused(self, good_feed(url=bad), "url")

    def test_size_is_a_whole_number_of_at_most_50_mb(self):
        uc.validate_feed(good_feed(size=uc.MAX_TARBALL_BYTES), RUNNING_BUILD)
        uc.validate_feed(good_feed(size=1), RUNNING_BUILD)
        for bad in (uc.MAX_TARBALL_BYTES + 1, 10**12, 0, -1, "1234", 12.5, 12.0, True, None):
            refused(self, good_feed(size=bad), "size")
        self.assertEqual(uc.MAX_TARBALL_BYTES, 50 * 1024 * 1024)

    def test_sha256_and_signature_shapes(self):
        for bad in ("", "ab" * 31, "zz" * 32, "ab" * 33, None, 5):
            refused(self, good_feed(sha256=bad), "sha256")
        uc.validate_feed(good_feed(sha256="AB" * 32), RUNNING_BUILD)
        self.assertEqual(uc.validate_feed(good_feed(sha256="AB" * 32), RUNNING_BUILD).sha256, "ab" * 32)
        for bad in ("", "!!!", base64.b64encode(bytes(63)).decode(), base64.b64encode(bytes(65)).decode(), None, 5):
            refused(self, good_feed(signature=bad), "signature")

    def test_a_missing_field_is_refused(self):
        for key in ("version", "build", "url", "size", "sha256", "signature"):
            feed = good_feed()
            del feed[key]
            with self.assertRaises(UpdateError, msg=key):
                uc.validate_feed(feed, RUNNING_BUILD)

    def test_a_test_release_base_changes_the_prefix(self):
        base = "http://127.0.0.1:8000/mock"
        feed = good_feed(url=base + "/download/v2.0/a.tar.gz")
        uc.validate_feed(feed, RUNNING_BUILD, release_base=base)
        refused(self, feed, "url")  # the real base does not accept it

    def test_the_constants(self):
        self.assertEqual(uc.FEED_URL, "https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/latest-linux.json")
        self.assertEqual(len(base64.b64decode(uc.SU_PUBLIC_ED_KEY)), 32)

    def test_the_public_key_is_the_one_in_project_yml(self):
        project = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "project.yml")
        if not os.path.isfile(project):
            self.skipTest("no project.yml beside the checkout")
        with open(project, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(f"SUPublicEDKey: {uc.SU_PUBLIC_ED_KEY}", text)


class VerifyDownloadTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="psu")
        self.payload = os.urandom(50_000)
        self.path = os.path.join(self.work, "update.tar.gz")
        with open(self.path, "wb") as handle:
            handle.write(self.payload)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def feed(self, **overrides):
        signature = base64.b64encode(ed25519.sign(SEED, self.payload)).decode()
        values = {"size": len(self.payload), "sha256": hashlib.sha256(self.payload).hexdigest(), "signature": signature}
        values.update(overrides)
        return uc.validate_feed(good_feed(**values), RUNNING_BUILD)

    def refused(self, feed, reason):
        with self.assertRaises(UpdateError) as caught:
            uc.verify_download(self.path, feed, PUBLIC_B64)
        self.assertEqual(caught.exception.reason, reason)

    def test_a_good_download_passes(self):
        uc.verify_download(self.path, self.feed(), PUBLIC_B64)

    def test_the_wrong_size_is_refused_first(self):
        self.refused(self.feed(size=len(self.payload) + 1), "size")
        self.refused(self.feed(size=len(self.payload) - 1), "size")

    def test_the_wrong_sha256_is_refused(self):
        self.refused(self.feed(sha256="00" * 32), "sha256")

    def test_a_tampered_byte_is_refused(self):
        feed = self.feed()
        data = bytearray(self.payload)
        data[100] ^= 1
        with open(self.path, "wb") as handle:
            handle.write(data)
        self.refused(feed, "sha256")

    def test_a_tampered_byte_with_a_matching_sha256_is_refused_by_the_signature(self):
        data = bytearray(self.payload)
        data[100] ^= 1
        with open(self.path, "wb") as handle:
            handle.write(data)
        feed = self.feed(sha256=hashlib.sha256(bytes(data)).hexdigest())  # an attacker can fix the hash, not the signature
        self.refused(feed, "signature")

    def test_the_wrong_key_is_refused(self):
        other = base64.b64encode(ed25519.public_key_from_seed(bytes(32))).decode()
        with self.assertRaises(UpdateError) as caught:
            uc.verify_download(self.path, self.feed(), other)
        self.assertEqual(caught.exception.reason, "signature")

    def test_it_fails_closed_when_the_verifier_cannot_say_yes(self):
        for verifier in (lambda *a, **k: False, lambda *a, **k: None):
            with self.assertRaises(UpdateError) as caught:
                uc.verify_download(self.path, self.feed(), PUBLIC_B64, verify_file=verifier)
            self.assertEqual(caught.exception.reason, "signature")

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(UpdateError):
            uc.verify_download(os.path.join(self.work, "nope"), self.feed(), PUBLIC_B64)


class VersionFileTests(unittest.TestCase):
    FEED = Feed("2.0", 31, "u", 1, "a" * 64, "s")

    def check(self, text, running=RUNNING_BUILD):
        return uc.check_version_text(text, self.FEED, running)

    def test_a_matching_file(self):
        info = self.check("version=2.0\nbuild=31\n")
        self.assertEqual((info.version, info.build), ("2.0", 31))
        self.check("2.0 31\n")

    def test_a_mismatched_version(self):
        for text in ("version=2.1\nbuild=31\n", "version=1.9\nbuild=31\n", "version=2.0\nbuild=32\n",
                     "version=2.0\nbuild=30\n", "2.0 99"):
            with self.assertRaises(UpdateError, msg=text) as caught:
                self.check(text)
            self.assertIn(caught.exception.reason, ("version-file", "older"))
            self.assertEqual(caught.exception.reason, "version-file", text)

    def test_the_build_must_be_newer_than_the_running_one(self):
        with self.assertRaises(UpdateError) as caught:
            self.check("version=2.0\nbuild=31\n", running=31)
        self.assertEqual(caught.exception.reason, "older")
        with self.assertRaises(UpdateError) as caught:
            self.check("version=2.0\nbuild=31\n", running=40)
        self.assertEqual(caught.exception.reason, "older")

    def test_garbage_is_refused(self):
        for text in ("", "version=2.0\n", "build=31\n", "garbage", "version=2.0\nbuild=x\n", "version=2\nbuild=31\n",
                     "version=2.0\nbuild=31\nextra=1\n", "2.0\n", "a b c"):
            with self.assertRaises(UpdateError, msg=text) as caught:
                self.check(text)
            self.assertEqual(caught.exception.reason, "version-file")

    def test_the_extracted_file(self):
        work = tempfile.mkdtemp(prefix="psv")
        try:
            def write(text):
                with open(os.path.join(work, "VERSION"), "w") as handle:
                    handle.write(text)

            write("version=2.0\nbuild=31\n")
            self.assertEqual(uc.check_extracted_version(work, self.FEED, RUNNING_BUILD).build, 31)
            write("version=2.0\nbuild=32\n")
            with self.assertRaises(UpdateError):
                uc.check_extracted_version(work, self.FEED, RUNNING_BUILD)
            write("version=2.0\nbuild=31\n" + "#" * 2000)
            with self.assertRaises(UpdateError):
                uc.check_extracted_version(work, self.FEED, RUNNING_BUILD)
            os.remove(os.path.join(work, "VERSION"))
            with self.assertRaises(UpdateError):
                uc.check_extracted_version(work, self.FEED, RUNNING_BUILD)
            real = os.path.join(work, "real")
            with open(real, "w") as handle:
                handle.write("version=2.0\nbuild=31\n")
            os.symlink(real, os.path.join(work, "VERSION"))
            with self.assertRaises(UpdateError):       # a link is not accepted as the VERSION file
                uc.check_extracted_version(work, self.FEED, RUNNING_BUILD)
        finally:
            shutil.rmtree(work, ignore_errors=True)


class ArchiveBuilder:
    """Builds small gzip tarballs with exactly the members a test wants."""

    def __init__(self):
        self.buffer = io.BytesIO()
        self.tar = tarfile.open(fileobj=self.buffer, mode="w:gz", format=tarfile.PAX_FORMAT)

    def file(self, name, data=b"data", mode=0o644):
        info = tarfile.TarInfo(name)
        info.size = len(data)
        info.mode = mode
        self.tar.addfile(info, io.BytesIO(data))
        return self

    def directory(self, name, mode=0o755):
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE
        info.mode = mode
        self.tar.addfile(info)
        return self

    def symlink(self, name, target):
        info = tarfile.TarInfo(name)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        self.tar.addfile(info)
        return self

    def hardlink(self, name, target):
        info = tarfile.TarInfo(name)
        info.type = tarfile.LNKTYPE
        info.linkname = target
        self.tar.addfile(info)
        return self

    def special(self, name, kind):
        info = tarfile.TarInfo(name)
        info.type = kind
        info.devmajor, info.devminor = 1, 3
        self.tar.addfile(info)
        return self

    def write(self, path):
        self.tar.close()
        with open(path, "wb") as handle:
            handle.write(self.buffer.getvalue())
        return path


class SafeExtractTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="pst")
        self.archive = os.path.join(self.work, "a.tar.gz")
        self.dest = os.path.join(self.work, "out")
        self.outside = os.path.join(self.work, "outside")
        os.mkdir(self.outside)

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def extract(self, builder, **kwargs):
        builder.write(self.archive)
        return uc.safe_extract(self.archive, self.dest, **kwargs)

    def refused(self, builder, **kwargs):
        with self.assertRaises(UpdateError) as caught:
            self.extract(builder, **kwargs)
        self.assertEqual(caught.exception.reason, "archive")
        # nothing was written: the scan pass refuses the whole archive first
        self.assertEqual(os.listdir(self.dest) if os.path.isdir(self.dest) else [], [])
        self.assertEqual(os.listdir(self.outside), [])
        return caught.exception

    def test_a_normal_archive_unpacks(self):
        builder = (ArchiveBuilder().file("VERSION", b"version=2.0\nbuild=31\n")
                   .directory("bin").file("bin/pixelswitchd", b"#!/usr/bin/python3\n", 0o755)
                   .directory("pixelswitch").file("pixelswitch/__init__.py", b"")
                   .symlink("bin/ps", "pixelswitchd").symlink("current", "pixelswitch"))
        count = self.extract(builder)
        self.assertEqual(count, 7)
        with open(os.path.join(self.dest, "VERSION")) as handle:
            self.assertEqual(handle.read(), "version=2.0\nbuild=31\n")
        self.assertTrue(os.access(os.path.join(self.dest, "bin", "pixelswitchd"), os.X_OK))
        self.assertTrue(os.path.islink(os.path.join(self.dest, "bin", "ps")))
        self.assertEqual(stat.S_IMODE(os.stat(self.dest).st_mode), 0o700)

    def test_the_destination_is_created_private(self):
        self.extract(ArchiveBuilder().file("a", b"x"))
        self.assertEqual(stat.S_IMODE(os.stat(self.dest).st_mode), 0o700)

    def test_path_traversal_with_dot_dot(self):
        self.refused(ArchiveBuilder().file("ok", b"x").file("../evil", b"x"))
        self.refused(ArchiveBuilder().file("a/../../evil", b"x"))
        self.refused(ArchiveBuilder().file("a/b/../../../evil", b"x"))
        self.refused(ArchiveBuilder().directory(".."))
        self.assertFalse(os.path.exists(os.path.join(self.work, "evil")))

    def test_absolute_paths(self):
        self.refused(ArchiveBuilder().file("/etc/evil", b"x"))
        self.refused(ArchiveBuilder().file("/tmp/evil", b"x"))

    def test_backslash_nul_and_empty_names(self):
        self.refused(ArchiveBuilder().file("a\\..\\evil", b"x"))
        info = tarfile.TarInfo("")
        info.size = 0
        builder = ArchiveBuilder()
        builder.tar.addfile(info)
        self.refused(builder)

    def test_links_that_leave_the_tree(self):
        self.refused(ArchiveBuilder().symlink("link", "/etc"))
        self.refused(ArchiveBuilder().symlink("link", "../outside"))
        self.refused(ArchiveBuilder().symlink("sub/link", "../../outside"))
        self.refused(ArchiveBuilder().symlink("link", ".."))
        self.refused(ArchiveBuilder().symlink("link", "a/../../x"))
        self.refused(ArchiveBuilder().hardlink("hl", "/etc/passwd"))
        self.refused(ArchiveBuilder().hardlink("hl", "../outside/x"))

    def test_a_link_then_a_file_through_it(self):
        self.refused(ArchiveBuilder().symlink("d", "/tmp").file("d/evil", b"x"))
        self.refused(ArchiveBuilder().symlink("d", "..").file("d/evil", b"x"))

    def test_links_that_stay_inside_are_fine(self):
        builder = (ArchiveBuilder().directory("lib").file("lib/real.py", b"x").symlink("lib/alias.py", "real.py")
                   .symlink("top", "lib").symlink("lib/up", "../lib/real.py").hardlink("copy.py", "lib/real.py"))
        self.assertEqual(self.extract(builder), 6)
        self.assertTrue(os.path.islink(os.path.join(self.dest, "top")))
        self.assertEqual(os.stat(os.path.join(self.dest, "copy.py")).st_ino, os.stat(os.path.join(self.dest, "lib", "real.py")).st_ino)

    def test_devices_and_pipes(self):
        self.refused(ArchiveBuilder().special("dev", tarfile.CHRTYPE))
        self.refused(ArchiveBuilder().special("blk", tarfile.BLKTYPE))
        self.refused(ArchiveBuilder().special("fifo", tarfile.FIFOTYPE))

    def test_too_many_members(self):
        builder = ArchiveBuilder()
        for index in range(12):
            builder.file(f"f{index}", b"x")
        self.refused(builder, max_members=10)

    def test_too_many_declared_bytes(self):
        builder = ArchiveBuilder().file("a", b"x" * 600).file("b", b"y" * 600)
        self.refused(builder, max_total_bytes=1000)

    def test_a_destination_that_is_not_empty_is_refused(self):
        os.mkdir(self.dest)
        with open(os.path.join(self.dest, "already"), "w") as handle:
            handle.write("x")
        ArchiveBuilder().file("a", b"x").write(self.archive)
        with self.assertRaises(UpdateError):
            uc.safe_extract(self.archive, self.dest)
        self.assertEqual(os.listdir(self.dest), ["already"])

    def test_a_destination_that_is_a_file_or_a_link_is_refused(self):
        ArchiveBuilder().file("a", b"x").write(self.archive)
        with open(self.dest, "w") as handle:
            handle.write("x")
        with self.assertRaises(UpdateError):
            uc.safe_extract(self.archive, self.dest)
        os.remove(self.dest)
        os.symlink(self.outside, self.dest)
        with self.assertRaises(UpdateError):
            uc.safe_extract(self.archive, self.dest)
        self.assertEqual(os.listdir(self.outside), [])

    def test_an_empty_destination_directory_is_fine(self):
        os.mkdir(self.dest)
        self.extract(ArchiveBuilder().file("a", b"x"))
        self.assertEqual(os.listdir(self.dest), ["a"])

    def test_not_a_gzip_or_a_truncated_one(self):
        with open(self.archive, "wb") as handle:
            handle.write(b"this is not an archive")
        with self.assertRaises(UpdateError) as caught:
            uc.safe_extract(self.archive, self.dest)
        self.assertEqual(caught.exception.reason, "archive")
        good = ArchiveBuilder().file("a", b"x" * 100_000).write(self.archive)
        with open(good, "rb") as handle:
            blob = handle.read()
        with open(self.archive, "wb") as handle:
            handle.write(blob[: len(blob) // 2])
        shutil.rmtree(self.dest, ignore_errors=True)
        with self.assertRaises(UpdateError):
            uc.safe_extract(self.archive, self.dest)

    def test_an_uncompressed_tar_is_not_accepted(self):
        with tarfile.open(self.archive, "w") as tar:
            info = tarfile.TarInfo("a")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(UpdateError):
            uc.safe_extract(self.archive, self.dest)

    def test_setuid_and_group_write_bits_are_dropped(self):
        self.extract(ArchiveBuilder().file("suid", b"x", 0o4777).file("gw", b"x", 0o664).directory("d", 0o2777))
        for name in ("suid", "gw", "d"):
            mode = os.stat(os.path.join(self.dest, name)).st_mode
            self.assertEqual(mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX), 0, name)
            self.assertEqual(mode & (stat.S_IWGRP | stat.S_IWOTH), 0, name)

    def test_the_owner_in_the_archive_is_ignored(self):
        builder = ArchiveBuilder()
        info = tarfile.TarInfo("owned")
        info.size = 1
        info.uid, info.gid, info.uname, info.gname = 0, 0, "root", "root"
        builder.tar.addfile(info, io.BytesIO(b"x"))
        self.extract(builder)
        self.assertEqual(os.stat(os.path.join(self.dest, "owned")).st_uid, os.getuid())

    def test_a_decompression_bomb_is_stopped_by_the_declared_size_cap(self):
        builder = ArchiveBuilder().file("zeros", bytes(5_000_000))
        self.refused(builder, max_total_bytes=1_000_000)

    def test_a_hostile_tar_with_a_lying_header_cannot_write_outside(self):
        """A PAX header that claims a path with .. in it is caught by the scan, and the data filter would catch it too."""
        builder = ArchiveBuilder()
        info = tarfile.TarInfo("innocent")
        info.pax_headers = {"path": "../../escaped"}
        info.size = 1
        builder.tar.addfile(info, io.BytesIO(b"x"))
        self.refused(builder)

    def test_gzip_of_a_second_member_does_not_hide_files(self):
        """Two concatenated gzip members: the archive is read as one stream by tarfile, both passes see the same members."""
        first = ArchiveBuilder().file("ok", b"x")
        first.tar.close()
        raw = gzip.decompress(first.buffer.getvalue())
        # the tar end-of-archive blocks end the first stream; whatever follows is never extracted
        trailing = ArchiveBuilder().file("../evil", b"x")
        trailing.tar.close()
        with open(self.archive, "wb") as handle:
            handle.write(gzip.compress(raw.rstrip(b"\0")) + trailing.buffer.getvalue())
        try:
            uc.safe_extract(self.archive, self.dest)
        except UpdateError:
            pass
        self.assertFalse(os.path.exists(os.path.join(self.work, "evil")))
        self.assertEqual(os.listdir(self.outside), [])


if __name__ == "__main__":
    unittest.main()
