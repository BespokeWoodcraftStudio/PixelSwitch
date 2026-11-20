// The age line on a Usage card ("Updated 4 min ago") and the rule that keeps the
// footer from flickering (PixelSwitch/Models/FooterAge.swift).
//
// `CardFooterRow` picks a layout variant by the width of the age text. A text that
// changes width between two ticks of the clock makes that choice flip at a fit
// boundary, so the rule pinned here is: the age is a whole number of minutes, and
// for every instant inside one minute the text is identical, in every language.
// The view side (a TimelineView that redraws on the minute) cannot compile in this
// harness, so a few source pins tie it to the pure rule.
//
// Compiled into the unit harness by Tests/run-unit-tests.sh and run from
// main.swift. Uses the global `check(_:_:_:)` defined there.
import Foundation

private let ageLanguages = ["en", "de", "fr", "ja", "zh-Hans"]

@MainActor func runFooterAgeTests() {
    footerAgeMinuteTests()
    footerAgeStabilityTests()
    footerAgeScheduleTests()
    footerAgeWordingTests()
    footerAgeSourcePins()
}

/// Spaces of every width compare equal, so a newer system's no-break space does not fail a wording check.
private func plain(_ text: String) -> String {
    text.replacingOccurrences(of: "\u{00A0}", with: " ").replacingOccurrences(of: "\u{202F}", with: " ")
}

// MARK: - Whole minutes

@MainActor private func footerAgeMinuteTests() {
    let sampled = Date(timeIntervalSince1970: 1_790_000_000)
    func at(_ seconds: TimeInterval) -> Int { FooterAge.minutes(since: sampled, now: sampled.addingTimeInterval(seconds)) }
    check(at(0) == 0 && at(59.9) == 0, "footer age: under a minute is 0 minutes")
    check(at(60) == 1 && at(89) == 1 && at(119.9) == 1, "footer age: a card shows its age from 90 s, and 90 s is still 1 minute")
    check(at(120) == 2 && at(3599) == 59 && at(3600) == 60 && at(86_400) == 1440, "footer age: minutes are floored, not rounded")
    check(at(-30) == 0 && at(-86_400) == 0, "footer age: a reading stamped in the future is 0 minutes old, never negative")
    check(FooterAge.minutes(since: .distantPast, now: .distantFuture) <= 10_000_000, "footer age: a nonsense span is capped, never traps")

    // A boundary computed as sampledAt + 60 * k must read back as exactly k, whatever
    // the fraction in the stamp, or the text would lag one minute at every tick.
    var exact = true, justBefore = true
    for base in [1_790_000_000.0, 1_790_000_123.4567, 1_789_999_999.999, 1_700_000_000.123456, 1_900_000_000.5] {
        let start = Date(timeIntervalSince1970: base)
        for k in 1...3000 {
            let boundary = start.addingTimeInterval(TimeInterval(k) * 60)
            if FooterAge.minutes(since: start, now: boundary) != k { exact = false }
            if FooterAge.minutes(since: start, now: boundary.addingTimeInterval(-0.5)) != k - 1 { justBefore = false }
        }
    }
    check(exact, "footer age: an instant exactly k minutes after the reading is k minutes, for 3000 boundaries at five stamps")
    check(justBefore, "footer age: half a second before a boundary is still the minute before")
}

// MARK: - The stability rule

@MainActor private func footerAgeStabilityTests() {
    let sampled = Date(timeIntervalSince1970: 1_790_000_007.25)
    let minutesToCheck = [1, 2, 9, 10, 59, 60, 61, 119, 120, 599, 600, 779, 1439, 1440, 1441, 2879, 10_079, 100_000]
    var unstable: [String] = []
    var checkedInstants = 0
    for language in ageLanguages {
        let locale = Locale(identifier: language)
        for k in minutesToCheck {
            let boundary = sampled.addingTimeInterval(TimeInterval(k) * 60)
            let expected = FooterAge.text(since: sampled, now: boundary, locale: locale)
            // Every instant inside this minute, on a fine grid, reads the same text.
            var offset = 0.0
            while offset < 59.99 {
                let text = FooterAge.text(since: sampled, now: boundary.addingTimeInterval(offset), locale: locale)
                checkedInstants += 1
                if text != expected { unstable.append("\(language) \(k) min +\(offset)s: \(text) vs \(expected)") }
                offset += 0.37
            }
            if expected != FooterAge.text(minutes: k, locale: locale) { unstable.append("\(language) \(k): text(since:) differs from text(minutes:)") }
        }
    }
    check(unstable.isEmpty && checkedInstants > 5 * 18 * 150,
          "footer age: the text is identical for every instant inside a minute, in all five languages (\(checkedInstants) instants)",
          unstable.prefix(3).joined(separator: " | "))

    // So the width, and the variant ViewThatFits chose from it, can change only at a boundary.
    // The footer's own clock is the same two numbers: floor and next boundary.
    var nextOK = true
    for k in [0, 1, 2, 59, 600, 2879] {
        let boundary = sampled.addingTimeInterval(TimeInterval(k) * 60)
        for offset in [0.0, 0.001, 1, 30, 59, 59.99] {
            let now = boundary.addingTimeInterval(offset)
            let next = FooterAge.nextChange(since: sampled, after: now)
            let wantedStep = FooterAge.minutes(since: sampled, now: now) + 1
            if !(next > now) || FooterAge.minutes(since: sampled, now: next) != wantedStep
                || abs(next.timeIntervalSince(sampled) - TimeInterval(wantedStep) * 60) > 1e-6
                || next.timeIntervalSince(now) > 60.0 + 1e-6 { nextOK = false }
        }
    }
    check(nextOK, "footer age: the next change is later than now, at most a minute away, and lands on the reading's next whole minute")
    check(FooterAge.nextChange(since: sampled, after: sampled.addingTimeInterval(-500)) > sampled.addingTimeInterval(-500),
          "footer age: a reading stamped in the future still names a later instant for the next change")

    // The one seam: the text of two neighbouring minutes is what changes, and only when the age really moved on.
    let en = Locale(identifier: "en")
    check(FooterAge.text(minutes: 4, locale: en) != FooterAge.text(minutes: 5, locale: en)
          && FooterAge.text(minutes: 779, locale: en) != FooterAge.text(minutes: 780, locale: en),
          "footer age: the age does move on when a minute is up")
}

// MARK: - The schedule the view follows

@MainActor private func footerAgeScheduleTests() {
    // `FooterAgeSchedule` (a TimelineSchedule, in the view file) is
    // `sequence(first: start) { FooterAge.nextChange(since:after:) }`. Walk the same chain.
    let sampled = Date(timeIntervalSince1970: 1_790_000_031.5)
    for startOffset in [95.0, 119.9, 120.0, 3_600.3, 46_740.7] {
        let start = sampled.addingTimeInterval(startOffset)
        var entries: [Date] = [start]
        for _ in 0..<300 { entries.append(FooterAge.nextChange(since: sampled, after: entries[entries.count - 1])) }
        let increasing = zip(entries, entries.dropFirst()).allSatisfy { $0 < $1 }
        let startMinute = FooterAge.minutes(since: sampled, now: start)
        let steps = entries.indices.dropFirst().allSatisfy { FooterAge.minutes(since: sampled, now: entries[$0]) == startMinute + $0 }
        // After the first entry (the moment the view appears), every redraw is a whole minute after the last one.
        let gaps = zip(entries.dropFirst(), entries.dropFirst(2)).allSatisfy { abs($1.timeIntervalSince($0) - 60) < 1e-6 }
        check(increasing && steps && gaps && entries[1].timeIntervalSince(start) <= 60 + 1e-6,
              "footer age: the redraw schedule from \(startOffset) s ticks once a minute on the minute, 300 ticks, each one minute older")
    }
}

// MARK: - The words

@MainActor private func footerAgeWordingTests() {
    let en = Locale(identifier: "en")
    let de = Locale(identifier: "de")
    let fr = Locale(identifier: "fr")
    check(plain(FooterAge.text(minutes: 4, locale: en)) == "4 min", "footer age words: en 4 minutes", FooterAge.text(minutes: 4, locale: en))
    check(plain(FooterAge.text(minutes: 779, locale: en)) == "12 hr, 59 min", "footer age words: en 12 hr, 59 min", FooterAge.text(minutes: 779, locale: en))
    check(plain(FooterAge.text(minutes: 2879, locale: en)) == "1 day, 23 hr",
          "footer age words: a day or more shows days and hours, cut not rounded (1 day, 23 hr and 59 min is not '2 days')", FooterAge.text(minutes: 2879, locale: en))
    check(plain(FooterAge.text(minutes: 60, locale: en)) == "1 hr" && plain(FooterAge.text(minutes: 0, locale: en)) == "0 min",
          "footer age words: whole hours drop the empty minutes, and the first minute reads 0 min")
    check(plain(FooterAge.text(minutes: 779, locale: de)).hasPrefix("12 Std") && plain(FooterAge.text(minutes: 779, locale: fr)).hasPrefix("12 h"),
          "footer age words: de and fr use their own unit words", "\(FooterAge.text(minutes: 779, locale: de)) | \(FooterAge.text(minutes: 779, locale: fr))")

    var secondsFound: [String] = []
    var missing: [String] = []
    for language in ageLanguages {
        let locale = Locale(identifier: language)
        for k in [1, 90, 779, 2879, 10_079] {
            let text = FooterAge.text(minutes: k, locale: locale)
            if text.isEmpty { missing.append("\(language) \(k)") }
            if ["sec", "Sek", "秒"].contains(where: { text.contains($0) }) { secondsFound.append("\(language) \(k): \(text)") }
            if text.contains("\u{2014}") { missing.append("em dash in \(language) \(k)") }
        }
    }
    check(missing.isEmpty && secondsFound.isEmpty, "footer age words: never empty, never seconds, no em dash, in all five languages",
          (missing + secondsFound).joined(separator: " | "))

    let sampled = Date(timeIntervalSince1970: 1_790_000_000)
    check(plain(FooterAge.sentence(template: "Updated %@ ago", since: sampled, now: sampled.addingTimeInterval(4 * 60 + 41), locale: en)) == "Updated 4 min ago",
          "footer age words: the sentence puts the age into the card's own 'Updated %@ ago' once")
}

// MARK: - Wiring the view test cannot compile

private func ageSource(_ path: String) -> String {
    (try? String(contentsOfFile: path, encoding: .utf8)) ?? ""
}

@MainActor private func footerAgeSourcePins() {
    let label = ageSource("PixelSwitch/Views/Components/FooterAgeLabel.swift")
    let row = ageSource("PixelSwitch/Views/Components/CardFooterRow.swift")
    let dashboard = ageSource("PixelSwitch/Views/UsageDashboardView.swift")
    let shots = ageSource("Tools/screenshots/make_screenshots.swift")
    let script = ageSource("Tests/run-unit-tests.sh")
    check(!label.isEmpty && !row.isEmpty && !dashboard.isEmpty && !shots.isEmpty, "footer age pins: the sources are found from the repo root")

    // The card's age is the minute-aligned label, never a text that counts up by the second.
    guard let start = dashboard.range(of: "private func sampleAgeLabel(") else {
        check(false, "footer age pins: sampleAgeLabel is found")
        return
    }
    let member = String(dashboard[start.lowerBound...].prefix(700))
    let memberEnd = member.range(of: "    @ViewBuilder\n    private func accountHeader(").map { String(member[..<$0.lowerBound]) } ?? member
    check(memberEnd.contains("LiveFooterAge(sampledAt: sampledAt)") && !memberEnd.contains(".relative") && !memberEnd.contains("Text("),
          "footer age pins: the card's age is LiveFooterAge, not Text(date, style: .relative)")
    check(label.contains("TimelineView(FooterAgeSchedule(sampledAt: sampledAt))")
          && label.contains("sequence(first: startDate) { FooterAge.nextChange(since: sampledAt, after: $0) }")
          && label.contains("now: context.date"),
          "footer age pins: the label redraws on FooterAge's own boundaries and reads its minute from the schedule's date")
    check(!label.contains(".relative") && !label.contains(".periodic(") && !label.contains("Timer") && !label.contains("Date()"),
          "footer age pins: the label has no live-counting text and no clock of its own")
    check(label.contains(".lineLimit(1)") && !label.contains("minimumScaleFactor") && !label.contains("truncationMode")
          && label.contains("template: String(localized: \"Updated %@ ago\", bundle: L10n.bundle)") && label.contains("locale: L10n.locale"),
          "footer age pins: the age is one line, never scaled, in the app's language and locale")
    check(!row.contains(".relative") && !row.contains("TimelineView") && !row.contains("Date()") && row.contains("FooterAge"),
          "footer age pins: the row measures whatever age it is given and has no clock of its own; its comment names the rule")

    // The screenshot harness draws the same label with the same formatter.
    check(shots.contains("FooterAgeLabel(text: strings.age(relative))") && shots.contains("FooterAge.text(minutes:")
          && shots.contains("CardFooterRow(") && shots.contains("PixelSwitch/Views/Components/FooterAgeLabel.swift PixelSwitch/Models/FooterAge.swift"),
          "footer age pins: the screenshot harness draws the app's FooterAgeLabel and the app's FooterAge words through the shared CardFooterRow")
    check(script.contains("PixelSwitch/Models/FooterAge.swift") && script.contains("Tests/UnitTests/FooterAgeTests.swift"),
          "footer age pins: the unit harness compiles FooterAge and these tests")
}
