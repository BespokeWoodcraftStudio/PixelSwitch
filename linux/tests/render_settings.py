"""Renders the settings window to PNG files, headless:

    xvfb-run -a -s "-screen 0 1920x2800x24" python3 linux/tests/render_settings.py --out DIR

CI uploads the result as the artifact ``linux-settings-renders``. The window is built from fixture data and a
stand-in link (no daemon, no socket, no network), presented on the virtual display the caller provides, and each
page is drawn light and dark through GSK, so nothing here uses a screenshot tool, a portal or the live desktop. It
refuses to run on a Wayland session (spec 5.7: the picture never comes from the live desktop) or without a display.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LINUX = os.path.dirname(HERE)
sys.path.insert(0, LINUX)

if os.environ.get("WAYLAND_DISPLAY"):
    sys.exit("render_settings: WAYLAND_DISPLAY is set; run this under xvfb-run with it unset, never on the live desktop.")
os.environ.setdefault("GDK_BACKEND", "x11")
os.environ.setdefault("GSK_RENDERER", "cairo")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gtk  # noqa: E402

from pixelswitch.gui import model  # noqa: E402
from pixelswitch.gui.settings import SettingsWindow  # noqa: E402
from tests import gui_fixtures as fx  # noqa: E402

WIDTH = 820
#: (page, height, variant, snapshot-builder): the whole page fits the window, so one picture shows all of it.
SHOTS = (
    ("accounts", 2400, "", lambda: fx.fixture_snapshot()),
    ("general", 1500, "", lambda: fx.fixture_snapshot()),
    ("general", 1500, "-resets-soonest", lambda: _soonest()),
    ("sign-in", 760, "", lambda: fx.fixture_snapshot()),
    ("sign-in", 900, "-links", lambda: fx.fixture_snapshot(sign_in=fx.fixture_sign_in())),
    ("about", 820, "", lambda: fx.fixture_snapshot()),
)


def _soonest() -> model.Snapshot:
    snapshot = fx.fixture_snapshot()
    snapshot.settings["autoSwitch.strategy"] = "resetsSoonest"
    snapshot.status["autoSwitch"]["strategy"] = "resetsSoonest"
    return snapshot


def capture(widget: Gtk.Widget, path: str) -> None:
    width, height = widget.get_width(), widget.get_height()
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, width, height)
    node = snapshot.to_node()
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, width, height))
    texture.save_to_png(path)


class Renderer:
    def __init__(self, out: str) -> None:
        self.out = out
        self.written: list[str] = []
        self.failed = False
        self.steps = [(scheme, shot) for scheme in ("light", "dark") for shot in SHOTS]
        self.app = Adw.Application(application_id="ai.pixelventures.pixelswitch.render")
        self.app.connect("activate", self.on_activate)
        self.window: SettingsWindow | None = None

    def on_activate(self, app: Adw.Application) -> None:
        display = Gdk.Display.get_default()
        icons = os.path.join(LINUX, "packaging", "icons")
        Gtk.IconTheme.get_for_display(display).add_search_path(icons)
        app.hold()  # keep the application alive between windows
        GLib.idle_add(self.next_step)

    def next_step(self) -> bool:
        if not self.steps:
            self.app.release()
            return False
        scheme, (page, height, variant, build) = self.steps.pop(0)
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if scheme == "dark" else Adw.ColorScheme.FORCE_LIGHT)
        snapshot = build()
        holder: dict = {}
        link = fx.FixtureLink(lambda fn: GLib.idle_add(lambda: fn() or False),
                              lambda s: holder["window"].apply_snapshot(s), snapshot)
        window = SettingsWindow(self.app, link)
        holder["window"] = window
        window.set_default_size(WIDTH, height)
        window.show_page(page)
        window.present()
        link.start()
        GLib.timeout_add(900, self.shoot, window, f"settings-{page}{variant}-{scheme}.png")
        return False

    def shoot(self, window: SettingsWindow, name: str) -> bool:
        path = os.path.join(self.out, name)
        try:
            capture(window, path)
            size = os.path.getsize(path)
            self.written.append(path)
            print(f"wrote {path} ({size} bytes)", flush=True)
            if size < 5000:
                self.failed = True
                print(f"  too small to be a drawn window: {name}", file=sys.stderr, flush=True)
        except Exception as error:  # report and keep going: one bad page should not hide the others
            self.failed = True
            print(f"  could not render {name}: {type(error).__name__}: {error}", file=sys.stderr, flush=True)
        window.destroy()
        GLib.idle_add(self.next_step)
        return False

    def run(self) -> int:
        self.app.run([])
        expected = len(SHOTS) * 2
        if len(self.written) != expected:
            self.failed = True
        return 1 if self.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, help="folder for the PNG files")
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if Gdk.Display.get_default() is None:
        print("render_settings: no display; run it under xvfb-run", file=sys.stderr)
        return 2
    return Renderer(args.out).run()


if __name__ == "__main__":
    sys.exit(main())
