"""Sign-in through the coordinator against the stub claude: a new account, an account that already
exists, re-authentication (right and wrong account), cancel, busy, and what opens only on request."""
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

from pixelswitch import browsers, fsio
from pixelswitch.coordinator import ActionError, BUSY_MESSAGE
from pixelswitch.protocol import ControlError
from pixelswitch.signin import Purpose

from .lb_support import Rig, credential_text, read_file, read_json, wait_for
from .mock_api import R, fabricated


def configure_login(rig, email, uuid="new-uuid", subscription="max", org="Org"):
    fsio.atomic_write(os.path.join(rig.home, "stub-login.json"),
                      json.dumps({"email": email, "uuid": uuid, "subscription": subscription, "org": org}))


def approve(rig):
    fsio.atomic_write(os.path.join(rig.home, "stub-login-approve"), "1")


def setup(case, **kw):
    rig = Rig(case)
    a = rig.seed_account("a@example.invalid", active=True)
    b = rig.seed_account("b@example.invalid")
    app = rig.build(**kw)
    return rig, app, a, b


def run_to_end(rig, app, session, approve_it=True):
    assert wait_for(lambda: session.manual_link and session.automatic_link, 20), session.state
    if approve_it:
        approve(rig)
    assert session.wait_finished(30), session.state


class NewAccountTests(unittest.TestCase):
    def test_a_new_sign_in_saves_the_account_and_makes_it_active(self):
        rig, app, a, b = setup(self)
        configure_login(rig, "c@example.invalid", "uuid-c", "pro", "Cee Org")
        session = app.start_sign_in(Purpose.new_account())
        self.assertIs(app.current_sign_in(), session)
        self.assertEqual(session.state, "starting")
        self.assertTrue(app.gate.busy, "the sign-in holds the one mutex")
        # the profile of whatever login the CLI wrote is C
        rig.api.script("profile", None, R(200, {"account": {"uuid": "uuid-c", "email": "c@example.invalid"}}))
        run_to_end(rig, app, session)
        self.assertEqual(session.state, "succeeded", session.message)
        account = app.account_by_id(session.result_account_id)
        self.assertEqual((account.email, account.is_active, account.subscription_type, account.org_name),
                         ("c@example.invalid", True, "pro", "Cee Org"))
        self.assertEqual([x.id for x in app.accounts if x.is_active], [account.id])
        self.assertEqual(app.accounts[-1].id, account.id, "a new account has no priority number, so it sorts last")
        backup = rig.backup_of(type("S", (), {"id": account.id}))
        self.assertEqual(backup.oauth_account["emailAddress"], "c@example.invalid")
        live = rig.live_credential()["claudeAiOauth"]["accessToken"]
        self.assertEqual(json.loads(backup.token)["claudeAiOauth"]["accessToken"], live)
        self.assertFalse(app.gate.busy)
        self.assertIsNotNone(app.last_auto_switch_at, "a sign-in is a deliberate choice: it gets the cooldown")
        self.assertEqual(rig.events_of("activeAccountChanged")[-1]["accountId"], account.id)

    def test_the_account_that_was_active_is_backed_up_before_the_cli_can_overwrite_it(self):
        rig, app, a, b = setup(self)
        rotated = fabricated("oat01")
        rig.api.add_account(rotated, a.uuid, a.email)
        rig.write_live(a, credential_text(rotated, fabricated("ort01")))  # Claude Code renewed A since it was saved
        configure_login(rig, "c@example.invalid", "uuid-c")
        cli_started_when_saved = []
        real_save = app.backups.save

        def watching(token, oauth, account_id):
            cli_started_when_saved.append(os.path.exists(os.path.join(rig.home, "stub-calls.log")))
            return real_save(token, oauth, account_id)

        app.backups.save = watching
        session = app.start_sign_in(Purpose.new_account())
        self.assertTrue(wait_for(lambda: json.loads(rig.backup_of(a).token)["claudeAiOauth"]["accessToken"] == rotated, 20))
        self.assertEqual(cli_started_when_saved, [False], "saved before `claude auth login` was started")
        self.assertTrue(wait_for(lambda: session.manual_link, 20))
        self.assertEqual(read_calls(rig)[0]["args"], ["auth", "login"])
        session.cancel()
        session.wait_finished(20)

    def test_progress_is_announced_once_per_change(self):
        rig, app, a, b = setup(self)
        configure_login(rig, "c@example.invalid", "uuid-c")
        rig.api.script("profile", None, R(200, {"account": {"uuid": "uuid-c", "email": "c@example.invalid"}}))
        session = app.start_sign_in(Purpose.new_account())
        run_to_end(rig, app, session)
        infos = rig.events_of("signInChanged")
        states = [i["signIn"]["state"] for i in infos]
        collapsed = [s for n, s in enumerate(states) if n == 0 or s != states[n - 1]]
        self.assertEqual(collapsed[0], "starting")
        self.assertEqual(collapsed[-1], "succeeded")
        self.assertIn("waitingForUser", collapsed)
        self.assertEqual(len({json.dumps(i, sort_keys=True) for i in infos}), len(infos), "no duplicate announcements")
        self.assertTrue(any(i["signIn"].get("automaticLink") for i in infos))
        info = app.sign_in_info()
        self.assertEqual((info["state"], info["purpose"]), ("succeeded", "newAccount"))
        self.assertEqual(info["resultAccountId"], session.result_account_id)

    def test_the_sign_in_info_has_both_links(self):
        rig, app, a, b = setup(self)
        session = app.start_sign_in(Purpose.new_account())
        self.assertTrue(wait_for(lambda: session.manual_link and session.automatic_link, 20))
        info = app.session_info(session)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost", info["automaticLink"])
        self.assertIn("redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback", info["manualLink"])
        self.assertEqual(info["codeSubmitted"], False)
        session.cancel()
        session.wait_finished(20)

    def test_a_pasted_code_finishes_the_sign_in(self):
        rig, app, a, b = setup(self)
        configure_login(rig, "c@example.invalid", "uuid-c")
        rig.api.script("profile", None, R(200, {"account": {"uuid": "uuid-c", "email": "c@example.invalid"}}))
        session = app.start_sign_in(Purpose.new_account())
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 20))
        with self.assertRaises(ControlError) as raised:
            app.submit_sign_in_code("only-half")
        self.assertEqual((raised.exception.kind, raised.exception.message),
                         ("invalidValue", "That isn't the whole code. Copy all of it, including the part after #."))
        info = app.submit_sign_in_code("CODE#TESTSTATE456")
        self.assertTrue(info["codeSubmitted"])
        with self.assertRaises(ControlError) as raised:
            app.submit_sign_in_code("CODE#TESTSTATE456")
        self.assertEqual((raised.exception.kind, raised.exception.message),
                         ("invalidValue", "This sign-in is not waiting for a code."))
        self.assertTrue(session.wait_finished(30))
        self.assertEqual(session.state, "succeeded")

    def test_without_a_sign_in_the_code_and_cancel_calls_say_so(self):
        rig, app, a, b = setup(self)
        for call in (lambda: app.submit_sign_in_code("a#b"), app.cancel_sign_in):
            with self.assertRaises(ControlError) as raised:
                call()
            self.assertEqual((raised.exception.kind, raised.exception.message), ("notFound", "No sign-in is running."))
        self.assertIsNone(app.sign_in_info())


class ExistingAndReauthTests(unittest.TestCase):
    def test_signing_in_as_an_account_we_already_have_refreshes_it(self):
        rig, app, a, b = setup(self)
        configure_login(rig, "b@example.invalid", b.uuid)
        rig.api.script("profile", None, R(200, {"account": {"uuid": b.uuid, "email": b.email}}))
        before = rig.backup_of(b).token
        session = app.start_sign_in(Purpose.new_account())
        run_to_end(rig, app, session)
        self.assertEqual(session.state, "succeeded")
        self.assertEqual(session.result_account_id, b.id)
        self.assertNotEqual(rig.backup_of(b).token, before)
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(len(app.accounts), 2, "no duplicate account")
        self.assertEqual(app.error_message, "Account already exists - credentials refreshed")

    def test_reauthenticating_the_right_account_replaces_its_login_and_clears_the_flag(self):
        rig, app, a, b = setup(self)
        app.flag_needs_reauth(app.account_by_id(b.id))
        configure_login(rig, "b@example.invalid", b.uuid)
        rig.api.script("profile", None, R(200, {"account": {"uuid": b.uuid, "email": b.email}}))
        session = app.start_sign_in(Purpose.reauthenticate(b.id, b.email))
        run_to_end(rig, app, session)
        self.assertEqual((session.state, session.result_account_id), ("succeeded", b.id))
        calls = read_calls(rig)
        self.assertIn(["auth", "login", "--email", "b@example.invalid"], [c["args"] for c in calls])
        self.assertFalse(app.guard.is_needs_reauth(b.id))
        self.assertTrue(app.is_switchable(app.account_by_id(b.id)))
        self.assertEqual(app.active_account().id, b.id)
        self.assertEqual(read_json(rig.paths.needs_reauth), [])

    def test_reauthenticating_as_the_wrong_account_changes_nothing(self):
        rig, app, a, b = setup(self)
        configure_login(rig, "c@example.invalid", "uuid-c")
        before = (rig.backup_of(a).token, rig.backup_of(b).token)
        session = app.start_sign_in(Purpose.reauthenticate(b.id, b.email))
        run_to_end(rig, app, session)
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.message, "Logged in as c@example.invalid, but expected b@example.invalid. "
                         "Credentials not updated.")
        self.assertEqual((rig.backup_of(a).token, rig.backup_of(b).token), before)
        self.assertEqual(len(app.accounts), 2)
        self.assertFalse(app.gate.busy)
        self.assertEqual(app.error_message, session.message)
        self.assertEqual(rig.events_of("error")[-1]["message"], session.message)

    def test_a_reauthentication_for_an_account_removed_meanwhile_saves_nothing(self):
        rig, app, a, b = setup(self)
        configure_login(rig, "b@example.invalid", b.uuid)
        session = app.start_sign_in(Purpose.reauthenticate(b.id, b.email))
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 20))
        app._mark_active(a.id)
        app.accounts_file.save([x for x in app.accounts if x.id != b.id])
        with app._state_lock:
            app._accounts = [x for x in app._accounts if x.id != b.id]
        approve(rig)
        self.assertTrue(session.wait_finished(30))
        self.assertEqual(session.state, "failed")
        self.assertEqual(session.message, "That account was removed during the sign-in, so nothing was saved.")


class CancelBusyAndOpenTests(unittest.TestCase):
    def test_cancel_changes_nothing_and_frees_the_mutex(self):
        rig, app, a, b = setup(self)
        before = (rig.backup_of(a).token, read_file(rig.paths.credentials))
        session = app.start_sign_in(Purpose.new_account())
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 20))
        info = app.cancel_sign_in()
        self.assertIn(info["state"], ("waitingForUser", "cancelled"))
        self.assertTrue(session.wait_finished(20))
        self.assertEqual(session.state, "cancelled")
        self.assertEqual((rig.backup_of(a).token, read_file(rig.paths.credentials)), before)
        self.assertEqual(len(app.accounts), 2)
        self.assertFalse(app.gate.busy)
        app.dismiss_sign_in()
        self.assertIsNone(app.current_sign_in())

    def test_one_sign_in_at_a_time_and_none_during_a_switch(self):
        rig, app, a, b = setup(self)
        session = app.start_sign_in(Purpose.new_account())
        with self.assertRaises(ActionError) as raised:
            app.start_sign_in(Purpose.new_account())
        self.assertEqual((raised.exception.kind, raised.exception.message), ("busy", BUSY_MESSAGE))
        with self.assertRaises(ActionError) as raised:
            app.perform_switch(app.account_by_id(b.id))
        self.assertEqual(raised.exception.kind, "busy")
        self.assertTrue(wait_for(lambda: session.state == "waitingForUser", 20))
        session.cancel()
        session.wait_finished(20)
        again = app.start_sign_in(Purpose.new_account())  # a finished sign-in does not block the next
        again.cancel()
        again.wait_finished(20)

    def test_no_claude_is_reported_first(self):
        rig, app, a, b = setup(self)
        app.settings.replace({})
        app.cli._base_env["PATH"] = "/nonexistent"
        with self.assertRaises(ActionError) as raised:
            app.start_sign_in(Purpose.new_account())
        self.assertEqual((raised.exception.kind, raised.exception.message),
                         ("claudeUnavailable", "The claude command could not be found on this computer."))
        self.assertFalse(app.gate.busy)

    def test_nothing_opens_a_browser_unless_asked(self):
        rig, app, a, b = setup(self)
        with mock.patch.object(browsers, "open_url") as opener:
            session = app.start_sign_in(Purpose.new_account())
            self.assertTrue(wait_for(lambda: session.automatic_link is not None, 20))
            session.cancel()
            session.wait_finished(20)
        opener.assert_not_called()

    def test_open_default_opens_the_automatic_link_once_when_it_arrives(self):
        rig, app, a, b = setup(self)
        with mock.patch.object(browsers, "open_url", return_value=True) as opener:
            session = app.start_sign_in(Purpose.new_account(), "default")
            self.assertTrue(wait_for(lambda: opener.called, 20))
            session.cancel()
            session.wait_finished(20)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(opener.call_args.args[1], session.automatic_link)
        self.assertIsNone(opener.call_args.args[2], "the default browser")

    def test_test_mode_never_opens_a_browser(self):
        rig, app, a, b = setup(self)
        app.test_mode = True
        with mock.patch.object(browsers, "open_url") as opener:
            session = app.start_sign_in(Purpose.new_account(), "default")
            self.assertTrue(wait_for(lambda: session.automatic_link is not None, 20))
            session.cancel()
            session.wait_finished(20)
        opener.assert_not_called()

    def test_an_unknown_browser_name_is_refused_with_the_choices(self):
        rig, app, a, b = setup(self)
        with mock.patch.object(browsers, "installed_browsers", return_value=[browsers.Browser("Firefox", "firefox.desktop", ("firefox", "%u"))]):
            with self.assertRaises(ControlError) as raised:
                app.check_browser("Netscape")
            self.assertEqual(raised.exception.kind, "invalidValue")
            self.assertEqual(raised.exception.message, 'No browser called "Netscape" is installed on this computer.')
            self.assertEqual(raised.exception.candidates, ["default", "Firefox"])
            app.check_browser("firefox")
            app.check_browser("DEFAULT")


class BrowserHelpersTests(unittest.TestCase):
    def test_command_expansion(self):
        firefox = browsers.Browser("Firefox", "firefox.desktop", ("firefox", "--new-window", "%u"))
        self.assertEqual(browsers.command_for(firefox, "https://x.example/a"), ["firefox", "--new-window", "https://x.example/a"])
        no_code = browsers.Browser("B", "b.desktop", ("b", "%F", "--flag"))
        self.assertEqual(browsers.command_for(no_code, "https://x.example/"), ["b", "--flag", "https://x.example/"])

    def test_only_https_links_are_opened(self):
        self.assertFalse(browsers.open_url("/home/x", "file:///etc/passwd"))
        self.assertFalse(browsers.open_url("/home/x", "javascript:alert(1)"))

    def test_desktop_files_are_read_from_the_applications_folders(self):
        from .lb_support import tmpdir

        home = tmpdir(self)
        apps = os.path.join(home, ".local", "share", "applications")
        os.makedirs(apps)
        with open(os.path.join(apps, "myfox.desktop"), "w") as handle:
            handle.write("[Desktop Entry]\nType=Application\nName=My Fox\nExec=/opt/fox/fox %u\nMimeType=text/html;x-scheme-handler/https;\n")
        with open(os.path.join(apps, "editor.desktop"), "w") as handle:
            handle.write("[Desktop Entry]\nType=Application\nName=Editor\nExec=ed %f\nMimeType=text/plain;\n")
        found = browsers.installed_browsers(home)
        self.assertEqual([b.name for b in found][:1], ["My Fox"])
        self.assertNotIn("Editor", [b.name for b in found])
        self.assertEqual(browsers.find_browser(home, "my fox").desktop_id, "myfox.desktop")
        self.assertEqual(browsers.find_browser(home, "myfox").name, "My Fox")


def read_calls(rig):
    with open(os.path.join(rig.home, "stub-calls.log"), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines()]


if __name__ == "__main__":
    unittest.main()
