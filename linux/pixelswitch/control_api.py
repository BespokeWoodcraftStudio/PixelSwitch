"""Turns one request line into one response line: every one of the 21 control methods, with the
validation and texts of ``ControlAPI.swift`` and the 2.0 additions of spec section 4.

Ports ``ControlAPI.swift:61-208`` over the encoding, envelope, error kinds, parameter shapes and
result builders of lane LA's :mod:`pixelswitch.protocol` and the settings of
:mod:`pixelswitch.settings_catalog`. The log line per call is ``[control] <method><detail>
ok|<kind>|failed`` where detail is ``" <accountUUID>"``, ``" reauthenticate <uuid>"``,
``" newAccount"`` or ``" <settingKey>"`` -- never an email, a link or an argument.

Nothing here can hand out a login token: results are built by the protocol module from plain
inputs that never include one.
"""
from __future__ import annotations

from typing import Any, Callable

from . import protocol, settings_catalog as catalog
from .accounts import ABSENT
from .coordinator import Coordinator
from .protocol import ControlError
from .signin import Purpose


class ControlApi:
    def __init__(self, coordinator: Coordinator, log: Callable[[str], None] = lambda message: None) -> None:
        self._app = coordinator
        self._log = log

    # -- the envelope ----------------------------------------------------------------------------------

    def handle(self, line: str, on_subscribe: Callable[[], None] = lambda: None) -> str | None:
        """The response line for ``line``, or None when ``line`` is a notification (no reply, ever,
        even on error)."""
        try:
            request = protocol.decode_request(line)
        except ControlError as error:
            self._log("[control] unreadable request")
            return protocol.failure_line(None, error)
        if request.jsonrpc != "2.0":
            return protocol.failure_line(request.id, protocol.unsupported_jsonrpc())
        detail = [""]
        try:
            result = self._dispatch(request, detail, on_subscribe)
            self._log(protocol.log_line(request.method, detail[0], "ok"))
            return None if request.id is None else protocol.success_line(request.id, result)
        except ControlError as error:
            self._log(protocol.log_line(request.method, detail[0], error.kind))
            return None if request.id is None else protocol.failure_line(request.id, error)
        except Exception as error:  # a bug must never take the connection or the daemon down
            self._log(protocol.log_line(request.method, detail[0], f"failed ({type(error).__name__})"))
            failure = ControlError("failed", "PixelSwitch hit an unexpected problem. The log has details.")
            return None if request.id is None else protocol.failure_line(request.id, failure)

    # -- dispatch ---------------------------------------------------------------------------------------------

    def _resolve(self, reference: str):
        return protocol.resolve(reference, self._app.accounts)

    def _dispatch(self, request: protocol.Request, detail: list[str], on_subscribe: Callable[[], None]) -> Any:
        method = request.method
        if method not in protocol.METHODS:
            raise protocol.method_not_found(method)
        app = self._app
        p = protocol.parse_params(method, request.params)

        if method == "status.get":
            return app.status_info()
        if method == "accounts.list":
            return {"accounts": app.account_infos()}
        if method == "accounts.switch":
            target = self._resolve(p["account"])
            detail[0] = f" {protocol.uuid_string(target.id)}"
            app.switch_account_by_id(target.id)
            return {"account": app.info_for(target.id)}
        if method == "accounts.addCurrent":
            account_id = app.add_current_account()
            detail[0] = f" {protocol.uuid_string(account_id)}"
            return {"account": app.info_for(account_id)}
        if method == "accounts.signIn.start":
            if p["account"] is not None:
                account = self._resolve(p["account"])
                detail[0] = f" reauthenticate {protocol.uuid_string(account.id)}"
                purpose = Purpose.reauthenticate(account.id, account.email)
            else:
                detail[0] = " newAccount"
                purpose = Purpose.new_account()
            open_with = protocol.normalize_open(p["open"])
            if open_with is not None:
                app.check_browser(open_with)
            session = app.start_sign_in(purpose, open_with)
            return {"signIn": app.session_info(session)}
        if method == "accounts.signIn.status":
            return {"signIn": app.sign_in_info()}
        if method == "accounts.signIn.submitCode":
            return {"signIn": app.submit_sign_in_code(p["code"])}
        if method == "accounts.signIn.cancel":
            return {"signIn": app.cancel_sign_in()}
        if method == "accounts.remove":
            target = self._resolve(p["account"])
            detail[0] = f" {protocol.uuid_string(target.id)}"
            before = app.info_for(target.id)
            app.remove_account(target.id)
            return {"removed": before}
        if method == "accounts.setLabel":
            target = self._resolve(p["account"])
            detail[0] = f" {protocol.uuid_string(target.id)}"
            app.set_label(protocol.normalize_label(p["label"]), target.id)
            return {"account": app.info_for(target.id)}
        if method == "accounts.setThreshold":
            target = self._resolve(p["account"])
            detail[0] = f" {protocol.uuid_string(target.id)}"
            app.set_threshold(protocol.normalize_legacy_threshold(p["threshold"]), target.id)
            return {"account": app.info_for(target.id)}
        if method == "accounts.setThresholds":
            target = self._resolve(p["account"])
            detail[0] = f" {protocol.uuid_string(target.id)}"
            values = {key: protocol.normalize_window_threshold(p[key]) if key in p else ABSENT
                      for key in ("session", "weekly")}
            app.set_thresholds(target.id, session=values["session"], weekly=values["weekly"])
            return {"account": app.info_for(target.id)}
        if method == "accounts.setPriority":
            target = self._resolve(p["account"])
            detail[0] = f" {protocol.uuid_string(target.id)}"
            if not app.set_priority(target.id, protocol.normalize_priority(p["priority"])):
                raise ControlError("invalidValue", protocol.PRIORITY_RANGE_TEXT)
            return {"accounts": app.account_infos()}
        if method == "accounts.setOrder":
            accounts = app.accounts
            listed = protocol.resolve_all(p["accounts"], accounts)
            ids = [a.id for a in listed]
            if len(set(ids)) != len(ids) or set(ids) != {a.id for a in accounts}:
                raise ControlError("invalidValue", "List every account exactly once, in the order you want.",
                                   [protocol.describe(a) for a in accounts])
            if not app.set_account_order(ids):
                raise ControlError("invalidValue", "The accounts changed while the order was being set. Try again.")
            return {"accounts": app.account_infos()}
        if method == "usage.get":
            only = self._resolve(p["account"]) if p["account"] is not None else None
            return self._usage_report(only)
        if method == "usage.refresh":
            app.refresh_usage()
            return self._usage_report(None)
        if method == "settings.get":
            keys = [catalog.named(p["key"])] if p["key"] is not None else list(catalog.KEYS)
            detail[0] = f" {p['key']}" if p["key"] is not None else ""
            return {"settings": {key.name: app.setting_value(key) for key in keys}}
        if method == "settings.set":
            key = catalog.named(p["key"])
            detail[0] = f" {key.name}"
            app.set_setting(key, catalog.normalize(key, p["value"]))
            return {"settings": {key.name: app.setting_value(key)}}
        if method == "events.subscribe":
            on_subscribe()
            return {"subscribed": True}
        if method == "app.checkForUpdates":
            app.request_check_for_updates()
            return {"ok": True}
        if method == "app.quit":
            app.request_quit()
            return {"ok": True}
        raise protocol.method_not_found(method)  # unreachable: METHODS is exhaustive above

    def _usage_report(self, only) -> dict:
        infos = self._app.account_infos()
        if only is not None:
            wanted = protocol.uuid_string(only.id)
            infos = [info for info in infos if info["id"] == wanted]
        return protocol.build_usage_report(infos, self._app.last_usage_refresh)
