"""A self-contained check of the tray's StatusNotifierItem and Dbusmenu over a PRIVATE session bus.

Run it as ``dbus-run-session -- python3 linux/tests/gui_sni_probe.py`` (``tests/test_gui_tray.py`` does).
It never touches the desktop's own bus: ``dbus-run-session`` starts a bus of its own. Modes:

* ``main`` (default): starts a fake ``org.kde.StatusNotifierWatcher`` and the tray item as two child
  processes, then checks, as a client of the bus, that the item registered, that its properties and its
  menu layout are what the tray built, that a label change is announced, that a menu activation reaches
  the tray, and that the registration is gone after the item stops. Exit 0 and ``OK`` on success.
* ``watcher`` and ``item``: the two children.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg type="s" name="service" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg type="s" name="service" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s" name="service"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s" name="service"/></signal>
  </interface>
</node>
"""


def run_watcher() -> int:
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    items: list[str] = []
    watches: dict[str, int] = {}
    info = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
    loop = GLib.MainLoop()

    def vanished(_connection, name):
        for entry in [i for i in items if i.startswith(name + "/")]:
            items.remove(entry)
            print(f"unregistered {entry}", flush=True)

    def method_call(_c, sender, _path, _iface, method, parameters, invocation):
        if method == "RegisterStatusNotifierItem":
            (service,) = parameters.unpack()
            entry = f"{service}/StatusNotifierItem"
            items.append(entry)
            watches[service] = Gio.bus_watch_name_on_connection(connection, service, Gio.BusNameWatcherFlags.NONE, None, vanished)
            print(f"registered {entry}", flush=True)
        invocation.return_value(None)

    def get_property(_c, _s, _p, _i, name):
        if name == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(items))
        if name == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        return GLib.Variant("i", 0)

    from pixelswitch.gui import sni

    sni.register_object(connection, "/StatusNotifierWatcher", info, method_call, get_property)
    Gio.bus_own_name_on_connection(connection, "org.kde.StatusNotifierWatcher", Gio.BusNameOwnerFlags.NONE, None, None)
    sni.unix_signal_add(signal.SIGTERM, lambda: loop.quit() or False)
    print("watcher-ready", flush=True)
    loop.run()
    return 0


def run_item() -> int:
    from pixelswitch.gui import model, sni

    actions: list[str] = []
    loop = GLib.MainLoop()

    def on_action(action: str) -> None:
        print(f"action {action}", flush=True)
        actions.append(action)
        if action == "quit":
            loop.quit()

    backend = sni.SniBackend(on_action)
    accounts = [
        {"id": "A", "email": "alice@work.com", "isActive": True, "isSwitchable": True, "position": 1, "priority": 1,
         "usage": {"session": {"utilization": 42}, "weekly": {"utilization": 61}}},
        {"id": "B", "email": "bob@home.com", "isActive": False, "isSwitchable": True, "position": 2,
         "usage": {"error": "Anthropic's usage service isn't responding (HTTP 503). Your account is fine; PixelSwitch keeps trying."}},
    ]
    snapshot = model.Snapshot(connected=True, status={"appVersion": "2.0", "autoSwitch": {"enabled": True}}, accounts=accounts)
    backend.set_menu(model.build_tray_menu(snapshot))
    backend.set_label(model.tray_label(accounts))
    backend.start()
    sni.unix_signal_add(signal.SIGTERM, lambda: loop.quit() or False)
    # A label change a moment after start, to prove the signal and the property follow.
    GLib.timeout_add(1500, lambda: backend.set_label("43%") or False)
    print(f"item-ready {sni.item_bus_name()}", flush=True)
    loop.run()
    backend.stop()
    return 0


def read_line(process: subprocess.Popen, timeout: float = 15.0) -> str:
    deadline = time.time() + timeout
    import select

    while time.time() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.2)
        if ready:
            line = process.stdout.readline()
            if line:
                return line.strip()
        if process.poll() is not None:
            break
    raise AssertionError("no line from the child in time")


def get_property(connection, name: str, path: str, interface: str, prop: str):
    result = connection.call_sync(name, path, "org.freedesktop.DBus.Properties", "Get", GLib.Variant("(ss)", (interface, prop)),
                                  None, Gio.DBusCallFlags.NONE, 5000, None)
    return result.unpack()[0]


def layout_labels(connection, name: str) -> list[tuple[str, dict]]:
    result = connection.call_sync(name, "/MenuBar", "com.canonical.dbusmenu", "GetLayout", GLib.Variant("(iias)", (0, -1, [])),
                                  None, Gio.DBusCallFlags.NONE, 5000, None)
    _revision, (_root_id, _root_props, children) = result.unpack()
    out = []
    for child in children:
        item_id, props, _ = child
        out.append((item_id, props))
    return out


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"ok: {message}", flush=True)


def run_main() -> int:
    me = [sys.executable, os.path.abspath(__file__)]
    env = dict(os.environ)
    watcher = subprocess.Popen(me + ["watcher"], stdout=subprocess.PIPE, text=True, env=env)
    item = None
    try:
        check(read_line(watcher) == "watcher-ready", "the fake StatusNotifierWatcher is up")
        item = subprocess.Popen(me + ["item"], stdout=subprocess.PIPE, text=True, env=env)
        first = read_line(item)
        check(first.startswith("item-ready org.kde.StatusNotifierItem-"), "the tray item is up")
        name = first.split(" ", 1)[1]
        registered = read_line(watcher)
        check(registered == f"registered {name}/StatusNotifierItem", f"the item registered with the watcher ({registered})")

        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        iface = "org.kde.StatusNotifierItem"
        check(get_property(connection, name, "/StatusNotifierItem", iface, "Id") == "pixelswitch", "Id is pixelswitch")
        check(get_property(connection, name, "/StatusNotifierItem", iface, "Category") == "ApplicationStatus", "Category is ApplicationStatus")
        check(get_property(connection, name, "/StatusNotifierItem", iface, "Status") == "Active", "Status is Active")
        check(get_property(connection, name, "/StatusNotifierItem", iface, "IconName") == "pixelswitch-symbolic", "the symbolic icon is named")
        check(get_property(connection, name, "/StatusNotifierItem", iface, "Menu") == "/MenuBar", "Menu is /MenuBar")
        check(get_property(connection, name, "/StatusNotifierItem", iface, "XAyatanaLabel") in ("42%", "43%"), "the label is the active session percentage")

        labels = layout_labels(connection, name)
        texts = [props.get("label", "") for _, props in labels if props.get("type", "standard") != "separator"]
        check(texts[0] == "P1 alice@work.com · S 42% · W 61%", f"the first menu line is the active account ({texts[0]})")
        check(texts[1] == "bob@home.com · S — · W —", f"the second account follows ({texts[1]})")
        check(texts[2].strip().startswith("Anthropic's usage service isn't responding"), "a short message sits under the account")
        check("Auto-switch" in texts and "Refresh" in texts and "Settings…" in texts and "Quit tray" in texts, "the fixed items are there")
        first_props = labels[0][1]
        check(first_props.get("toggle-type") == "checkmark" and first_props.get("toggle-state") == 1, "the active account is checked")

        # A click on Quit tray reaches the tray's on_action.
        quit_id = next(i for i, props in labels if props.get("label") == "Quit tray")
        # Wait for the label change announced by the item (1.5 s after start).
        time.sleep(2.0)
        check(get_property(connection, name, "/StatusNotifierItem", iface, "XAyatanaLabel") == "43%", "a label change is visible in the property")
        connection.call_sync(name, "/MenuBar", "com.canonical.dbusmenu", "Event",
                             GLib.Variant("(isvu)", (quit_id, "clicked", GLib.Variant("s", ""), int(time.time()))),
                             None, Gio.DBusCallFlags.NONE, 5000, None)
        check(read_line(item) == "action quit", "clicking Quit tray reaches the tray")
        item.wait(timeout=10)
        gone = read_line(watcher)
        check(gone == f"unregistered {name}/StatusNotifierItem", f"the registration is gone after the item stops ({gone})")
        check(item.returncode == 0, "the item exited cleanly")
        print("OK", flush=True)
        return 0
    finally:
        for process in (item, watcher):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "main"
    sys.exit({"main": run_main, "watcher": run_watcher, "item": run_item}[mode]())
