"""The account record, priority numbers (spec D2, D3, section 3), the account order
(AccountOrder.swift, Tests/UnitTests/AutoSwitchRulesTests.swift accountOrderTests) and the
threshold writes (spec 2.1, D8)."""
from __future__ import annotations

import json
import unittest

from pixelswitch import accounts as acc
from pixelswitch.accounts import ABSENT, Account
from tests.support import acct


def names(accounts):
    return "".join(a.id for a in accounts) if accounts is not None else "refused"


class AccountRecordTests(unittest.TestCase):
    LEGACY = ('[{"id":"CB7797E5-5257-402B-80D6-ADAE1220D368","email":"a@x.com","displayName":"A",'
              '"provider":"Claude Code","isActive":true,"customLabel":"Work"}]')

    def test_saved_accounts_from_before_thresholds_decode_with_none_of_their_own(self):
        decoded = acc.load_accounts(json.loads(self.LEGACY))
        self.assertEqual(len(decoded), 1)
        self.assertIsNone(decoded[0].switch_threshold)
        self.assertEqual((decoded[0].custom_label, decoded[0].is_active), ("Work", True))

    def test_threshold_and_priority_survive_a_round_trip(self):
        own = Account(id="B", email="b@x.com", display_name="B", switch_threshold=75, session_threshold=40, weekly_threshold=55,
                      priority=3, priority_set_order=7)
        back = Account.from_json(json.loads(json.dumps(own.to_json())))
        self.assertEqual(back, own)

    def test_a_default_account_saves_no_optional_keys(self):
        text = json.dumps(Account(id="C", email="c@x.com").to_json())
        for key in ("switchThreshold", "sessionThreshold", "weeklyThreshold", "priority", "prioritySetOrder",
                    "orgName", "customLabel"):
            self.assertNotIn(key, text)

    def test_whole_numbers_are_written_without_a_fraction(self):
        text = json.dumps(Account(id="C", email="c@x.com", switch_threshold=70.0, priority=2).to_json())
        self.assertIn('"switchThreshold": 70,', text + ",")
        self.assertNotIn("70.0", text)

    def test_unknown_keys_are_preserved(self):
        raw = {"id": "A", "email": "a@x.com", "future": {"nested": [1, 2]}, "lastUsed": 812345678.5, "orgName": "Org"}
        out = Account.from_json(raw).to_json()
        self.assertEqual(out["future"], {"nested": [1, 2]})
        self.assertEqual(out["lastUsed"], 812345678.5)
        self.assertEqual(out["orgName"], "Org")

    def test_manual_only_round_trips(self):
        a = Account.from_json({"id": "M", "email": "m@x.com", "switchThreshold": 0})
        self.assertTrue(a.manual_only)
        self.assertEqual(a.to_json()["switchThreshold"], 0)

    def test_a_wrongly_typed_optional_field_reads_as_absent(self):
        a = Account.from_json({"id": "A", "email": "a@x.com", "switchThreshold": "70", "priority": 2.5,
                               "isActive": "yes", "sessionThreshold": True})
        self.assertIsNone(a.switch_threshold)
        self.assertIsNone(a.priority)
        self.assertIsNone(a.session_threshold)
        self.assertFalse(a.is_active)

    def test_a_record_without_an_id_or_email_is_refused(self):
        for bad in ({"email": "a@x.com"}, {"id": "A"}, {"id": "", "email": "a"}, [], "x", {"id": 1, "email": "a"}):
            with self.assertRaises(acc.AccountsFormatError, msg=bad):
                Account.from_json(bad)
        with self.assertRaises(acc.AccountsFormatError):
            acc.load_accounts({"not": "a list"})

    def test_a_damaged_list_is_refused_whole(self):
        with self.assertRaises(acc.AccountsFormatError):
            acc.load_accounts([{"id": "A", "email": "a@x.com"}, {"id": "B"}])

    def test_subscription_is_capitalised(self):
        self.assertEqual(Account(id="A", email="e", subscription_type="max").display_subscription_type, "Max")
        self.assertIsNone(Account(id="A", email="e").display_subscription_type)

    def test_loading_sorts_by_priority(self):
        data = [{"id": "A", "email": "a"}, {"id": "B", "email": "b", "priority": 2}, {"id": "C", "email": "c", "priority": 1}]
        self.assertEqual(names(acc.load_accounts(data)), "CBA")


class SortedByPriorityTests(unittest.TestCase):
    def test_lower_first_and_unnumbered_last(self):
        a, b, c, d = acct("A"), acct("B", priority=3), acct("C", priority=1), acct("D")
        self.assertEqual(names(acc.sorted_by_priority([a, b, c, d])), "CBAD")

    def test_gaps_are_allowed(self):
        self.assertEqual(names(acc.sorted_by_priority([acct("A", priority=90), acct("B", priority=7), acct("C", priority=50)])), "BCA")

    def test_among_equals_the_one_set_most_recently_goes_first(self):
        a, b, c = acct("A", priority=2, set_order=1), acct("B", priority=2, set_order=5), acct("C", priority=2, set_order=3)
        self.assertEqual(names(acc.sorted_by_priority([a, b, c])), "BCA")

    def test_equal_without_a_set_order_keeps_the_existing_order(self):
        a, b, c = acct("A", priority=2), acct("B", priority=2), acct("C", priority=2)
        self.assertEqual(names(acc.sorted_by_priority([a, b, c])), "ABC")
        self.assertEqual(names(acc.sorted_by_priority([c, a, b])), "CAB")

    def test_a_set_order_beats_none_among_equals(self):
        a, b = acct("A", priority=2), acct("B", priority=2, set_order=1)
        self.assertEqual(names(acc.sorted_by_priority([a, b])), "BA")

    def test_unnumbered_keep_their_relative_order(self):
        self.assertEqual(names(acc.sorted_by_priority([acct("C"), acct("A"), acct("B")])), "CAB")

    def test_last_set_overrides(self):
        a, b = acct("A", priority=2, set_order=9), acct("B", priority=2, set_order=1)
        self.assertEqual(names(acc.sorted_by_priority([a, b], last_set={"B": 20})), "BA")

    def test_is_stable_and_pure(self):
        items = [acct("A", priority=1), acct("B", priority=1)]
        before = list(items)
        acc.sorted_by_priority(items)
        self.assertEqual(items, before)


class SettingPriorityTests(unittest.TestCase):
    """The founder's example: gmail = 3, pixelventures = 1 (D2)."""

    def test_the_typed_number_stays_the_typed_number(self):
        gmail, pv, other = acct("G"), acct("P"), acct("O")
        step1 = acc.setting_priority([gmail, pv, other], "G", 3)
        self.assertEqual(names(step1), "GPO")                  # 3 < unnumbered
        step2 = acc.setting_priority(step1, "P", 1)
        self.assertEqual(names(step2), "PGO")
        self.assertEqual({a.id: a.priority for a in step2}, {"P": 1, "G": 3, "O": None})

    def test_re_sorts(self):
        got = acc.setting_priority([acct("A", priority=1), acct("B", priority=2), acct("C", priority=3)], "C", 1)
        self.assertEqual(names(got), "CAB")                    # C was set most recently: first among the 1s

    def test_the_most_recently_set_goes_first_among_equals(self):
        base = acc.setting_priority([acct("A"), acct("B"), acct("C")], "A", 2)
        base = acc.setting_priority(base, "B", 2)
        self.assertEqual(names(base), "BAC")
        # Setting the number an account already has changes nothing, not even its recency
        # (AccountOrder.settingPriority; Tests/UnitTests/TwoWindowTests.swift:278-279).
        again = acc.setting_priority(base, "A", 2)
        self.assertEqual(names(again), "BAC")
        self.assertEqual(again, base)
        self.assertEqual({a.id: a.priority_set_order for a in again}, {a.id: a.priority_set_order for a in base})

    def test_clearing_drops_the_number_and_the_stamp(self):
        got = acc.setting_priority([acct("A", priority=1, set_order=4), acct("B")], "A", None)
        self.assertIsNone(got[0].priority)
        self.assertIsNone(got[0].priority_set_order)

    def test_unknown_id_and_out_of_range_values_are_none(self):
        items = [acct("A"), acct("B")]
        self.assertIsNone(acc.setting_priority(items, "Z", 1))
        for bad in (0, 100, -1, 1.5, "2", True, False):
            self.assertIsNone(acc.setting_priority(items, "A", bad), bad)
        self.assertIsNotNone(acc.setting_priority(items, "A", 1))
        self.assertIsNotNone(acc.setting_priority(items, "A", 99))

    def test_inputs_are_not_changed(self):
        items = [acct("A"), acct("B")]
        acc.setting_priority(items, "A", 1)
        self.assertIsNone(items[0].priority)


class NumberedInOrderTests(unittest.TestCase):
    def test_numbers_one_to_n_and_drops_stamps(self):
        got = acc.numbered_in_order([acct("C", priority=50, set_order=3), acct("A"), acct("B", priority=1)])
        self.assertEqual([(a.id, a.priority, a.priority_set_order) for a in got],
                         [("C", 1, None), ("A", 2, None), ("B", 3, None)])

    def test_empty(self):
        self.assertEqual(acc.numbered_in_order([]), [])


class AccountOrderTests(unittest.TestCase):
    def setUp(self):
        self.a, self.b, self.c, self.d = acct("A"), acct("B"), acct("C"), acct("D")
        self.listing = [self.a, self.b, self.c, self.d]
        self.gone = acct("Gone")

    def test_reordered(self):
        r = acc.reordered
        self.assertEqual(names(r(self.listing, ["C", "A", "D", "B"])), "CADB")
        self.assertEqual(names(r(self.listing, ["A", "B", "C", "D"])), "ABCD")
        self.assertEqual(names(r(self.listing, ["A", "B", "C"])), "refused")
        self.assertEqual(names(r(self.listing, ["A", "B", "C", "Gone"])), "refused")
        self.assertEqual(names(r(self.listing, ["A", "B", "C", "D", "Gone"])), "refused")
        self.assertEqual(names(r(self.listing, ["A", "A", "C", "D"])), "refused")
        self.assertEqual(names(r([], [])), "")

    def test_moving(self):
        def moved(sources, to):
            return names(acc.moving(self.listing, sources, to))

        self.assertEqual(moved([0], 3), "BCAD")
        self.assertEqual(moved([3], 0), "DABC")
        self.assertEqual(moved([1, 2], 4), "ADBC")
        self.assertEqual(moved([1], 2), "ABCD")
        self.assertEqual(moved([1], 1), "ABCD")
        self.assertEqual(moved([9], 0), "ABCD")
        self.assertEqual(moved([0], 99), "BCDA")
        self.assertEqual(moved([0], -5), "ABCD")

    def test_set_order_numbers_every_account(self):
        got = acc.set_order([acct("A", priority=9), acct("B", priority=1), acct("C")], ["C", "A", "B"])
        self.assertEqual([(a.id, a.priority) for a in got], [("C", 1), ("A", 2), ("B", 3)])
        self.assertIsNone(acc.set_order(self.listing, ["A"]))

    def test_a_drag_is_never_undone_by_the_sort(self):
        dragged = acc.numbered_in_order(acc.moving(acc.sorted_by_priority(
            [acct("A", priority=1), acct("B", priority=2), acct("C", priority=3)]), [2], 0))
        self.assertEqual(names(acc.sorted_by_priority(dragged)), "CAB")

    def test_fallback_is_the_first_account_that_is_not_manual_only(self):
        m_a, m_b = acct("M", switch=0), acct("N", switch=0)
        b, c = acct("B"), acct("C")
        self.assertEqual(acc.fallback([m_a, b, c]).id, "B")
        self.assertEqual(acc.fallback([b, m_a]).id, "B")
        self.assertEqual(acc.fallback([m_a, m_b]).id, "M")
        self.assertIsNone(acc.fallback([]))

    def test_fallback_uses_the_priority_order(self):
        low, high = acct("L", priority=5), acct("H", priority=1)
        self.assertEqual(acc.fallback([low, high]).id, "H")
        manual_first = acct("M", switch=0, priority=1)
        self.assertEqual(acc.fallback([low, manual_first]).id, "L")


class LegacyThresholdWriteTests(unittest.TestCase):
    """D8: a legacy write means both windows and clears the account's window values."""

    def test_clears_window_values(self):
        a = acct("A", switch=70, session=40, weekly=55)
        got = acc.with_legacy_threshold(a, 80)
        self.assertEqual((got.switch_threshold, got.session_threshold, got.weekly_threshold), (80, None, None))

    def test_clears_even_when_the_single_value_is_unchanged(self):
        a = acct("A", switch=80, weekly=55)
        got = acc.with_legacy_threshold(a, 80)
        self.assertEqual(acc.thresholds_triple(got), (80, None, None))
        self.assertNotEqual(acc.thresholds_triple(got), acc.thresholds_triple(a))  # the no-op check sees the triple

    def test_default_and_manual(self):
        a = acct("A", switch=70, session=40)
        self.assertEqual(acc.thresholds_triple(acc.with_legacy_threshold(a, None)), (None, None, None))
        self.assertEqual(acc.thresholds_triple(acc.with_legacy_threshold(a, 0)), (0, None, None))
        self.assertEqual(acc.thresholds_triple(acc.with_legacy_threshold(a, 72.6)), (73, None, None))


class WindowThresholdWriteTests(unittest.TestCase):
    """2.1: splitting the single value, and the Manual only rules."""

    def triple(self, account):
        return acc.thresholds_triple(account)

    def test_default_on_one_window_after_70(self):
        """The vector: switchThreshold 70, Session set to Default."""
        got = acc.with_window_thresholds(acct("A", switch=70), session=None)
        self.assertEqual(self.triple(got), (None, None, 70))

    def test_a_number_on_one_window_keeps_the_other_at_the_old_single_value(self):
        got = acc.with_window_thresholds(acct("A", switch=70), session=40)
        self.assertEqual(self.triple(got), (None, 40, 70))
        got = acc.with_window_thresholds(acct("A", switch=70), weekly=55)
        self.assertEqual(self.triple(got), (None, 70, 55))

    def test_both_windows_in_one_call(self):
        got = acc.with_window_thresholds(acct("A", switch=70), session=90, weekly=50)
        self.assertEqual(self.triple(got), (None, 90, 50))
        got = acc.with_window_thresholds(acct("A", switch=70), session=None, weekly=None)
        self.assertEqual(self.triple(got), (None, None, None))

    def test_existing_window_values_are_kept_by_the_split(self):
        got = acc.with_window_thresholds(acct("A", switch=70, session=40), weekly=55)
        self.assertEqual(self.triple(got), (None, 40, 55))

    def test_no_single_value_means_no_split(self):
        got = acc.with_window_thresholds(acct("A", session=40), weekly=55)
        self.assertEqual(self.triple(got), (None, 40, 55))
        got = acc.with_window_thresholds(acct("A"), session=None)
        self.assertEqual(self.triple(got), (None, None, None))

    def test_neither_given_changes_nothing(self):
        a = acct("A", switch=70)
        self.assertIs(acc.with_window_thresholds(a), a)
        self.assertIs(acc.with_window_thresholds(a, ABSENT, ABSENT), a)

    def test_only_a_number_clears_manual_only(self):
        manual = acct("M", switch=0)
        got = acc.with_window_thresholds(manual, session=60)
        self.assertEqual(self.triple(got), (None, 60, None))          # the other window follows its default
        got = acc.with_window_thresholds(manual, weekly=45)
        self.assertEqual(self.triple(got), (None, None, 45))

    def test_null_on_a_manual_only_account_changes_nothing(self):
        manual = acct("M", switch=0)
        self.assertEqual(acc.with_window_thresholds(manual, session=None), manual)
        self.assertEqual(acc.with_window_thresholds(manual, session=None, weekly=None), manual)

    def test_a_number_and_a_null_on_manual_only(self):
        got = acc.with_window_thresholds(acct("M", switch=0), session=60, weekly=None)
        self.assertEqual(self.triple(got), (None, 60, None))

    def test_a_stale_window_value_on_a_manual_only_account_does_not_survive(self):
        got = acc.with_window_thresholds(acct("M", switch=0, weekly=30), session=60)
        self.assertEqual(self.triple(got), (None, 60, None))

    def test_settings_default_choice_clears_manual_only(self):
        """Settings: Default in either menu of a Manual only account clears Manual only (1.9's Default)."""
        manual = acct("M", switch=0)
        self.assertEqual(self.triple(acc.with_default_choice(manual, "session")), (None, None, None))
        self.assertEqual(self.triple(acc.with_default_choice(manual, "weekly")), (None, None, None))
        # ...while the wire's null still leaves Manual only alone (see above), and on other accounts it splits
        self.assertEqual(self.triple(acc.with_default_choice(acct("A", switch=70), "session")), (None, None, 70))
        with self.assertRaises(ValueError):
            acc.with_default_choice(manual, "both")

    def test_own_window_values_for_the_wire(self):
        self.assertEqual(acc.own_window_values(acct("A", session=40, weekly=0)), (40, None))
        self.assertEqual(acc.own_window_values(acct("A")), (None, None))

    def test_whole_threshold(self):
        w = acc.whole_threshold
        self.assertEqual([w(72.6), w(72.5), w(0.4), w(-1)], [73, 73, 0, -1])
        self.assertIsNone(w("1"))
        self.assertIsNone(w(float("nan")))
        self.assertIsNone(w(True))


if __name__ == "__main__":
    unittest.main()
