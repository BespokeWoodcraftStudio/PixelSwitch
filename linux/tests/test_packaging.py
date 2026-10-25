"""What gets shipped: the systemd unit's keys in their sections, the desktop files, the icons, the launchers, the
deterministic tarball and its VERSION file, the feed and its signature, and install.sh in a scratch HOME
(``--from`` a local tarball, ``--no-systemd --no-autostart``, then ``--uninstall``)."""
from __future__ import annotations

import base64
import functools
import glob
import hashlib
import http.server
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import xml.dom.minidom

from pixelswitch import ed25519, testmode
from pixelswitch.update_check import safe_extract

from .lc_support import LINUX_ROOT, REPO_ROOT

PACKAGING = os.path.join(LINUX_ROOT, "packaging")
BIN = os.path.join(LINUX_ROOT, "bin")
BUILD = os.path.join(LINUX_ROOT, "build-release.sh")
INSTALL = os.path.join(LINUX_ROOT, "install.sh")
LAUNCHERS = ("pixelswitch", "pixelswitchd", "pixelswitch-tray", "pixelswitch-settings", "pixelswitch-rollback")



def slurp(path, mode="r", **kw):
    with open(path, mode, **kw) as handle:
        return handle.read()


def spit(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w") as handle:
        handle.write(data)


def clean_path() -> str:
    """The test interpreter's folder, then the system's: no per-user guard scripts that shadow rm."""
    return os.pathsep.join([os.path.dirname(sys.executable), "/usr/bin", "/bin"])


def gnu_tar() -> str | None:
    for name in ("tar", "gtar"):
        path = shutil.which(name)
        if path and b"GNU tar" in subprocess.run([path, "--version"], capture_output=True).stdout:
            return path
    return None


def openssl3() -> str | None:
    for path in (shutil.which("openssl"), "/opt/homebrew/bin/openssl", "/usr/local/bin/openssl"):
        if path and os.path.exists(path):
            out = subprocess.run([path, "version"], capture_output=True, text=True).stdout
            if out.startswith("OpenSSL 3") or out.startswith("OpenSSL 4"):
                return path
    return None


def scratch_dir(case: unittest.TestCase, prefix: str) -> str:
    """A fresh, unique folder under the REAL ~/.cache/pixelswitch-dev/ (test mode accepts no other HOME, and the
    password database decides where that is, whatever HOME says). Unique per call, so two test runs at once never
    share a folder; removed when the test ends."""
    root = testmode.scratch_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    path = tempfile.mkdtemp(prefix=prefix, dir=root)
    case.addCleanup(shutil.rmtree, path, True)
    return path


def make_shims(folder: str, marker: str) -> str:
    """A folder of stand-ins for sudo and pkexec that leave a marker file and fail. Put first on PATH, they prove at
    run time that the installer never reaches for either."""
    os.makedirs(folder, exist_ok=True)
    for name in ("sudo", "pkexec"):
        path = os.path.join(folder, name)
        spit(path, f'#!/bin/sh\ntouch "{marker}"\necho "{name} was called" >&2\nexit 1\n')
        os.chmod(path, 0o755)
    return folder


def project_values() -> tuple[str, str, str]:
    text = slurp(os.path.join(REPO_ROOT, "project.yml"), encoding="utf-8")
    version = re.search(r'^\s*MARKETING_VERSION: "([^"]+)"', text, re.M).group(1)
    build = re.search(r'^\s*CURRENT_PROJECT_VERSION: "([^"]+)"', text, re.M).group(1)
    key = re.search(r"^\s*SUPublicEDKey: (\S+)", text, re.M).group(1)
    return version, build, key


def parse_unit(text: str) -> dict[str, dict[str, list[str]]]:
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            current = sections.setdefault(line.strip("[]"), {})
        else:
            key, _, value = line.partition("=")
            current.setdefault(key, []).append(value)
    return sections


class UnitTests(unittest.TestCase):
    def setUp(self):
        self.unit = parse_unit(slurp(os.path.join(PACKAGING, "pixelswitch.service"), encoding="utf-8"))

    def test_every_key_is_in_its_section(self):
        self.assertEqual(self.unit["Unit"]["StartLimitIntervalSec"], ["3600"])
        self.assertEqual(self.unit["Unit"]["StartLimitBurst"], ["4"])
        service = self.unit["Service"]
        self.assertEqual(service["Restart"], ["on-failure"])
        self.assertEqual(service["RestartSec"], ["120"])
        self.assertEqual(service["KillMode"], ["mixed"])
        self.assertEqual(service["TimeoutStopSec"], ["90"])
        self.assertEqual(service["UMask"], ["0077"])
        for key in ("StartLimitIntervalSec", "StartLimitBurst"):
            self.assertNotIn(key, service)
        for key in ("Restart", "RestartSec", "KillMode", "TimeoutStopSec", "UMask"):
            self.assertNotIn(key, self.unit["Unit"])

    def test_exec_lines(self):
        service = self.unit["Service"]
        self.assertEqual(service["ExecStart"], ["%h/.local/bin/pixelswitchd"])
        # The rollback check is a stand-alone copy outside the versioned folders, and may not stop the start.
        self.assertEqual(service["ExecStartPre"], ["-/usr/bin/env python3 -I %h/.local/share/pixelswitch/rollback.py"])
        self.assertNotIn("/app/", service["ExecStartPre"][0])
        self.assertEqual(self.unit["Install"]["WantedBy"], ["default.target"])

    def test_burst_covers_the_update_and_rollback_sequence(self):
        # boot start + update restart + 2 failures (the second is the automatic retry) fit in 4, and the
        # rolled-back start is the 4th: rollback.py rolls back after MAX_UNPROVEN_ATTEMPTS = 2.
        from pixelswitch import rollback

        self.assertEqual(rollback.MAX_UNPROVEN_ATTEMPTS, 2)
        self.assertGreaterEqual(int(self.unit["Unit"]["StartLimitBurst"][0]), 1 + rollback.MAX_UNPROVEN_ATTEMPTS + 1)


class FilesTests(unittest.TestCase):
    def test_desktop_files(self):
        for name, extras in (("pixelswitch-tray.desktop", ("X-GNOME-Autostart-enabled=true",)),
                             ("pixelswitch-settings.desktop", ("Categories=Utility;GTK;", "StartupWMClass=ai.pixelventures.pixelswitch.settings"))):
            text = slurp(os.path.join(PACKAGING, name), encoding="utf-8")
            self.assertTrue(text.startswith("[Desktop Entry]\n"))
            for line in ("Type=Application", "Icon=pixelswitch", "Terminal=false") + extras:
                self.assertIn(line, text.splitlines(), name)
            self.assertRegex(text, r"(?m)^Exec=@HOME@/\.local/bin/pixelswitch-(tray|settings)$")
        # lane LB writes the tray entry itself for launchAtLogin; both must point at the same launcher.
        from pixelswitch import autostart

        self.assertIn("Exec=/h/.local/bin/pixelswitch-tray", autostart.desktop_entry("/h"))

    def test_icons_are_valid_svg(self):
        for rel in ("hicolor/scalable/apps/pixelswitch.svg", "hicolor/symbolic/apps/pixelswitch-symbolic.svg"):
            document = xml.dom.minidom.parse(os.path.join(PACKAGING, "icons", rel))
            self.assertEqual(document.documentElement.tagName, "svg")
            self.assertTrue(document.documentElement.getAttribute("viewBox"))

    def test_launchers(self):
        for name in LAUNCHERS:
            path = os.path.join(BIN, name)
            text = slurp(path, encoding="utf-8")
            self.assertTrue(os.access(path, os.X_OK), name)
            self.assertEqual(text.splitlines()[0], "#!/usr/bin/env python3")
            self.assertIn("os.umask(0o077)", text)
            self.assertIn("os.path.realpath(__file__)", text)
            self.assertIn("sys.path.insert(0, _INSTALL_DIR)", text)
            self.assertLess(text.index("os.umask(0o077)"), text.index("from pixelswitch"))
        self.assertIn("from pixelswitch.daemon import main", slurp(os.path.join(BIN, "pixelswitchd")))
        self.assertIn("from pixelswitch.rollback import main", slurp(os.path.join(BIN, "pixelswitch-rollback")))
        self.assertIn("from pixelswitch.gui.tray import main", slurp(os.path.join(BIN, "pixelswitch-tray")))
        self.assertIn("from pixelswitch.gui.settings import main", slurp(os.path.join(BIN, "pixelswitch-settings")))

    def test_a_launcher_runs_from_a_symlink_and_loads_its_own_version(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as scratch:
            link = os.path.join(scratch, "pixelswitch")
            os.symlink(os.path.join(BIN, "pixelswitch"), link)
            out = subprocess.run([sys.executable, "-I", link, "--version"], capture_output=True, text=True, cwd=scratch)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertRegex(out.stdout, r"^pixelswitch \d+\.\d+ \(PixelSwitch remote-control protocol 2\)\n$")
            out = subprocess.run([sys.executable, "-I", os.path.join(BIN, "pixelswitchd"), "--self-check"], capture_output=True, text=True,
                                 env={"HOME": scratch, "PATH": os.environ.get("PATH", "")})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertRegex(out.stdout, r"^pixelswitchd \d+\.\d+ \(build \d+\)\n$")

    def test_the_rollback_launcher_runs(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as scratch:
            out = subprocess.run([sys.executable, "-I", os.path.join(BIN, "pixelswitch-rollback")], capture_output=True, text=True,
                                 env={"HOME": scratch})
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertTrue(out.stdout.startswith("pixelswitch-rollback: "))

    def test_install_sh_embeds_the_project_public_key(self):
        _, _, key = project_values()
        self.assertIn(f'\nSU_PUBLIC_ED_KEY="{key}"\n', slurp(INSTALL, encoding="utf-8"))
        from pixelswitch.update_check import SU_PUBLIC_ED_KEY

        self.assertEqual(SU_PUBLIC_ED_KEY, key)

    def test_scripts_parse(self):
        for script in (INSTALL, BUILD):
            self.assertEqual(subprocess.run(["bash", "-n", script], capture_output=True).returncode, 0, script)


# --- the tarball ------------------------------------------------------------------------------------


def python_tarball(path: str, version: str = "9.8", build: str = "99", package_text: str | None = None,
                   installer: str | None = None) -> None:
    """The release layout built with tarfile (for hosts without GNU tar): one top folder holding VERSION, pixelswitch/,
    bin/ and packaging/."""
    top = f"pixelswitch-linux-{version}"
    with tarfile.open(path, "w:gz") as archive:
        for sub in ("pixelswitch", "bin", "packaging"):
            for directory, names, files in os.walk(os.path.join(LINUX_ROOT, sub)):
                names[:] = sorted(n for n in names if n != "__pycache__")
                for name in sorted(files):
                    full = os.path.join(directory, name)
                    info = archive.gettarinfo(full, arcname=f"{top}/{os.path.relpath(full, LINUX_ROOT)}")
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with open(full, "rb") as handle:
                        archive.addfile(info, handle)
        data = f"version={version}\nbuild={build}\n".encode()
        info = tarfile.TarInfo(f"{top}/VERSION")
        info.size = len(data)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
        if installer is not None:
            data = installer.encode()
            info = tarfile.TarInfo(f"{top}/install-linux.sh")
            info.size = len(data)
            info.mode = 0o755
            archive.addfile(info, io.BytesIO(data))


def run_release_sh(scratch: str, notes: str, answer: str = "n\n") -> subprocess.CompletedProcess:
    """scripts/release.sh 2.0 in a throwaway git repository (on main, clean, with a local bare "origin") holding this
    checkout's project.yml and linux/install.sh and the given RELEASE_NOTES.md. Answers the prompt with ``answer``,
    so nothing is ever bumped, tagged or pushed (the check runs before the prompt)."""
    repo = tempfile.mkdtemp(prefix="psrel", dir=scratch)
    origin = tempfile.mkdtemp(prefix="psorigin", dir=scratch)
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "-C", repo]
    subprocess.run(["git", "init", "-q", "--bare", origin], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    shutil.copy(os.path.join(REPO_ROOT, "project.yml"), repo)
    os.makedirs(os.path.join(repo, "linux"))
    shutil.copy(INSTALL, os.path.join(repo, "linux", "install.sh"))
    spit(os.path.join(repo, "RELEASE_NOTES.md"), notes)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    subprocess.run([*git, "remote", "add", "origin", origin], check=True)
    return subprocess.run(["bash", os.path.join(REPO_ROOT, "scripts", "release.sh"), "2.0"], input=answer, capture_output=True,
                          text=True, cwd=repo, env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull})


class ReleaseScriptTests(unittest.TestCase):
    """scripts/release.sh refuses to tag unless RELEASE_NOTES.md carries the SHA-256 of the published installer."""

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="psrelease", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def expected_sha(self) -> str:
        _, _, key = project_values()
        text = re.sub(r'(?m)^SU_PUBLIC_ED_KEY=".*"$', f'SU_PUBLIC_ED_KEY="{key}"', slurp(INSTALL, encoding="utf-8"))
        return hashlib.sha256(text.encode()).hexdigest()

    def test_the_placeholder_blocks_the_tag_and_the_message_gives_the_hash(self):
        done = run_release_sh(self.scratch, "sha256sum install-linux.sh   # must print INSTALLER_SHA256_PLACEHOLDER\n")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("INSTALLER_SHA256_PLACEHOLDER", done.stderr)
        self.assertIn(self.expected_sha(), done.stderr)
        self.assertNotIn("Proceed?", done.stdout)

    def test_a_stale_hash_blocks_the_tag(self):
        done = run_release_sh(self.scratch, f"must print {'0' * 64}\n")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("does not carry the SHA-256 of install-linux.sh", done.stderr)
        self.assertIn(self.expected_sha(), done.stderr)

    def test_the_right_hash_lets_it_reach_the_prompt_and_nothing_changes(self):
        done = run_release_sh(self.scratch, f"must print {self.expected_sha()}\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Aborted.", done.stdout)
        self.assertIn("Linux installer SHA-256 is in RELEASE_NOTES.md", done.stdout)

    def test_the_repository_notes_hold_no_placeholder_once_a_release_is_cut(self):
        # Informational pin: at release time this must be the real hash. Until then the placeholder is expected,
        # and release.sh is what stops a tag.
        notes = slurp(os.path.join(REPO_ROOT, "RELEASE_NOTES.md"), encoding="utf-8")
        self.assertTrue("INSTALLER_SHA256_PLACEHOLDER" in notes or self.expected_sha() in notes)


@unittest.skipUnless(gnu_tar(), "GNU tar is needed (on a Mac: brew install gnu-tar)")
class BuildReleaseTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="psbuild", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def build(self, out, extra=(), env=None, root=None):
        script = BUILD if root is None else os.path.join(root, "linux", "build-release.sh")
        full_env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000", **(env or {})}
        return subprocess.run(["bash", script, "--out", out, *extra], capture_output=True, text=True, env=full_env)

    def tarball(self, out):
        version, _, _ = project_values()
        return os.path.join(out, f"pixelswitch-linux-{version}.tar.gz")

    def test_two_builds_are_byte_identical(self):
        one, two = os.path.join(self.scratch, "one"), os.path.join(self.scratch, "two")
        for out in (one, two):
            done = self.build(out)
            self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(slurp(self.tarball(one), "rb"), slurp(self.tarball(two), "rb"))
        self.assertEqual(slurp(os.path.join(one, "install-linux.sh"), "rb"), slurp(os.path.join(two, "install-linux.sh"), "rb"))

    def test_a_different_source_date_epoch_changes_only_times(self):
        one, two = os.path.join(self.scratch, "a"), os.path.join(self.scratch, "b")
        self.build(one)
        self.build(two, env={"SOURCE_DATE_EPOCH": "1700000001"})
        self.assertNotEqual(slurp(self.tarball(one), "rb"), slurp(self.tarball(two), "rb"))

        def members(path):
            with tarfile.open(path) as archive:
                return [(m.name, m.size, m.mode, m.mtime, archive.extractfile(m).read() if m.isfile() else None)
                        for m in archive.getmembers()]

        first, second = members(self.tarball(one)), members(self.tarball(two))
        self.assertEqual([m[:3] + m[4:] for m in first], [m[:3] + m[4:] for m in second])  # names, sizes, modes, contents
        self.assertEqual({m[3] for m in first}, {1700000000})
        self.assertEqual({m[3] for m in second}, {1700000001})

    def test_contents_modes_and_the_version_file(self):
        out = os.path.join(self.scratch, "out")
        self.assertEqual(self.build(out).returncode, 0)
        version, build, _ = project_values()
        top = f"pixelswitch-linux-{version}"
        with tarfile.open(self.tarball(out)) as archive:
            order = [m.name for m in archive.getmembers()]
            members = {m.name: m for m in archive.getmembers()}
            names = sorted(members)
            # --sort=name: archive order is GNU tar's depth-first, per-folder name order (a sorted() of the same
            # list would prove nothing). Without the flag, the order is whatever readdir returned.
            self.assertEqual(order, sorted(order, key=lambda n: n.split("/")))
            self.assertTrue(all(n == top or n.startswith(top + "/") for n in names))
            self.assertEqual(archive.extractfile(f"{top}/VERSION").read().decode(), f"version={version}\nbuild={build}\n")
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc") or "/tests/" in n or ".DS_Store" in n])
            for required in ("pixelswitch/__init__.py", "pixelswitch/cli.py", "pixelswitch/mcp.py", "pixelswitch/gui/tray.py",
                             "pixelswitch/gui/settings.py", "pixelswitch/rollback.py", "packaging/pixelswitch.service",
                             "packaging/pixelswitch-tray.desktop", "packaging/pixelswitch-settings.desktop",
                             "packaging/icons/hicolor/scalable/apps/pixelswitch.svg", "README.md", "install-linux.sh"):
                self.assertIn(f"{top}/{required}", members, required)
            for name in LAUNCHERS:
                self.assertEqual(members[f"{top}/bin/{name}"].mode, 0o755, name)
            self.assertEqual(members[f"{top}/install-linux.sh"].mode, 0o755)
            self.assertEqual(archive.extractfile(f"{top}/install-linux.sh").read(), slurp(os.path.join(out, "install-linux.sh"), "rb"))
            self.assertEqual(members[f"{top}/pixelswitch/cli.py"].mode, 0o644)
            for member in members.values():
                self.assertEqual((member.uid, member.gid, member.uname, member.gname), (0, 0, "", ""), member.name)
                self.assertEqual(member.mtime, 1700000000, member.name)
        # gzip -n: no file name or time in the header.
        header = slurp(self.tarball(out), "rb")[:10]
        self.assertEqual(header[4:8], b"\0\0\0\0")

    def test_the_tarball_passes_the_updaters_own_checks(self):
        from pixelswitch.update_check import Feed, check_extracted_version

        out = os.path.join(self.scratch, "out")
        self.assertEqual(self.build(out).returncode, 0)
        dest = os.path.join(self.scratch, "x")
        safe_extract(self.tarball(out), dest)
        (root,) = os.listdir(dest)
        version, build, _ = project_values()
        feed = Feed(version, int(build), "u", 1, "0" * 64, "x")
        check_extracted_version(os.path.join(dest, root), feed, int(build) - 1)
        done = subprocess.run([sys.executable, "-I", os.path.join(dest, root, "bin", "pixelswitchd"), "--self-check"],
                              capture_output=True, text=True, env={"HOME": self.scratch})
        self.assertEqual(done.stdout.strip(), f"pixelswitchd {version} (build {build})", done.stderr)

    def test_install_linux_sh_carries_the_key_and_the_sha_is_printed(self):
        out = os.path.join(self.scratch, "out")
        done = self.build(out)
        _, _, key = project_values()
        text = slurp(os.path.join(out, "install-linux.sh"), encoding="utf-8")
        self.assertIn(f'\nSU_PUBLIC_ED_KEY="{key}"\n', text)
        self.assertIn(hashlib.sha256(text.encode()).hexdigest(), done.stdout)
        self.assertTrue(os.access(os.path.join(out, "install-linux.sh"), os.X_OK))
        self.assertFalse(os.path.exists(os.path.join(out, "latest-linux.json")))  # no key given: no feed

    def test_the_installer_inside_the_tarball_is_the_published_one_and_a_pipe_install_keeps_it(self):
        out = os.path.join(self.scratch, "out")
        self.assertEqual(self.build(out).returncode, 0)
        published = slurp(os.path.join(out, "install-linux.sh"), encoding="utf-8")
        home = os.path.join(self.scratch, "home")
        os.makedirs(home)
        env = {"HOME": home, "PATH": clean_path(), "PIXELSWITCH_INSTALL_SKIP_CHECKS": "1"}
        done = subprocess.run(["bash", "-s", "--", "--from", self.tarball(out), "--no-systemd", "--no-autostart"], input=published,
                              capture_output=True, text=True, env=env, cwd=self.scratch)
        self.assertEqual(done.returncode, 0, done.stderr)
        kept = os.path.join(home, ".local", "share", "pixelswitch", "install-linux.sh")
        self.assertEqual(slurp(kept, encoding="utf-8"), published)  # the piped run had no file of its own to copy
        self.assertIn(f"To remove it: {kept} --uninstall", done.stdout)

    def test_the_signing_key_file_is_gone_after_a_kill_and_after_a_refusal(self):
        if not openssl3():
            self.skipTest("OpenSSL 3 is needed to sign")
        tmp = os.path.join(self.scratch, "tmp")
        os.makedirs(tmp)
        key_file = os.path.join(self.scratch, "seed.b64")
        spit(key_file, base64.b64encode(bytes(32)).decode())
        # A key of the wrong length is refused after the key file was made: nothing may stay behind.
        short = os.path.join(self.scratch, "short.b64")
        spit(short, base64.b64encode(b"x" * 33).decode())
        done = self.build(os.path.join(self.scratch, "o1"), ("--signing-key", short), env={"TMPDIR": tmp})
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(glob.glob(os.path.join(tmp, "pixelswitch-key.*")), [])
        # A kill while openssl is signing (a slow stand-in), as when a CI job is cancelled or Ctrl-C is pressed.
        marker = os.path.join(self.scratch, "signing")
        slow = os.path.join(self.scratch, "slow-openssl")
        spit(slow, f'#!/bin/sh\ntouch "{marker}"\nsleep 30\n')
        os.chmod(slow, 0o755)
        env = {**os.environ, "SOURCE_DATE_EPOCH": "1700000000", "TMPDIR": tmp, "OPENSSL": slow}
        process = subprocess.Popen(["bash", BUILD, "--out", os.path.join(self.scratch, "o2"), "--signing-key", key_file],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 60
            while not os.path.exists(marker) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(os.path.exists(marker), "the build never reached the signing step")
            self.assertEqual(len(glob.glob(os.path.join(tmp, "pixelswitch-key.*"))), 1)  # the test can see the key file
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        self.assertEqual(glob.glob(os.path.join(tmp, "pixelswitch-key.*")), [])

    def test_release_sh_uses_the_same_installer_hash_as_the_build(self):
        out = os.path.join(self.scratch, "out")
        self.assertEqual(self.build(out).returncode, 0)
        sha = hashlib.sha256(slurp(os.path.join(out, "install-linux.sh"), "rb")).hexdigest()
        done = run_release_sh(self.scratch, f"The installer: {sha}\n")
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Aborted.", done.stdout)
        self.assertIn(sha, done.stdout)

    def project_with_key(self, seed: bytes) -> str:
        """A copy of linux/ and project.yml whose SUPublicEDKey is the test seed's public key."""
        root = os.path.join(self.scratch, "tree")
        shutil.copytree(LINUX_ROOT, os.path.join(root, "linux"), ignore=shutil.ignore_patterns("__pycache__", "tests"))
        text = slurp(os.path.join(REPO_ROOT, "project.yml"), encoding="utf-8")
        public = base64.b64encode(ed25519.public_key_from_seed(seed)).decode()
        text = re.sub(r"(SUPublicEDKey: ).*", r"\g<1>" + public, text)
        spit(os.path.join(root, "project.yml"), text)
        return root

    @unittest.skipUnless(openssl3(), "OpenSSL 3 is needed to sign")
    def test_the_feed_is_signed_and_verifies(self):
        seed = bytes(range(32))
        root = self.project_with_key(seed)
        key_file = os.path.join(self.scratch, "seed.b64")
        spit(key_file, base64.b64encode(seed).decode() + "\n")
        os.chmod(key_file, 0o600)
        out = os.path.join(self.scratch, "out")
        done = self.build(out, ("--signing-key", key_file), env={"OPENSSL": openssl3()}, root=root)
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        feed = json.loads(slurp(os.path.join(out, "latest-linux.json")))
        version, build, _ = project_values()
        tar_path = self.tarball(out)
        self.assertEqual(sorted(feed), ["build", "sha256", "signature", "size", "url", "version"])
        self.assertEqual((feed["version"], feed["build"]), (version, int(build)))
        self.assertEqual(feed["url"], f"https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v{version}/pixelswitch-linux-{version}.tar.gz")
        self.assertEqual(feed["size"], os.path.getsize(tar_path))
        self.assertEqual(feed["sha256"], hashlib.sha256(slurp(tar_path, "rb")).hexdigest())
        public = base64.b64encode(ed25519.public_key_from_seed(seed)).decode()
        self.assertTrue(ed25519.verify_file(tar_path, feed["signature"], public, use_openssl=False))
        # The updater accepts exactly this feed.
        from pixelswitch.update_check import validate_feed

        validate_feed(feed, int(build) - 1)
        self.assertNotIn(base64.b64encode(seed).decode(), done.stdout + done.stderr)  # the secret is never printed

    @unittest.skipUnless(openssl3(), "OpenSSL 3 is needed to sign")
    def test_sign_only_signs_the_tarball_already_built_and_reads_env_keys(self):
        seed = bytes(range(32, 64))
        root = self.project_with_key(seed)
        out = os.path.join(self.scratch, "out")
        self.assertEqual(self.build(out, root=root).returncode, 0)
        before = slurp(self.tarball(out), "rb")
        done = self.build(out, ("--sign-only", "--signing-key", "env:TEST_SEED"),
                          env={"OPENSSL": openssl3(), "TEST_SEED": base64.b64encode(seed).decode()}, root=root)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(slurp(self.tarball(out), "rb"), before)
        self.assertTrue(os.path.exists(os.path.join(out, "latest-linux.json")))

    @unittest.skipUnless(openssl3(), "OpenSSL 3 is needed to sign")
    def test_a_key_that_does_not_match_the_public_key_publishes_nothing(self):
        out = os.path.join(self.scratch, "out")
        key_file = os.path.join(self.scratch, "wrong.b64")
        spit(key_file, base64.b64encode(bytes(32)).decode())
        done = self.build(out, ("--signing-key", key_file), env={"OPENSSL": openssl3()})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("does not verify against SUPublicEDKey", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "latest-linux.json")))

    def test_a_key_of_the_wrong_length_is_refused(self):
        out = os.path.join(self.scratch, "out")
        key_file = os.path.join(self.scratch, "short.b64")
        spit(key_file, base64.b64encode(b"x" * 33).decode())
        done = self.build(out, ("--signing-key", key_file))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("must decode to 32 bytes", done.stderr)


# --- install.sh -----------------------------------------------------------------------------------------


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="psinst", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.home = os.path.join(self.scratch, "home")
        os.makedirs(self.home)
        self.tar = os.path.join(self.scratch, "t.tar.gz")
        python_tarball(self.tar)
        # sudo and pkexec stand-ins first on PATH: any call to either leaves a marker, and every test fails on it.
        self.sudo_marker = os.path.join(self.scratch, "sudo-called")
        self.shims = make_shims(os.path.join(self.scratch, "shims"), self.sudo_marker)
        self.addCleanup(lambda: self.assertFalse(os.path.exists(self.sudo_marker), "install.sh called sudo or pkexec"))

    def run_install(self, *args, extra_env=None, script=INSTALL, path_first=()):
        path = os.pathsep.join([*path_first, self.shims, clean_path()])
        env = {"HOME": self.home, "PATH": path, "PIXELSWITCH_INSTALL_SKIP_CHECKS": "1", **(extra_env or {})}
        return subprocess.run(["bash", script, *args], capture_output=True, text=True, env=env, cwd=self.scratch)

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def test_install_from_a_local_tarball_in_a_scratch_home(self):
        done = self.run_install("--from", self.tar, "--no-systemd", "--no-autostart")
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("UNVERIFIED local build", done.stdout)
        data = self.path(".local", "share", "pixelswitch")
        self.assertTrue(os.path.isdir(os.path.join(data, "app", "9.8-99", "pixelswitch")))
        self.assertEqual(os.readlink(os.path.join(data, "app", "current")), "9.8-99")
        for name in ("pixelswitch", "pixelswitchd", "pixelswitch-tray", "pixelswitch-settings"):
            link = self.path(".local", "bin", name)
            self.assertEqual(os.readlink(link), os.path.join(data, "app", "current", "bin", name))
            self.assertTrue(os.access(link, os.X_OK))
        self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch-rollback")))
        rollback = os.path.join(data, "rollback.py")
        self.assertTrue(os.path.isfile(rollback))
        self.assertEqual(slurp(rollback), slurp(os.path.join(LINUX_ROOT, "pixelswitch", "rollback.py")))
        desktop = slurp(self.path(".local", "share", "applications", "pixelswitch-settings.desktop"))
        self.assertIn(f"Exec={self.home}/.local/bin/pixelswitch-settings", desktop)
        self.assertNotIn("@HOME@", desktop)
        for rel in ("scalable/apps/pixelswitch.svg", "symbolic/apps/pixelswitch-symbolic.svg"):
            self.assertTrue(os.path.isfile(self.path(".local", "share", "icons", "hicolor", rel)))
        # --no-systemd and --no-autostart touched neither.
        self.assertFalse(os.path.exists(self.path(".config", "systemd")))
        self.assertFalse(os.path.exists(self.path(".config", "autostart")))
        self.assertTrue(os.path.isfile(os.path.join(data, "install-linux.sh")))
        # Nothing it wrote is wider than the user's own, and no settings were created (auto-switch stays off).
        self.assertFalse(os.path.exists(self.path(".config", "pixelswitch")))
        for directory, names, files in os.walk(data):
            for name in names + files:
                full = os.path.join(directory, name)
                if not os.path.islink(full):
                    self.assertEqual(os.stat(full).st_mode & 0o022, 0, full)
        # The words at the end: the next step, auto-switch off, and the one sentence about shared logins.
        self.assertIn("pixelswitch accounts add-current", done.stdout)
        self.assertIn("Auto-switch is OFF", done.stdout)
        self.assertIn("CabDesign farm", done.stdout)
        self.assertIn("Switching changes their account too", done.stdout)
        out = subprocess.run([self.path(".local", "bin", "pixelswitch"), "--version"], capture_output=True, text=True, env={"HOME": self.home})
        self.assertEqual(out.stdout, "pixelswitch 9.8 (PixelSwitch remote-control protocol 2)\n")

    def test_the_autostart_entry_when_asked_for(self):
        done = self.run_install("--from", self.tar, "--no-systemd")
        self.assertEqual(done.returncode, 0, done.stderr)
        entry = slurp(self.path(".config", "autostart", "pixelswitch-tray.desktop"))
        self.assertIn(f"Exec={self.home}/.local/bin/pixelswitch-tray", entry)
        from pixelswitch import autostart
        from pixelswitch.paths import Paths

        self.assertTrue(autostart.is_enabled(Paths(home=self.home, runtime_dir=None)))  # lane LB reads it as on

    def test_a_second_install_keeps_the_previous_version_and_flips_current(self):
        self.run_install("--from", self.tar, "--no-systemd", "--no-autostart")
        newer = os.path.join(self.scratch, "n.tar.gz")
        python_tarball(newer, "9.9", "100")
        done = self.run_install("--from", newer, "--no-systemd", "--no-autostart")
        self.assertEqual(done.returncode, 0, done.stderr)
        app = self.path(".local", "share", "pixelswitch", "app")
        self.assertEqual(os.readlink(os.path.join(app, "current")), "9.9-100")
        self.assertEqual(os.readlink(os.path.join(app, "previous")), "9.8-99")
        self.assertTrue(os.path.isdir(os.path.join(app, "9.8-99")))

    def test_it_refuses_to_overwrite_a_file_that_is_not_its_own(self):
        os.makedirs(self.path(".local", "bin"))
        spit(self.path(".local", "bin", "pixelswitch"), "mine")
        done = self.run_install("--from", self.tar, "--no-systemd", "--no-autostart")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("already exists and is not PixelSwitch's", done.stderr)
        self.assertEqual(slurp(self.path(".local", "bin", "pixelswitch")), "mine")

    def test_a_broken_archive_installs_nothing(self):
        bad = os.path.join(self.scratch, "bad.tar.gz")
        with tarfile.open(bad, "w:gz") as archive:
            data = b"hello"
            info = tarfile.TarInfo("top/README")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        done = self.run_install("--from", bad, "--no-systemd", "--no-autostart")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("Nothing was installed", done.stderr)
        self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")))

    def test_a_path_that_leaves_the_folder_is_refused_before_unpacking(self):
        bad = os.path.join(self.scratch, "evil.tar.gz")
        with tarfile.open(bad, "w:gz") as archive:
            data = b"x"
            info = tarfile.TarInfo("top/../../escape")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        done = self.run_install("--from", bad, "--no-systemd", "--no-autostart")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("outside its folder", done.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.scratch, "escape")))

    def test_a_version_file_that_is_wrong_is_refused(self):
        bad = os.path.join(self.scratch, "v.tar.gz")
        python_tarball(bad, "9.8", "99")
        # Rewrite VERSION to garbage.
        with tarfile.open(bad) as source, tarfile.open(bad + ".2", "w:gz") as target:
            for member in source.getmembers():
                data = source.extractfile(member).read() if member.isfile() else None
                if member.name.endswith("/VERSION"):
                    data = b"version=abc\nbuild=1\n"
                    member.size = len(data)
                target.addfile(member, io.BytesIO(data) if data is not None else None)
        done = self.run_install("--from", bad + ".2", "--no-systemd", "--no-autostart")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("VERSION file is not readable", done.stderr)

    def test_uninstall_keeps_saved_logins_and_purge_removes_them(self):
        self.run_install("--from", self.tar, "--no-systemd", "--no-autostart")
        os.makedirs(self.path(".config", "pixelswitch"))
        spit(self.path(".config", "pixelswitch", "settings.json"), "{}")
        data = self.path(".local", "share", "pixelswitch")
        spit(os.path.join(data, "backups.json"), "{}")
        done = self.run_install("--uninstall", "--no-systemd")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(os.path.join(data, "app")))
        for name in ("pixelswitch", "pixelswitchd", "pixelswitch-tray", "pixelswitch-settings"):
            self.assertFalse(os.path.lexists(self.path(".local", "bin", name)), name)
        self.assertFalse(os.path.exists(self.path(".local", "share", "applications", "pixelswitch-settings.desktop")))
        self.assertTrue(os.path.exists(os.path.join(data, "backups.json")))
        self.assertTrue(os.path.exists(self.path(".config", "pixelswitch", "settings.json")))
        self.assertIn("saved logins are kept", done.stdout)
        done = self.run_install("--uninstall", "--purge", "--no-systemd")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(data))
        self.assertFalse(os.path.exists(self.path(".config", "pixelswitch")))

    def test_uninstall_leaves_a_link_that_is_not_its_own(self):
        os.makedirs(self.path(".local", "bin"))
        os.symlink("/usr/bin/true", self.path(".local", "bin", "pixelswitch"))
        done = self.run_install("--uninstall", "--no-systemd")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(os.readlink(self.path(".local", "bin", "pixelswitch")), "/usr/bin/true")

    def test_bad_options_and_no_root(self):
        for args in (["--bogus"], ["--signature", "x"], ["--purge"], ["--from"]):
            done = self.run_install(*args)
            self.assertNotEqual(done.returncode, 0, args)
        done = self.run_install("--help")
        self.assertEqual(done.returncode, 0)
        self.assertIn("No sudo, ever", done.stdout)

    @staticmethod
    def sudo_outside_messages(text: str) -> list[str]:
        """Lines where ``sudo`` is anywhere but inside the quoted text of a die, warn or say call. A comment line is
        ignored; ``sudo apt install`` executed, or merely sitting at the start of a line, is reported."""
        found = []
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            for match in re.finditer(r"\bsudo\b", line):
                if not re.search(r'\b(die|warn|say)\s+"[^"]*$', line[: match.start()]):
                    found.append(line.strip())
        return found

    def test_the_script_never_calls_sudo(self):
        text = slurp(INSTALL, encoding="utf-8")
        self.assertEqual(self.sudo_outside_messages(text), [])
        self.assertNotIn("pkexec", "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
        # The check can fail: an executed install, whatever its spelling, is found.
        for bad in ("  sudo apt install -y python3-gi", "  sudo  apt-get install x", '  warn "x"; sudo apt install y',
                    '  command -v gi || sudo apt install -y python3-gi gir1.2-gtk-4.0'):
            self.assertTrue(self.sudo_outside_messages(text + "\n" + bad), bad)
        self.assertEqual(self.sudo_outside_messages('  die "Install it with: sudo apt install x (never uses sudo)."'), [])

    def test_the_scripts_work_when_a_guard_script_shadows_rm(self):
        # On the founder's laptop a login shell puts a guard `rm` first on PATH. Every delete goes to /bin/rm.
        marker = os.path.join(self.scratch, "rm-called")
        guard = os.path.join(self.scratch, "guard")
        os.makedirs(guard)
        spit(os.path.join(guard, "rm"), f'#!/bin/sh\ntouch "{marker}"\necho "refused" >&2\nexit 1\n')
        os.chmod(os.path.join(guard, "rm"), 0o755)
        common = ("--no-systemd", "--no-autostart")
        for args in (("--from", self.tar), ("--from", self.tar)):  # a fresh install, then the same build again
            done = self.run_install(*args, *common, path_first=(guard,))
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        newer = os.path.join(self.scratch, "n.tar.gz")
        python_tarball(newer, "9.9", "100")
        done = self.run_install("--from", newer, *common, path_first=(guard,))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        os.makedirs(self.path(".config", "pixelswitch"))
        done = self.run_install("--uninstall", "--purge", "--no-systemd", path_first=(guard,))
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertFalse(os.path.exists(marker), "a script called the guard rm")
        self.assertFalse(os.path.exists(self.path(".local", "share", "pixelswitch")))
        self.assertFalse(os.path.exists(self.path(".config", "pixelswitch")))
        for name in ("pixelswitch", "pixelswitchd", "pixelswitch-tray", "pixelswitch-settings"):
            self.assertFalse(os.path.lexists(self.path(".local", "bin", name)), name)
        # build-release.sh deletes through /bin/rm too (checked as text; the build itself runs in BuildReleaseTests).
        self.assertIn('rm() { /bin/rm "$@"; }', slurp(BUILD, encoding="utf-8"))
        self.assertIn('rm() { /bin/rm "$@"; }', slurp(INSTALL, encoding="utf-8"))

    def test_a_one_line_install_keeps_a_copy_of_the_installer_and_the_uninstall_command_runs(self):
        text = slurp(INSTALL, encoding="utf-8")
        tar = os.path.join(self.scratch, "with-installer.tar.gz")
        python_tarball(tar, installer=text)
        env = {"HOME": self.home, "PATH": os.pathsep.join([self.shims, clean_path()]), "PIXELSWITCH_INSTALL_SKIP_CHECKS": "1"}
        done = subprocess.run(["bash", "-s", "--", "--from", tar, "--no-systemd", "--no-autostart"], input=text, capture_output=True,
                              text=True, env=env, cwd=self.scratch)  # `curl ... | bash`: no script file, only stdin
        self.assertEqual(done.returncode, 0, done.stderr)
        kept = self.path(".local", "share", "pixelswitch", "install-linux.sh")
        self.assertEqual(slurp(kept, encoding="utf-8"), text)
        self.assertTrue(os.access(kept, os.X_OK))
        self.assertIn(f"To remove it: {kept} --uninstall", done.stdout)
        done = self.run_install("--uninstall", "--no-systemd", script=kept)  # the documented uninstall command
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(self.path(".local", "share", "pixelswitch", "app")))
        self.assertFalse(os.path.exists(kept))

    def test_a_script_cut_off_in_the_middle_runs_nothing(self):
        # curl | bash executes what has arrived. Everything after the definitions is in main(), called on the last
        # line, so a download that stops part way is a syntax error and changes nothing.
        text = slurp(INSTALL, encoding="utf-8")
        lines = text.splitlines(keepends=True)
        self.assertEqual(lines[-1].strip(), 'main "$@"')
        for fraction in (0.5, 0.8, 0.95):
            cut = "".join(lines[: int(len(lines) * fraction)])
            env = {"HOME": self.home, "PATH": os.pathsep.join([self.shims, clean_path()]), "PIXELSWITCH_INSTALL_SKIP_CHECKS": "1"}
            done = subprocess.run(["bash", "-s", "--", "--from", self.tar, "--no-systemd", "--no-autostart"], input=cut,
                                  capture_output=True, text=True, env=env, cwd=self.scratch)
            self.assertNotEqual(done.returncode, 0, fraction)
            self.assertEqual(os.listdir(self.home), [], f"a script cut at {fraction} changed the home folder")

    def test_launch_at_login_stays_off_when_the_installer_is_run_again(self):
        done = self.run_install("--from", self.tar, "--no-systemd")
        self.assertEqual(done.returncode, 0, done.stderr)
        entry = self.path(".config", "autostart", "pixelswitch-tray.desktop")
        self.assertTrue(os.path.isfile(entry))
        os.unlink(entry)  # the user turned "Launch at login" off: the file is the setting
        done = self.run_install("--from", self.tar, "--no-systemd")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertFalse(os.path.exists(entry))
        self.assertIn("Launch at login is off", done.stdout)
        # while a file that is there is kept up to date
        os.makedirs(os.path.dirname(entry), exist_ok=True)
        spit(entry, "old")
        self.run_install("--from", self.tar, "--no-systemd")
        self.assertIn(f"Exec={self.home}/.local/bin/pixelswitch-tray", slurp(entry))

    def test_a_hostile_archive_with_a_late_bad_name_or_an_outside_link_is_refused(self):
        victim = os.path.join(self.scratch, "victim")
        spit(victim, "private")
        os.chmod(victim, 0o600)
        for kind in ("late", "link"):
            bad = os.path.join(self.scratch, f"{kind}.tar.gz")
            with tarfile.open(bad, "w:gz") as archive:
                if kind == "late":
                    for number in range(3000):  # more than a pipe holds, then the bad name
                        info = tarfile.TarInfo(f"top/{'n' * 60}{number:05d}")
                        archive.addfile(info, io.BytesIO(b""))
                    info = tarfile.TarInfo("top/../../escape")
                    archive.addfile(info, io.BytesIO(b""))
                else:
                    info = tarfile.TarInfo("top/bin/pixelswitchd")
                    info.type = tarfile.SYMTYPE
                    info.linkname = victim
                    archive.addfile(info)
                    data = b"version=9.8\nbuild=99\n"
                    info = tarfile.TarInfo("top/VERSION")
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            done = self.run_install("--from", bad, "--no-systemd", "--no-autostart")
            self.assertNotEqual(done.returncode, 0, kind)
            self.assertIn("Nothing was installed", done.stderr, kind)
            self.assertEqual(os.stat(victim).st_mode & 0o777, 0o600, kind)
            self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")), kind)

    @unittest.skipUnless(openssl3(), "OpenSSL 3 is needed to verify an Ed25519 signature")
    def test_the_bytes_checked_are_the_bytes_installed_even_if_the_file_changes_afterwards(self):
        seed = bytes(range(11, 43))
        public = base64.b64encode(ed25519.public_key_from_seed(seed)).decode()
        source = os.path.join(self.scratch, "shared.tar.gz")
        shutil.copy(self.tar, source)
        sig_file = os.path.join(self.scratch, "shared.sig")
        spit(sig_file, base64.b64encode(ed25519.sign(seed, slurp(source, "rb"))).decode())
        other = os.path.join(self.scratch, "other.tar.gz")
        python_tarball(other, "9.7", "1")
        # An openssl stand-in that verifies for real and then lets "someone else" swap the file.
        shim = os.path.join(self.scratch, "openssl-swap")
        spit(shim, f'#!/bin/sh\n"{openssl3()}" "$@"\nrc=$?\ncp "{other}" "{source}"\nexit $rc\n')
        os.chmod(shim, 0o755)
        done = self.run_install("--from", source, "--signature", sig_file, "--no-systemd", "--no-autostart",
                                extra_env={"PIXELSWITCH_INSTALL_PUBLIC_KEY": public, "PIXELSWITCH_OPENSSL": shim})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(os.readlink(self.path(".local", "share", "pixelswitch", "app", "current")), "9.8-99")

    def test_test_mode_hooks_are_ignored_outside_test_mode_and_refused_outside_the_scratch_root(self):
        # Without PIXELSWITCH_TEST_MODE the release base is ignored: the download goes to GitHub (a stand-in curl
        # records the address and fails, so nothing leaves this machine).
        record = os.path.join(self.scratch, "curl-args")
        shim_dir = os.path.join(self.scratch, "curlshim")
        os.makedirs(shim_dir)
        spit(os.path.join(shim_dir, "curl"), f'#!/bin/sh\necho "$@" >> "{record}"\nexit 22\n')
        os.chmod(os.path.join(shim_dir, "curl"), 0o755)
        os.makedirs(testmode.scratch_root(), mode=0o700, exist_ok=True)  # the refusal below is about HOME, not a missing folder
        hooks = {"PIXELSWITCH_TEST_RELEASE_BASE": "http://127.0.0.1:9", "PIXELSWITCH_TEST_PUBLIC_KEY": "A" * 43 + "="}
        done = self.run_install("--no-systemd", "--no-autostart", extra_env=hooks, path_first=(shim_dir,))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/latest-linux.json", slurp(record))
        self.assertNotIn("127.0.0.1", slurp(record))
        self.assertIn("--proto =https", slurp(record))
        # Test mode with a HOME outside ~/.cache/pixelswitch-dev/, or under systemd, is refused before anything runs.
        done = self.run_install("--no-systemd", "--no-autostart", extra_env={**hooks, "PIXELSWITCH_TEST_MODE": "1"}, path_first=(shim_dir,))
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("needs HOME under", done.stderr)
        done = self.run_install("--no-systemd", "--no-autostart", extra_env={**hooks, "PIXELSWITCH_TEST_MODE": "1", "INVOCATION_ID": "x"},
                                path_first=(shim_dir,))
        self.assertIn("under systemd", done.stderr)
        self.assertEqual(len(slurp(record).splitlines()), 1)  # only the first run reached curl

    # -- signatures --

    def sign(self, seed: bytes, path: str) -> str:
        return base64.b64encode(ed25519.sign(seed, slurp(path, "rb"))).decode()

    @unittest.skipUnless(openssl3(), "OpenSSL 3 is needed to verify an Ed25519 signature")
    def test_a_signed_tarball_installs_and_a_tampered_one_does_not(self):
        seed = bytes(range(7, 39))
        public = base64.b64encode(ed25519.public_key_from_seed(seed)).decode()
        sig_file = os.path.join(self.scratch, "t.sig")
        spit(sig_file, self.sign(seed, self.tar))
        env = {"PIXELSWITCH_INSTALL_PUBLIC_KEY": public, "PIXELSWITCH_OPENSSL": openssl3()}
        done = self.run_install("--from", self.tar, "--signature", sig_file, "--no-systemd", "--no-autostart", extra_env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("verified signature", done.stdout)
        self.assertNotIn("UNVERIFIED", done.stdout)
        # tampered: one byte appended
        shutil.rmtree(self.path(".local"))
        tampered = os.path.join(self.scratch, "tampered.tar.gz")
        shutil.copy(self.tar, tampered)
        with open(tampered, "ab") as handle:
            handle.write(b"\0")
        done = self.run_install("--from", tampered, "--signature", sig_file, "--no-systemd", "--no-autostart", extra_env=env)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("does not match PixelSwitch's public key", done.stderr)
        self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")))
        # a raw 64-byte signature file works too
        raw = os.path.join(self.scratch, "t.sig.bin")
        spit(raw, ed25519.sign(seed, slurp(self.tar, "rb")))
        done = self.run_install("--from", self.tar, "--signature", raw, "--no-systemd", "--no-autostart", extra_env=env)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_without_openssl_that_can_check_ed25519_it_fails_closed(self):
        shim = os.path.join(self.scratch, "openssl")
        spit(shim, "#!/bin/sh\necho 'unknown option -rawin' >&2\nexit 1\n")
        os.chmod(shim, 0o755)
        sig_file = os.path.join(self.scratch, "t.sig")
        spit(sig_file, self.sign(bytes(32), self.tar))
        done = self.run_install("--from", self.tar, "--signature", sig_file, "--no-systemd", "--no-autostart",
                                extra_env={"PIXELSWITCH_OPENSSL": shim})
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("cannot check an Ed25519 signature", done.stderr)
        self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")))

    # -- the dependency check, with stand-ins for python3, gdbus (Python 3.12, each typelib, the tray watcher) --

    PYTHON_SHIM = """#!/bin/sh
# Answers the installer's probes the way the test says, and runs the real Python for everything else.
code=""; prev=""
for a in "$@"; do [ "$prev" = "-c" ] && code="$a"; prev="$a"; done
case "$code" in
  *'sys.version_info >= (3, 12)'*) [ "${FAKE_PY_OLD:-}" = 1 ] && exit 1; exit 0 ;;
  *'print("%d.%d"'*) [ "${FAKE_PY_OLD:-}" = 1 ] && { echo 3.11; exit 0; }; exec "$REAL_PYTHON" "$@" ;;
  *require_version*) case "$code" in *"\"${FAKE_MISSING_TYPELIB:-@none@}\""*) exit 1 ;; esac; exit 0 ;;
  "import gi") exit 0 ;;
esac
exec "$REAL_PYTHON" "$@"
"""

    def dependency_run(self, extra_env=None, gdbus_answer=None):
        shim_dir = os.path.join(self.scratch, "depshim")
        os.makedirs(shim_dir, exist_ok=True)
        spit(os.path.join(shim_dir, "python3"), self.PYTHON_SHIM)
        os.chmod(os.path.join(shim_dir, "python3"), 0o755)
        if gdbus_answer is not None:
            spit(os.path.join(shim_dir, "gdbus"), f"#!/bin/sh\necho '{gdbus_answer}'\n")
            os.chmod(os.path.join(shim_dir, "gdbus"), 0o755)
        env = {"HOME": self.home, "PATH": os.pathsep.join([shim_dir, self.shims, clean_path()]), "REAL_PYTHON": sys.executable,
               **(extra_env or {})}  # no PIXELSWITCH_INSTALL_SKIP_CHECKS: the real checks run
        return subprocess.run(["bash", INSTALL, "--from", self.tar, "--no-systemd", "--no-autostart"], capture_output=True, text=True,
                              env=env, cwd=self.scratch)

    def assert_refused(self, done, *needles, sudo_note=True):
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        for needle in needles:
            self.assertIn(needle, done.stderr)
        if sudo_note:
            self.assertIn("never uses sudo", done.stderr)
        self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")))

    def test_python_older_than_3_12_is_refused_in_one_sentence(self):
        self.assert_refused(self.dependency_run({"FAKE_PY_OLD": "1"}), "Python 3.12 or newer", "3.11", sudo_note=False)

    def test_each_missing_typelib_is_named_with_its_package(self):
        for typelib, package in (('Gtk", "4.0', "gir1.2-gtk-4.0"), ('Adw", "1', "gir1.2-adw-1"), ('Gtk", "3.0', "gir1.2-gtk-3.0"),
                                 ("Dbusmenu", "gir1.2-dbusmenu-glib-0.4")):
            with self.subTest(package=package):
                done = self.dependency_run({"FAKE_MISSING_TYPELIB": typelib})
                self.assert_refused(done, f"sudo apt install {package}")

    def test_a_missing_tray_watcher_is_only_a_warning(self):
        done = self.dependency_run({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}, gdbus_answer="(false,)")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("shows no tray icons", done.stderr)
        self.assertTrue(os.path.isfile(self.path(".local", "bin", "pixelswitch")))

    def test_a_present_tray_watcher_gives_no_warning(self):
        done = self.dependency_run({"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}, gdbus_answer="(true,)")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn("shows no tray icons", done.stderr)

    def test_the_dependency_check_names_what_is_missing_on_this_machine(self):
        # No stand-ins: whatever this host has. A missing piece is refused in one plain sentence; a complete host installs.
        env = {"HOME": self.home, "PATH": os.pathsep.join([self.shims, clean_path()])}
        done = subprocess.run(["bash", INSTALL, "--from", self.tar, "--no-systemd", "--no-autostart"], capture_output=True, text=True, env=env, cwd=self.scratch)
        gi_ok = subprocess.run([sys.executable, "-c", "import gi"], capture_output=True).returncode == 0
        if done.returncode != 0:
            self.assertIn("sudo apt install", done.stderr)
            self.assertIn("never uses sudo", done.stderr)
            self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")))
        else:
            self.assertTrue(gi_ok)


# --- install.sh's download path, against a local release server --------------------------------------


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(openssl3(), "OpenSSL 3 is needed to verify an Ed25519 signature")
class DownloadPathTests(unittest.TestCase):
    """The path every real first install takes: fetch latest-linux.json, check it, download, compare size and SHA-256,
    verify the Ed25519 signature, compare VERSION with the feed. A local http server stands in for GitHub through the
    test-mode hooks (PIXELSWITCH_TEST_MODE with HOME under the real ~/.cache/pixelswitch-dev/), with a throwaway key."""

    def setUp(self):
        self.scratch = scratch_dir(self, "dl-")
        self.home = os.path.join(self.scratch, "home")
        os.makedirs(self.home)
        self.www = os.path.join(self.scratch, "www")
        os.makedirs(self.www)
        self.seed = bytes(range(21, 53))
        self.public = base64.b64encode(ed25519.public_key_from_seed(self.seed)).decode()
        self.sudo_marker = os.path.join(self.scratch, "sudo-called")
        self.shims = make_shims(os.path.join(self.scratch, "shims"), self.sudo_marker)
        self.addCleanup(lambda: self.assertFalse(os.path.exists(self.sudo_marker), "install.sh called sudo or pkexec"))
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=self.www))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.tar = os.path.join(self.scratch, "t.tar.gz")
        python_tarball(self.tar, "9.8", "99")
        self.publish(self.tar)

    def publish(self, tar, seed=None, **overrides):
        version = "9.8"  # the folder and file name on the server; a feed may still claim something else (overrides)
        data = slurp(tar, "rb")
        name = f"pixelswitch-linux-{version}.tar.gz"
        folder = os.path.join(self.www, "releases", "download", f"v{version}")
        os.makedirs(folder, exist_ok=True)
        shutil.copy(tar, os.path.join(folder, name))
        feed = {"version": version, "build": 99, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "url": f"{self.base}/releases/download/v{version}/{name}",
                "signature": base64.b64encode(ed25519.sign(seed or self.seed, data)).decode()}
        feed.update(overrides)
        latest = os.path.join(self.www, "releases", "latest", "download")
        os.makedirs(latest, exist_ok=True)
        spit(os.path.join(latest, "latest-linux.json"), json.dumps(feed))

    def install(self, home=None, extra_env=None):
        env = {"HOME": home or self.home, "PATH": os.pathsep.join([self.shims, clean_path()]), "PIXELSWITCH_INSTALL_SKIP_CHECKS": "1",
               "PIXELSWITCH_TEST_MODE": "1", "PIXELSWITCH_TEST_RELEASE_BASE": self.base, "PIXELSWITCH_TEST_PUBLIC_KEY": self.public,
               "PIXELSWITCH_OPENSSL": openssl3(), **(extra_env or {})}
        return subprocess.run(["bash", INSTALL, "--no-systemd", "--no-autostart"], capture_output=True, text=True, env=env, cwd=self.scratch)

    def launcher(self):
        return os.path.join(self.home, ".local", "bin", "pixelswitch")

    def refused(self, text, overrides=None, tar=None, seed=None):
        self.publish(tar or self.tar, seed=seed, **(overrides or {}))
        done = self.install()
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn(text, done.stderr)
        self.assertFalse(os.path.lexists(self.launcher()), "something was installed")
        return done

    def test_a_good_signed_release_installs(self):
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
        self.assertIn("verified: size, SHA-256 and Ed25519 signature", done.stdout)
        self.assertEqual(os.readlink(os.path.join(self.home, ".local", "share", "pixelswitch", "app", "current")), "9.8-99")
        self.assertTrue(os.path.lexists(self.launcher()))

    def test_a_wrong_sha256_is_refused(self):
        self.refused("does not match its SHA-256", {"sha256": "0" * 64})

    def test_a_size_that_is_not_the_downloads_size_is_refused(self):
        size = os.path.getsize(self.tar)
        self.refused("not the size the release promised", {"size": size + 1})
        self.refused("the download failed", {"size": size - 1})

    def test_a_signature_by_another_key_is_refused(self):
        self.refused("does not match PixelSwitch's public key", seed=bytes(range(100, 132)))

    def test_a_feed_that_is_not_what_the_installer_expects_is_refused(self):
        size = os.path.getsize(self.tar)
        for overrides in ({"url": "https://evil.example/releases/download/v9.8/pixelswitch-linux-9.8.tar.gz"},
                          {"url": f"{self.base}/releases/download/v9.7/pixelswitch-linux-9.8.tar.gz"},
                          {"size": 60 * 1024 * 1024}, {"version": "2.0.1"}, {"build": "99"}, {"sha256": "xyz"}, {"signature": "short"}):
            with self.subTest(overrides=overrides):
                self.refused("is not what this installer expects", overrides)
        self.assertGreater(size, 0)

    def test_a_signed_tarball_whose_version_differs_from_the_feed_is_refused(self):
        self.refused("does not match the release feed", {"build": 100})
        other = os.path.join(self.scratch, "other.tar.gz")
        python_tarball(other, "9.8", "98")
        self.refused("does not match the release feed", tar=other)  # the feed still says build 99

    def test_an_older_release_never_replaces_a_newer_install_but_the_same_build_repairs(self):
        self.assertEqual(self.install().returncode, 0)
        older = os.path.join(self.scratch, "older.tar.gz")
        python_tarball(older, "9.8", "98")
        self.publish(older, build=98)
        done = self.install()
        self.assertEqual(done.returncode, 1)
        self.assertIn("older than the installed build 99", done.stderr)
        current = os.path.join(self.home, ".local", "share", "pixelswitch", "app", "current")
        self.assertEqual(os.readlink(current), "9.8-99")
        self.publish(self.tar)  # the same build again
        done = self.install()
        self.assertEqual(done.returncode, 0, done.stderr)
        newer = os.path.join(self.scratch, "newer.tar.gz")
        python_tarball(newer, "9.8", "100")
        self.publish(newer, build=100)
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(os.readlink(current), "9.8-100")

    def test_a_release_server_on_a_claude_host_is_refused(self):
        done = self.install(extra_env={"PIXELSWITCH_TEST_RELEASE_BASE": "https://downloads.claude.com"})
        self.assertEqual(done.returncode, 1)
        self.assertIn("refuses any request to downloads.claude.com", done.stderr)


# --- scripts/verify-release.sh, Linux part, against a stand-in gh and curl ---------------------------------


class VerifyReleaseTests(unittest.TestCase):
    VERIFY = os.path.join(REPO_ROOT, "scripts", "verify-release.sh")

    def setUp(self):
        self.scratch = tempfile.mkdtemp(prefix="psverify", dir="/tmp")
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.seed = bytes(range(90, 122))
        self.public = base64.b64encode(ed25519.public_key_from_seed(self.seed)).decode()
        self.release = os.path.join(self.scratch, "release")
        os.makedirs(self.release)
        self.tar = os.path.join(self.release, "pixelswitch-linux-9.8.tar.gz")
        python_tarball(self.tar)
        installer = slurp(INSTALL, encoding="utf-8")
        installer = re.sub(r'(?m)^SU_PUBLIC_ED_KEY=".*"$', f'SU_PUBLIC_ED_KEY="{self.public}"', installer)
        spit(os.path.join(self.release, "install-linux.sh"), installer)
        self.installer_sha = hashlib.sha256(installer.encode()).hexdigest()
        self.write_body(f"Release notes. Check it: sha256sum install-linux.sh must print {self.installer_sha}\n")
        spit(os.path.join(self.release, "project.yml"),
             f'targets:\n  A:\n    settings:\n      base:\n        MARKETING_VERSION: "9.8"\n        CURRENT_PROJECT_VERSION: "99"\n'
             f'        SUPublicEDKey: {self.public}\n')
        self.write_feed()
        bin_dir = os.path.join(self.scratch, "bin")
        os.makedirs(bin_dir)
        spit(os.path.join(bin_dir, "gh"), f"""#!/bin/sh
case "$*" in
  *"json body"*) cat "{self.release}/body.md" ;;
  *"release view"*) echo "pixelswitch-linux-9.8.tar.gz install-linux.sh latest-linux.json" ;;
  *"release download"*)
    while [ $# -gt 0 ]; do [ "$1" = "-D" ] && dest="$2"; shift; done
    cp "{self.release}/pixelswitch-linux-9.8.tar.gz" "{self.release}/install-linux.sh" "{self.release}/latest-linux.json" "$dest/" ;;
esac
""")
        spit(os.path.join(bin_dir, "curl"), f"""#!/bin/sh
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
cp "{self.release}/project.yml" "$out"
""")
        for name in ("gh", "curl"):
            os.chmod(os.path.join(bin_dir, name), 0o755)
        self.bin = bin_dir

    def write_body(self, text):
        spit(os.path.join(self.release, "body.md"), text)

    def write_feed(self, **overrides):
        data = slurp(self.tar, "rb")
        feed = {"version": "9.8", "build": 99, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                "url": "https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v9.8/pixelswitch-linux-9.8.tar.gz",
                "signature": base64.b64encode(ed25519.sign(self.seed, data)).decode()}
        feed.update(overrides)
        spit(os.path.join(self.release, "latest-linux.json"), json.dumps(feed))

    def verify(self):
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ.get("PATH", ""))
        return subprocess.run(["bash", self.VERIFY, "v9.8", "--linux-only"], capture_output=True, text=True, env=env, cwd=self.scratch)

    def test_a_good_release_passes_every_linux_check(self):
        done = self.verify()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout.count("PASS"), 3 + 9)
        self.assertNotIn("FAIL", done.stdout)
        self.assertIn(f"install-linux.sh sha256 {self.installer_sha}", done.stdout)
        self.assertIn("the release notes carry install-linux.sh's SHA-256", done.stdout)

    def test_release_notes_without_the_installer_hash_fail(self):
        for body, text in (("Nothing here.\n", "the release notes carry install-linux.sh's SHA-256"),
                           (f"old hash {'0' * 64}\n", "the release notes carry install-linux.sh's SHA-256"),
                           ("must print INSTALLER_SHA256_PLACEHOLDER\n", "still hold INSTALLER_SHA256_PLACEHOLDER")):
            with self.subTest(body=body):
                self.write_body(body)
                done = self.verify()
                self.assertEqual(done.returncode, 1, done.stdout)
                line = next(l for l in done.stdout.splitlines() if "FAIL" in l)
                self.assertIn(text, line)

    def test_each_wrong_thing_fails(self):
        data = slurp(self.tar, "rb")
        for overrides, text in (
            ({"build": 100}, "build 100 matches project.yml 99"),
            ({"version": "9.9"}, "version 9.9 matches project.yml 9.8"),
            ({"sha256": "0" * 64}, "SHA-256 matches the tarball"),
            ({"size": len(data) + 1}, "size"),
            ({"signature": base64.b64encode(bytes(64)).decode()}, "Ed25519 signature verifies"),
        ):
            self.write_feed(**overrides)
            done = self.verify()
            self.assertEqual(done.returncode, 1, (overrides, done.stdout))
            line = next(l for l in done.stdout.splitlines() if "FAIL" in l)
            self.assertIn(text, line)

    def test_a_missing_asset_fails(self):
        spit(os.path.join(self.bin, "gh"), "#!/bin/sh\necho 'pixelswitch-linux-9.8.tar.gz latest-linux.json'\n")
        done = self.verify()
        self.assertEqual(done.returncode, 1)
        self.assertIn("FAIL  asset missing: install-linux.sh", done.stdout)

    def test_the_script_still_has_its_mac_checks(self):
        text = slurp(self.VERIFY, encoding="utf-8")
        for needle in ("== EdDSA", "hdiutil attach", "codesign --verify --deep --strict", "xcrun stapler validate", "== feed"):
            self.assertIn(needle, text)
        self.assertEqual(subprocess.run(["bash", "-n", self.VERIFY]).returncode, 0)


# --- the Linux jobs in .github/workflows/build.yml (read as text: PyYAML is not in the standard library) ------


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = slurp(os.path.join(REPO_ROOT, ".github", "workflows", "build.yml"), encoding="utf-8")
        jobs = self.text.split("\njobs:\n", 1)[1]
        self.jobs = {m.group(1): m.group(2) for m in re.finditer(r"(?ms)^  ([a-z][a-z-]*):\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)", jobs)}

    def test_publish_waits_for_the_linux_tests_and_is_the_only_linux_job_that_can_write(self):
        for name in ("linux-test", "linux-package"):
            self.assertRegex(self.jobs[name], r"(?m)^    permissions:\n      contents: read$", name)
        publish = self.jobs["linux-publish"]
        self.assertRegex(publish, r"(?m)^    permissions:\n      contents: write$")
        needs = re.search(r"(?m)^    needs: \[([^\]]*)\]$", publish).group(1)
        self.assertEqual(sorted(n.strip() for n in needs.split(",")), ["build", "linux-package", "linux-test"])
        self.assertNotIn("SPARKLE_EDDSA_KEY", self.jobs["linux-test"] + self.jobs["linux-package"])

    def test_a_skipped_test_fails_the_linux_job_and_a_missing_installer_hash_stops_the_upload(self):
        test = self.jobs["linux-test"]
        self.assertIn("shell: bash", test)
        self.assertIn("tee ", test)
        self.assertIn("tests were skipped in CI", test)
        publish = self.jobs["linux-publish"]
        check, upload = publish.index("INSTALLER_SHA256_PLACEHOLDER"), publish.index("gh release upload")
        self.assertLess(check, upload)
        self.assertIn("exit 1", publish[check:upload])
        self.assertNotIn("::warning::RELEASE_NOTES", publish)


if __name__ == "__main__":
    unittest.main()
