"""The pure parts of sign-in: reading the two links out of what ``claude auth login`` prints,
the ``code#state`` shape, the gate, the classification of ``claude auth status`` after a sign-in,
and the private folder holding the ``$BROWSER`` capture helper.

Ports ``SignInOutputParser.swift``, ``SignInRules.swift`` and ``SignInLinkCapture.swift``
(recon ``signin.md`` sections 2.8-2.10 and 3.2). Claude Code 2.1.280 prints, in order,
``Opening browser to sign in...``, ``If the browser didn't open, visit: <manual link>`` and the
prompt ``Paste code here if prompted > `` with no newline. The manual link sends the browser
back to ``https://platform.claude.com/oauth/code/callback`` (a page that shows a code to paste
back); the automatic link, handed to ``$BROWSER``, sends it back to ``http://localhost:<port>/callback``
served by the CLI itself.

Never log what these functions return: the query carries ``state`` and ``code_challenge``. Log
:func:`redacted`.
"""
from __future__ import annotations

import os
import shutil
import time
import unicodedata
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import unquote, urlsplit

from . import fsio
from .protocol import trim_whitespace_and_newlines

# --- the parser (SignInOutputParser.swift) -----------------------------------------------------------


def strip_terminal_escapes(text: str) -> str:
    """``text`` without terminal escape sequences: OSC (ended by BEL or ESC \\), CSI (parameters,
    then one final byte) and two-character escapes. A lone carriage return becomes a newline;
    other control characters are dropped."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "\x1b":
            if i + 1 >= n:
                break
            nxt = text[i + 1]
            if nxt == "]":
                i += 2
                while i < n:
                    if text[i] == "\x07":
                        i += 1
                        break
                    if text[i] == "\x1b" and i + 1 < n and text[i + 1] == "\\":
                        i += 2
                        break
                    i += 1
            elif nxt == "[":
                i += 2
                while i < n and not (0x40 <= ord(text[i]) <= 0x7E):
                    i += 1
                i += 1
            else:
                i += 2
            continue
        if c == "\r":
            out.append("\n")
        elif c in "\n\t" or unicodedata.category(c) != "Cc":
            out.append(c)
        i += 1
    return "".join(out)


def hyperlink_targets(text: str) -> list[str]:
    """The targets of OSC-8 hyperlinks (``ESC ] 8 ; params ; URI ST``), in order."""
    targets: list[str] = []
    i, n = 0, len(text)
    while i + 1 < n:
        if not (text[i] == "\x1b" and text[i + 1] == "]"):
            i += 1
            continue
        j = i + 2
        body: list[str] = []
        while j < n:
            if text[j] == "\x07":
                j += 1
                break
            if text[j] == "\x1b" and j + 1 < n and text[j + 1] == "\\":
                j += 2
                break
            body.append(text[j])
            j += 1
        parts = "".join(body).split(";", 2)
        if len(parts) == 3 and parts[0] == "8" and parts[2]:
            targets.append(parts[2])
        i = j
    return targets


def _https_url(token: str) -> str | None:
    try:
        parts = urlsplit(token)
    except ValueError:
        return None
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in token):
        return None
    return token


def https_urls(text: str) -> list[str]:
    """Every https URL in ``text``, in order. A URL ends at whitespace, a control character or a
    quote or angle bracket; trailing punctuation is dropped."""
    urls: list[str] = []
    rest = text
    lowered = rest.lower()
    while True:
        start = lowered.find("https://")
        if start < 0:
            break
        tail = rest[start:]
        end = len(tail)
        for index, ch in enumerate(tail):
            if ch.isspace() or unicodedata.category(ch) == "Cc" or ch in "\"'<>`":
                end = index
                break
        token = tail[:end]
        while token and token[-1] in ".,;:!?)]}":
            token = token[:-1]
        url = _https_url(token)
        if url is not None:
            urls.append(url)
        rest = tail[end:]
        lowered = rest.lower()
    return urls


def redirect_uri(url: str) -> str | None:
    """Where the sign-in page sends the browser back to, percent-decoded (the first
    ``redirect_uri`` query item)."""
    try:
        query = urlsplit(url).query
    except ValueError:
        return None
    for item in query.split("&"):
        name, _, value = item.partition("=")
        if unquote(name) == "redirect_uri":
            decoded = unquote(value)
            try:
                parts = urlsplit(decoded)
            except ValueError:
                return None
            return decoded if parts.scheme and (parts.netloc or parts.path) else None
    return None


def redirects_to_localhost(url: str) -> bool:
    """True when the link finishes through Claude Code's own listener on this machine."""
    redirect = redirect_uri(url)
    if redirect is None:
        return False
    parts = urlsplit(redirect)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return False
    return parts.hostname.lower() in ("localhost", "127.0.0.1", "::1", "[::1]")


def redirects_to_code_callback(url: str) -> bool:
    """True when the link ends on the page that shows a code to paste back."""
    redirect = redirect_uri(url)
    if redirect is None:
        return False
    parts = urlsplit(redirect)
    return parts.scheme.lower() == "https" and parts.path.endswith("/oauth/code/callback")


def manual_link(output: str) -> str | None:
    """The manual (code-callback) link: the sign-in URL on the ``visit:`` line (one with a
    ``redirect_uri`` that is not localhost), else the first https URL anywhere whose
    ``redirect_uri`` is a ``/oauth/code/callback`` URL."""
    visible = strip_terminal_escapes(output)
    for line in visible.splitlines():
        marker = line.lower().find("visit:")
        if marker < 0:
            continue
        for url in https_urls(line[marker + len("visit:"):]):
            if redirect_uri(url) is not None and not redirects_to_localhost(url):
                return url
    candidates = [u for u in (_https_url(t) for t in hyperlink_targets(output)) if u is not None] + https_urls(visible)
    return next((u for u in candidates if redirects_to_code_callback(u)), None)


def automatic_link(capture_file: str) -> str | None:
    """The automatic (localhost) link from the capture file the ``$BROWSER`` helper appends to:
    the first URL, in file order, whose ``redirect_uri`` is a localhost URL."""
    for line in strip_terminal_escapes(capture_file).splitlines():
        for url in https_urls(line):
            if redirects_to_localhost(url):
                return url
    return None


def redacted(url: str) -> str:
    """A form of ``url`` that is safe to log: scheme, host and path, and where it sends the
    browser back to. No query value (``state``, ``code_challenge``, ``code``) ever appears in it."""
    parts = urlsplit(url)
    text = f"{parts.scheme or '?'}://{parts.hostname or '?'}{parts.path}"
    redirect = redirect_uri(url)
    if redirect is not None:
        back = urlsplit(redirect)
        port = f":{back.port}" if back.port else ""
        text += f" (returns to {back.hostname or '?'}{port}{back.path})"
    return text


# --- rules (SignInRules.swift) ----------------------------------------------------------------------------


def normalized_code(pasted: str) -> str | None:
    """The line to hand to Claude Code, or None unless ``pasted`` is one whole ``code#state``:
    exactly one ``#``, text on both sides, and no whitespace or control character inside (a
    newline would send two lines)."""
    trimmed = trim_whitespace_and_newlines(pasted)
    if not trimmed or any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in trimmed):
        return None
    halves = trimmed.split("#")
    if len(halves) != 2 or not halves[0] or not halves[1]:
        return None
    return trimmed


def gate_decide(claude_available: bool, is_switching: bool, is_logging_in: bool, current_state_finished: bool | None
                ) -> str:
    """``allowed``, ``busy`` or ``claudeUnavailable``. ``current_state_finished`` is None when no
    sign-in exists, else whether it has finished."""
    if not claude_available:
        return "claudeUnavailable"
    if is_switching or is_logging_in:
        return "busy"
    if current_state_finished is False:
        return "busy"
    return "allowed"


def classify_new_account(status, accounts: Sequence) -> tuple[str, object | None]:
    """``(notLoggedIn|noIdentity|existing|new, detail)`` -- detail is the account id for
    ``existing`` and the email for ``new``. Emails compare exactly, as they always have."""
    if not status.logged_in:
        return "notLoggedIn", None
    if not status.email:
        return "noIdentity", None
    for account in accounts:
        if account.email == status.email:
            return "existing", account.id
    return "new", status.email


def classify_reauthentication(status, expected_email: str) -> tuple[str, str | None]:
    """``(notLoggedIn|noIdentity|matches|wrongAccount, signedInAs)``."""
    if not status.logged_in:
        return "notLoggedIn", None
    if not status.email:
        return "noIdentity", None
    if status.email == expected_email:
        return "matches", None
    return "wrongAccount", status.email


# --- the capture folder (SignInLinkCapture.swift) --------------------------------------------------------------

FOLDER_PREFIX = "pixelswitch-signin-"
CAPTURE_VARIABLE = "PIXELSWITCH_SIGNIN_CAPTURE_FILE"
STALE_AFTER = 60 * 60

HELPER_SCRIPT = f"""#!/bin/sh
# PixelSwitch sign-in link capture. Claude Code runs this as $BROWSER with
# the sign-in link as its only argument; it records the link instead of
# opening a browser.
file="${{{CAPTURE_VARIABLE}:-$(dirname "$0")/captured-links}}"
printf '%s\\n' "$1" >> "$file"
exit 0
"""


@dataclass(frozen=True)
class LinkCapture:
    """A private per-sign-in folder (0700) holding the ``$BROWSER`` helper (0700) and the file it
    writes the automatic link to (0600). Removed when the sign-in ends."""

    directory: str
    helper: str
    capture_file: str

    @classmethod
    def create(cls, parent: str, sign_in_id: str, now: float | None = None) -> "LinkCapture":
        remove_stale(parent, now)
        fsio.ensure_private_dir(parent)
        directory = os.path.join(parent, FOLDER_PREFIX + sign_in_id)
        os.mkdir(directory, 0o700)
        os.chmod(directory, 0o700)
        helper = os.path.join(directory, "capture-sign-in-link")
        fd = os.open(helper, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
        try:
            os.write(fd, HELPER_SCRIPT.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(helper, 0o700)
        capture_file = os.path.join(directory, "captured-links")
        fd = os.open(capture_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os.chmod(capture_file, 0o600)
        return cls(directory, helper, capture_file)

    @property
    def environment_overrides(self) -> dict[str, str]:
        """What Claude Code must run with so the link lands in the capture file."""
        return {"BROWSER": self.helper, CAPTURE_VARIABLE: self.capture_file}

    def read(self) -> str:
        try:
            with open(self.capture_file, encoding="utf-8", errors="replace") as handle:
                return handle.read(256 * 1024)
        except OSError:
            return ""

    def remove(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def remove_stale(parent: str, now: float | None = None) -> None:
    """Removes sign-in folders under ``parent`` last modified more than an hour ago (a crash
    mid-sign-in). Only names starting ``pixelswitch-signin-`` are touched."""
    now = time.time() if now is None else now
    try:
        names = os.listdir(parent)
    except OSError:
        return
    for name in names:
        if not name.startswith(FOLDER_PREFIX):
            continue
        path = os.path.join(parent, name)
        try:
            if not os.path.isdir(path) or os.path.islink(path):
                continue
            if now - os.lstat(path).st_mtime > STALE_AFTER:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue
