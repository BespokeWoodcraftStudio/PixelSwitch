"""Tray icons without sudo or an AppIndicator package (spec D14).

Two backends with one small interface (``set_label``, ``set_tooltip``, ``set_menu``, ``start``, ``stop``):

* :class:`SniBackend` registers a **StatusNotifierItem** itself over Gio D-Bus on
  ``org.kde.StatusNotifierWatcher`` and exports its menu with **Dbusmenu 0.4** (``gir1.2-dbusmenu-glib-0.4``,
  installed on the founder's laptop). It watches the watcher's name, so it registers again if the shell's
  AppIndicator extension is switched off and on. The label beside the icon is the Ayatana ``XAyatanaLabel``
  property, which the Ubuntu AppIndicator extension shows.
* :class:`AppIndicatorBackend` uses AyatanaAppIndicator3 and a GTK 3 menu, and is chosen only when that
  typelib can be imported (:func:`pick_backend`).

The menu is plain data (``model.MenuEntry``); a click calls ``on_action(action)``. Needs PyGObject; the pure
parts of the tray live in ``model.py`` and ``link.py``.
"""
from __future__ import annotations

import os
from typing import Callable, Sequence

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import model  # noqa: E402

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
WATCHER_INTERFACE = "org.kde.StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
ITEM_INTERFACE = "org.kde.StatusNotifierItem"
MENU_PATH = "/MenuBar"
ICON_NAME = "pixelswitch-symbolic"

ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="u" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <property name="XAyatanaOrderingIndex" type="u" access="read"/>
    <method name="ContextMenu"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="Activate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="SecondaryActivate"><arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/></method>
    <method name="Scroll"><arg type="i" name="delta" direction="in"/><arg type="s" name="orientation" direction="in"/></method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg type="s" name="status"/></signal>
    <signal name="XAyatanaNewLabel"><arg type="s" name="label"/><arg type="s" name="guide"/></signal>
  </interface>
</node>
"""


def register_object(connection, path: str, info, method_call, get_property):
    """``register_object`` is deprecated in newer GLib in favour of ``register_object_with_closures2``;
    use the new one when there is one."""
    newer = getattr(connection, "register_object_with_closures2", None)
    if newer is not None:
        return newer(path, info, method_call, get_property, None)
    return connection.register_object(path, info, method_call, get_property, None)


def unix_signal_add(signum: int, callback) -> int:
    """``GLibUnix.signal_add`` where it exists (``GLib.unix_signal_add`` is deprecated)."""
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix

        return GLibUnix.signal_add(GLib.PRIORITY_HIGH, signum, callback)
    except (ValueError, ImportError, AttributeError):
        return GLib.unix_signal_add(GLib.PRIORITY_HIGH, signum, callback)


def item_bus_name() -> str:
    """``org.kde.StatusNotifierItem-<pid>-1``: the name this process's item is registered under."""
    return f"org.kde.StatusNotifierItem-{os.getpid()}-1"


class SniBackend:
    """The StatusNotifierItem and its Dbusmenu."""

    def __init__(self, on_action: Callable[[str], None], *, title: str = "PixelSwitch", item_id: str = "pixelswitch",
                 icon_theme_path: str = "") -> None:
        gi.require_version("Dbusmenu", "0.4")
        from gi.repository import Dbusmenu

        self._dbusmenu = Dbusmenu
        self._on_action = on_action
        self._title = title
        self._id = item_id
        self._icon_theme_path = icon_theme_path
        self._label = ""
        self._tooltip = title
        self._status = "Active"
        self._connection: Gio.DBusConnection | None = None
        self._registration = 0
        self._owner_id = 0
        self._watch_id = 0
        self._registered = False
        self._server = None
        self._root = None
        self._info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        self.registrations = 0  # how many times the watcher accepted this item (tests read it)

    # --- the interface the tray uses ------------------------------------------------------------------

    def start(self) -> None:
        self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._registration = register_object(
            self._connection, ITEM_PATH, self._info, self._on_method_call, self._on_get_property)
        self._server = self._dbusmenu.Server.new(MENU_PATH)
        if self._root is None:
            self._root = self._dbusmenu.Menuitem.new()
        self._server.set_root(self._root)
        self._owner_id = Gio.bus_own_name_on_connection(
            self._connection, item_bus_name(), Gio.BusNameOwnerFlags.NONE, None, None)
        self._watch_id = Gio.bus_watch_name_on_connection(
            self._connection, WATCHER_NAME, Gio.BusNameWatcherFlags.NONE, self._watcher_appeared, self._watcher_vanished)

    def stop(self) -> None:
        if self._connection is None:
            return
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._registration:
            self._connection.unregister_object(self._registration)
            self._registration = 0
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        self._connection.flush_sync(None)
        self._connection = None

    def set_label(self, text: str) -> None:
        if text == self._label:
            return
        self._label = text
        self._emit("XAyatanaNewLabel", GLib.Variant("(ss)", (text, "100%")))

    def set_tooltip(self, text: str) -> None:
        if text == self._tooltip:
            return
        self._tooltip = text
        self._emit("NewToolTip", None)

    def set_menu(self, entries: Sequence[model.MenuEntry]) -> None:
        """Replaces the menu. A new root every time: the shell redraws from LayoutUpdated."""
        dbusmenu = self._dbusmenu
        root = dbusmenu.Menuitem.new()
        for entry in entries:
            item = dbusmenu.Menuitem.new()
            if entry.kind == "separator":
                item.property_set("type", "separator")
            else:
                # Dbusmenu reads a single underscore as an access-key mark and hides it; a real one is doubled.
                item.property_set("label", model.dbusmenu_label(entry.label))
                item.property_set_bool("enabled", entry.enabled)
                if entry.kind == "check":
                    item.property_set("toggle-type", "checkmark")
                    item.property_set_int("toggle-state", 1 if entry.checked else 0)
                if entry.action is not None:
                    item.connect("item-activated", self._activated, entry.action)
            root.child_append(item)
        self._root = root
        if self._server is not None:
            self._server.set_root(root)

    # --- D-Bus ----------------------------------------------------------------------------------------------

    def _activated(self, _item, _timestamp, action: str) -> None:
        self._on_action(action)

    def _emit(self, signal: str, parameters: GLib.Variant | None) -> None:
        if self._connection is None:
            return
        try:
            self._connection.emit_signal(None, ITEM_PATH, ITEM_INTERFACE, signal, parameters)
        except GLib.Error:
            pass

    def _on_get_property(self, _connection, _sender, _path, _interface, name: str):
        variant = GLib.Variant
        if name == "Category":
            return variant("s", "ApplicationStatus")
        if name == "Id":
            return variant("s", self._id)
        if name == "Title":
            return variant("s", self._title)
        if name == "Status":
            return variant("s", self._status)
        if name == "WindowId":
            return variant("u", 0)
        if name == "IconName":
            return variant("s", ICON_NAME)
        if name in ("IconPixmap", "OverlayIconPixmap", "AttentionIconPixmap"):
            return variant("a(iiay)", [])
        if name in ("OverlayIconName", "AttentionIconName", "AttentionMovieName"):
            return variant("s", "")
        if name == "ToolTip":
            return variant("(sa(iiay)ss)", (ICON_NAME, [], self._title, self._tooltip))
        if name == "ItemIsMenu":
            return variant("b", True)
        if name == "Menu":
            return variant("o", MENU_PATH)
        if name == "IconThemePath":
            return variant("s", self._icon_theme_path)
        if name == "XAyatanaLabel":
            return variant("s", self._label)
        if name == "XAyatanaLabelGuide":
            return variant("s", "100%")
        if name == "XAyatanaOrderingIndex":
            return variant("u", 0)
        return None

    def _on_method_call(self, _connection, _sender, _path, _interface, method: str, _parameters, invocation) -> None:
        # The menu is the interface (ItemIsMenu is true); a plain click opens it in the shell, so
        # Activate and the others have nothing to do.
        invocation.return_value(None)

    def _watcher_appeared(self, connection, _name, _owner) -> None:
        self._register(connection)

    def _watcher_vanished(self, _connection, _name) -> None:
        self._registered = False

    def _register(self, connection) -> None:
        def done(source, result) -> None:
            try:
                source.call_finish(result)
            except GLib.Error as error:
                print(f"pixelswitch-tray: the tray host refused the item ({error.message})", flush=True)
                return
            self._registered = True
            self.registrations += 1

        connection.call(WATCHER_NAME, WATCHER_PATH, WATCHER_INTERFACE, "RegisterStatusNotifierItem",
                        GLib.Variant("(s)", (item_bus_name(),)), None, Gio.DBusCallFlags.NONE, 5000, None, done)

    @property
    def registered(self) -> bool:
        return self._registered


class AppIndicatorBackend:
    """AyatanaAppIndicator3 with a GTK 3 menu. Only used when the typelib imports."""

    def __init__(self, on_action: Callable[[str], None], *, item_id: str = "pixelswitch", icon_theme_path: str = "") -> None:
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
        from gi.repository import Gtk

        self._gtk = Gtk
        self._lib = AppIndicator
        self._on_action = on_action
        self._indicator = AppIndicator.Indicator.new(item_id, ICON_NAME, AppIndicator.IndicatorCategory.APPLICATION_STATUS)
        if icon_theme_path:
            self._indicator.set_icon_theme_path(icon_theme_path)
        self._menu = Gtk.Menu()
        self._indicator.set_menu(self._menu)

    def start(self) -> None:
        self._indicator.set_status(self._lib.IndicatorStatus.ACTIVE)

    def stop(self) -> None:
        self._indicator.set_status(self._lib.IndicatorStatus.PASSIVE)

    def set_label(self, text: str) -> None:
        self._indicator.set_label(text, "100%")

    def set_tooltip(self, text: str) -> None:
        self._indicator.set_title(text)

    def set_menu(self, entries: Sequence[model.MenuEntry]) -> None:
        gtk = self._gtk
        menu = gtk.Menu()
        for entry in entries:
            if entry.kind == "separator":
                item = gtk.SeparatorMenuItem()
            elif entry.kind == "check":
                item = gtk.CheckMenuItem.new_with_label(entry.label)
                item.set_active(entry.checked)
                item.set_draw_as_radio(False)
            else:
                item = gtk.MenuItem.new_with_label(entry.label)
            item.set_sensitive(entry.enabled)
            if entry.action is not None or entry.kind == "check":
                item.connect("activate", self._activate_handler(entry))
            menu.append(item)
        menu.show_all()
        self._menu = menu
        self._indicator.set_menu(menu)


    def _activate_handler(self, entry: model.MenuEntry) -> Callable:
        def activated(widget) -> None:
            if entry.kind == "check":
                # A click flips a check item's mark before this runs. The daemon decides the real state, and the menu
                # is redrawn only when its entries change, so a click that changes nothing (the active account, a
                # failed switch) would leave a wrong mark. Put the mark back; the next snapshot moves it if it should move.
                widget.handler_block_by_func(activated)
                widget.set_active(entry.checked)
                widget.handler_unblock_by_func(activated)
            if entry.action is not None:
                self._on_action(entry.action)

        return activated


def appindicator_available() -> bool:
    """Whether AyatanaAppIndicator3 (and GTK 3) can be imported here."""
    try:
        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: F401
        return True
    except (ValueError, ImportError):
        return False


def pick_backend(on_action: Callable[[str], None], *, icon_theme_path: str = "", forced: str | None = None):
    """AppIndicator only when importable, else the StatusNotifierItem. ``forced`` (``sni`` or
    ``appindicator``, from ``PIXELSWITCH_TRAY_BACKEND``) is for tests."""
    choice = (forced or "").lower()
    if choice == "sni" or (choice != "appindicator" and not appindicator_available()):
        return SniBackend(on_action, icon_theme_path=icon_theme_path)
    return AppIndicatorBackend(on_action, icon_theme_path=icon_theme_path)
