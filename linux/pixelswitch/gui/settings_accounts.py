"""The settings window's Accounts page: one card per account, in priority order.

Each card shows what the Mac's account row and usage card show: the account (label and address), a green
Active badge, both usage windows with their percentage and reset time, Fable, "Updated N ago", the account's
message, Retry and Re-sign where they help, the Priority menu (P1 to P9 and a number), the Session and Weekly
limit menus (Default (N%), Manual only, 10 to 100 and a number), the help sentence for those numbers, the
label, Switch and Remove (with a confirmation). Under the cards: the sentences of spec D3 and D6 with Use
priority, and the buttons that add an account.

The cards are updated in place from each snapshot, and rebuilt only when the accounts or their order change,
so nothing under the pointer is replaced while it is being used.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import model  # noqa: E402
from .widgets import (  # noqa: E402
    LimitControl, PriorityControl, caption, confirm_dialog, plain_row, set_usage, usage_bar,
)

if TYPE_CHECKING:  # pragma: no cover
    from .settings import SettingsWindow


def escape(text: str) -> str:
    """Group titles and descriptions are markup; account text must show as written."""
    return GLib.markup_escape_text(text)


class AccountCard(Adw.PreferencesGroup):
    def __init__(self, window: "SettingsWindow", account_id: str) -> None:
        super().__init__()
        self.window = window
        self.account_id = account_id
        self.info: dict = {}
        self._updating = False
        self._label_dirty = False

        # Header: Active badge, Switch, Remove.
        suffix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, valign=Gtk.Align.CENTER)
        self.badge = Gtk.Label(label="Active")
        self.badge.add_css_class("active-badge")
        self.badge.set_valign(Gtk.Align.CENTER)
        self.switch_button = Gtk.Button(label="Switch to this account")
        self.switch_button.set_valign(Gtk.Align.CENTER)
        self.switch_button.connect("clicked", self._switch_clicked)
        self.remove_button = Gtk.Button(label="Remove…")
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.set_valign(Gtk.Align.CENTER)
        self.remove_button.connect("clicked", self._remove_clicked)
        for widget in (self.badge, self.switch_button, self.remove_button):
            suffix.append(widget)
        self.set_header_suffix(suffix)

        # Usage.
        self.session_row = plain_row("Session (5 hours)")
        self.session_bar = usage_bar()
        self.session_row.add_suffix(self.session_bar)
        self.weekly_row = plain_row("Weekly (7 days)")
        self.weekly_bar = usage_bar()
        self.weekly_row.add_suffix(self.weekly_bar)
        self.fable_row = plain_row("Fable")
        self.fable_bar = usage_bar()
        self.fable_row.add_suffix(self.fable_bar)

        # The account's message, with Retry and Re-sign.
        self.message_row = plain_row("")
        self.message_row.set_title_lines(0)
        self.message_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        self.retry_button = Gtk.Button(label="Retry")
        self.retry_button.set_valign(Gtk.Align.CENTER)
        self.retry_button.connect("clicked", lambda _b: window.run("usage.refresh", None, timeout=120.0))
        self.resign_button = Gtk.Button(label="Re-sign")
        self.resign_button.add_css_class("suggested-action")
        self.resign_button.set_valign(Gtk.Align.CENTER)
        self.resign_button.connect("clicked", lambda _b: window.start_resign(self.info))
        self.message_row.add_suffix(self.retry_button)
        self.message_row.add_suffix(self.resign_button)

        # Priority and the two limits.
        self.priority_row = plain_row("Priority", model.TEXT_PRIORITY_ORDER)
        self.priority_control = PriorityControl(self._priority_changed)
        self.priority_row.add_suffix(self.priority_control)
        self.session_limit_row = plain_row("Session limit")
        self.session_limit = LimitControl("session", lambda kind, value: self._limit_chosen("session", kind, value))
        self.session_limit_row.add_suffix(self.session_limit)
        self.weekly_limit_row = plain_row("Weekly limit")
        self.weekly_limit = LimitControl("weekly", lambda kind, value: self._limit_chosen("weekly", kind, value))
        self.weekly_limit_row.add_suffix(self.weekly_limit)

        # The label.
        self.label_row = Adw.EntryRow()
        self.label_row.set_title("Label")
        self.label_row.set_show_apply_button(True)
        self.label_row.connect("changed", self._label_edited)
        self.label_row.connect("apply", self._label_applied)

        for row in (self.session_row, self.weekly_row, self.fable_row, self.message_row, self.priority_row,
                    self.session_limit_row, self.weekly_limit_row, self.label_row):
            self.add(row)
        self.help_label = caption("")
        self.help_label.add_css_class("account-help")
        self.add(self.help_label)

    # --- from the daemon ------------------------------------------------------------------------------------

    def update(self, info: dict, snapshot: model.Snapshot, now: float | None = None) -> None:
        self.info = info
        self._updating = True
        try:
            self.set_title(escape(model.account_title(info)))
            parts = []
            subtitle = model.account_subtitle(info)
            if subtitle:
                parts.append(subtitle)
            ago = model.updated_ago((info.get("usage") or {}).get("sampledAt"), now)
            if ago:
                parts.append(ago)
            self.set_description(escape("  ·  ".join(parts)) if parts else "")

            active = bool(info.get("isActive"))
            self.badge.set_visible(active)
            self.switch_button.set_visible(not active and bool(info.get("isSwitchable", True)))

            self._usage_row(self.session_row, self.session_bar, "session")
            self._usage_row(self.weekly_row, self.weekly_bar, "weekly")
            fable, resets = model.window_of(info, "fable")
            self.fable_row.set_visible(fable is not None)
            if fable is not None:
                self._usage_row(self.fable_row, self.fable_bar, "fable")

            message = model.message_of(info)
            self.message_row.set_visible(bool(message))
            if message:
                self.message_row.set_title(message)
            self.resign_button.set_visible(bool(message) and model.needs_resign(info))
            self.retry_button.set_visible(bool(message) and model.can_retry(info))

            defaults = snapshot.defaults
            self.priority_control.update(info.get("priority"))
            self.session_limit.update(info, defaults)
            self.weekly_limit.update(info, defaults)
            self.session_limit_row.set_subtitle(model.limit_menu_label(info, "session", defaults))
            self.weekly_limit_row.set_subtitle(model.limit_menu_label(info, "weekly", defaults))

            help_text = model.limit_help(info, defaults)
            if snapshot.auto_switch_on:
                note = model.manual_note(info, defaults)
                if note:
                    help_text += "\n" + note
            self.help_label.set_label(help_text)

            if not self._label_dirty:
                label = info.get("label") or ""
                if self.label_row.get_text() != label:
                    self.label_row.set_text(label)
                self._label_dirty = False
        finally:
            self._updating = False

    def _usage_row(self, row: Adw.ActionRow, bar: Gtk.ProgressBar, name: str) -> None:
        percent, resets = model.window_of(self.info, name)
        if percent is None and not model.reset_words(resets):
            row.set_subtitle("No reading yet.")
        else:
            words = model.reset_words(resets)
            text = f"{model.percent_text(percent)} used" if percent is not None else ""
            row.set_subtitle(" · ".join(p for p in (text, words) if p))
        set_usage(bar, percent)

    # --- from the user ------------------------------------------------------------------------------------------

    @property
    def reference(self) -> str:
        return self.info.get("id", self.account_id)

    def _priority_changed(self, priority: int | None) -> None:
        self.window.run("accounts.setPriority", {"account": self.reference, "priority": priority})

    def _limit_chosen(self, window_name: str, kind: str, value: float | None) -> None:
        reference = self.reference
        if kind == "manual":
            self.window.run("accounts.setThreshold", {"account": reference, "threshold": 0})
        elif kind == "default":
            if self.info.get("manualOnly"):
                # Default on a Manual only account clears Manual only: both windows follow their defaults.
                self.window.run("accounts.setThreshold", {"account": reference, "threshold": None})
            else:
                self.window.run("accounts.setThresholds", {"account": reference, window_name: None})
        else:
            self.window.run("accounts.setThresholds", {"account": reference, window_name: value})

    def _switch_clicked(self, _button) -> None:
        self.window.run("accounts.switch", {"account": self.reference}, timeout=120.0)

    def _remove_clicked(self, _button) -> None:
        name = model.account_title(self.info)
        confirm_dialog(
            self.window, f"Remove {name}?",
            "This deletes the account's saved login from PixelSwitch. Signing it back in needs a person at a browser. "
            "Claude Code's own login is not touched.",
            "Remove", lambda: self.window.run("accounts.remove", {"account": self.reference}),
        )

    def _label_edited(self, _row) -> None:
        if not self._updating:
            self._label_dirty = True

    def _label_applied(self, _row) -> None:
        text = self.label_row.get_text().strip()
        self._label_dirty = False
        self.window.run("accounts.setLabel", {"account": self.reference, "label": text or None})

    def flush(self) -> None:
        self.priority_control.flush()
        self.session_limit.flush()
        self.weekly_limit.flush()


class AccountsPage(Adw.PreferencesPage):
    def __init__(self, window: "SettingsWindow") -> None:
        super().__init__()
        self.window = window
        self.set_title("Accounts")
        self.set_icon_name("system-users-symbolic")
        self._cards: dict[str, AccountCard] = {}
        self._order: list[str] = []
        self._shown: list[Adw.PreferencesGroup] = []

        # The notes group.
        self.notes = Adw.PreferencesGroup()
        self.warning_row = plain_row(model.TEXT_NOWHERE_TO_GO)
        self.warning_row.set_title_lines(0)
        self.warning_row.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        self.notes.add(self.warning_row)
        self.order_row = plain_row(model.TEXT_PRIORITY_ORDER, model.TEXT_MANUAL_ONLY_NOTE)
        self.order_row.set_title_lines(0)
        self.order_row.set_subtitle_lines(0)
        self.notes.add(self.order_row)
        self.use_priority_row = plain_row(model.TEXT_PRIORITY_NOTE)
        self.use_priority_row.set_title_lines(0)
        button = Gtk.Button(label="Use priority")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: window.run("settings.set", {"key": "autoSwitch.strategy", "value": "priority"}))
        self.use_priority_row.add_suffix(button)
        self.notes.add(self.use_priority_row)

        # The empty state and the buttons that add an account.
        self.empty = Adw.PreferencesGroup()
        status = Adw.StatusPage()
        status.set_title("No Accounts")
        status.set_description("Add your current Claude Code account to get started.")
        status.set_icon_name("system-users-symbolic")
        self.empty.add(status)

        self.add_group = Adw.PreferencesGroup()
        add_row = plain_row("Add the account Claude Code is signed in as",
                            "Saves the login Claude Code uses right now, so PixelSwitch can switch back to it.")
        add_row.set_subtitle_lines(0)
        add_button = Gtk.Button(label="Add current account")
        add_button.set_valign(Gtk.Align.CENTER)
        add_button.connect("clicked", lambda _b: window.run("accounts.addCurrent", None, timeout=120.0))
        add_row.add_suffix(add_button)
        self.add_group.add(add_row)
        sign_row = plain_row("Sign in another account", "Opens nothing until you choose a link on the Sign in page.")
        sign_button = Gtk.Button(label="Sign in…")
        sign_button.set_valign(Gtk.Align.CENTER)
        sign_button.connect("clicked", lambda _b: window.show_page("sign-in"))
        sign_row.add_suffix(sign_button)
        self.add_group.add(sign_row)

    def update(self, snapshot: model.Snapshot, now: float | None = None) -> None:
        accounts = snapshot.accounts
        order = [a["id"] for a in accounts]
        if order != self._order or not self._shown:
            self._rebuild(accounts)
            self._order = order
        for info in accounts:
            card = self._cards.get(info["id"])
            if card is not None:
                card.update(info, snapshot, now)
        manual_only = any(a.get("manualOnly") for a in accounts)
        self.warning_row.set_visible(model.nowhere_to_go(accounts, snapshot.auto_switch_on))
        self.order_row.set_subtitle(model.TEXT_MANUAL_ONLY_NOTE if manual_only else "")
        self.order_row.set_subtitle_lines(0)
        self.use_priority_row.set_visible(model.priority_note_visible(snapshot.strategy, accounts))

    def _rebuild(self, accounts: list[dict]) -> None:
        for group in self._shown:
            self.remove(group)
        self._shown = []
        keep = {a["id"] for a in accounts}
        for stale in [i for i in self._cards if i not in keep]:
            del self._cards[stale]
        groups: list[Adw.PreferencesGroup] = []
        if accounts:
            groups.append(self.notes)
            for info in accounts:
                card = self._cards.get(info["id"]) or AccountCard(self.window, info["id"])
                self._cards[info["id"]] = card
                groups.append(card)
        else:
            groups.append(self.empty)
        groups.append(self.add_group)
        for group in groups:
            self.add(group)
        self._shown = groups

    def flush(self) -> None:
        for card in self._cards.values():
            card.flush()
