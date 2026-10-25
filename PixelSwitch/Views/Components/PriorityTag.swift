import SwiftUI

/// "P1": an account's priority number, shown before its name wherever accounts
/// are listed. The P keeps it from being read as a position (#1), which is what
/// `pixelswitch switch 1` and the `#` column mean.
struct PriorityTag: View {
    let priority: Int

    var body: some View {
        Text(verbatim: "P\(priority)")
            .font(.caption2.weight(.semibold))
            .monospacedDigit()
            .foregroundStyle(.secondary)
            .fixedSize()
            .help(Text("Priority"))
    }
}
