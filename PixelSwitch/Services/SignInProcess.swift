import Foundation

// MARK: - Running the CLI

/// A running `claude auth login`. Safe to call from any thread.
protocol SignInProcessHandle: AnyObject, Sendable {
    /// Writes `line` plus a newline to the process's stdin. False if the
    /// process has exited or its stdin is closed; never raises SIGPIPE.
    func writeLine(_ line: String) -> Bool
    /// SIGTERM.
    func terminate()
    /// SIGKILL.
    func kill()
    /// Closes stdin. Idempotent.
    func closeInput()
}

/// Starts processes for `SignInSession`. The fake in the unit tests stands in
/// for this so the session's state machine runs without launching `claude`.
protocol SignInProcessRunner: Sendable {
    /// Starts `executable` with stdout and stderr on one pipe and stdin on
    /// another. `onOutput` receives each chunk as it arrives, and `onExit` the
    /// exit status once, after the last chunk; both run on the main actor.
    func start(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        onOutput: @escaping @MainActor @Sendable (Data) -> Void,
        onExit: @escaping @MainActor @Sendable (Int32) -> Void
    ) throws -> any SignInProcessHandle
}

/// The real runner: `Process` and `Pipe`, read incrementally. The prompt
/// Claude Code prints last has no newline and the process does not exit until
/// the sign-in ends, so reading to end-of-file (as `runClaude` does) would
/// never deliver the links.
struct ProcessSignInRunner: SignInProcessRunner {
    /// How long, after the process exits, to wait for the end of its output.
    /// A grandchild holding the pipe open must not hold up the exit report.
    static let outputDrainTimeout: TimeInterval = 2

    func start(
        executable: URL,
        arguments: [String],
        environment: [String: String],
        onOutput: @escaping @MainActor @Sendable (Data) -> Void,
        onExit: @escaping @MainActor @Sendable (Int32) -> Void
    ) throws -> any SignInProcessHandle {
        let process = Process()
        let output = Pipe()
        let input = Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = environment
        process.standardOutput = output
        process.standardError = output
        process.standardInput = input

        let outputEnded = DispatchSemaphore(value: 0)
        output.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if data.isEmpty {
                handle.readabilityHandler = nil
                outputEnded.signal()
            } else {
                DispatchQueue.main.async { MainActor.assumeIsolated { onOutput(data) } }
            }
        }
        process.terminationHandler = { finished in
            let status = finished.terminationStatus
            // Every chunk was queued to the main queue before end-of-file was
            // signalled, so the exit report queued after it arrives last.
            _ = outputEnded.wait(timeout: .now() + Self.outputDrainTimeout)
            DispatchQueue.main.async { MainActor.assumeIsolated { onExit(status) } }
        }
        do {
            try process.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            throw error
        }
        return ProcessSignInHandle(process: process, input: input.fileHandleForWriting)
    }
}

final class ProcessSignInHandle: SignInProcessHandle, @unchecked Sendable {
    private let process: Process
    private let input: FileHandle
    private let lock = NSLock()
    private var inputClosed = false

    init(process: Process, input: FileHandle) {
        self.process = process
        self.input = input
        // A write after the CLI exits must fail with EPIPE, not kill the app.
        _ = fcntl(input.fileDescriptor, F_SETNOSIGPIPE, 1)
    }

    func writeLine(_ line: String) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard !inputClosed, process.isRunning else { return false }
        do {
            try input.write(contentsOf: Data((line + "\n").utf8))
            return true
        } catch {
            return false
        }
    }

    func terminate() {
        if process.isRunning { process.terminate() }
    }

    func kill() {
        if process.isRunning { _ = Darwin.kill(process.processIdentifier, SIGKILL) }
    }

    func closeInput() {
        lock.lock()
        defer { lock.unlock() }
        guard !inputClosed else { return }
        inputClosed = true
        try? input.close()
    }
}

// MARK: - Timers

/// A scheduled action that can be called off.
@MainActor
final class SignInTimer {
    private(set) var isCancelled = false
    func cancel() { isCancelled = true }
}

/// The session's clock. The unit tests use a fake that fires on demand, so the
/// 10-second and 15-minute limits are tested without waiting.
@MainActor
protocol SignInScheduler: AnyObject {
    var now: Date { get }
    @discardableResult
    func after(_ seconds: TimeInterval, _ action: @escaping @MainActor @Sendable () -> Void) -> SignInTimer
}

/// The real clock: the main queue.
@MainActor
final class MainQueueSignInScheduler: SignInScheduler {
    var now: Date { Date() }

    @discardableResult
    func after(_ seconds: TimeInterval, _ action: @escaping @MainActor @Sendable () -> Void) -> SignInTimer {
        let timer = SignInTimer()
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) {
            MainActor.assumeIsolated {
                guard !timer.isCancelled else { return }
                action()
            }
        }
        return timer
    }
}
