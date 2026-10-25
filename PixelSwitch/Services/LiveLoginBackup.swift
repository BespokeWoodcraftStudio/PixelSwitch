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
    static func afterFirstPass(seen: String?, liveNow: String?, ownerKnown: Bool) -> FirstPassStep {
        if ownerKnown, seen == nil { return .abort(.unreadable) }
        return CredentialOwnership.liveLoginChanged(savedAtStep4: seen, now: liveNow) ? .retry : .proceed
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
