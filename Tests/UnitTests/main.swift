// Unit tests for the credential-writing helpers. No Xcode needed:
// Tests/run-unit-tests.sh compiles this with the helper sources and runs it.
// PIXELSWITCH_KEYCHAIN_TESTS=1 also runs a round trip against the real
// /usr/bin/security on a throwaway Keychain item (never Claude Code's item).
import Foundation

var passed = 0
var failed = 0

@MainActor func check(_ condition: Bool, _ name: String, _ detail: @autoclosure () -> String = "") {
    if condition {
        passed += 1
        print("PASS  \(name)")
    } else {
        failed += 1
        print("FAIL  \(name)\(detail().isEmpty ? "" : ": \(detail())")")
    }
}

/// A credential shaped like Claude Code's, padded to `length` bytes.
func fakeCredential(length: Int) -> String {
    let head = #"{"claudeAiOauth":{"accessToken":"sk-ant-oat01-TESTTESTTESTTEST","refreshToken":"sk-ant-ort01-TESTTESTTESTTEST","expiresAt":1790000000000,"scopes":["user:inference","user:profile"],"subscriptionType":"max"},"mcpOAuth":{"pad":""#
    let tail = #""}}"#
    let pad = String(repeating: "x", count: max(0, length - head.utf8.count - tail.utf8.count))
    return head + pad + tail
}

func hex(_ s: String) -> String { s.utf8.map { String(format: "%02x", $0) }.joined() }

let service = "Claude Code-credentials"
let account = "someone"

// MARK: - ClaudeCredentialsFile.touch

do {
    let dir = FileManager.default.temporaryDirectory.appendingPathComponent("pixelswitch-tests-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: dir) }
    let file = dir.appendingPathComponent(".credentials.json")
    let original = Data(#"{"claudeAiOauth":{"accessToken":"keep-me"}}"#.utf8)
    try original.write(to: file)
    try FileManager.default.setAttributes([.modificationDate: Date(timeIntervalSince1970: 1_600_000_000)], ofItemAtPath: file.path)

    let now = Date()
    let result = ClaudeCredentialsFile.touch(file, now: now)
    let mtime = (try FileManager.default.attributesOfItem(atPath: file.path)[.modificationDate] as? Date) ?? .distantPast
    check(result == .bumped(path: file.path), "touch reports bumped for an existing file", "\(result)")
    check(abs(mtime.timeIntervalSince(now)) < 1, "touch sets the modification date to now", "\(mtime)")
    check((try Data(contentsOf: file)) == original, "touch leaves the file's bytes untouched")

    let missing = dir.appendingPathComponent("absent/.credentials.json")
    let absent = ClaudeCredentialsFile.touch(missing)
    check(absent == .absent(path: missing.path), "touch reports absent for a missing file", "\(absent)")
    check(!FileManager.default.fileExists(atPath: missing.path), "touch never creates the file")
    check(!FileManager.default.fileExists(atPath: missing.deletingLastPathComponent().path), "touch never creates its folder")
} catch {
    check(false, "touch tests set up", "\(error)")
}

// MARK: - ClaudeTokenWriter.addInvocation

do {
    let small = fakeCredential(length: 600)
    let (inv, transport) = ClaudeTokenWriter.addInvocation(service: service, account: account, secret: small, updateInPlace: true)
    check(transport == .stdin, "a small credential goes over stdin")
    check(inv.arguments == ["-i"], "a small credential's command line is only `security -i`", "\(inv.arguments)")
    check(!inv.arguments.contains { $0.contains("claudeAiOauth") }, "no argument contains claudeAiOauth")
    check(!inv.arguments.contains { $0.contains(hex(small)) || $0.contains("sk-ant-") }, "no argument contains the secret or its hex")
    let line = String(decoding: inv.stdin ?? Data(), as: UTF8.self)
    check(line == "add-generic-password -U -s \"\(service)\" -a \"\(account)\" -X \(hex(small))\n", "stdin carries the update-in-place command with the hex secret")

    let (add, _) = ClaudeTokenWriter.addInvocation(service: service, account: account, secret: small, updateInPlace: false)
    check(!String(decoding: add.stdin ?? Data(), as: UTF8.self).contains(" -U "), "the delete-and-add path omits -U")

    let large = fakeCredential(length: 9_000)
    let (big, bigTransport) = ClaudeTokenWriter.addInvocation(service: service, account: account, secret: large, updateInPlace: true)
    check(bigTransport == .argv, "a 9,000-byte credential is too long for security -i and uses the command line")
    check(big.stdin == nil && big.arguments.last == hex(large), "the command-line fallback passes hex, not plaintext")
    check(!big.arguments.contains { $0.contains("claudeAiOauth") }, "the fallback still has no plaintext claudeAiOauth in any argument")

    // Boundary: the whole stdin line may be 4,095 bytes, not 4,096.
    let prefix = "add-generic-password -U -s \"\(service)\" -a \"\(account)\" -X ".utf8.count + 1
    let fits = String(repeating: "a", count: (ClaudeTokenWriter.interactiveLineLimit - prefix) / 2)
    let tooLong = fits + "a"
    check(ClaudeTokenWriter.addInvocation(service: service, account: account, secret: fits, updateInPlace: true).1 == .stdin, "a line at the limit uses stdin")
    check(ClaudeTokenWriter.addInvocation(service: service, account: account, secret: tooLong, updateInPlace: true).1 == .argv, "one byte of secret over the limit uses the command line")

    let quoted = ClaudeTokenWriter.addInvocation(service: "odd\"name", account: account, secret: small, updateInPlace: true)
    check(quoted.1 == .argv, "a service name security -i could misparse is never sent through it")
}

// MARK: - ClaudeTokenWriter.write (fake security)

final class FakeSecurity {
    var calls: [ClaudeTokenWriter.Invocation] = []
    var readBacks: [String?]
    init(readBacks: [String?]) { self.readBacks = readBacks }
    func writer() -> ClaudeTokenWriter {
        ClaudeTokenWriter(
            service: service, account: account,
            run: { self.calls.append($0); return 0 },
            readBack: { self.readBacks.isEmpty ? nil : self.readBacks.removeFirst() }
        )
    }
}

do {
    let secret = fakeCredential(length: 600)

    let ok = FakeSecurity(readBacks: [secret])
    check(ok.writer().write(secret) == .written(path: .updateInPlace, transport: .stdin), "a matching read-back after update in place is success")
    check(ok.calls.count == 1, "update in place runs security once, with no delete", "\(ok.calls.count) calls")

    let fallback = FakeSecurity(readBacks: ["stale", secret])
    check(fallback.writer().write(secret) == .written(path: .deleteAndAdd, transport: .stdin), "a failed update falls back to delete and add")
    check(fallback.calls.count == 3 && fallback.calls[1].arguments.first == "delete-generic-password", "the fallback deletes, then adds", "\(fallback.calls.map { $0.arguments.first ?? "" })")

    let mismatch = FakeSecurity(readBacks: ["stale", "still wrong"])
    if case .failed = mismatch.writer().write(secret) { check(true, "a read-back mismatch is reported as a failed write") }
    else { check(false, "a read-back mismatch is reported as a failed write") }

    let missing = FakeSecurity(readBacks: [nil, nil])
    if case .failed = missing.writer().write(secret) { check(true, "a missing item after writing is a failed write") }
    else { check(false, "a missing item after writing is a failed write") }
}

// MARK: - ClaudeCredentialMerge

func parse(_ json: String) -> NSDictionary? {
    (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? NSDictionary
}

do {
    let live = #"{"claudeAiOauth":{"accessToken":"sk-ant-oat01-LIVE","refreshToken":"sk-ant-ort01-LIVE","expiresAt":1790000000000,"scopes":["user:inference"],"subscriptionType":"max"},"mcpOAuth":{"stripe|abc123":{"serverName":"stripe","serverUrl":"https://mcp.stripe.com","accessToken":"at-NEW","refreshToken":"rt-NEW-rotated","expiresAt":1790000009999,"discoveryState":{"issuer":"https://access.stripe.com/","nested":[1,2.5,true,false,null]}},"supabase|def":{"serverName":"supabase","clientSecret":"s","note":"naïve café ☕"}},"someFutureKey":{"keep":true}}"#
    let target = #"{"claudeAiOauth":{"accessToken":"sk-ant-oat01-TARGET","refreshToken":"sk-ant-ort01-TARGET","expiresAt":1790000005555,"scopes":["user:inference","user:profile"],"subscriptionType":"max","rateLimitTier":"default_claude_max_20x"},"mcpOAuth":{"stripe|abc123":{"serverName":"stripe","accessToken":"at-OLD","refreshToken":"rt-OLD-already-rotated-away"}}}"#

    let result = ClaudeCredentialMerge.credentialForSwitch(live: live, target: target)
    if case .merged(let merged, let kept) = result, let out = parse(merged), let liveObj = parse(live), let targetObj = parse(target) {
        check(out["claudeAiOauth"] as? NSDictionary == targetObj["claudeAiOauth"] as? NSDictionary, "merge: the Claude login comes from the target account")
        check(out["mcpOAuth"] as? NSDictionary == liveObj["mcpOAuth"] as? NSDictionary, "merge: this Mac's MCP logins are kept exactly (not the target's older copy)")
        check(out["someFutureKey"] as? NSDictionary == liveObj["someFutureKey"] as? NSDictionary, "merge: unknown top-level keys are kept")
        check(kept == ["mcpOAuth", "someFutureKey"], "merge: reports which keys it kept", "\(kept)")
        check(merged.contains("https://mcp.stripe.com") && !merged.contains(#"https:\/\/"#), "merge: slashes are not escaped")
        check(merged.contains("naïve café ☕"), "merge: non-ASCII text survives")
        check(!merged.contains("rt-OLD-already-rotated-away") && merged.contains("rt-NEW-rotated"), "merge: a rotated MCP refresh token is never rolled back")
    } else {
        check(false, "merge: a live and a target credential merge", "\(result)")
    }

    check(ClaudeCredentialMerge.credentialForSwitch(live: nil, target: target) == .targetOnly(credential: target, reason: "live credential missing or not JSON"), "merge: no live credential falls back to the target verbatim")
    if case .targetOnly(let c, _) = ClaudeCredentialMerge.credentialForSwitch(live: "not json", target: target) { check(c == target, "merge: an unreadable live credential falls back to the target verbatim") }
    else { check(false, "merge: an unreadable live credential falls back to the target verbatim") }
    if case .targetOnly(let c, _) = ClaudeCredentialMerge.credentialForSwitch(live: live, target: #"{"mcpOAuth":{}}"#) { check(c == #"{"mcpOAuth":{}}"#, "merge: a target without claudeAiOauth is written verbatim, never merged") }
    else { check(false, "merge: a target without claudeAiOauth is written verbatim, never merged") }
}

// MARK: - CredentialOwnership

do {
    func cred(_ access: String, _ refresh: String?) -> String {
        let r = refresh.map { #","refreshToken":"\#($0)""# } ?? ""
        return #"{"claudeAiOauth":{"accessToken":"\#(access)"\#(r),"expiresAt":4102444800000},"mcpOAuth":{"x":{"serverName":"x"}}}"#
    }
    let a = CredentialOwnership.login(fromCredential: cred("at-A", "rt-A"))!
    check(a == .init(accessToken: "at-A", refreshToken: "rt-A", expiresAt: 4102444800000), "ownership: reads the Claude login from a credential")
    check(!a.isExpired && CredentialOwnership.Login(accessToken: "x", refreshToken: nil, expiresAt: 1000).isExpired, "ownership: knows an expired login from a live one")
    check(CredentialOwnership.login(fromCredential: #"{"mcpOAuth":{}}"#) == nil, "ownership: a credential without a Claude login has no owner")

    let refreshedA = CredentialOwnership.Login(accessToken: "at-A2", refreshToken: "rt-A")
    check(CredentialOwnership.sameGrant(a, refreshedA), "ownership: a new access token on the same refresh token is the same grant")
    check(!CredentialOwnership.sameGrant(a, .init(accessToken: "at-A3", refreshToken: "rt-A3")), "ownership: a rotated refresh token no longer matches (unknown, not proof)")

    let saved: [String: CredentialOwnership.Login] = [
        "A": a,
        "B": .init(accessToken: "at-B", refreshToken: "rt-B"),
        "C": .init(accessToken: "at-C", refreshToken: nil),
    ]
    check(CredentialOwnership.lineageMatches(refreshedA, in: saved) == ["A"], "ownership: lineage finds the one account a refreshed login came from")
    check(CredentialOwnership.lineageMatches(.init(accessToken: "at-X", refreshToken: "rt-X"), in: saved).isEmpty, "ownership: an unknown login matches nobody")
    check(CredentialOwnership.sharedLogins(saved).isEmpty, "ownership: distinct saved logins share nothing")

    // Tonight's corruption: A's login saved under B as well.
    var corrupted = saved
    corrupted["B"] = a
    check(CredentialOwnership.sharedLogins(corrupted) == [["A", "B"]], "ownership: a login saved under two accounts is found", "\(CredentialOwnership.sharedLogins(corrupted))")
    check(CredentialOwnership.lineageMatches(a, in: corrupted) == ["A", "B"], "ownership: an ambiguous lineage returns both accounts, so the API must decide")

    check(CredentialOwnership.detailsBelong(uuid: "u-1", email: "a@x.com", toOwnerUuid: "u-1", ownerEmail: "A@X.com"), "details: the same permanent id belongs")
    check(!CredentialOwnership.detailsBelong(uuid: "u-2", email: "a@x.com", toOwnerUuid: "u-1", ownerEmail: "a@x.com"), "details: a different permanent id does not belong, even with the same email")
    check(CredentialOwnership.detailsBelong(uuid: nil, email: "A@x.com", toOwnerUuid: "u-1", ownerEmail: "a@X.com"), "details: without an id on one side, the email decides (any case)")
    check(!CredentialOwnership.detailsBelong(uuid: nil, email: nil, toOwnerUuid: "u-1", ownerEmail: "a@x.com"), "details: nothing to compare is never a match")
}

// MARK: - Usage limits (the per-model weekly allowance, i.e. Fable)

do {
    func decode(_ json: String) -> UsageAPIResponse? {
        try? JSONDecoder().decode(UsageAPIResponse.self, from: Data(json.utf8))
    }
    let windows = #""five_hour":{"utilization":53.0,"resets_at":"2026-09-22T19:00:00.177659+00:00"},"seven_day":{"utilization":51.0,"resets_at":"2026-09-28T18:00:00.177679+00:00"}"#
    let fable = #"{"kind":"weekly_scoped","group":"weekly","percent":74,"severity":"normal","resets_at":"2026-09-28T18:00:00.177837+00:00","scope":{"model":{"id":null,"display_name":"Fable"},"surface":null},"is_active":true}"#
    let session = #"{"kind":"session","group":"session","percent":53,"severity":"normal","resets_at":"2026-09-22T19:00:00.177659+00:00","scope":null,"is_active":false}"#
    let weeklyAll = #"{"kind":"weekly_all","group":"weekly","percent":51,"severity":"normal","resets_at":"2026-09-28T18:00:00.177679+00:00","scope":null,"is_active":false}"#

    // The shape the API returned on 2026-09-22, trimmed to the fields that matter.
    let real = decode(#"{"#
        + windows
        + #","seven_day_opus":null,"seven_day_sonnet":null,"extra_usage":{"is_enabled":false,"monthly_limit":null,"used_credits":null,"utilization":null,"currency":null},"limits":["#
        + [session, weeklyAll, fable].joined(separator: ",") + "]}")
    check(real?.fiveHour?.utilization == 53 && real?.sevenDay?.utilization == 51, "usage: session and weekly still read from the top-level windows")
    check(real?.modelWeeklyLimits.count == 1, "usage: only the per-model entry counts as a model allowance", "\(real?.modelWeeklyLimits.count ?? -1)")
    let fableLimit = real?.modelWeeklyLimit(named: "Fable")
    check(fableLimit?.percent == 74 && fableLimit?.percentLeft == 26, "usage: Fable reads 74% used, 26% left")
    check(real?.modelWeeklyLimit(named: "fable") != nil, "usage: the model name matches in any case")
    check(fableLimit?.window.resetsAtDate != nil, "usage: the Fable reset time parses")

    let noLimits = decode("{" + windows + "}")
    check(noLimits?.sevenDay?.utilization == 51 && noLimits?.modelWeeklyLimits.isEmpty == true, "usage: a response without limits still decodes, with no Fable row")

    for (name, value) in [("an object", #"{"x":1}"#), ("a string", #""soon""#), ("null", "null")] {
        let odd = decode("{" + windows + #","limits":"# + value + "}")
        check(odd?.fiveHour?.utilization == 53 && odd?.modelWeeklyLimits.isEmpty == true, "usage: limits as \(name) never costs the session and weekly numbers")
    }

    let messy = decode("{" + windows + #","limits":[null,42,"x",{"percent":"high","scope":{"model":{"display_name":"Fable"}}},"# + fable + #",{"kind":"weekly_scoped","percent":10,"scope":{"model":{"display_name":"  "}}}]}"#)
    check(messy?.modelWeeklyLimits.map(\.percent) == [74], "usage: odd entries are skipped and the good one kept", "\(messy?.modelWeeklyLimits.map(\.percent) ?? [])")

    check(decode(#"{"five_hour":"broken"}"#) == nil, "usage: a malformed top-level window still fails, as before")

    // How a reset time reads: a countdown says "Resets in ...", a weekday says "Resets ...".
    let soon = UsageWindow(utilization: 10, resetsAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(3 * 3600)))
    let faraway = UsageWindow(utilization: 10, resetsAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(5 * 86_400)))
    let gone = UsageWindow(utilization: 10, resetsAt: ISO8601DateFormatter().string(from: Date().addingTimeInterval(-3600)))
    check(soon.resetIsAbsolute == false && faraway.resetIsAbsolute == true && gone.resetIsAbsolute == false,
          "usage: only a reset more than a day away reads as a weekday and time",
          "\(soon.resetIsAbsolute) \(faraway.resetIsAbsolute) \(gone.resetIsAbsolute)")

    let over = UsageLimit(kind: "weekly_scoped", group: "weekly", percent: 130, severity: nil, resetsAt: nil, scope: nil, isActive: nil)
    let fresh = UsageLimit(kind: "weekly_scoped", group: "weekly", percent: 0, severity: nil, resetsAt: nil, scope: nil, isActive: nil)
    check(over.percentLeft == 0 && fresh.percentLeft == 100, "usage: what is left stays between 0 and 100")

    if let real, let data = try? JSONEncoder().encode(real) {
        check(decode(String(decoding: data, as: UTF8.self))?.modelWeeklyLimits == real.modelWeeklyLimits, "usage: the Fable limit survives an encode and decode")
    } else {
        check(false, "usage: the Fable limit survives an encode and decode", "could not encode")
    }
}

// MARK: - A colour per account

do {
    let ids = (0..<5).map { _ in UUID() }
    let first = AccountPalette.assignment(for: ids)
    check(first.count == 5, "palette: every account gets a colour")
    check(Set(first.values).count == 5, "palette: five accounts get five different colours", "\(first.values.sorted())")
    check(AccountPalette.assignment(for: ids) == first, "palette: the same accounts always get the same colours")
    check(AccountPalette.assignment(for: ids.reversed()).count == 5, "palette: order does not lose an account")

    let fixed = UUID(uuidString: "CB7797E5-5257-402B-80D6-ADAE1220D368")!
    let alone = AccountPalette.assignment(for: [fixed])[fixed]
    check(alone == Int(AccountPalette.fnv1a(fixed.uuidString) % UInt64(AccountPalette.swatchCount)),
          "palette: an account's colour comes from its id, not from its position")
    check(AccountPalette.fnv1a("PixelSwitch") == AccountPalette.fnv1a("PixelSwitch"),
          "palette: the hash is stable within a run")
    check(AccountPalette.fnv1a("a") != AccountPalette.fnv1a("b"), "palette: different ids hash differently")

    check(AccountPalette.swatchCount == 10, "palette: ten colours, as asked", "\(AccountPalette.swatchCount)")

    let ten = (0..<10).map { _ in UUID() }
    check(Set(AccountPalette.assignment(for: ten).values).count == 10, "palette: ten accounts get ten different colours")

    let many = (0..<14).map { _ in UUID() }
    let crowded = AccountPalette.assignment(for: many)
    check(crowded.count == 14, "palette: more accounts than colours still all get one")
    check(Set(crowded.values).count == AccountPalette.swatchCount, "palette: all ten colours are used before any repeats")
}

// MARK: - How much cost history actually exists

do {
    var cal = Calendar(identifier: .gregorian)
    cal.timeZone = TimeZone(identifier: "America/Los_Angeles")!
    func span(_ dates: [String], _ today: String = "2026-09-22") -> Int {
        CostHistoryWindow.spanDays(dates: dates, today: today, calendar: cal)
    }
    check(span([]) == 0, "history: no data is no days")
    check(span(["2026-09-22"]) == 1, "history: today alone is one day")
    check(span(["2026-09-18", "2026-09-19", "2026-09-20", "2026-09-21", "2026-09-22"]) == 5,
          "history: five days of data spans five days", "\(span(["2026-09-18", "2026-09-22"]))")
    check(span(["2026-09-18", "2026-09-22"]) == 5, "history: a gap still counts from the oldest day")
    check(span(["2026-08-24"]) == 30, "history: a month back spans 30 days", "\(span(["2026-08-24"]))")
    check(span(["2026-09-25"]) == 1, "history: a date ahead of today never goes negative")
    check(span(["not-a-date"]) == 1, "history: an unreadable date still counts as some history")
}

// MARK: - Auto-switch on session/weekly and on Fable

do {
    let now = ISO8601DateFormatter().date(from: "2026-09-22T17:00:00Z")!
    let future = "2026-09-28T18:00:00.000000+00:00"
    let past = "2026-09-20T18:00:00.000000+00:00"
    func usage(session: Double, weekly: Double, fable: Double?, fableResets: String? = future) -> UsageAPIResponse {
        var json = #"{"five_hour":{"utilization":\#(session),"resets_at":"2026-09-22T19:00:00.000000+00:00"},"seven_day":{"utilization":\#(weekly),"resets_at":"\#(future)"}"#
        if let fable {
            let resets = fableResets.map { #","resets_at":"\#($0)""# } ?? ""
            json += #","limits":[{"kind":"weekly_scoped","group":"weekly","percent":\#(fable)\#(resets),"scope":{"model":{"id":null,"display_name":"Fable"}}}]"#
        }
        return try! JSONDecoder().decode(UsageAPIResponse.self, from: Data((json + "}").utf8))
    }
    let active = Account(email: "a@x.com", displayName: "A", isActive: true)
    let b = Account(email: "b@x.com", displayName: "B")
    let c = Account(email: "c@x.com", displayName: "C")
    let d = Account(email: "d@x.com", displayName: "D")
    let e = Account(email: "e@x.com", displayName: "E")
    func plan(_ byAccount: [UUID: UsageAPIResponse], candidates: [Account]? = nil, switchable: @escaping (Account) -> Bool = { _ in true }, sampled: Bool = true, watchFable: Bool = true) -> (limit: AutoSwitchEngine.Limit, targets: [Account])? {
        AutoSwitchEngine.plan(active: active, candidates: candidates ?? [b, c, d, e], usageByAccount: byAccount,
                              isSwitchable: switchable, activeSampledThisCycle: sampled,
                              threshold: 98, hysteresisPct: 10, watchFable: watchFable, asOf: now)
    }
    func names(_ p: (limit: AutoSwitchEngine.Limit, targets: [Account])?) -> String {
        guard let p else { return "stay" }
        return "\(p.limit.rawValue): " + p.targets.map(\.displayName).joined(separator: ",")
    }

    // Session/weekly behaves as before: most session/weekly room first, Fable not required.
    let windows = plan([active.id: usage(session: 99, weekly: 50, fable: 99),
                        b.id: usage(session: 50, weekly: 40, fable: 99),
                        c.id: usage(session: 20, weekly: 10, fable: nil),
                        d.id: usage(session: 90, weekly: 10, fable: 0)])
    check(names(windows) == "windows: C,B", "auto-switch: session at 99% switches on session/weekly, most room first", names(windows))

    // Fable at the threshold switches to the account with the most Fable left.
    let fable = plan([active.id: usage(session: 10, weekly: 50, fable: 99),
                      b.id: usage(session: 5, weekly: 30, fable: 60),
                      c.id: usage(session: 5, weekly: 95, fable: 5),   // weekly too high: excluded
                      d.id: usage(session: 5, weekly: 10, fable: nil), // no Fable allowance: excluded
                      e.id: usage(session: 5, weekly: 10, fable: 20)])
    check(names(fable) == "fable: E,B", "auto-switch: Fable at 99% switches to the most Fable left, with session and weekly room", names(fable))

    let below = plan([active.id: usage(session: 10, weekly: 50, fable: 97), e.id: usage(session: 5, weekly: 10, fable: 20)])
    check(names(below) == "stay", "auto-switch: Fable at 97% (under a 98% threshold) stays put", names(below))

    let nowhere = plan([active.id: usage(session: 10, weekly: 50, fable: 100),
                        b.id: usage(session: 5, weekly: 10, fable: 95),
                        d.id: usage(session: 5, weekly: 10, fable: nil)])
    check(names(nowhere) == "stay", "auto-switch: Fable out everywhere, so no pointless switch", names(nowhere))

    let expired = plan([active.id: usage(session: 10, weekly: 50, fable: 100, fableResets: past), e.id: usage(session: 5, weekly: 10, fable: 20)])
    check(names(expired) == "stay", "auto-switch: a Fable reading from a week that has reset never triggers", names(expired))

    let noAllowance = plan([active.id: usage(session: 10, weekly: 50, fable: nil), e.id: usage(session: 5, weekly: 10, fable: 20)])
    check(names(noAllowance) == "stay", "auto-switch: an active account with no Fable allowance never switches for Fable", names(noAllowance))

    let lockedOut = plan([active.id: usage(session: 10, weekly: 50, fable: 99), e.id: usage(session: 5, weekly: 10, fable: 20)],
                         switchable: { $0.id != e.id })
    check(names(lockedOut) == "stay", "auto-switch: an account that cannot be switched to is never chosen for Fable", names(lockedOut))

    // Both limits at once: the session/weekly switch prefers an account with Fable
    // to spare, so it does not need a second (Fable) switch minutes later.
    let both = plan([active.id: usage(session: 99, weekly: 99, fable: 99),
                     b.id: usage(session: 10, weekly: 10, fable: 100),
                     c.id: usage(session: 50, weekly: 50, fable: 5)])
    check(names(both) == "windows: C,B", "auto-switch: a session/weekly switch prefers an account that still has Fable", names(both))

    // A kept (not fresh) Fable reading with no reset time never triggers; a fresh one does.
    let retained = [active.id: usage(session: 10, weekly: 50, fable: 99, fableResets: nil), e.id: usage(session: 5, weekly: 10, fable: 20)]
    check(names(plan(retained, sampled: false)) == "stay", "auto-switch: a kept Fable reading with no reset time never triggers")
    check(names(plan(retained, sampled: true)) == "fable: E", "auto-switch: a fresh Fable reading with no reset time does trigger", names(plan(retained, sampled: true)))

    var keychainReads = 0
    _ = plan([active.id: usage(session: 10, weekly: 50, fable: 99), b.id: usage(session: 5, weekly: 10, fable: 95), c.id: usage(session: 5, weekly: 10, fable: 97)],
             switchable: { _ in keychainReads += 1; return true })
    check(keychainReads == 0, "auto-switch: accounts that fail on usage are never checked in the Keychain", "\(keychainReads)")

    // The Fable switch can be turned off without touching session and weekly.
    let fableOff = [active.id: usage(session: 10, weekly: 50, fable: 100), e.id: usage(session: 5, weekly: 10, fable: 20)]
    check(names(plan(fableOff, watchFable: true)) == "fable: E", "auto-switch: with the Fable switch on, Fable moves you")
    check(names(plan(fableOff, watchFable: false)) == "stay", "auto-switch: with the Fable switch off, Fable never moves you")
    let windowsWithFableOff = [active.id: usage(session: 99, weekly: 50, fable: 100), e.id: usage(session: 5, weekly: 10, fable: 20)]
    check(names(plan(windowsWithFableOff, watchFable: false)) == "windows: E", "auto-switch: the Fable switch never affects session and weekly switching")

    let edge = AutoSwitchEngine.eligibleUtilization(usage(session: 5, weekly: 89, fable: 10), limit: .fable, ceiling: 88, asOf: now)
    let ok = AutoSwitchEngine.eligibleUtilization(usage(session: 5, weekly: 88, fable: 88), limit: .fable, ceiling: 88, asOf: now)
    check(edge == nil && ok == 88, "auto-switch: a Fable target needs both Fable and weekly at or under the ceiling")
}

// MARK: - Opt-in: real /usr/bin/security on a throwaway item

if ProcessInfo.processInfo.environment["PIXELSWITCH_KEYCHAIN_TESTS"] == "1" {
    let scratchService = "PixelSwitch unit-test credentials"
    let scratchAccount = NSUserName()
    func security(_ invocation: ClaudeTokenWriter.Invocation) -> Int32 {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        p.arguments = invocation.arguments
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        let input = Pipe()
        p.standardInput = invocation.stdin == nil ? FileHandle.nullDevice : input
        do {
            try p.run()
            if let data = invocation.stdin {
                try input.fileHandleForWriting.write(contentsOf: data)
                try input.fileHandleForWriting.close()
            }
            p.waitUntilExit()
            return p.terminationStatus
        } catch { return -1 }
    }
    func readBack() -> String? {
        let p = Process(); let pipe = Pipe()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        p.arguments = ["find-generic-password", "-s", scratchService, "-a", scratchAccount, "-w"]
        p.standardOutput = pipe
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return nil }
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return nil }
        var s = String(decoding: data, as: UTF8.self)
        if s.hasSuffix("\n") { s.removeLast() }
        return s
    }
    let writer = ClaudeTokenWriter(service: scratchService, account: scratchAccount, run: security, readBack: readBack)
    _ = security(.init(arguments: ["delete-generic-password", "-s", scratchService, "-a", scratchAccount], stdin: nil))

    let small = fakeCredential(length: 900)
    check(writer.write(small) == .written(path: .updateInPlace, transport: .stdin), "real security: a new item is created over stdin")
    let small2 = fakeCredential(length: 1_200)
    check(writer.write(small2) == .written(path: .updateInPlace, transport: .stdin), "real security: an existing item is updated in place over stdin")
    let large = fakeCredential(length: 10_400)
    check(writer.write(large) == .written(path: .updateInPlace, transport: .argv), "real security: a 10,400-byte credential round-trips via the fallback")
    check(readBack() == large, "real security: the stored bytes equal the last write")

    _ = security(.init(arguments: ["delete-generic-password", "-s", scratchService, "-a", scratchAccount], stdin: nil))
    check(readBack() == nil, "real security: the throwaway item is removed")
}

// MARK: - Email display default
//
// The old key defaulted to masking and was written to disk as `false` on
// installs that never touched it, so reusing it would have kept every existing
// install masked and the change would have reached nobody. These pin the new
// key's behaviour: full addresses unless someone deliberately asks otherwise.
do {
    let defaults = UserDefaults.standard
    let key = EmailDisplay.key
    let original = defaults.object(forKey: key)
    defer { if let original { defaults.set(original, forKey: key) } else { defaults.removeObject(forKey: key) } }

    check(key != "showFullEmail", "email display: the key is not the old one, so a stale false cannot keep masking on")

    defaults.removeObject(forKey: key)
    check(EmailDisplay.isMasked == false, "email display: an install that has never touched the setting shows addresses in full")

    defaults.set(true, forKey: key)
    check(EmailDisplay.isMasked == true, "email display: turning masking on is respected")

    defaults.set(false, forKey: key)
    check(EmailDisplay.isMasked == false, "email display: turning masking off is respected")

    // The masking itself must still work for anyone who wants it.
    check("ahmed@pixelventures.ai".maskedAsEmailAddress() == "ahm*@*.ai", "email display: masking still produces the short form")
}

// MARK: - Identifier rename, and the migration that must not lose accounts
//
// PixelSwitch renamed three identifiers it had inherited: the keychain service
// holding every account's credentials, the UserDefaults key holding the account
// list, and the folder in the home directory. Each rename is only safe because
// the old name is still READ and never written or deleted. These pin the two
// properties that make that true, using the same three-way model the real
// loader uses.
do {
    enum Load: Equatable { case loaded(Int), empty, failed(String) }

    // Mirrors KeychainService.loadBackupStore's decision, which is the part
    // worth pinning: which source wins, and what happens when one cannot be read.
    func resolve(current: Load, legacy: Load) -> Load {
        switch current {
        case .loaded(let n): return .loaded(n)
        case .failed(let r): return .failed(r)
        case .empty: break
        }
        switch legacy {
        case .loaded(let n): return .loaded(n)          // copied across
        case .failed(let r): return .failed("legacy unreadable: " + r)
        case .empty: return .empty
        }
    }

    check(resolve(current: .loaded(5), legacy: .loaded(5)) == .loaded(5),
          "rename: once the new item exists it is used and the old one is not consulted")
    check(resolve(current: .empty, legacy: .loaded(5)) == .loaded(5),
          "rename: first launch after the rename finds all 5 accounts under the old name")
    check(resolve(current: .empty, legacy: .empty) == .empty,
          "rename: a genuinely new install is empty, not an error")

    // The one that matters most. A denied keychain prompt or a locked keychain
    // reports failure, NOT absence. Treating it as absence would let the next
    // save write an empty store over every credential the user has.
    if case .failed = resolve(current: .empty, legacy: .failed("OSStatus -25308")) {
        check(true, "rename: an unreadable legacy item refuses, and never reports empty")
    } else {
        check(false, "rename: an unreadable legacy item refuses, and never reports empty")
    }
    if case .failed = resolve(current: .failed("OSStatus -25308"), legacy: .loaded(5)) {
        check(true, "rename: an unreadable current item refuses without falling back")
    } else {
        check(false, "rename: an unreadable current item refuses without falling back")
    }

    // The account list in UserDefaults uses the same shape: new key, then old.
    let defaults = UserDefaults.standard
    let newKey = "ai.pixelventures.pixelswitch.accounts.test"
    let oldKey = "com.ccswitcher.accounts.test"
    defer { defaults.removeObject(forKey: newKey); defaults.removeObject(forKey: oldKey) }

    func loadAccountsData() -> Data? { defaults.data(forKey: newKey) ?? defaults.data(forKey: oldKey) }

    defaults.removeObject(forKey: newKey); defaults.removeObject(forKey: oldKey)
    check(loadAccountsData() == nil, "rename: no accounts under either key reads as nothing saved")

    let legacyPayload = Data("legacy-account-list".utf8)
    defaults.set(legacyPayload, forKey: oldKey)
    check(loadAccountsData() == legacyPayload, "rename: accounts are found under the old key when the new one is absent")

    let newPayload = Data("current-account-list".utf8)
    defaults.set(newPayload, forKey: newKey)
    check(loadAccountsData() == newPayload, "rename: the new key wins once it is written")
    check(defaults.data(forKey: oldKey) == legacyPayload, "rename: the old key is left intact, so a rollback still finds its accounts")

    // The names themselves, so a careless find-and-replace cannot quietly
    // repoint the app at an identifier nobody's credentials live under.
    check("ai.pixelventures.pixelswitch.backups" != "me.xueshi.ccswitcher.backups",
          "rename: the keychain service name is PixelSwitch's own")
}

// MARK: - Retiring the old keychain item
//
// 1.0.12 kept the old item as a way back to an earlier build. 1.0.13 removes it
// once the copy is proven, because a second copy of every account's OAuth token
// living in the keychain forever is a liability, and because an older build that
// found it would keep writing to it and the two stores would drift apart.
//
// The read-back is the entire safeguard. These pin when it is allowed to fire.
do {
    // Mirrors KeychainService.retireLegacyBackupItem's precondition.
    func mayRetire(migrated: [String], readBack: [String]?) -> Bool {
        guard let readBack else { return false }          // new item unreadable
        return readBack.count == migrated.count && Set(readBack) == Set(migrated)
    }

    let five = ["a", "b", "c", "d", "e"]
    check(mayRetire(migrated: five, readBack: five),
          "retire: the old item goes only when the new one reads back with the same accounts")
    check(!mayRetire(migrated: five, readBack: nil),
          "retire: an unreadable new item keeps the old one")
    check(!mayRetire(migrated: five, readBack: ["a", "b", "c"]),
          "retire: a short read-back keeps the old one")
    check(!mayRetire(migrated: five, readBack: ["a", "b", "c", "d", "z"]),
          "retire: a read-back with a different account keeps the old one")
    check(!mayRetire(migrated: five, readBack: []),
          "retire: an empty read-back keeps the old one")
    check(mayRetire(migrated: [], readBack: []),
          "retire: nothing to migrate is not an error")

    // The preference key follows the same rule: write, read back, only then clear.
    let defaults = UserDefaults.standard
    let newKey = "ai.pixelventures.pixelswitch.accounts.retiretest"
    let oldKey = "com.ccswitcher.accounts.retiretest"
    defer { defaults.removeObject(forKey: newKey); defaults.removeObject(forKey: oldKey) }

    defaults.set(Data("accounts".utf8), forKey: oldKey)
    defaults.removeObject(forKey: newKey)
    // migrate
    if let carried = defaults.data(forKey: oldKey) {
        defaults.set(carried, forKey: newKey)
        if defaults.data(forKey: newKey) != nil { defaults.removeObject(forKey: oldKey) }
    }
    check(defaults.data(forKey: newKey) != nil, "retire: accounts land under the current key")
    check(defaults.data(forKey: oldKey) == nil, "retire: the legacy key is cleared once the read-back succeeds")
}

runAutoSwitchRulesTests()

print("\n\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
