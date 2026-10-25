"""The stores: settings.json, accounts.json, needs-reauth ids and backups.json (saved logins).

Spec D12 is enforced inside the ONE save function, so every path inherits it; these tests pin each
unusable shape against the save. The three-way load (loaded / empty / failed) means a save or a
removal refuses when the store could not be read.
"""
from __future__ import annotations

import json
import os
import stat
import unittest

from pixelswitch.accounts import Account
from pixelswitch.logfile import Log
from pixelswitch.stores import AccountsFile, BackupStore, NeedsReauthFile, SettingsFile

from .lb_support import credential_text, read_file, read_json, tmpdir

LOG = Log(None).category("test")
IDENTITY = {"accountUuid": "u-1", "emailAddress": "a@example.invalid"}


def good():
    return credential_text("access-A", "refresh-A")


class BackupStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tmpdir(self)
        self.path = os.path.join(self.dir, "share", "backups.json")
        self.store = BackupStore(self.path, LOG)

    def raw(self):
        with open(self.path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_the_mac_keychain_items_shape(self):
        self.assertTrue(self.store.save(good(), IDENTITY, "ID-1"))
        data = self.raw()
        self.assertEqual(list(data), ["ID-1"])
        self.assertEqual(sorted(data["ID-1"]), ["oauthAccount", "token"])
        self.assertIsInstance(data["ID-1"]["token"], str)  # the WHOLE credential JSON, as a string
        self.assertEqual(json.loads(data["ID-1"]["token"])["claudeAiOauth"]["accessToken"], "access-A")
        self.assertEqual(data["ID-1"]["oauthAccount"], IDENTITY)

    def test_new_file_is_0600_in_a_0700_directory(self):
        self.store.save(good(), IDENTITY, "ID-1")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)

    def test_lookup_get_all_remove(self):
        self.assertEqual(self.store.lookup("ID-1"), ("missing", None))
        self.assertEqual(self.store.all(), {})
        self.store.save(good(), IDENTITY, "ID-1")
        self.store.save(credential_text("access-B", "refresh-B"), {"accountUuid": "u-2"}, "ID-2")
        kind, backup = self.store.lookup("ID-1")
        self.assertEqual(kind, "found")
        self.assertEqual(backup.oauth_account, IDENTITY)
        self.assertEqual(set(self.store.all()), {"ID-1", "ID-2"})
        self.assertTrue(self.store.remove("ID-1"))
        self.assertEqual(self.store.lookup("ID-1"), ("missing", None))
        self.assertIsNotNone(self.store.get("ID-2"))
        self.assertTrue(self.store.remove("never-there"))

    # -- D12: each shape, against save ----------------------------------------------------------------------

    def refused(self, token):
        self.store.save(good(), IDENTITY, "KEEP")
        before = read_file(self.path, "rb")
        self.assertFalse(self.store.save(token, IDENTITY, "KEEP"))
        self.assertEqual(read_file(self.path, "rb"), before, "a refused save leaves the file as it was")

    def test_a_blank_access_token_is_never_saved(self):
        self.refused(credential_text("", "refresh-A"))
        self.refused(credential_text("   ", "refresh-A"))

    def test_a_blank_refresh_token_is_never_saved(self):
        self.refused(credential_text("access-A", ""))
        self.refused(credential_text("access-A", "\n\t"))

    def test_a_missing_claude_ai_oauth_is_never_saved(self):
        self.refused(json.dumps({"mcpOAuth": {}}))
        self.refused(json.dumps({"claudeAiOauth": None}))

    def test_null_fields_are_never_saved(self):
        self.refused(json.dumps({"claudeAiOauth": {"accessToken": None, "refreshToken": "r", "expiresAt": 1}}))
        self.refused(json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": None, "expiresAt": 1}}))
        self.refused(json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "r", "expiresAt": None}}))

    def test_expires_at_zero_is_never_saved(self):
        self.refused(credential_text("access-A", "refresh-A", expires_ms=0))

    def test_not_json_is_never_saved(self):
        self.refused("not json")
        self.refused("[]")

    def test_identity_must_be_an_object(self):
        self.assertFalse(self.store.save(good(), ["not", "an", "object"], "ID-9"))  # type: ignore[arg-type]
        self.assertFalse(os.path.exists(self.path))

    # -- the three-way load ----------------------------------------------------------------------------------------

    def damage(self, content: bytes):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "wb") as handle:
            handle.write(content)

    def test_a_save_refuses_when_the_store_cannot_be_read(self):
        for content in (b"not json", b"[]", b'{"X": 5}', b'{"X": {"token": 1, "oauthAccount": {}}}', b"",
                        b'{"X": {"token": "t"}}'):
            self.damage(content)
            self.assertFalse(self.store.save(good(), IDENTITY, "NEW"), content)
            self.assertEqual(read_file(self.path, "rb"), content, "the damaged file is not overwritten")
            self.assertEqual(self.store.lookup("X"), ("storeUnavailable", None))
            self.assertIsNone(self.store.all())
            self.assertFalse(self.store.remove("X"))
            self.assertEqual(self.store.readable()[0], False)

    def test_an_unreadable_file_is_unavailable_not_empty(self):
        if os.geteuid() == 0:
            self.skipTest("root can read anything")
        self.store.save(good(), IDENTITY, "ID-1")
        os.chmod(self.path, 0)
        try:
            self.assertEqual(self.store.lookup("ID-1"), ("storeUnavailable", None))
            self.assertFalse(self.store.save(good(), IDENTITY, "ID-2"))
        finally:
            os.chmod(self.path, 0o600)

    def test_a_missing_file_is_empty_and_first_save_creates_it(self):
        self.assertEqual(self.store.readable(), (True, ""))
        self.assertTrue(self.store.save(good(), IDENTITY, "ID-1"))
        self.assertTrue(os.path.exists(self.path))

    def test_a_symlinked_store_is_not_replaced(self):
        self.store.save(good(), IDENTITY, "ID-1")
        real = os.path.join(self.dir, "real.json")
        os.replace(self.path, real)
        os.symlink(real, self.path)
        self.assertFalse(self.store.save(credential_text("access-B", "refresh-B"), IDENTITY, "ID-2"))
        self.assertTrue(os.path.islink(self.path))
        self.assertEqual(list(read_json(real)), ["ID-1"])


class SettingsFileTests(unittest.TestCase):
    def test_round_trip_and_defaults(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "c", "settings.json")
        settings = SettingsFile(path, LOG)
        self.assertEqual(settings.snapshot(), {})
        settings.replace({"autoSwitchThreshold": 80})
        self.assertEqual(SettingsFile(path, LOG).snapshot(), {"autoSwitchThreshold": 80})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_a_snapshot_is_a_copy(self):
        directory = tmpdir(self)
        settings = SettingsFile(os.path.join(directory, "s.json"), LOG)
        settings.replace({"a": [1]})
        snap = settings.snapshot()
        snap["a"].append(2)
        self.assertEqual(settings.snapshot(), {"a": [1]})

    def test_a_damaged_file_is_moved_aside_not_deleted(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "s.json")
        with open(path, "w") as handle:
            handle.write("[1,2")
        settings = SettingsFile(path, LOG, clock=lambda: 42)
        self.assertEqual(settings.snapshot(), {})
        self.assertEqual(read_file(path + ".unreadable-42"), "[1,2")


class AccountsFileTests(unittest.TestCase):
    def test_saved_sorted_on_load_and_unknown_keys_kept(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "accounts.json")
        with open(path, "w") as handle:
            json.dump([
                {"id": "B", "email": "b@x.test", "priority": 2, "future": {"k": 1}},
                {"id": "A", "email": "a@x.test", "priority": 1},
                {"id": "C", "email": "c@x.test"},
            ], handle)
        accounts = AccountsFile(path, LOG).load()
        self.assertEqual([a.id for a in accounts], ["A", "B", "C"])
        os.chmod(path, 0o600)
        AccountsFile(path, LOG).save(accounts)
        data = read_json(path)
        self.assertEqual([d["id"] for d in data], ["A", "B", "C"])
        self.assertEqual(data[1]["future"], {"k": 1})
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_a_damaged_file_is_moved_aside(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "accounts.json")
        with open(path, "w") as handle:
            handle.write('{"not": "a list"}')
        self.assertEqual(AccountsFile(path, LOG, clock=lambda: 7).load(), [])
        self.assertTrue(os.path.exists(path + ".unreadable-7"))
        self.assertFalse(os.path.exists(path))

    def test_absent_file_is_empty(self):
        self.assertEqual(AccountsFile(os.path.join(tmpdir(self), "none.json"), LOG).load(), [])


class NeedsReauthFileTests(unittest.TestCase):
    def test_ids_only_written_only_when_changed(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "state", "needs-reauth.json")
        store = NeedsReauthFile(path, LOG)
        self.assertEqual(store.load(), set())
        store.save(set())
        self.assertFalse(os.path.exists(path), "nothing changed, nothing written")
        store.save({"B", "A"})
        self.assertEqual(read_json(path), ["A", "B"])
        mtime = os.stat(path).st_mtime_ns
        store.save({"A", "B"})
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)
        self.assertEqual(NeedsReauthFile(path, LOG).load(), {"A", "B"})

    def test_damage_reads_as_none(self):
        directory = tmpdir(self)
        path = os.path.join(directory, "n.json")
        with open(path, "w") as handle:
            handle.write("{{{")
        self.assertEqual(NeedsReauthFile(path, LOG).load(), set())


if __name__ == "__main__":
    unittest.main()
