"""Opening a sign-in link in a browser, only when a person asked for it (the control API's ``open``
parameter, a face's Open button). Nothing here runs by itself, and nothing runs in test mode.

``default`` is ``xdg-open``; a named browser is found from the ``.desktop`` files that handle
``x-scheme-handler/https`` and started with its own ``Exec`` line (``%u`` / ``%U`` replaced by the
link; the other field codes dropped). The command is split with ``shlex`` and run without a
shell, so a link can never become a command.
"""
from __future__ import annotations

import glob
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass

DESKTOP_DIRS_SYSTEM = ("/usr/share/applications", "/usr/local/share/applications",
                       "/var/lib/snapd/desktop/applications", "/var/lib/flatpak/exports/share/applications")


@dataclass(frozen=True)
class Browser:
    name: str
    desktop_id: str
    command: tuple[str, ...]


def _desktop_dirs(home: str) -> list[str]:
    return [os.path.join(home, ".local", "share", "applications"),
            os.path.join(home, ".local", "share", "flatpak", "exports", "share", "applications"),
            *DESKTOP_DIRS_SYSTEM]


def _parse_desktop(path: str) -> dict[str, str] | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            entry: dict[str, str] = {}
            inside = False
            for raw in handle:
                line = raw.strip()
                if line.startswith("["):
                    inside = line == "[Desktop Entry]"
                    continue
                if inside and "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    entry.setdefault(key.strip(), value.strip())
            return entry
    except OSError:
        return None


def installed_browsers(home: str) -> list[Browser]:
    """Browsers that handle https links, in the order their ``.desktop`` files are found."""
    found: list[Browser] = []
    seen: set[str] = set()
    for directory in _desktop_dirs(home):
        for path in sorted(glob.glob(os.path.join(directory, "*.desktop"))):
            desktop_id = os.path.basename(path)
            if desktop_id in seen:
                continue
            entry = _parse_desktop(path)
            if not entry or entry.get("Type", "Application") != "Application" or entry.get("NoDisplay") == "true":
                continue
            if "x-scheme-handler/https" not in entry.get("MimeType", "").split(";"):
                continue
            exec_line, name = entry.get("Exec"), entry.get("Name")
            if not exec_line or not name:
                continue
            try:
                parts = shlex.split(exec_line)
            except ValueError:
                continue
            seen.add(desktop_id)
            found.append(Browser(name, desktop_id, tuple(parts)))
    return found


def find_browser(home: str, wanted: str) -> Browser | None:
    """A browser by name or desktop id, case-insensitive."""
    key = wanted.strip().casefold()
    for browser in installed_browsers(home):
        if browser.name.casefold() == key or browser.desktop_id.casefold() == key \
                or browser.desktop_id.casefold().removesuffix(".desktop") == key:
            return browser
    return None


def command_for(browser: Browser, url: str) -> list[str]:
    command: list[str] = []
    used = False
    for part in browser.command:
        if part in ("%u", "%U"):
            command.append(url)
            used = True
        elif part in ("%f", "%F", "%i", "%c", "%k"):
            continue
        else:
            command.append(part.replace("%%", "%"))
    if not used:
        command.append(url)
    return command


def open_url(home: str, url: str, browser: Browser | None = None) -> bool:
    """Starts the default browser (``xdg-open``) or ``browser`` on ``url``. True if it started."""
    if not url.lower().startswith("https://"):
        return False
    if browser is None:
        opener = shutil.which("xdg-open")
        if opener is None:
            return False
        command = [opener, url]
    else:
        command = command_for(browser, url)
    try:
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
        return True
    except OSError:
        return False
