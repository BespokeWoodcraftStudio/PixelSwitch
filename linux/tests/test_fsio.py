"""fsio: the atomic write every PixelSwitch file goes through (spec 5.2)."""
from __future__ import annotations

import os
import stat
import unittest
from unittest import mock

from pixelswitch import fsio

from .lb_support import tmpdir


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.dir = tmpdir(self)
        self.path = os.path.join(self.dir, "data", "file.json")

    def mode(self, path=None):
        return stat.S_IMODE(os.stat(path or self.path).st_mode)

    def test_a_new_file_is_0600_in_a_0700_directory(self):
        previous = os.umask(0o022)
        try:
            fsio.atomic_write(self.path, '{"a":1}')
        finally:
            os.umask(previous)
        self.assertEqual(self.mode(), 0o600)
        self.assertEqual(self.mode(os.path.dirname(self.path)), 0o700)
        with open(self.path) as handle:
            self.assertEqual(handle.read(), '{"a":1}')

    def test_every_missing_parent_is_created_0700(self):
        deep = os.path.join(self.dir, "a", "b", "c", "file")
        fsio.atomic_write(deep, "x")
        for part in ("a", "a/b", "a/b/c"):
            self.assertEqual(self.mode(os.path.join(self.dir, part)), 0o700, part)

    def test_an_existing_directory_is_never_touched(self):
        existing = os.path.join(self.dir, "shared")
        os.mkdir(existing)
        os.chmod(existing, 0o755)
        fsio.atomic_write(os.path.join(existing, "f"), "x")
        self.assertEqual(self.mode(existing), 0o755)

    def test_a_replaced_file_keeps_its_permissions(self):
        fsio.atomic_write(self.path, "one")
        os.chmod(self.path, 0o640)
        fsio.atomic_write(self.path, "two")
        self.assertEqual(self.mode(), 0o640)
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "two")

    def test_a_tighter_file_is_never_widened(self):
        fsio.atomic_write(self.path, "one")
        os.chmod(self.path, 0o400)
        fsio.atomic_write(self.path, "two")
        self.assertEqual(self.mode(), 0o400)

    def test_returns_the_state_before_the_write(self):
        before = fsio.atomic_write(self.path, "one")
        self.assertFalse(before.exists)
        again = fsio.atomic_write(self.path, "two")
        self.assertTrue(again.exists)
        self.assertEqual(again.mode, 0o600)
        self.assertIsNotNone(again.mtime_ns)

    def test_a_symlinked_target_is_refused(self):
        os.makedirs(os.path.dirname(self.path))
        real = os.path.join(self.dir, "real")
        with open(real, "w") as handle:
            handle.write("keep")
        os.symlink(real, self.path)
        with self.assertRaises(fsio.FileSafetyError):
            fsio.atomic_write(self.path, "new")
        with open(real) as handle:
            self.assertEqual(handle.read(), "keep")
        self.assertTrue(os.path.islink(self.path))

    def test_a_hard_linked_target_is_refused(self):
        os.makedirs(os.path.dirname(self.path))
        other = os.path.join(self.dir, "other")
        with open(self.path, "w") as handle:
            handle.write("keep")
        os.link(self.path, other)
        with self.assertRaises(fsio.FileSafetyError):
            fsio.atomic_write(self.path, "new")
        with open(other) as handle:
            self.assertEqual(handle.read(), "keep")

    def test_a_directory_target_is_refused(self):
        os.makedirs(self.path)
        with self.assertRaises(fsio.FileSafetyError):
            fsio.atomic_write(self.path, "new")

    def test_no_temp_file_survives_a_failed_write(self):
        fsio.atomic_write(self.path, "one")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                fsio.atomic_write(self.path, "two")
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["file.json"])
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "one")

    def test_the_temp_file_is_in_the_targets_directory_and_0600_while_written(self):
        seen = {}

        def look(temp):
            seen["dir"] = os.path.dirname(temp)
            seen["mode"] = stat.S_IMODE(os.stat(temp).st_mode)

        fsio.atomic_write(self.path, "x", before_replace=look)
        self.assertEqual(seen["dir"], os.path.dirname(self.path))
        self.assertEqual(seen["mode"], 0o600)

    def test_the_directory_is_fsynced_after_the_rename(self):
        order = []
        real_replace, real_fsync_dir = os.replace, fsio.fsync_dir
        with mock.patch("os.replace", side_effect=lambda a, b: (order.append("replace"), real_replace(a, b))[1]), \
                mock.patch.object(fsio, "fsync_dir", side_effect=lambda d: (order.append("fsync_dir"), real_fsync_dir(d))[1]):
            fsio.atomic_write(self.path, "x")
        self.assertEqual(order[-2:], ["replace", "fsync_dir"])

    def test_bytes_and_text_both_work(self):
        fsio.atomic_write(self.path, b"\x00\x01")
        with open(self.path, "rb") as handle:
            self.assertEqual(handle.read(), b"\x00\x01")

    def test_tighten_permissions_removes_group_and_other_only(self):
        fsio.atomic_write(self.path, "x")
        os.chmod(self.path, 0o664)
        self.assertTrue(fsio.tighten_permissions(self.path))
        self.assertEqual(self.mode(), 0o600)
        self.assertFalse(fsio.tighten_permissions(self.path))
        self.assertFalse(fsio.tighten_permissions(os.path.join(self.dir, "missing")))


class ReadTests(unittest.TestCase):
    def test_absent_is_none_and_large_is_refused(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "f")
        self.assertIsNone(fsio.read_bytes(path))
        with open(path, "wb") as handle:
            handle.write(b"x" * 100)
        self.assertEqual(len(fsio.read_bytes(path)), 100)
        with self.assertRaises(ValueError):
            fsio.read_bytes(path, limit=10)

    def test_json_helpers(self):
        self.assertEqual(fsio.dumps_compact({"b": [1, 2], "a": "é"}), '{"b":[1,2],"a":"é"}')
        with self.assertRaises(ValueError):
            fsio.loads_strict("NaN")
        with self.assertRaises(ValueError):
            fsio.dumps_compact(float("nan"))
        self.assertFalse(fsio.dumps_indented_no_newline({"a": 1}).endswith("\n"))
        self.assertTrue(fsio.dumps_pretty({"a": 1}).endswith("\n"))


if __name__ == "__main__":
    unittest.main()
