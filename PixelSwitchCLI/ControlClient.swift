import Foundation
import Darwin

/// The command-line tool's end of remote control: one connection to the app's
/// socket, blocking calls with a timeout. Starts PixelSwitch if it is not
/// running. Pure Foundation, so the unit harness drives it against a real
/// `ControlServer`.
final class ControlClient {
    enum ClientError: Error, Equatable, CustomStringConvertible {
        /// PixelSwitch is not running and could not be started, or its socket is missing.
        case unreachable(String)
        case timedOut(String)
        case closed

        var description: String {
            switch self {
            case .unreachable(let why): return "PixelSwitch is not reachable: \(why)"
            case .timedOut(let method): return "PixelSwitch did not answer \(method) in time."
            case .closed: return "PixelSwitch closed the connection."
            }
        }
    }

    let path: String
    private var fd: Int32 = -1
    private var buffer = Data()
    private var nextId = 1

    /// `PIXELSWITCH_CONTROL_SOCKET` points the tool at another socket and
    /// `PIXELSWITCH_NO_LAUNCH=1` stops it starting the app; both exist for the
    /// integration script, so it never touches the real app by accident.
    init(path: String = ProcessInfo.processInfo.environment["PIXELSWITCH_CONTROL_SOCKET"] ?? ControlProtocol.socketPath()) {
        self.path = path
    }

    private var launchAllowed: Bool {
        ProcessInfo.processInfo.environment["PIXELSWITCH_NO_LAUNCH"] != "1"
    }

    deinit { disconnect() }

    /// Connects, starting PixelSwitch first when `launch` is true and nothing
    /// answers (`open -g -b ai.pixelventures.pixelswitch`), waiting up to `wait` seconds.
    func connect(launch: Bool = true, wait: TimeInterval = 10) throws {
        if tryConnect() { return }
        guard launch, launchAllowed else { throw ClientError.unreachable("nothing is listening at \(path)") }
        let open = Process()
        open.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        open.arguments = ["-g", "-b", ControlProtocol.appBundleIdentifier]
        open.standardOutput = FileHandle.nullDevice
        open.standardError = FileHandle.nullDevice
        do { try open.run(); open.waitUntilExit() } catch {
            throw ClientError.unreachable("could not start PixelSwitch (\(error.localizedDescription))")
        }
        guard open.terminationStatus == 0 else {
            throw ClientError.unreachable("PixelSwitch is not installed, or no one is signed in to this Mac's desktop")
        }
        let deadline = Date().addingTimeInterval(wait)
        while Date() < deadline {
            if tryConnect() { return }
            usleep(250_000)
        }
        throw ClientError.unreachable("PixelSwitch started but did not open its socket within \(Int(wait)) seconds")
    }

    func disconnect() {
        if fd >= 0 { close(fd); fd = -1 }
        buffer.removeAll()
    }

    /// Sends one request and waits for its reply. Throws `ControlError` for an
    /// error reply, `ClientError` for a transport problem.
    func call(_ method: ControlMethod, _ params: JSONValue? = nil, timeout: TimeInterval = 30) throws -> JSONValue {
        let id = nextId
        nextId += 1
        let request = RPCRequest(id: .int(id), method: method.rawValue, params: params)
        let data = try ControlCoding.encoder.encode(request)
        try send(String(decoding: data, as: UTF8.self))
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            guard let line = try readLine(until: deadline) else { throw ClientError.timedOut(method.rawValue) }
            guard let response = try? ControlCoding.decoder.decode(RPCResponse.self, from: Data(line.utf8)),
                  response.id == .int(id) else { continue }  // a pushed event, or another reply
            if let error = response.error { throw ControlError(rpc: error) }
            return response.result ?? .null
        }
    }

    /// The next pushed event, or nil at the deadline. For `watch`, after `events.subscribe`.
    func nextEvent(until deadline: Date) throws -> RPCNotification? {
        while true {
            guard let line = try readLine(until: deadline) else { return nil }
            if let note = try? ControlCoding.decoder.decode(RPCNotification.self, from: Data(line.utf8)),
               note.method == ControlEvent.notificationMethod {
                return note
            }
        }
    }

    // MARK: - Transport

    private func tryConnect() -> Bool {
        disconnect()
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8.prefix(ControlProtocol.maxSocketPathBytes))
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: bytes)
            raw[bytes.count] = 0
        }
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { Darwin.connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard result == 0 else { close(fd); return false }
        var on: Int32 = 1
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
        self.fd = fd
        return true
    }

    private func send(_ line: String) throws {
        guard fd >= 0 else { throw ClientError.closed }
        var bytes = Array((line + "\n").utf8)
        var offset = 0
        while offset < bytes.count {
            let written = bytes.withUnsafeMutableBytes { raw in write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset) }
            if written > 0 { offset += written } else if errno != EINTR { throw ClientError.closed }
        }
    }

    private func readLine(until deadline: Date) throws -> String? {
        while true {
            if let newline = buffer.firstIndex(of: 0x0A) {
                let line = String(decoding: buffer[buffer.startIndex..<newline], as: UTF8.self)
                buffer.removeSubrange(buffer.startIndex...newline)
                return line
            }
            guard fd >= 0 else { throw ClientError.closed }
            let remaining = deadline.timeIntervalSinceNow
            guard remaining > 0 else { return nil }
            var pfd = pollfd(fd: fd, events: Int16(POLLIN), revents: 0)
            let ready = poll(&pfd, 1, Int32(min(remaining, 3600) * 1000))
            if ready < 0, errno == EINTR { continue }
            guard ready > 0 else { return nil }
            var chunk = [UInt8](repeating: 0, count: 64 * 1024)
            let count = read(fd, &chunk, chunk.count)
            if count > 0 {
                buffer.append(contentsOf: chunk[0..<count])
                if buffer.count > ControlProtocol.maxLineBytes { throw ClientError.closed }
            } else if count == 0 || errno != EINTR {
                disconnect()
                throw ClientError.closed
            }
        }
    }
}
