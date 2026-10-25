"""What gets shipped: the systemd unit's keys in their sections, the desktop files, the icons, the launchers, the
deterministic tarball and its VERSION file, the feed and its signature, and install.sh in a scratch HOME
(``--from`` a local tarball, ``--no-systemd --no-autostart``, then ``--uninstall``)."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import xml.dom.minidom

from pixelswitch import ed25519
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


def python_tarball(path: str, version: str = "9.8", build: str = "99", package_text: str | None = None) -> None:
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

    def test_contents_modes_and_the_version_file(self):
        out = os.path.join(self.scratch, "out")
        self.assertEqual(self.build(out).returncode, 0)
        version, build, _ = project_values()
        top = f"pixelswitch-linux-{version}"
        with tarfile.open(self.tarball(out)) as archive:
            members = {m.name: m for m in archive.getmembers()}
            names = sorted(members)
            self.assertEqual(names, sorted(names))
            self.assertTrue(all(n == top or n.startswith(top + "/") for n in names))
            self.assertEqual(archive.extractfile(f"{top}/VERSION").read().decode(), f"version={version}\nbuild={build}\n")
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc") or "/tests/" in n or ".DS_Store" in n])
            for required in ("pixelswitch/__init__.py", "pixelswitch/cli.py", "pixelswitch/mcp.py", "pixelswitch/gui/tray.py",
                             "pixelswitch/gui/settings.py", "pixelswitch/rollback.py", "packaging/pixelswitch.service",
                             "packaging/pixelswitch-tray.desktop", "packaging/pixelswitch-settings.desktop",
                             "packaging/icons/hicolor/scalable/apps/pixelswitch.svg", "README.md"):
                self.assertIn(f"{top}/{required}", members, required)
            for name in LAUNCHERS:
                self.assertEqual(members[f"{top}/bin/{name}"].mode, 0o755, name)
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

    def run_install(self, *args, extra_env=None, script=INSTALL):
        env = {"HOME": self.home, "PATH": clean_path(), "PIXELSWITCH_INSTALL_SKIP_CHECKS": "1", **(extra_env or {})}
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

    def test_the_script_never_calls_sudo(self):
        code = "\n".join(l for l in slurp(INSTALL, encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
        self.assertNotRegex(code.replace('"sudo apt install', ""), r"\bsudo\s+(?!apt install)")
        self.assertNotIn("pkexec", code)

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

    def test_the_dependency_check_names_what_is_missing(self):
        # Without the test bypass, a Python without PyGObject (or a missing typelib) refuses with one plain sentence.
        env = {"HOME": self.home, "PATH": clean_path()}
        done = subprocess.run(["bash", INSTALL, "--from", self.tar, "--no-systemd", "--no-autostart"], capture_output=True, text=True, env=env, cwd=self.scratch)
        gi_ok = subprocess.run([sys.executable, "-c", "import gi"], capture_output=True).returncode == 0
        if done.returncode != 0:
            self.assertIn("sudo apt install", done.stderr)
            self.assertIn("never uses sudo", done.stderr)
            self.assertFalse(os.path.exists(self.path(".local", "bin", "pixelswitch")))
        else:
            self.assertTrue(gi_ok)


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
        spit(os.path.join(self.release, "project.yml"),
             f'targets:\n  A:\n    settings:\n      base:\n        MARKETING_VERSION: "9.8"\n        CURRENT_PROJECT_VERSION: "99"\n'
             f'        SUPublicEDKey: {self.public}\n')
        self.write_feed()
        bin_dir = os.path.join(self.scratch, "bin")
        os.makedirs(bin_dir)
        spit(os.path.join(bin_dir, "gh"), f"""#!/bin/sh
case "$*" in
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
        self.assertEqual(done.stdout.count("PASS"), 3 + 8)
        self.assertNotIn("FAIL", done.stdout)
        self.assertIn("install-linux.sh sha256", done.stdout)

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


if __name__ == "__main__":
    unittest.main()
