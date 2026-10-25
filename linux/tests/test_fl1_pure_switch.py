"""Lane FL1: the pure decisions of the Linux switch, ported case by case from
Tests/UnitTests/RemovalAndBackupTests.swift (live_login_backup.py = LiveLoginBackup.swift,
removal.py = AccountRemoval.swift)."""
from __future__ import annotations

import itertools
import json
import unittest

from pixelswitch import live_login_backup as llb
from pixelswitch import removal as rm
from pixelswitch.logins import Login, login_from_credential
from pixelswitch.usage_guard import fingerprint_of


def good(access="A1", refresh="R1"):
    return json.dumps({"claudeAiOauth": {"accessToken": access, "refreshToken": refresh, "expiresAt": 1790000000000}})


BLANKED = json.dumps({"claudeAiOauth": {"accessToken": "", "refreshToken": "", "expiresAt": 0}, "mcpOAuth": {"s": {"x": 1}}})


class BackupResultTests(unittest.TestCase):
    def result(self, fresh, owner=True, proven=True, identity=True, store=True):
        return llb.result(fresh, owner_known=owner, proven=proven, identity_matches=identity, store_accepted=store)

    def test_a_usable_proven_login_that_the_store_took_is_saved(self):
        self.assertEqual(self.result(good()), llb.SAVED)

    def test_a_read_that_returned_nothing_is_never_saved_or_settled(self):
        self.assertEqual(self.result(None), llb.not_saved(llb.UNREADABLE))
        for owner, proven, identity, store in itertools.product([True, False], repeat=4):
            self.assertFalse(self.result(None, owner, proven, identity, store).is_settled)

    def test_text_that_is_not_a_credential_object_is_unreadable(self):
        for text in ("not json at all", "[]", ""):
            self.assertEqual(self.result(text), llb.not_saved(llb.UNREADABLE), text)

    def test_a_blanked_login_is_nothing_worth_saving(self):
        self.assertEqual(self.result(BLANKED), llb.NOTHING_WORTH_SAVING)
        self.assertTrue(self.result(BLANKED).is_settled)
        self.assertEqual(self.result('{"mcpOAuth":{"s":1}}'), llb.NOTHING_WORTH_SAVING)

    def test_each_reason_has_its_own_result(self):
        self.assertEqual(self.result(good(), owner=False), llb.not_saved(llb.NO_OWNER))
        self.assertEqual(self.result(good(), proven=False), llb.not_saved(llb.UNPROVEN))
        self.assertEqual(self.result(good(), identity=False), llb.not_saved(llb.IDENTITY_MISMATCH))
        self.assertEqual(self.result(good(), store=False), llb.not_saved(llb.STORE_REFUSED))

    def test_only_a_credential_object_counts(self):
        self.assertTrue(llb.is_credential_object(BLANKED))
        self.assertFalse(llb.is_credential_object(None))
        self.assertFalse(llb.is_credential_object("{"))


class FirstPassTests(unittest.TestCase):
    A1, A2 = good("A1", "R1"), good("A2", "R2")

    def first(self, result, owner=True, seen=A1, now=A1):
        return llb.after_first_pass(seen, now, owner, result)

    def test_a_login_that_could_not_be_proven_or_saved_is_not_written_over(self):
        for reason in (llb.UNPROVEN, llb.STORE_REFUSED):
            step = self.first(llb.not_saved(reason))
            self.assertEqual((step.kind, step.abort), ("abort", llb.Abort("notSaved", reason)))

    def test_a_saved_login_or_one_with_nothing_worth_saving_goes_ahead(self):
        self.assertEqual(self.first(llb.SAVED), llb.PROCEED)
        self.assertEqual(self.first(llb.NOTHING_WORTH_SAVING), llb.PROCEED)

    def test_only_the_two_reasons_stop_an_unchanged_login(self):
        self.assertEqual(self.first(llb.not_saved(llb.IDENTITY_MISMATCH)), llb.PROCEED)
        self.assertEqual(self.first(llb.not_saved(llb.NO_OWNER)), llb.PROCEED)

    def test_with_no_proven_owner_these_reasons_do_not_stop_the_switch(self):
        self.assertEqual(self.first(llb.not_saved(llb.UNPROVEN), owner=False), llb.PROCEED)
        self.assertEqual(self.first(llb.not_saved(llb.STORE_REFUSED), owner=False), llb.PROCEED)

    def test_a_login_that_changed_is_looked_at_again_first(self):
        self.assertEqual(self.first(llb.not_saved(llb.UNPROVEN), now=self.A2), llb.RETRY)
        self.assertEqual(self.first(llb.not_saved(llb.STORE_REFUSED), now=self.A2), llb.RETRY)

    def test_an_unreadable_login_with_a_proven_owner_stops_with_nothing_written(self):
        unreadable = llb.not_saved(llb.UNREADABLE)
        self.assertEqual(self.first(unreadable, seen=None, now=None).abort, llb.UNREADABLE_ABORT)
        self.assertEqual(self.first(unreadable, seen=None, now=self.A1).abort, llb.UNREADABLE_ABORT)
        self.assertEqual(self.first(unreadable, owner=False, seen=None, now=None), llb.PROCEED)
        self.assertEqual(self.first(unreadable, owner=False, seen=None, now=self.A1), llb.RETRY)

    def test_it_cannot_refuse_every_switch(self):
        """Over every fact that can hold with an unchanged, usable login and a known owner, only a login
        that differs from the proven one AND was not vouched for by the API, or a store that failed,
        stops it."""
        verified = login_from_credential(self.A1)
        refused_with_proof = False
        stopped_for_the_right_reason = True
        for fresh in (self.A1, self.A2):
            proof = llb.fresh_login_proof(verified, login_from_credential(fresh))
            for api_vouches, identity, store in itertools.product([True, False], repeat=3):
                proven = proof == llb.SAME_GRANT or (proof == llb.NEEDS_API and api_vouches)
                outcome = llb.result(fresh, owner_known=True, proven=proven, identity_matches=identity, store_accepted=store)
                step = llb.after_first_pass(fresh, fresh, True, outcome)
                if proof == llb.SAME_GRANT and identity and store and step != llb.PROCEED:
                    refused_with_proof = True
                if step.kind == "abort":
                    because_unproven = proof == llb.NEEDS_API and not api_vouches
                    because_store = proven and identity and not store
                    if not (because_unproven or because_store):
                        stopped_for_the_right_reason = False
        self.assertFalse(refused_with_proof)
        self.assertTrue(stopped_for_the_right_reason)

    def test_the_proof_of_a_fresh_login(self):
        verified = login_from_credential(self.A1)
        self.assertEqual(llb.fresh_login_proof(verified, verified), llb.SAME_GRANT)
        self.assertEqual(llb.fresh_login_proof(verified, login_from_credential(self.A2)), llb.NEEDS_API)
        self.assertEqual(llb.fresh_login_proof(None, verified), llb.NO_PROOF)
        self.assertEqual(llb.fresh_login_proof(verified, None), llb.NO_PROOF)


class RetryTests(unittest.TestCase):
    A1, A2, A3 = good("A1", "R1"), good("A2", "R2"), good("A3", "R3")

    def test_a_retry_that_reads_nothing_twice_after_a_login_was_seen_stops(self):
        unreadable = llb.not_saved(llb.UNREADABLE)
        self.assertEqual(llb.after_retry(self.A1, None, unreadable, None, True), llb.UNREADABLE_ABORT)
        self.assertEqual(llb.after_retry(self.A1, None, unreadable, None, False), llb.UNREADABLE_ABORT)
        self.assertIsNone(llb.after_retry(None, None, unreadable, None, False), "a login that was never there")

    def test_still_changing_and_not_saved_are_different_aborts(self):
        self.assertEqual(llb.after_retry(self.A1, self.A2, llb.SAVED, self.A3, True), llb.STILL_CHANGING)
        self.assertEqual(llb.after_retry(self.A1, self.A2, llb.not_saved(llb.UNPROVEN), self.A2, True),
                         llb.Abort("notSaved", llb.UNPROVEN))
        self.assertEqual(llb.after_retry(self.A1, self.A2, llb.not_saved(llb.STORE_REFUSED), self.A2, True),
                         llb.Abort("notSaved", llb.STORE_REFUSED))
        self.assertEqual(llb.after_retry(self.A1, self.A2, llb.not_saved(llb.IDENTITY_MISMATCH), self.A2, True),
                         llb.Abort("notSaved", llb.IDENTITY_MISMATCH))

    def test_a_renewed_login_that_was_saved_and_holds_still_goes_ahead(self):
        self.assertIsNone(llb.after_retry(self.A1, self.A2, llb.SAVED, self.A2, True))
        self.assertIsNone(llb.after_retry(self.A1, self.A2, llb.not_saved(llb.NO_OWNER), self.A2, False))
        self.assertIsNone(llb.after_retry(self.A1, BLANKED, llb.NOTHING_WORTH_SAVING, BLANKED, True))


class JudgedLoginTests(unittest.TestCase):
    K, K2, OTHER = good("K1", "RK"), good("K2", "RK2"), good("O1", "RO")

    def test_a_switch_that_judged_nothing_is_not_bound(self):
        self.assertIsNone(llb.after_judged_login(None, self.OTHER, self.K))

    def test_the_login_that_was_judged_goes_ahead(self):
        self.assertIsNone(llb.after_judged_login(llb.JudgedLogin(self.K), self.K, self.K))

    def test_a_different_login_stops_the_switch(self):
        judged = llb.JudgedLogin(self.K)
        self.assertEqual(llb.after_judged_login(judged, self.OTHER, self.OTHER), llb.STILL_CHANGING)
        self.assertEqual(llb.after_judged_login(judged, self.K, self.K2), llb.STILL_CHANGING)
        self.assertEqual(llb.after_judged_login(judged, self.K2, self.K2), llb.STILL_CHANGING)

    def test_a_none_judged_read_against_a_real_login_counts_as_changed(self):
        judged = llb.JudgedLogin(None)
        self.assertEqual(llb.after_judged_login(judged, self.K, self.K), llb.STILL_CHANGING)
        self.assertIsNone(llb.after_judged_login(judged, None, None))
        self.assertEqual(llb.after_judged_login(judged, None, self.K), llb.STILL_CHANGING)

    def test_a_blanked_login_that_was_judged_goes_ahead_a_real_one_in_its_place_does_not(self):
        judged = llb.JudgedLogin(BLANKED)
        self.assertIsNone(llb.after_judged_login(judged, BLANKED, BLANKED))
        self.assertEqual(llb.after_judged_login(judged, self.K, self.K), llb.STILL_CHANGING)

    def test_the_reviews_scenario(self):
        """The first read failed (nothing to lose), then step 1 reads K's only good login."""
        decision = rm.decide_disposal(live_login_usable=False, api=rm.COULD_NOT_SAY, remaining_same_grant=None,
                                      same_grant_as_removed_login=False)
        self.assertEqual((decision.kind, decision.basis), ("proceed", rm.NOTHING_TO_LOSE))
        self.assertEqual(llb.after_first_pass(self.K, self.K, False, llb.not_saved(llb.NO_OWNER)), llb.PROCEED)
        self.assertEqual(llb.after_judged_login(llb.JudgedLogin(None), self.K, self.K), llb.STILL_CHANGING)


class FallbackLoopTests(unittest.TestCase):
    class Script:
        def __init__(self, answers):
            self.answers = {key: list(value) for key, value in answers.items()}
            self.tried: list[str] = []
            self.notes: list[str] = []
            self.pauses = 0
            self.needed = True
            self.after_attempt = None
            self.after_pause = None

        def run(self, candidates, max_busy_waits=rm.DEFAULT_MAX_BUSY_WAITS):
            def attempt(account_id):
                self.tried.append(account_id)
                queue = self.answers.get(account_id) or [rm.GONE]
                answer = queue.pop(0) if queue else rm.GONE
                if self.after_attempt:
                    self.after_attempt(account_id)
                return answer

            def pause():
                self.pauses += 1
                if self.after_pause:
                    self.after_pause()

            return rm.run_fallback(candidates, still_needed=lambda: self.needed, attempt=attempt, pause=pause,
                                   note=self.notes.append, max_busy_waits=max_busy_waits)

    def test_the_first_candidate_that_works_takes_over(self):
        script = self.Script({"one": [rm.SWITCHED]})
        self.assertEqual(script.run(["one", "two"]), rm.FallbackResult("switched", "one"))
        self.assertEqual(script.tried, ["one"])

    def test_a_failed_candidate_is_logged_with_its_reason_and_the_next_is_tried(self):
        script = self.Script({"one": [rm.failed("The saved login for one has expired.")], "two": [rm.SWITCHED]})
        self.assertEqual(script.run(["one", "two"]).account_id, "two")
        self.assertEqual(script.tried, ["one", "two"])
        self.assertTrue(any("The saved login for one has expired." in note for note in script.notes))

    def test_when_none_works_the_last_sentence_is_returned_and_every_failure_logged(self):
        script = self.Script({"one": [rm.failed("first reason")], "two": [rm.failed("second reason")],
                              "three": [rm.failed(None)]})
        outcome = script.run(["one", "two", "three"])
        self.assertEqual(outcome, rm.FallbackResult("noneWorked", message="second reason"))
        self.assertEqual(script.tried, ["one", "two", "three"])
        text = "\n".join(script.notes)
        self.assertIn("first reason", text)
        self.assertIn("second reason", text)
        self.assertIn("no reason given", text)

    def test_candidates_that_are_gone_are_skipped_and_leave_no_failure(self):
        script = self.Script({"one": [rm.GONE], "two": [rm.GONE]})
        self.assertEqual(script.run(["one", "two"]), rm.FallbackResult("noneWorked", message=None))

    def test_it_stands_down_once_another_account_is_active(self):
        script = self.Script({"one": [rm.failed("x")], "two": [rm.SWITCHED]})
        script.after_attempt = lambda _id: setattr(script, "needed", False)
        self.assertEqual(script.run(["one", "two"]), rm.STOOD_DOWN)
        self.assertEqual(script.tried, ["one"])

    def test_if_the_removed_account_is_no_longer_active_at_the_start_nothing_is_tried(self):
        script = self.Script({"one": [rm.SWITCHED]})
        script.needed = False
        self.assertEqual(script.run(["one"]), rm.STOOD_DOWN)
        self.assertEqual((script.tried, script.pauses), ([], 0))

    def test_a_busy_switch_is_waited_out_and_the_same_candidate_retried(self):
        script = self.Script({"one": [rm.busy("busy"), rm.busy("busy"), rm.SWITCHED]})
        self.assertEqual(script.run(["one", "two"]).account_id, "one")
        self.assertEqual((script.pauses, script.tried), (2, ["one", "one", "one"]))

    def test_a_sign_in_that_finishes_inside_the_wait_is_never_switched_away_from(self):
        script = self.Script({"one": [rm.busy("busy"), rm.SWITCHED], "two": [rm.SWITCHED]})
        script.after_pause = lambda: setattr(script, "needed", False)
        self.assertEqual(script.run(["one", "two"]), rm.STOOD_DOWN)
        self.assertEqual(script.tried, ["one"])

    def test_busy_for_the_whole_wait_gives_up_without_moving_on(self):
        script = self.Script({"one": [rm.busy("A switch or sign-in is in progress.")] * 20, "two": [rm.SWITCHED]})
        outcome = script.run(["one", "two"], max_busy_waits=3)
        self.assertEqual(outcome, rm.FallbackResult("busyGaveUp", message="A switch or sign-in is in progress."))
        self.assertEqual((script.pauses, script.tried), (3, ["one"] * 4))

    def test_the_default_wait_is_150_seconds(self):
        self.assertEqual(rm.DEFAULT_MAX_BUSY_WAITS, 150)


class ShortcutTests(unittest.TestCase):
    def test_the_cases(self):
        self.assertTrue(rm.already_live("a", "b", "a"))
        self.assertFalse(rm.already_live("a", "b", "b"))
        self.assertTrue(rm.already_live(None, "b", "b"))
        self.assertFalse(rm.already_live(None, None, "b"))
        self.assertFalse(rm.already_live(None, "a", "b"))


class DisposalTests(unittest.TestCase):
    def decide(self, usable=True, api=rm.COULD_NOT_SAY, remaining=(), removed=False):
        return rm.decide_disposal(live_login_usable=usable, api=api,
                                  remaining_same_grant=None if remaining is None else list(remaining),
                                  same_grant_as_removed_login=removed)

    def proceed(self, basis):
        return rm.Disposal("proceed", basis)

    def test_nothing_to_lose(self):
        self.assertEqual(self.decide(usable=False), self.proceed(rm.NOTHING_TO_LOSE))
        self.assertEqual(self.decide(usable=False, api=rm.NAMES_REMAINING_ACCOUNT, remaining=["k", "l"]),
                         self.proceed(rm.NOTHING_TO_LOSE))

    def test_the_api_proves_the_login_is_nobodys_on_the_list(self):
        for remaining in ([], None, ["k"]):
            self.assertEqual(self.decide(api=rm.NAMES_NO_REMAINING_ACCOUNT, remaining=remaining),
                             self.proceed(rm.API_NAMES_NO_REMAINING_ACCOUNT), remaining)

    def test_the_same_grant_as_the_removed_accounts_logins(self):
        self.assertEqual(self.decide(removed=True), self.proceed(rm.SAME_GRANT_AS_REMOVED_ACCOUNT))
        self.assertEqual(self.decide(api=rm.COULD_NOT_SAY, remaining=[], removed=True),
                         self.proceed(rm.SAME_GRANT_AS_REMOVED_ACCOUNT))
        self.assertEqual(self.decide(remaining=None, removed=True), rm.REFUSE,
                         "lineage proves nothing while the backup store cannot be read")

    def test_the_same_grant_as_exactly_one_remaining_account_makes_it_the_believed_active_one(self):
        self.assertEqual(self.decide(remaining=["k"]), rm.Disposal("treatAsActive", account_id="k"))
        self.assertEqual(self.decide(remaining=["k"], removed=True), rm.Disposal("treatAsActive", account_id="k"),
                         "a login that is both a remaining account's and a removed one's is never discarded")

    def test_otherwise_it_refuses(self):
        self.assertEqual(self.decide(), rm.REFUSE)
        self.assertEqual(self.decide(remaining=["k", "l"]), rm.REFUSE)
        self.assertEqual(self.decide(remaining=["k", "l"], removed=True), rm.REFUSE)
        for removed, remaining in itertools.product([True, False], [[], ["k"]]):
            self.assertEqual(self.decide(api=rm.NAMES_REMAINING_ACCOUNT, remaining=remaining, removed=removed), rm.REFUSE)

    def test_over_every_combination_a_usable_login_is_replaced_only_on_proof(self):
        discarded_a_remaining_login = proceeded_without_proof = False
        for usable, api, remaining, removed in itertools.product(
                [True, False], [rm.COULD_NOT_SAY, rm.NAMES_NO_REMAINING_ACCOUNT, rm.NAMES_REMAINING_ACCOUNT],
                [None, [], ["k"], ["k", "l"]], [True, False]):
            decision = self.decide(usable, api, remaining, removed)
            if decision.kind == "proceed":
                if usable and api != rm.NAMES_NO_REMAINING_ACCOUNT and len(remaining or []) > 0:
                    discarded_a_remaining_login = True
                if usable and decision.basis == rm.NOTHING_TO_LOSE:
                    proceeded_without_proof = True
                if usable and api != rm.NAMES_NO_REMAINING_ACCOUNT and not removed:
                    proceeded_without_proof = True
        self.assertFalse(discarded_a_remaining_login)
        self.assertFalse(proceeded_without_proof)

    def test_the_refusal_sentence(self):
        self.assertEqual(rm.REFUSAL_SENTENCE, "PixelSwitch cannot tell whose login Claude Code holds right now. "
                                              "Use Claude Code once, then try again.")
        self.assertNotIn("—", rm.REFUSAL_SENTENCE)


class RemovedLoginsTests(unittest.TestCase):
    def login(self, refresh, access="any"):
        return Login(access, refresh)

    def test_only_fingerprints_are_kept(self):
        saved, live = good("SAVED-A", "R-SAVED"), good("LIVE-A", "R-LIVE")
        prints = rm.fingerprints_of_credentials([saved, live, None, BLANKED])
        self.assertEqual(len(prints), 2)
        self.assertFalse(any("R-SAVED" in p or "R-LIVE" in p for p in prints))
        self.assertTrue(rm.is_removed_login(self.login("R-SAVED"), prints))
        self.assertTrue(rm.is_removed_login(self.login("R-LIVE", "other"), prints))
        self.assertFalse(rm.is_removed_login(self.login("R-NEW", "SAVED-A"), prints))
        self.assertFalse(rm.is_removed_login(self.login(None), prints))
        self.assertFalse(rm.is_removed_login(self.login("R-SAVED"), []))

    def test_remembering_adds_without_repeats_and_is_bounded(self):
        memory = rm.remembering(["a", "b"], [])
        memory = rm.remembering(["b", "c"], memory)
        self.assertEqual(memory, ["a", "b", "c"])
        many = rm.remembering([f"f{i}" for i in range(40)], ["old"])
        self.assertEqual(len(many), rm.REMOVED_LOGINS_LIMIT)
        self.assertEqual(many[-1], "f39")
        self.assertNotIn("old", many)

    def recorded(self, live, proof=None, saved=good("SAVED-A", "R-SAVED"), removed="removed"):
        return rm.fingerprints_to_record(removed, saved, live, proof)

    def test_the_saved_login_is_always_remembered(self):
        self.assertTrue(rm.is_removed_login(self.login("R-SAVED"), self.recorded(None)))
        self.assertEqual(self.recorded(None, saved=None), [])
        self.assertEqual(self.recorded(good("K-LIVE", "R-K"), saved=None), [])

    def test_belief_is_not_proof(self):
        """K signed in by hand while the removed account was believed active."""
        unproven = self.recorded(good("K-LIVE", "R-K"))
        self.assertEqual(len(unproven), 1)
        self.assertFalse(rm.is_removed_login(self.login("R-K"), unproven))
        decision = rm.decide_disposal(live_login_usable=True, api=rm.COULD_NOT_SAY, remaining_same_grant=[],
                                      same_grant_as_removed_login=rm.is_removed_login(self.login("R-K"), unproven))
        self.assertEqual(decision, rm.REFUSE)

    def test_the_same_grant_as_the_saved_login_is_remembered_with_it(self):
        same_access = self.recorded(good("SAVED-A", "R-ROTATED"))
        self.assertTrue(rm.is_removed_login(self.login("R-ROTATED"), same_access))
        self.assertTrue(rm.is_removed_login(self.login("R-SAVED"), same_access))
        same_refresh = self.recorded(good("A-NEW", "R-SAVED"))
        self.assertEqual(len(same_refresh), 1)

    def test_an_api_proof_is_remembered_but_only_for_the_removed_account_and_that_token(self):
        k_live = good("K-LIVE", "R-K")
        proof = rm.proof_for("removed", k_live)
        self.assertIsNotNone(proof)
        self.assertIsNone(rm.proof_for("removed", BLANKED))
        self.assertIsNone(rm.proof_for("removed", None))
        self.assertNotIn("R-K", proof.refresh_fingerprint)
        self.assertTrue(rm.is_removed_login(self.login("R-K"), self.recorded(k_live, proof)))
        other = rm.proof_for("someone else", k_live)
        self.assertFalse(rm.is_removed_login(self.login("R-K"), self.recorded(k_live, other)))
        self.assertFalse(rm.is_removed_login(self.login("R-K2"), self.recorded(good("K2", "R-K2"), proof)))
        self.assertTrue(all("R-K" not in p and "R-SAVED" not in p for p in self.recorded(k_live, proof)))
        self.assertEqual(proof.refresh_fingerprint, fingerprint_of("R-K"))


if __name__ == "__main__":
    unittest.main()
