import Foundation

/// The pure decisions behind step 1 of a switch (save the live login under its
/// owner) and the re-read before the write (spec 5.3). `ClaudeService.switchAccount`
/// gathers the facts (what the Keychain returned, what the API proved, whether the
/// store took the save) and asks these functions what they mean, so the rules are
/// testable without a Keychain.
///
/// The rule they exist for: a read that returned nothing, or nothing usable,
/// never counts as "saved". Only a credential that is there, parses, and holds
/// a blanked login counts as "nothing worth saving" (spec D12), because Claude
/// Code leaves the file in place when it blanks a dead login.
enum LiveLoginBackup {

    /// Why the live login was not saved under its owner. Logged, never shown.
    enum NotSavedReason: String, Equatable, Sendable {
        /// The Keychain read returned nothing, or text that is not a credential object.
        case unreadable
        /// No account is the proven owner, so nothing may be saved for anyone.
        case noOwner
        /// The API could not say the login is still the owner's.
        case unproven
        /// No identity details on record match the owner.
        case identityMismatch
        /// The backup store refused or failed the write.
        case storeRefused
    }

    /// What step 1 came to.
    enum Result: Equatable, Sendable {
        /// The login was saved under its owner.
        case saved
        /// The live credential is present and holds no usable login (blanked or
        /// without `claudeAiOauth`): there is nothing to save, and that is fine.
        case nothingWorthSaving
        /// The login was not saved, for this reason.
        case notSaved(NotSavedReason)

        /// True when nothing that matters was lost by not saving.
        var isSettled: Bool {
            switch self {
            case .saved, .nothingWorthSaving: return true
            case .notSaved: return false
            }
        }
    }

    /// The outcome of step 1 from the facts, in the order they apply.
    ///
    /// - `fresh`: what the Keychain returned just now (nil if it returned nothing).
    /// - `ownerKnown`: a proven owner was passed in.
    /// - `proven`: the API (or the same grant as the proven login) says `fresh` is the owner's.
    /// - `identityMatches`: some identity details on record belong to the owner.
    /// - `storeAccepted`: the backup store took the save.
    static func result(fresh: String?, ownerKnown: Bool, proven: Bool, identityMatches: Bool, storeAccepted: Bool) -> Result {
        guard let fresh, CredentialOwnership.isCredentialObject(fresh) else { return .notSaved(.unreadable) }
        if !CredentialOwnership.isUsableLogin(credential: fresh) { return .nothingWorthSaving }
        guard ownerKnown else { return .notSaved(.noOwner) }
        guard proven else { return .notSaved(.unproven) }
        guard identityMatches else { return .notSaved(.identityMismatch) }
        return storeAccepted ? .saved : .notSaved(.storeRefused)
    }

    // MARK: - The re-read before the write

    /// Why a switch stopped with nothing written.
    enum Abort: Equatable, Sendable {
        /// Claude Code is still renewing the login: it differs between the read
        /// step 1 saw and the read just before the write.
        case stillChanging
        /// The live login was read a moment ago and cannot be read now.
        case unreadable
        /// The renewed login could not be saved for its owner.
        case notSaved(NotSavedReason)
    }

    /// What to do after the first pass of step 1 and the re-read before the write.
    enum FirstPassStep: Equatable, Sendable {
        case proceed
        /// The live login changed since step 1 saw it: run step 1 again, with proof.
        case retry
        case abort(Abort)
    }

    /// - `seen`: the live credential step 1 looked at.
    /// - `liveNow`: the live credential read immediately before the write.
    /// - `ownerKnown`: a proven owner was passed in, so a login was readable a
    ///   moment ago. If step 1 then read nothing, it vanished or the Keychain
    ///   flaked: stop, because writing now could overwrite a login that is only
    ///   unreadable. The next try starts clean.
    /// - `result`: what step 1 came to. With the login unchanged and an owner
    ///   known, a save that could not be proven (`unproven`) or that the store
    ///   refused (`storeRefused`) means the owner's backup does not hold the
    ///   newest login, so writing now would lose it: stop. That cannot refuse
    ///   every switch: with the owner known, `unproven` arises only when the
    ///   fresh login differs from the one the API proved (`freshLoginProof` is
    ///   `.needsAPI`) and the API could not vouch for it, and `storeRefused` only
    ///   when the store itself failed. A login that changed goes to `.retry`
    ///   first, which asks again with proof. Other reasons (`noOwner`,
    ///   `identityMismatch`, `unreadable`) never stop an unchanged login here.
    static func afterFirstPass(seen: String?, liveNow: String?, ownerKnown: Bool, result: Result) -> FirstPassStep {
        if ownerKnown, seen == nil { return .abort(.unreadable) }
        if CredentialOwnership.liveLoginChanged(savedAtStep4: seen, now: liveNow) { return .retry }
        if ownerKnown, case .notSaved(let reason) = result, reason == .unproven || reason == .storeRefused {
            return .abort(.notSaved(reason))
        }
        return .proceed
    }

    /// The live credential a switch with no active account judged disposable
    /// (`LiveLoginDisposal`, spec D20 revision 4) before it went ahead. `credential`
    /// is nil when that read returned nothing: a nil judged read is still a
    /// judgment, and any real login that shows up afterwards was never judged.
    struct JudgedLogin: Equatable, Sendable {
        let credential: String?
    }

    /// Binds the no-active-account decision to the login it judged. With no proven
    /// owner nothing is compared against a saved login, so without this the login
    /// that gets overwritten could be one that was never judged: the decision
    /// would stand after a failed first read (`nothingToLose`), or after Claude
    /// Code or the user replaced the login while the target was being checked.
    /// Returns `.stillChanging` (nothing written) when the login step 1 looked at,
    /// or the one read just before the write, differs from the judged one; the
    /// next attempt decides again. A nil judged read against a real login counts
    /// as changed. `judged` is nil for every other switch (an owner is proven, or
    /// an account is believed active), which keeps 1.9's rules as they were.
    static func afterJudgedLogin(judged: JudgedLogin?, seen: String?, liveNow: String?) -> Abort? {
        guard let judged else { return nil }
        if CredentialOwnership.liveLoginChanged(savedAtStep4: judged.credential, now: seen)
            || CredentialOwnership.liveLoginChanged(savedAtStep4: judged.credential, now: liveNow) {
            return .stillChanging
        }
        return nil
    }

    /// How step 1 knows the login it is about to save is still the owner's.
    enum FreshLoginProof: Equatable, Sendable {
        /// The fresh login is the same grant as the one the API already proved
        /// (spec 5.3): no second question is needed.
        case sameGrant
        /// It differs (Claude Code renewed it since): the API must vouch for it.
        case needsAPI
        /// There is no proven login or no readable fresh login to compare.
        case none
    }

    /// Compares the login the API proved with the one the Keychain holds now.
    static func freshLoginProof(verified: CredentialOwnership.Login?, fresh: CredentialOwnership.Login?) -> FreshLoginProof {
        guard let verified, let fresh else { return .none }
        return CredentialOwnership.sameGrant(verified, fresh) ? .sameGrant : .needsAPI
    }

    /// The verdict after step 1 ran a second time.
    /// - `firstSeen`: what the first pass looked at.
    /// - `seen`, `result`: the second pass.
    /// - `liveNow`: the read immediately before the write, after the second pass.
    /// Returns nil to go ahead and write.
    static func afterRetry(firstSeen: String?, seen: String?, result: Result, liveNow: String?, ownerKnown: Bool) -> Abort? {
        if CredentialOwnership.liveLoginChanged(savedAtStep4: seen, now: liveNow) { return .stillChanging }
        // Two reads of nothing agree with each other, but the first pass saw a
        // login: this is an unreadable login, not a settled one.
        if liveNow == nil, firstSeen != nil { return .unreadable }
        if ownerKnown, case .notSaved(let reason) = result { return .notSaved(reason) }
        return nil
    }
}

extension CredentialOwnership {
    /// True when `json` parses as a JSON object (whatever it holds). Text that
    /// is not a credential at all, or a partial read, is false.
    static func isCredentialObject(_ json: String?) -> Bool {
        guard let json, let data = json.data(using: .utf8) else { return false }
        return (try? JSONSerialization.jsonObject(with: data)) is [String: Any]
    }
}
