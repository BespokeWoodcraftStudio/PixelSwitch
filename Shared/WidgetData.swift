import Foundation
import Security

/// Data shared between the main app and widget via direct file in the widget's sandbox container.
///
/// The main app (non-sandboxed) writes a JSON file into the widget extension's container directory.
/// The widget (sandboxed) reads from its own Application Support, which maps to the same path.
struct WidgetAccountData: Codable {
    let email: String          // pre-obfuscated
    let displayName: String    // pre-obfuscated
    let subscriptionType: String?
    let isActive: Bool
    let sessionUtilization: Double?
    let sessionResetTime: String?
    let weeklyUtilization: Double?
    let weeklyResetTime: String?
    let extraUsageEnabled: Bool?
    let hasError: Bool
    let errorMessage: String?
}

struct WidgetData: Codable {
    let accounts: [WidgetAccountData]
    let todayCost: Double
    let conversationTurns: Int
    let activeCodingTime: String
    let linesWritten: Int
    let modelUsage: [String: Int]
    let lastUpdated: Date

    // Team-ID-prefixed App Group (`$(TeamIdentifierPrefix)ai.pixelventures.pixelswitch`
    // in project.yml). macOS Sequoia (15+) prompts for App Management on
    // `group.<bundle-id>` style identifiers; the `<TEAMID>.<bundle-id>` form is
    // auto-authorized for Developer-ID-signed apps without a provisioning
    // profile and avoids the prompt entirely.
    //
    // The ID is read from this process's own signed entitlements rather than
    // hard-coded, so it always carries whichever team signed the build. An
    // unsigned or ad-hoc build has no entitlements, gets nil here, and never
    // touches a group container: touching one it is not entitled to makes
    // macOS ask "would like to access data from other apps", and the widget
    // cannot load in such a build anyway.
    private static let appGroupID: String? = {
        guard let task = SecTaskCreateFromSelf(kCFAllocatorDefault),
              let value = SecTaskCopyValueForEntitlement(
                task, "com.apple.security.application-groups" as CFString, nil),
              let groups = value as? [String] else { return nil }
        return groups.first
    }()
    private static let fileName = "widget-data.json"

    private static var sharedContainerURL: URL? {
        guard let appGroupID else { return nil }
        return FileManager.default.containerURL(forSecurityApplicationGroupIdentifier: appGroupID)
    }

    /// Load from the shared App Group container.
    static func load() -> WidgetData? {
        guard let containerURL = sharedContainerURL else { return nil }
        let fileURL = containerURL.appendingPathComponent(fileName)
        guard let data = try? Data(contentsOf: fileURL) else { return nil }
        return try? JSONDecoder().decode(WidgetData.self, from: data)
    }

    /// Save to the shared App Group container.
    func save() {
        guard let containerURL = Self.sharedContainerURL else { return }
        let fileURL = containerURL.appendingPathComponent(Self.fileName)
        if let data = try? JSONEncoder().encode(self) {
            try? data.write(to: fileURL, options: .atomic)
        }
    }
}
