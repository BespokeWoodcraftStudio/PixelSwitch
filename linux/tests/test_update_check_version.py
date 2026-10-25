"""version.py: the running version and build from one source, and the VERSION file format the
updater compares against the feed."""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from pixelswitch import version as v

PROJECT_YML = """\
targets:
  PixelSwitch:
    settings:
      base:
        MARKETING_VERSION: "2.0"
        CURRENT_PROJECT_VERSION: "31"
  PixelSwitchCLI:
    settings:
      base:
        MARKETING_VERSION: "2.0"
        CURRENT_PROJECT_VERSION: "31"
"""


class VersionFileTests(unittest.TestCase):
    def test_key_value_lines(self):
        info = v.parse_version_file("version=2.0\nbuild=31\n")
        self.assertEqual((info.version, info.build), ("2.0", 31))
        self.assertEqual(str(info), "2.0 (31)")

    def test_order_whitespace_and_case_of_keys(self):
        self.assertEqual(v.parse_version_file(" Build = 31 \n\nVERSION= 2.0\n"), v.VersionInfo("2.0", 31))

    def test_single_line_form(self):
        self.assertEqual(v.parse_version_file("2.0 31\n"), v.VersionInfo("2.0", 31))
        self.assertEqual(v.parse_version_file("  2.0    31  "), v.VersionInfo("2.0", 31))

    def test_refused(self):
        for text in ("", "\n", "version=2.0", "build=31", "2.0", "2.0 31 5", "version=2\nbuild=31", "version=2.0\nbuild=x",
                     "version=2.0\nbuild=-1", "version=2.0\nbuild=31\nother=1", "version 2.0\nbuild 31", "a=b\nc=d",
                     "version=2.0.1\nbuild=31", "version=\nbuild=31"):
            with self.assertRaises(ValueError, msg=text):
                v.parse_version_file(text)

    def test_valid_version(self):
        for good in ("1.9", "2.0", "10.25", "0.1"):
            self.assertTrue(v.valid_version(good), good)
        for bad in ("2", "2.0.0", "v2.0", "", "2.", ".5", "2.0 ", None, 2.0, "1e3.0", "-1.0"):
            self.assertFalse(v.valid_version(bad), bad)


class ProjectYmlTests(unittest.TestCase):
    def test_marketing_version_and_build(self):
        self.assertEqual(v.parse_project_yml(PROJECT_YML), v.VersionInfo("2.0", 31))

    def test_unquoted_values_and_comments(self):
        text = "MARKETING_VERSION: 1.9  # the release\nCURRENT_PROJECT_VERSION: 30\n"
        self.assertEqual(v.parse_project_yml(text), v.VersionInfo("1.9", 30))

    def test_missing_or_malformed(self):
        for text in ("", "MARKETING_VERSION: \"2.0\"\n", "CURRENT_PROJECT_VERSION: \"31\"\n",
                     'MARKETING_VERSION: "two"\nCURRENT_PROJECT_VERSION: "31"\n',
                     'MARKETING_VERSION: "2.0"\nCURRENT_PROJECT_VERSION: "x"\n'):
            with self.assertRaises(ValueError, msg=text):
                v.parse_project_yml(text)


class ReadVersionTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="psver")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def layout(self, version_file=None, project=None):
        package = os.path.join(self.root, "linux", "pixelswitch")
        os.makedirs(package)
        if version_file is not None:
            with open(os.path.join(self.root, "linux", "VERSION"), "w") as handle:
                handle.write(version_file)
        if project is not None:
            with open(os.path.join(self.root, "project.yml"), "w") as handle:
                handle.write(project)
        return package

    def test_an_installed_copy_reads_the_version_file_beside_the_package(self):
        package = self.layout(version_file="version=2.1\nbuild=32\n")
        self.assertEqual(v.read_version(package), v.VersionInfo("2.1", 32))

    def test_a_source_checkout_reads_project_yml(self):
        package = self.layout(project=PROJECT_YML)
        self.assertEqual(v.read_version(package), v.VersionInfo("2.0", 31))

    def test_the_version_file_wins(self):
        package = self.layout(version_file="version=2.1\nbuild=32\n", project=PROJECT_YML)
        self.assertEqual(v.read_version(package).build, 32)

    def test_a_damaged_version_file_is_an_error_not_a_fallback(self):
        package = self.layout(version_file="garbage", project=PROJECT_YML)
        with self.assertRaises(v.VersionUnavailable):
            v.read_version(package)

    def test_nothing_to_read(self):
        package = self.layout()
        with self.assertRaises(v.VersionUnavailable):
            v.read_version(package)

    def test_this_checkout_reads_its_own_project_yml(self):
        info = v.read_version()
        self.assertTrue(v.valid_version(info.version))
        self.assertGreater(info.build, 0)
        self.assertEqual(v.version(), info.version)
        self.assertEqual(v.build(), info.build)


if __name__ == "__main__":
    unittest.main()
