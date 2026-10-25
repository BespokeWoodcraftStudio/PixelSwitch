"""The Linux updater's checks (spec 5.4): what is trusted about a release feed, and how a
downloaded tarball is verified and unpacked. No network and no restart logic lives here:
the updater in the daemon downloads, then calls these in this order.

1. :func:`parse_feed` and :func:`validate_feed` - nothing in the feed is trusted until
   checked (version format, build greater than the running build, url prefix, size cap).
2. The download is cut off at the feed's ``size`` (``feed.size`` is validated to be at
   most :data:`MAX_TARBALL_BYTES`), then :func:`verify_download` checks size, SHA-256 and
   the Ed25519 signature BEFORE anything is extracted.
3. :func:`safe_extract` unpacks into a fresh temp directory (``tarfile`` with
   ``filter='data'`` plus refusals of absolute paths, ``..``, links that leave the tree,
   devices and oversize archives).
4. :func:`check_extracted_version` reads the ``VERSION`` file inside the tarball: it must
   equal the feed's version and build, and the build must be greater than the running one.
   Only then does the caller rename the directory to ``<version>-<build>``.

Every failure is an :class:`UpdateError`. A refused archive is refused by the scan pass before
anything is written; an error while extracting may leave partial files, so the caller removes
the temp directory on any :class:`UpdateError`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import posixpath
import re
import tarfile
from dataclasses import dataclass
from typing import Any, Callable

from . import ed25519
from .version import VersionInfo, parse_version_file, valid_version

REPOSITORY_URL = "https://github.com/BespokeWoodcraftStudio/PixelSwitch"
FEED_URL = REPOSITORY_URL + "/releases/latest/download/latest-linux.json"
#: ``<release base>/download/v<version>/`` is the only place a tarball may come from.
RELEASE_BASE = REPOSITORY_URL + "/releases"
#: ``SUPublicEDKey`` from project.yml: the base64 of the 32 raw bytes of the public key.
SU_PUBLIC_ED_KEY = "rk8t6LzU0KnCEzj6+xCXL5XC0CUSfLwtUneaNWX+5/U="

MAX_TARBALL_BYTES = 50 * 1024 * 1024
MAX_FEED_BYTES = 64 * 1024
#: Limits on what a verified archive may expand to (a signed archive is trusted, but a
#: bad build must not fill the disk).
MAX_ARCHIVE_MEMBERS = 20000
MAX_ARCHIVE_TOTAL_BYTES = 400 * 1024 * 1024
MAX_VERSION_FILE_BYTES = 1024

_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z", re.ASCII)


class UpdateError(Exception):
    """A feed, download or archive failed a check. ``reason`` is a short stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Feed:
    """A validated ``latest-linux.json``."""

    version: str
    build: int
    url: str
    size: int
    sha256: str
    signature: str  # base64 of the 64 raw bytes

    @property
    def signature_bytes(self) -> bytes:
        return base64.b64decode(self.signature, validate=True)


def parse_feed(text: "str | bytes | dict") -> dict:
    """The feed as a dict. A document over :data:`MAX_FEED_BYTES` or not a JSON object is refused."""
    if isinstance(text, dict):
        return text
    raw = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    if len(raw) > MAX_FEED_BYTES:
        raise UpdateError("feed", "The update feed is larger than expected.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise UpdateError("feed", "The update feed is not valid JSON.") from None
    if not isinstance(value, dict):
        raise UpdateError("feed", "The update feed is not a JSON object.")
    return value


def _whole_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_feed(feed: dict, running_build: int, *, release_base: str = RELEASE_BASE) -> Feed:
    """Spec 5.4. Nothing in the feed is trusted until checked.

    * ``version``: two whole numbers joined by a dot.
    * ``build``: a whole number greater than ``running_build`` (an older or equal build is
      "no update", reported as reason ``older``).
    * ``url``: starts with ``<release_base>/download/v<version>/`` followed by one plain file name.
    * ``size``: a whole number, at most 50 MB.
    * ``sha256``: 64 hex digits. ``signature``: base64 of 64 bytes.

    ``release_base`` is the default GitHub releases URL; a test hook may pass another in test
    mode (spec 5.7). Raises :class:`UpdateError`.
    """
    version = feed.get("version")
    if not valid_version(version):
        raise UpdateError("version", "The update feed names a version PixelSwitch cannot read.")
    build = feed.get("build")
    if not _whole_number(build) or build < 0:
        raise UpdateError("build", "The update feed names a build PixelSwitch cannot read.")
    if build <= running_build:
        raise UpdateError("older", "The update feed offers no newer build than the one running.")
    url = feed.get("url")
    prefix = f"{release_base}/download/v{version}/"
    if not isinstance(url, str) or not url.startswith(prefix) or _FILENAME.match(url[len(prefix):]) is None:
        raise UpdateError("url", "The update feed points somewhere PixelSwitch does not download from.")
    size = feed.get("size")
    if not _whole_number(size) or not 0 < size <= MAX_TARBALL_BYTES:
        raise UpdateError("size", "The update feed names a size that is not allowed.")
    digest = feed.get("sha256")
    if not isinstance(digest, str) or _SHA256.match(digest) is None:
        raise UpdateError("sha256", "The update feed has no usable SHA-256.")
    signature = feed.get("signature")
    try:
        raw = base64.b64decode(signature, validate=True) if isinstance(signature, str) else b""
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != 64:
        raise UpdateError("signature", "The update feed has no usable signature.")
    return Feed(version, build, url, size, digest.lower(), signature)


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_download(
    path: str,
    feed: Feed,
    public_key_b64: str = SU_PUBLIC_ED_KEY,
    *,
    verify_file: Callable[..., bool] = ed25519.verify_file,
) -> None:
    """Size, SHA-256 and signature of the downloaded file, in that order, before anything is
    extracted. The signature is checked over the raw bytes of the file. Fails closed: if neither
    openssl nor the pure verifier can say yes, the answer is no."""
    try:
        actual = os.path.getsize(path)
    except OSError:
        raise UpdateError("size", "The downloaded update could not be read.") from None
    if actual != feed.size:
        raise UpdateError("size", "The downloaded update is not the size the feed promised.")
    if _sha256_of(path) != feed.sha256:
        raise UpdateError("sha256", "The downloaded update does not match its SHA-256.")
    if not verify_file(path, feed.signature, public_key_b64):
        raise UpdateError("signature", "The downloaded update is not signed by PixelSwitch.")


def check_version_text(text: str, feed: Feed, running_build: int) -> VersionInfo:
    """The contents of the tarball's ``VERSION`` must equal the feed's version and build, and the
    build must be greater than the running one (a downgrade is refused: the feed is unsigned
    JSON, so its fields alone are not trustworthy)."""
    try:
        info = parse_version_file(text)
    except ValueError:
        raise UpdateError("version-file", "The update's VERSION file is not readable.") from None
    if info.version != feed.version or info.build != feed.build:
        raise UpdateError("version-file", "The update's VERSION file does not match the feed.")
    if info.build <= running_build:
        raise UpdateError("older", "The update's build is not newer than the one running.")
    return info


def check_extracted_version(directory: str, feed: Feed, running_build: int) -> VersionInfo:
    """Reads ``<directory>/VERSION`` (a regular file, not a link, at most 1 KiB) and applies
    :func:`check_version_text`."""
    path = os.path.join(directory, "VERSION")
    try:
        status = os.lstat(path)
        if not os.path.isfile(path) or os.path.islink(path) or status.st_size > MAX_VERSION_FILE_BYTES:
            raise OSError("not a plain small file")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        raise UpdateError("version-file", "The update has no readable VERSION file.") from None
    return check_version_text(text, feed, running_build)


# --- safe extraction -------------------------------------------------------------------------


def _plain_relative(name: str) -> bool:
    """A member name that is relative, has no ``..`` or empty-then-root component, no NUL
    and no backslash."""
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    return all(part != ".." for part in name.split("/"))


def _inside(base_dir_of_link: str, target: str) -> bool:
    """Whether a link target, resolved from the directory holding the link, stays inside
    the extraction root."""
    if target.startswith("/") or "\x00" in target:
        return False
    resolved = posixpath.normpath(posixpath.join(base_dir_of_link, target))
    return resolved != ".." and not resolved.startswith("../") and not resolved.startswith("/")


def _scan(member: tarfile.TarInfo) -> None:
    name = member.name
    if not _plain_relative(name):
        raise UpdateError("archive", "The update contains a path outside its folder.")
    if member.isdev() or member.isfifo():
        raise UpdateError("archive", "The update contains a device or pipe file.")
    if member.issym():
        if not _inside(posixpath.dirname(posixpath.normpath(name)), member.linkname):
            raise UpdateError("archive", "The update contains a link that leaves its folder.")
    elif member.islnk():
        if not _plain_relative(member.linkname):
            raise UpdateError("archive", "The update contains a link that leaves its folder.")
    elif not (member.isreg() or member.isdir()):
        raise UpdateError("archive", "The update contains a file of a kind that is not allowed.")


def safe_extract(
    tarball: str,
    dest: str,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_total_bytes: int = MAX_ARCHIVE_TOTAL_BYTES,
) -> int:
    """Unpacks the (already verified) gzip tarball into ``dest`` and returns the member count.

    ``dest`` is created 0700 if missing and must otherwise be an empty directory. Two passes:
    the first scans every member header and refuses the whole archive on any of: an absolute
    path, a ``..`` component, a symlink or hard link that leaves the tree, a device or pipe, a
    member of an unknown kind, too many members, or too many declared bytes; the second
    extracts with ``tarfile``'s ``filter='data'`` (which independently refuses the same things
    against the real paths, drops setuid bits and group/other write bits, and ignores owners).
    """
    if os.path.lexists(dest):
        if not os.path.isdir(dest) or os.path.islink(dest) or os.listdir(dest):
            raise UpdateError("archive", "The update folder is not empty.")
    else:
        os.makedirs(dest, mode=0o700)
    try:
        count = 0
        total = 0
        directories: list[tuple[str, int]] = []
        with tarfile.open(tarball, "r:gz") as archive:
            for member in archive:
                count += 1
                if count > max_members:
                    raise UpdateError("archive", "The update contains too many files.")
                _scan(member)
                total += max(member.size, 0)
                if total > max_total_bytes:
                    raise UpdateError("archive", "The update unpacks to more than is allowed.")
                if member.isdir():
                    directories.append((member.name, member.mode))
        with tarfile.open(tarball, "r:gz") as archive:
            archive.extractall(dest, filter="data")
        # The data filter ignores a directory's mode, so a directory gets 0777 & ~umask. Give
        # each one the archive's mode without the high bits and the group/other write bits,
        # and never less than owner rwx, whatever the umask of the process.
        root = os.path.realpath(dest)
        for name, mode in directories:
            path = os.path.join(dest, name)
            real = os.path.realpath(path)
            if os.path.islink(path) or not os.path.isdir(real) or os.path.commonpath([real, root]) != root:
                continue
            os.chmod(real, (mode & 0o755) | 0o700)
        return count
    except UpdateError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError) as error:
        raise UpdateError("archive", f"The update could not be unpacked ({type(error).__name__}).") from None
