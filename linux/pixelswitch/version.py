"""The running version and build, from one source.

Installed layout: a ``VERSION`` file beside the ``pixelswitch`` package
(``<install dir>/VERSION``, next to ``<install dir>/pixelswitch/``). Source
checkout: ``MARKETING_VERSION`` and ``CURRENT_PROJECT_VERSION`` parsed from the
repository's ``project.yml`` (``<repo>/project.yml``, two levels above this
package). The VERSION file wins when both exist.

VERSION file format (written by ``linux/build-release.sh``, read here and by
``update_check`` when it compares the file inside a downloaded tarball with the
feed): two ``key=value`` lines, ``version=2.0`` and ``build=31``. A single line
``2.0 31`` is read too.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

_VERSION_RE = re.compile(r"[0-9]{1,6}\.[0-9]{1,6}\Z")
_BUILD_RE = re.compile(r"[0-9]{1,9}\Z")


class VersionUnavailable(Exception):
    """No VERSION file and no project.yml could be found or read."""


@dataclass(frozen=True)
class VersionInfo:
    version: str
    build: int

    def __str__(self) -> str:
        return f"{self.version} ({self.build})"


def valid_version(text: object) -> bool:
    """A version is two whole numbers joined by a dot (``2.0``)."""
    return isinstance(text, str) and _VERSION_RE.match(text) is not None


def parse_version_file(text: str) -> VersionInfo:
    """Parse the text of a VERSION file. Raises ValueError when it is not one."""
    version: str | None = None
    build: str | None = None
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 1 and "=" not in lines[0]:
        parts = lines[0].split()
        if len(parts) == 2:
            version, build = parts
    else:
        for line in lines:
            key, sep, value = line.partition("=")
            if not sep:
                raise ValueError("VERSION lines must be key=value")
            key = key.strip().lower()
            value = value.strip()
            if key == "version":
                version = value
            elif key == "build":
                build = value
            else:
                raise ValueError(f"unknown VERSION key {key!r}")
    if version is None or build is None:
        raise ValueError("VERSION must name a version and a build")
    if not valid_version(version) or _BUILD_RE.match(build) is None:
        raise ValueError("VERSION has a malformed version or build")
    return VersionInfo(version, int(build))


def parse_project_yml(text: str) -> VersionInfo:
    """MARKETING_VERSION and CURRENT_PROJECT_VERSION from project.yml text.

    The first occurrence of each is used (the three targets carry the same
    values; the release script bumps them together).
    """
    marketing = re.search(r'^\s*MARKETING_VERSION:\s*"?([^"\s#]+)"?', text, re.MULTILINE)
    current = re.search(r'^\s*CURRENT_PROJECT_VERSION:\s*"?([^"\s#]+)"?', text, re.MULTILINE)
    if marketing is None or current is None:
        raise ValueError("project.yml names no MARKETING_VERSION or CURRENT_PROJECT_VERSION")
    version, build = marketing.group(1), current.group(1)
    if not valid_version(version) or _BUILD_RE.match(build) is None:
        raise ValueError("project.yml has a malformed version or build")
    return VersionInfo(version, int(build))


def _package_dir() -> str:
    return os.path.dirname(os.path.realpath(__file__))


def read_version(package_dir: str | None = None) -> VersionInfo:
    """The running version: VERSION file beside the package, else project.yml."""
    package = package_dir or _package_dir()
    install_dir = os.path.dirname(package)
    version_file = os.path.join(install_dir, "VERSION")
    if os.path.isfile(version_file):
        try:
            with open(version_file, encoding="utf-8") as handle:
                return parse_version_file(handle.read())
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise VersionUnavailable(f"VERSION is unreadable: {error}") from error
    repo_root = os.path.dirname(install_dir)
    project = os.path.join(repo_root, "project.yml")
    if os.path.isfile(project):
        try:
            with open(project, encoding="utf-8") as handle:
                return parse_project_yml(handle.read())
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise VersionUnavailable(f"project.yml is unreadable: {error}") from error
    raise VersionUnavailable("no VERSION file beside the package and no project.yml in a source checkout")


def version() -> str:
    return read_version().version


def build() -> int:
    return read_version().build
