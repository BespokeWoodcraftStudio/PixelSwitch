"""Test mode (spec 5.7 rule 2): how a scratch run is told apart from the real thing, and what it
may never do.

``PIXELSWITCH_TEST_MODE=1`` switches it on. In test mode:

* the daemon refuses to start unless ``HOME`` is under ``~/.cache/pixelswitch-dev/`` (the user's
  REAL home from the password database, never ``$HOME``);
* it refuses to start under systemd (``INVOCATION_ID`` set), so test mode can never be switched
  on for the real service by a stray variable in the user manager;
* ``PIXELSWITCH_TEST_API_BASE`` (one base URL for the usage, profile and token endpoints) is
  honoured, and so are ``PIXELSWITCH_TEST_RELEASE_BASE`` and ``PIXELSWITCH_TEST_PUBLIC_KEY`` for
  the updater;
* any request to an ``anthropic.com`` or ``claude.com`` host is refused, whatever sets the URL;
* ``status.get`` reports ``testMode: true``.

Outside test mode every ``PIXELSWITCH_TEST_*`` variable is ignored. Pure apart from the
password-database lookup in :func:`scratch_root`.
"""
from __future__ import annotations

import os
from typing import Mapping
from urllib.parse import urlsplit

from .paths import real_home

ENV_MODE = "PIXELSWITCH_TEST_MODE"
ENV_API_BASE = "PIXELSWITCH_TEST_API_BASE"
ENV_RELEASE_BASE = "PIXELSWITCH_TEST_RELEASE_BASE"
ENV_PUBLIC_KEY = "PIXELSWITCH_TEST_PUBLIC_KEY"
ENV_INVOCATION = "INVOCATION_ID"

BLOCKED_DOMAINS = ("anthropic.com", "claude.com")


class TestModeError(Exception):
    """Test mode is on and something about the run is not allowed. The text is for the person
    who started the run."""

    __test__ = False  # not a unittest case, whatever pytest-style collectors think


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get(ENV_MODE) == "1"


def scratch_root(home: str | None = None) -> str:
    """``~/.cache/pixelswitch-dev`` of the real user."""
    return os.path.join(home if home is not None else real_home(), ".cache", "pixelswitch-dev")


def home_is_scratch(home: str, real: str | None = None) -> bool:
    """Whether ``home`` (symlinks resolved) lies strictly inside the scratch root."""
    root = os.path.realpath(scratch_root(real))
    resolved = os.path.realpath(home)
    return resolved != root and resolved.startswith(root + os.sep)


def validate_start(env: Mapping[str, str], home: str, real: str | None = None) -> None:
    """Raises :class:`TestModeError` when test mode is on and this run breaks a rule."""
    if not enabled(env):
        return
    if env.get(ENV_INVOCATION):
        raise TestModeError("PIXELSWITCH_TEST_MODE is set under systemd (INVOCATION_ID is set); "
                            "test mode is for scratch runs only and the service refuses it.")
    if not home_is_scratch(home, real):
        raise TestModeError(f"PIXELSWITCH_TEST_MODE needs HOME under {scratch_root(real)}/, but HOME is {home}.")


def host_is_blocked(host: str | None) -> bool:
    """Whether ``host`` is ``anthropic.com`` / ``claude.com`` or one of their subdomains."""
    if not host:
        return False
    name = host.strip().rstrip(".").lower()
    return any(name == domain or name.endswith("." + domain) for domain in BLOCKED_DOMAINS)


def check_url(url: str) -> None:
    """Raises :class:`TestModeError` for a URL on a blocked host (used on every test-mode request)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        raise TestModeError("A test-mode request names a URL that cannot be read.") from None
    if host_is_blocked(host):
        raise TestModeError(f"Test mode refuses any request to {host}.")


def api_base(env: Mapping[str, str] | None = None) -> str | None:
    """The test API base, only in test mode, and only if it is not a blocked host."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    base = env.get(ENV_API_BASE, "").strip()
    if not base:
        return None
    check_url(base)
    if urlsplit(base).scheme not in ("http", "https"):
        raise TestModeError("PIXELSWITCH_TEST_API_BASE must be an http or https URL.")
    return base.rstrip("/")


def release_base(env: Mapping[str, str] | None = None) -> str | None:
    """``PIXELSWITCH_TEST_RELEASE_BASE`` (the updater's releases URL), test mode only."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    base = env.get(ENV_RELEASE_BASE, "").strip()
    if not base:
        return None
    check_url(base)
    return base.rstrip("/")


def public_key(env: Mapping[str, str] | None = None) -> str | None:
    """``PIXELSWITCH_TEST_PUBLIC_KEY`` (base64 Ed25519 public key), test mode only."""
    env = os.environ if env is None else env
    if not enabled(env):
        return None
    return env.get(ENV_PUBLIC_KEY, "").strip() or None
