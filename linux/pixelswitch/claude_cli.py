"""The ``claude`` command: where it is, the environment it runs in, and ``claude auth status``.

Ports ``ClaudeService.swift`` (binary lookup 83-200, ``getAuthStatus``/``runClaude`` 204-225 and
823-886) and ``ClaudeProcessEnvironment.swift``, and ``AuthStatus`` (``Models/Account.swift:148``).

Finding ``claude`` (spec 5.3): the ``claude.binaryPath`` setting if it names an executable file,
else ``PATH`` plus ``~/.local/bin``, else ``bash -lc 'command -v claude'``. The result is looked up
again every time (a native auto-update replaces the binary under a running service,
``laptop.md`` 5.1), never cached by inode.

The environment of every ``claude`` the daemon runs has ``CLAUDE_CONFIG_DIR`` and
``CLAUDE_SECURESTORAGE_CONFIG_DIR`` REMOVED (spec 5.1: PixelSwitch does not honour them, so the
CLI must read and write the same ``~/.claude`` PixelSwitch does).

In test mode (spec 5.7 rule 4) the only ``claude`` that may ever run is the one named by
``claude.binaryPath``, and it must lie under ``~/.cache/pixelswitch-dev/``: the real ``claude`` is
never found on ``PATH`` and never run with a scratch HOME.
"""
from __future__ import annotations

import glob
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

from . import fsio, testmode
from .logfile import CategoryLog

STRIPPED_VARIABLES = ("CLAUDE_CONFIG_DIR", "CLAUDE_SECURESTORAGE_CONFIG_DIR")
STATUS_TIMEOUT = 30.0
SHELL_LOOKUP_TIMEOUT = 3.0
SHELL_CACHE_SECONDS = 60.0


class ClaudeError(Exception):
    """A ``claude`` run failed. ``kind``: ``cliError``, ``timedOut``, ``launchFailed``,
    ``invalidOutput`` or ``unavailable``. The text is the Mac's (``ClaudeServiceError``)."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class AuthStatus:
    """What ``claude auth status`` prints (``--json`` is the default)."""

    logged_in: bool
    auth_method: str | None = None
    api_provider: str | None = None
    email: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    subscription_type: str | None = None

    @property
    def reports_claude_ai_identity(self) -> bool:
        return self.auth_method == "claude.ai"

    @property
    def shadowing_auth_method(self) -> str | None:
        """The credential source shadowing the stored claude.ai login, or None when nothing does."""
        return None if self.reports_claude_ai_identity else (self.auth_method or "unknown")


def parse_auth_status(output: str) -> AuthStatus | None:
    """The status in ``output``, or None. Tolerates text around the JSON (stderr is merged into
    the output): the whole text is tried, then the span from the first ``{`` to the last ``}``."""
    candidates = [output]
    start, end = output.find("{"), output.rfind("}")
    if 0 <= start < end:
        candidates.append(output[start:end + 1])
    for text in candidates:
        try:
            value = fsio.loads_strict(text)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict) or not isinstance(value.get("loggedIn"), bool):
            continue

        def opt(key: str) -> str | None:
            item = value.get(key)
            return item if isinstance(item, str) else None

        return AuthStatus(value["loggedIn"], opt("authMethod"), opt("apiProvider"), opt("email"), opt("orgId"),
                          opt("orgName"), opt("subscriptionType"))
    return None


def process_environment(base: Mapping[str, str], claude_path: str | None, home: str) -> dict[str, str]:
    """``ClaudeProcessEnvironment.make`` for Linux: the usual install folders in front of PATH, the
    resolved binary's own folder first (so an nvm-installed ``claude`` finds ``node``), ``HOME``
    set, and the two config-dir variables removed."""
    env = {key: value for key, value in base.items() if key not in STRIPPED_VARIABLES}
    extras = ["/usr/local/bin", os.path.join(home, ".local", "bin"), os.path.join(home, ".npm-global", "bin")]
    if claude_path and "/" in claude_path:
        extras.insert(0, os.path.dirname(os.path.realpath(claude_path)))
    existing = env.get("PATH") or "/usr/bin:/bin"
    env["PATH"] = ":".join(extras + [existing])
    env["HOME"] = home
    return env


def _is_executable_file(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


class ClaudeCli:
    def __init__(
        self,
        *,
        home: str,
        base_env: Mapping[str, str],
        log: CategoryLog,
        binary_path_setting: Callable[[], str],
        test_mode: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._home = home
        self._base_env = dict(base_env)
        self._log = log
        self._setting = binary_path_setting
        self._test_mode = test_mode
        self._clock = clock
        self._shell_cache: tuple[float, str | None] | None = None

    # -- finding it ---------------------------------------------------------------------------

    def candidates(self) -> list[str]:
        """Existing claude binaries from the usual folders and ``PATH``, in lookup order."""
        folders = [os.path.join(self._home, ".local", "bin")]
        folders += [p for p in (self._base_env.get("PATH") or "").split(":") if p]
        folders += ["/usr/local/bin", os.path.join(self._home, ".npm-global", "bin"),
                    os.path.join(self._home, ".claude", "local"), os.path.join(self._home, ".volta", "bin"),
                    os.path.join(self._home, ".bun", "bin"), os.path.join(self._home, ".yarn", "bin")]
        folders += sorted(glob.glob(os.path.join(self._home, ".nvm", "versions", "node", "*", "bin")), reverse=True)
        found: list[str] = []
        for folder in folders:
            candidate = os.path.join(folder, "claude")
            if candidate not in found and _is_executable_file(candidate):
                found.append(candidate)
        return found

    def _shell_lookup(self) -> str | None:
        now = self._clock()
        if self._shell_cache is not None and now - self._shell_cache[0] < SHELL_CACHE_SECONDS:
            return self._shell_cache[1]
        result: str | None = None
        bash = next((p for p in ("/bin/bash", "/usr/bin/bash") if _is_executable_file(p)), None)
        if bash is not None:
            try:
                completed = subprocess.run([bash, "-lc", "command -v claude"], stdin=subprocess.DEVNULL,
                                           capture_output=True, text=True, timeout=SHELL_LOOKUP_TIMEOUT,
                                           env=process_environment(self._base_env, None, self._home))
                line = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else ""
                if completed.returncode == 0 and os.path.isabs(line) and _is_executable_file(line):
                    result = line
            except (OSError, subprocess.SubprocessError):
                result = None
        self._shell_cache = (now, result)
        return result

    def path(self) -> str | None:
        """The ``claude`` to run, or None when there is none."""
        preferred = (self._setting() or "").strip()
        if preferred and os.path.isabs(preferred) and _is_executable_file(preferred):
            if self._test_mode and not testmode.home_is_scratch(os.path.dirname(os.path.realpath(preferred))):
                self._log.error("[claude] Test mode refuses a claude outside ~/.cache/pixelswitch-dev/")
                return None
            return preferred
        if preferred:
            self._log.warning("[claude] The preferred claude path is not an executable file; looking elsewhere")
        if self._test_mode:
            return None  # the real claude is never found on PATH in a scratch run
        found = self.candidates()
        if found:
            return found[0]
        return self._shell_lookup()

    def available(self) -> bool:
        return self.path() is not None

    def environment(self, claude_path: str | None = None) -> dict[str, str]:
        return process_environment(self._base_env, claude_path or self.path(), self._home)

    def env_warning(self) -> str | None:
        """One line for ``status`` when the daemon's own environment sets a variable PixelSwitch
        removes from every ``claude`` it runs."""
        present = [name for name in STRIPPED_VARIABLES if self._base_env.get(name)]
        if not present:
            return None
        return (f"{' and '.join(present)} {'is' if len(present) == 1 else 'are'} set in PixelSwitch's environment; "
                "PixelSwitch ignores it and always uses ~/.claude.")

    # -- running it --------------------------------------------------------------------------------

    def run(self, args: Sequence[str], timeout: float | None = None) -> str:
        """Runs ``claude <args>`` with stderr merged into the output and returns the output.
        Raises :class:`ClaudeError`. A run past ``timeout`` is stopped (SIGTERM to its process
        group, SIGKILL two seconds later) and answers ``timedOut``."""
        path = self.path()
        if path is None:
            raise ClaudeError("unavailable", "The claude command could not be found on this machine.")
        command = "claude " + " ".join(args)
        try:
            process = subprocess.Popen([path, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=self.environment(path), start_new_session=True,
                                       cwd=self._home if os.path.isdir(self._home) else None)
        except OSError as error:
            self._log.error(f"[runClaude] Process launch failed: {type(error).__name__}")
            raise ClaudeError("launchFailed", f"Failed to launch Claude: {error.strerror or type(error).__name__}") from None
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._log.error(f"[runClaude] Timed out after {int(timeout or 0)} s: {command}; stopping it")
            self._stop(process)
            raise ClaudeError("timedOut", f"Claude CLI did not answer in time ({command})") from None
        text = output.decode("utf-8", "replace") if output else ""
        if process.returncode != 0:
            self._log.error(f"[runClaude] Failed (exit {process.returncode})")
            raise ClaudeError("cliError", f"Claude CLI error: exit {process.returncode}") from _Output(text)
        return text

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        for sig, wait in ((signal.SIGTERM, 2.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(process.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                process.communicate(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                return

    def auth_status(self) -> AuthStatus:
        """``claude auth status``. A non-zero exit whose output is still a status (a CLI that exits
        1 when logged out) is returned as that status; anything else is an error as on the Mac."""
        self._log.info("[getAuthStatus] Fetching auth status...")
        try:
            output = self.run(["auth", "status"], STATUS_TIMEOUT)
        except ClaudeError as error:
            cause = error.__cause__
            if error.kind == "cliError" and isinstance(cause, _Output):
                parsed = parse_auth_status(cause.text)
                if parsed is not None:
                    return parsed
            raise
        status = parse_auth_status(output)
        if status is None:
            self._log.error("[getAuthStatus] Invalid output")
            raise ClaudeError("invalidOutput", "Invalid output from Claude CLI")
        self._log.info(f"[getAuthStatus] loggedIn={str(status.logged_in).lower()}, provider={status.api_provider}, "
                       f"sub={status.subscription_type}")
        return status


class _Output(Exception):
    """Carries a failed run's output to :meth:`ClaudeCli.auth_status` (never logged)."""

    def __init__(self, text: str) -> None:
        super().__init__("output")
        self.text = text
