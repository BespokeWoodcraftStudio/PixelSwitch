"""Lane LB's two fixes in lane LA's files, each with its test.

(a) ``logins.login_problem`` treats a whitespace-only accessToken or refreshToken as blank,
    exactly as the Mac's ``CredentialOwnership.unusableReason`` trims ``.whitespacesAndNewlines``.
(b) ``test_conformance`` FAILS (never skips) when the vectors file is missing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from pixelswitch import logins

HERE = os.path.dirname(os.path.abspath(__file__))
LINUX = os.path.dirname(HERE)


def credential(access="a-token", refresh="r-token", expires=1_900_000_000_000):
    return json.dumps({"claudeAiOauth": {"accessToken": access, "refreshToken": refresh, "expiresAt": expires}})


class WhitespaceTokensAreBlank(unittest.TestCase):
    BLANKS = (" ", "   ", "\t", "\n", " \r\n\t ", " ", " ", " ", "\u0085")

    def test_whitespace_only_access_token(self):
        for blank in self.BLANKS:
            self.assertFalse(logins.usable_login(credential(access=blank)), repr(blank))
            self.assertEqual(logins.login_problem(credential(access=blank)), "accessToken is missing or blank")

    def test_whitespace_only_refresh_token(self):
        for blank in self.BLANKS:
            self.assertFalse(logins.usable_login(credential(refresh=blank)), repr(blank))
            self.assertEqual(logins.login_problem(credential(refresh=blank)), "refreshToken is missing or blank")

    def test_a_token_with_inner_or_edge_spaces_is_still_a_login(self):
        self.assertTrue(logins.usable_login(credential(access=" a b ", refresh=" r ")))

    def test_a_mapping_is_judged_the_same(self):
        self.assertFalse(logins.usable_login({"claudeAiOauth": {"accessToken": "  ", "refreshToken": "r"}}))


class MissingVectorsFail(unittest.TestCase):
    def test_a_checkout_without_the_vectors_fails_the_conformance_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "repo", "linux")
            os.makedirs(os.path.join(root, "tests"))
            shutil.copytree(os.path.join(LINUX, "pixelswitch"), os.path.join(root, "pixelswitch"),
                            ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("__init__.py", "test_conformance.py"):
                shutil.copy(os.path.join(HERE, name), os.path.join(root, "tests", name))
            env = {k: v for k, v in os.environ.items() if k != "PIXELSWITCH_VECTORS"}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            proc = subprocess.run([sys.executable, "-m", "unittest", "tests.test_conformance"], cwd=root,
                                  env=env, capture_output=True, text=True, timeout=120)
            output = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0, output)
            self.assertIn("conformance vectors are missing", output)
            self.assertNotIn("skipped", output)


if __name__ == "__main__":
    unittest.main()
