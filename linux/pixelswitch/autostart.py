"""``launchAtLogin`` on Linux: the tray's XDG autostart file ``~/.config/autostart/pixelswitch-tray.desktop``
(spec 5.3). Turning it on writes the file, turning it off removes it, and the setting reads back
from the file, as the Mac reads the login item's real state (``SettingsStore.launchAtLogin``).

The tray itself is the lane LC launcher ``~/.local/bin/pixelswitch-tray``; the installer may have
written the same file already, in which case enabling it again changes nothing.
"""
from __future__ import annotations

import os

from . import fsio
from .paths import Paths


def desktop_entry(home: str) -> str:
    launcher = os.path.join(home, ".local", "bin", "pixelswitch-tray")
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=PixelSwitch\n"
        "Comment=PixelSwitch tray icon\n"
        f"Exec={launcher}\n"
        "Icon=pixelswitch\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _disabled(text: str) -> bool:
    for raw in text.splitlines():
        line = raw.strip().replace(" ", "")
        if line.lower() in ("hidden=true", "x-gnome-autostart-enabled=false"):
            return True
    return False


def is_enabled(paths: Paths) -> bool:
    try:
        text = fsio.read_text(paths.autostart_file, limit=64 * 1024)
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return text is not None and not _disabled(text)


def set_enabled(paths: Paths, enable: bool) -> None:
    """Raises ``OSError`` when the file cannot be written or removed."""
    if enable == is_enabled(paths):
        return
    if enable:
        fsio.atomic_write(paths.autostart_file, desktop_entry(paths.home), default_mode=0o644)
    else:
        try:
            os.unlink(paths.autostart_file)
        except FileNotFoundError:
            pass
