"""Fixture data and a stand-in daemon link for the faces' tests and the settings-window renders.

``fixture_snapshot`` is three accounts shaped like the daemon's replies: the active one with a label and every
window, one with split limits, a priority and a message that offers Retry, one Manual only with a re-sign
message. ``FixtureLink`` has the link's interface but never opens a socket; it records every call.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from pixelswitch import protocol
from pixelswitch.gui import model


def iso(now: float, seconds_from_now: float) -> str:
    return protocol.iso8601_utc(now + seconds_from_now)


def fixture_accounts(now: float | None = None) -> list[dict]:
    now = time.time() if now is None else now
    return [
        {"id": "11111111-1111-1111-1111-111111111111", "email": "ahamade@gmail.com", "label": "Personal", "subscription": "Max",
         "isActive": True, "isSwitchable": True, "position": 1, "priority": 1, "manualOnly": False,
         # follows the defaults of fixture_snapshot (session 90, weekly 50), as the daemon would report it
         "effectiveThreshold": 50, "effectiveSessionThreshold": 90, "effectiveWeeklyThreshold": 50,
         "usage": {"session": {"utilization": 42, "resetsAt": iso(now, 3 * 3600 + 12 * 60)},
                   "weekly": {"utilization": 61, "resetsAt": iso(now, 2 * 86400 + 5 * 3600)},
                   "fable": {"utilization": 10, "resetsAt": iso(now, 2 * 86400 + 5 * 3600)},
                   "sampledAt": iso(now, -20)}},
        {"id": "22222222-2222-2222-2222-222222222222", "email": "ahmed@pixelventures.ai", "isActive": False, "isSwitchable": True,
         "position": 2, "priority": 3, "manualOnly": False, "sessionThreshold": 90, "weeklyThreshold": 50,
         "effectiveThreshold": 50, "effectiveSessionThreshold": 90, "effectiveWeeklyThreshold": 50,
         "usage": {"session": {"utilization": 15, "resetsAt": iso(now, 4 * 3600)},
                   "weekly": {"utilization": 48, "resetsAt": iso(now, 4 * 86400)},
                   "sampledAt": iso(now, -600),
                   "error": "Anthropic's usage service isn't responding (HTTP 503). Your account is fine; PixelSwitch keeps trying."}},
        {"id": "33333333-3333-3333-3333-333333333333", "email": "ahmed@bespokewoodcraftstudio.com", "label": "Studio",
         "isActive": False, "isSwitchable": False, "position": 3, "manualOnly": True, "threshold": 0,
         "effectiveThreshold": 90, "effectiveSessionThreshold": 90, "effectiveWeeklyThreshold": 90,
         "usage": {"error": "Session expired. Re-authenticate (↻) to fix."}},
    ]


def fixture_settings() -> dict:
    return {"refreshInterval": 300, "autoSwitch.enabled": True, "autoSwitch.threshold": 90, "autoSwitch.sessionThreshold": 90,
            "autoSwitch.weeklyThreshold": 50, "autoSwitch.onFable": True, "autoSwitch.strategy": "mostRoom",
            "autoSwitch.drainEarly": True, "autoSwitch.drainWithinHours": 24, "updates.automatic": True, "launchAtLogin": True}


def fixture_snapshot(now: float | None = None, sign_in: dict | None = None, **overrides: Any) -> model.Snapshot:
    now = time.time() if now is None else now
    accounts = fixture_accounts(now)
    status = {"appVersion": "2.0", "protocolVersion": 2, "protocolRevision": 3, "features": ["priority", "splitThresholds"],
              "platform": "linux", "accountCount": len(accounts), "claudeAvailable": True,
              "activeAccountId": accounts[0]["id"], "activeAccountEmail": accounts[0]["email"],
              "autoSwitch": {"enabled": True, "defaultThreshold": 90, "defaultSessionThreshold": 90, "defaultWeeklyThreshold": 50,
                             "onFable": True, "strategy": "mostRoom", "drainEarly": True, "drainWithinHours": 24}}
    if sign_in is not None:
        status["signIn"] = sign_in
    snapshot = model.Snapshot(connected=True, status=status, accounts=accounts, settings=fixture_settings(), sign_in=sign_in, at=now)
    for key, value in overrides.items():
        setattr(snapshot, key, value)
    return snapshot


def fixture_sign_in(state: str = "waitingForUser", **extra: Any) -> dict:
    base = {"id": "S1", "purpose": "newAccount", "state": state, "codeSubmitted": False, "startedAt": "2026-10-24T17:00:00Z",
            "automaticLink": "https://claude.ai/oauth/authorize?code=true&client_id=fixture&redirect_uri=http%3A%2F%2Flocalhost%3A40123%2Fcallback",
            "manualLink": "https://claude.ai/oauth/authorize?code=true&client_id=fixture&redirect_uri=https%3A%2F%2Fconsole.anthropic.com%2Foauth%2Fcode%2Fcallback"}
    base.update(extra)
    return base


class FixtureLink:
    """The ``DaemonLink`` interface without a socket. ``results`` maps a method to a value, or to a callable
    ``(params) -> value`` that may raise; every call is recorded in ``calls``."""

    def __init__(self, post: Callable[[Callable[[], None]], None], on_snapshot: Callable[[model.Snapshot], None],
                 snapshot: model.Snapshot, results: dict[str, Any] | None = None) -> None:
        self._post = post
        self._on_snapshot = on_snapshot
        self.snapshot = snapshot
        self.results = results or {}
        self.calls: list[tuple[str, Any]] = []
        self.requested = 0

    def start(self) -> None:
        self._post(lambda: self._on_snapshot(self.snapshot))

    def stop(self) -> None:
        pass

    def request_snapshot(self) -> None:
        self.requested += 1

    def call(self, method: str, params: Any = None, done: Callable[[Any, Exception | None], None] | None = None,
             timeout: float = 30.0) -> None:
        self.calls.append((method, params))
        result: Any = None
        error: Exception | None = None
        handler = self.results.get(method)
        try:
            result = handler(params) if callable(handler) else handler
        except Exception as caught:
            error = caught
        if done is not None:
            self._post(lambda: done(result, error))

    def last(self, method: str) -> Any:
        for name, params in reversed(self.calls):
            if name == method:
                return params
        return None
