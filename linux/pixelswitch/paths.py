"""Where every PixelSwitch file lives on Linux (spec 5.2).

Every path derives from ``HOME`` alone, as the spec says of the daemon lock and as the client
(``client.socket_candidates``) reads ``socket-path``: ``XDG_CONFIG_HOME``, ``XDG_DATA_HOME`` and
``XDG_STATE_HOME`` are NOT honoured, so the daemon, the CLI and the faces can never disagree
about where the stores are. ``XDG_RUNTIME_DIR`` is used for the socket only. ``CLAUDE_CONFIG_DIR``
is not honoured either, as on the Mac (``ClaudeCredentialsFile.swift:22-27``): Claude Code's login
is ``~/.claude/.credentials.json`` and its identity ``~/.claude.json``.

Pure: building a :class:`Paths` touches nothing on disk.
"""
from __future__ import annotations

import os
import pwd
from dataclasses import dataclass
from typing import Mapping

SOCKET_NAME = "control.sock"


def real_home() -> str:
    """The user's real home from the password database, never from ``$HOME`` (which a scratch
    run overrides). Test mode compares ``HOME`` against this (spec 5.7 rule 2)."""
    try:
        return pwd.getpwuid(os.getuid()).pw_dir
    except KeyError:
        return os.path.expanduser("~")


def home_from_env(env: Mapping[str, str]) -> str:
    """``$HOME`` when it is an absolute path, else the password database's home."""
    home = env.get("HOME", "")
    if home and os.path.isabs(home):
        return home.rstrip("/") or "/"
    return real_home()


@dataclass(frozen=True)
class Paths:
    home: str
    runtime_dir: str | None

    # --- Claude Code's own files (not ours) -------------------------------------------------

    @property
    def claude_dir(self) -> str:
        return os.path.join(self.home, ".claude")

    @property
    def credentials(self) -> str:
        """``~/.claude/.credentials.json``, Claude Code's login."""
        return os.path.join(self.claude_dir, ".credentials.json")

    @property
    def claude_json(self) -> str:
        """``~/.claude.json``, holding the ``oauthAccount`` identity."""
        return os.path.join(self.home, ".claude.json")

    # --- PixelSwitch's own files --------------------------------------------------------------

    @property
    def config_dir(self) -> str:
        return os.path.join(self.home, ".config", "pixelswitch")

    @property
    def settings(self) -> str:
        return os.path.join(self.config_dir, "settings.json")

    @property
    def accounts(self) -> str:
        return os.path.join(self.config_dir, "accounts.json")

    @property
    def data_dir(self) -> str:
        return os.path.join(self.home, ".local", "share", "pixelswitch")

    @property
    def backups(self) -> str:
        """Every saved login, in the Mac keychain item's shape ``{uuid: {token, oauthAccount}}``."""
        return os.path.join(self.data_dir, "backups.json")

    @property
    def app_dir(self) -> str:
        """``app/<version>-<build>/`` directories and the ``current`` symlink live here."""
        return os.path.join(self.data_dir, "app")

    @property
    def state_dir(self) -> str:
        return os.path.join(self.home, ".local", "state", "pixelswitch")

    @property
    def log(self) -> str:
        return os.path.join(self.state_dir, "pixelswitch.log")

    @property
    def daemon_lock(self) -> str:
        return os.path.join(self.state_dir, "daemon.lock")

    @property
    def socket_path_file(self) -> str:
        return os.path.join(self.state_dir, "socket-path")

    @property
    def needs_reauth(self) -> str:
        """Account ids only, never a token."""
        return os.path.join(self.state_dir, "needs-reauth.json")

    @property
    def fallback_socket(self) -> str:
        return os.path.join(self.state_dir, SOCKET_NAME)

    @property
    def autostart_file(self) -> str:
        return os.path.join(self.home, ".config", "autostart", "pixelswitch-tray.desktop")

    def good_marker(self, build: int) -> str:
        """Written by a daemon after its first complete cycle (spec 5.4)."""
        return os.path.join(self.state_dir, f"good-{int(build)}")

    @property
    def socket(self) -> str:
        """``$XDG_RUNTIME_DIR/pixelswitch/control.sock``, else the fallback under the state folder."""
        if self.runtime_dir and os.path.isabs(self.runtime_dir):
            return os.path.join(self.runtime_dir, "pixelswitch", SOCKET_NAME)
        return self.fallback_socket

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Paths":
        env = os.environ if env is None else env
        runtime = env.get("XDG_RUNTIME_DIR") or None
        return cls(home=home_from_env(env), runtime_dir=runtime)
