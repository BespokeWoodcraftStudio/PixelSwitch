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

    let over = UsageLimit(kind: "weekly_scoped", group: "weekly", percent: 130, severity: nil, resetsAt: nil, scope: nil, isActive: nil)
    let fresh = UsageLimit(kind: "weekly_scoped", group: "weekly", percent: 0, severity: nil, resetsAt: nil, scope: nil, isActive: nil)
    check(over.percentLeft == 0 && fresh.percentLeft == 100, "usage: what is left stays between 0 and 100")

    if let real, let data = try? JSONEncoder().encode(real) {
        check(decode(String(decoding: data, as: UTF8.self))?.modelWeeklyLimits == real.modelWeeklyLimits, "usage: the Fable limit survives an encode and decode")
    } else {
        check(false, "usage: the Fable limit survives an encode and decode", "could not encode")
    }
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

print("\n\(passed) passed, \(failed) failed")
exit(failed == 0 ? 0 : 1)
