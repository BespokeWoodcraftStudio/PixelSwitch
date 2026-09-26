import Foundation

/// Turns what a person or an AI typed ("work", "a@x.com", "2", an id) into one
/// account, or a clear error that lists the choices.
///
/// Tried in order, each level case-insensitive: the account id, the email
/// address, the label, then a 1-based position in the priority order. The
/// first level with any match wins; two matches at that level is ambiguous.
enum AccountResolver {
    static func resolve(_ reference: String, in accounts: [Account]) throws -> Account {
        let ref = reference.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !ref.isEmpty else {
            throw ControlError(.invalidValue, "Name an account: its email, label, id or position.")
        }
        let levels: [(Account) -> Bool] = [
            { $0.id.uuidString.caseInsensitiveCompare(ref) == .orderedSame },
            { $0.email.caseInsensitiveCompare(ref) == .orderedSame },
            { ($0.customLabel ?? "").caseInsensitiveCompare(ref) == .orderedSame }
        ]
        for matches in levels {
            let hits = accounts.filter(matches)
            if hits.count == 1 { return hits[0] }
            if hits.count > 1 {
                throw ControlError(.ambiguous, "\"\(ref)\" matches \(hits.count) accounts. Use the email address or id.",
                                   candidates: hits.map(describe))
            }
        }
        if let position = Int(ref), position >= 1, position <= accounts.count {
            return accounts[position - 1]
        }
        throw ControlError(.notFound, "No account matches \"\(ref)\".", candidates: accounts.map(describe))
    }

    /// Resolves every reference, refusing the whole list if any fails.
    static func resolveAll(_ references: [String], in accounts: [Account]) throws -> [Account] {
        try references.map { try resolve($0, in: accounts) }
    }

    /// How an account is named in an error: its email, then its label if it has one.
    static func describe(_ account: Account) -> String {
        if let label = account.customLabel, !label.isEmpty { return "\(account.email) (\(label))" }
        return account.email
    }
}
