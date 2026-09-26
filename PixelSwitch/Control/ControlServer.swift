import Foundation
import Darwin

/// The app's end of remote control: a Unix-domain socket that only this Mac's
/// user can open, speaking newline-delimited JSON-RPC.
///
/// - The folder is made 0700 and the socket 0600, and every connection's peer
///   is checked with `getpeereid`; a different user is disconnected at once.
/// - There is no TCP listener of any kind. Another Mac reaches this one by
///   running `pixelswitch` here over SSH.
/// - A socket file left by a crash is replaced; one that still answers means
///   another copy of PixelSwitch is running, and this one does not start.
/// - Each connection handles its requests one at a time, in order, on the
///   main actor; connections do not wait for each other.
///
/// No dependency on the rest of the app, so the unit harness runs it for real.
final class ControlServer: @unchecked Sendable {
    typealias Handler = @MainActor @Sendable (_ line: String, _ connection: ControlConnection) async -> String?

    enum StartError: Error, Equatable, CustomStringConvertible {
        case pathTooLong(String)
        case alreadyRunning
        case system(String, Int32)

        var description: String {
            switch self {
            case .pathTooLong(let path): return "The socket path is too long for macOS (\(path.utf8.count) bytes): \(path)"
            case .alreadyRunning: return "Another copy of PixelSwitch is already listening."
            case .system(let call, let code): return "\(call) failed: \(String(cString: strerror(code)))"
            }
        }
    }

    let path: String
    private let handler: Handler
    private let log: @Sendable (String) -> Void
    private let writeTimeoutMilliseconds: Int32
    private let queue = DispatchQueue(label: "ai.pixelventures.pixelswitch.control")
    private let lock = NSLock()
    private var listenFD: Int32 = -1
    private var acceptSource: DispatchSourceRead?
    private var connections: [UUID: ControlConnection] = [:]

    /// `writeTimeoutMilliseconds`: how long a reply or event may wait for a
    /// client that is not reading.
    init(path: String, handler: @escaping Handler, log: @escaping @Sendable (String) -> Void = { _ in },
         writeTimeoutMilliseconds: Int32 = 2000) {
        self.path = path
        self.handler = handler
        self.log = log
        self.writeTimeoutMilliseconds = writeTimeoutMilliseconds
    }

    var isListening: Bool {
        lock.lock(); defer { lock.unlock() }
        return listenFD >= 0
    }

    /// Opens the socket and starts accepting. Throws `StartError`.
    func start() throws {
        guard path.utf8.count <= ControlProtocol.maxSocketPathBytes else { throw StartError.pathTooLong(path) }
        let folder = (path as NSString).deletingLastPathComponent
        do {
            try FileManager.default.createDirectory(atPath: folder, withIntermediateDirectories: true)
        } catch {
            throw StartError.system("mkdir", Int32((error as NSError).code))
        }
        chmod(folder, 0o700)

        if FileManager.default.fileExists(atPath: path) {
            if Self.canConnect(to: path) { throw StartError.alreadyRunning }
            unlink(path)
            log("[control] Replaced a socket left behind by an earlier run")
        }

        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { throw StartError.system("socket", errno) }
        var address = Self.address(path)
        let bound = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard bound == 0 else { let code = errno; close(fd); throw StartError.system("bind", code) }
        chmod(path, 0o600)
        guard listen(fd, 16) == 0 else { let code = errno; close(fd); unlink(path); throw StartError.system("listen", code) }
        _ = fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK)

        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in self?.acceptPending() }
        lock.lock()
        listenFD = fd
        acceptSource = source
        lock.unlock()
        source.resume()
        log("[control] Listening at \(path)")
    }

    /// Stops accepting, closes every connection and removes the socket file.
    func stop() {
        lock.lock()
        let source = acceptSource
        let fd = listenFD
        let open = Array(connections.values)
        acceptSource = nil
        listenFD = -1
        connections = [:]
        lock.unlock()
        guard fd >= 0 else { return }
        source?.setCancelHandler { close(fd) }
        source?.cancel()
        open.forEach { $0.close() }
        unlink(path)
        log("[control] Stopped")
    }

    /// Every connection that asked for events.
    func subscribers() -> [ControlConnection] {
        lock.lock(); defer { lock.unlock() }
        return connections.values.filter(\.isSubscribed)
    }

    // MARK: - Accepting

    private func acceptPending() {
        while true {
            lock.lock(); let fd = listenFD; lock.unlock()
            guard fd >= 0 else { return }
            let client = accept(fd, nil, nil)
            guard client >= 0 else { return }  // EAGAIN: nothing more waiting
            var peerUID: uid_t = 0
            var peerGID: gid_t = 0
            guard getpeereid(client, &peerUID, &peerGID) == 0, peerUID == getuid() else {
                log("[control] Refused a connection from another user (uid \(peerUID))")
                close(client)
                continue
            }
            var on: Int32 = 1
            setsockopt(client, SOL_SOCKET, SO_NOSIGPIPE, &on, socklen_t(MemoryLayout<Int32>.size))
            _ = fcntl(client, F_SETFL, fcntl(client, F_GETFL) | O_NONBLOCK)
            let connection = ControlConnection(fd: client, queue: queue, writeTimeoutMilliseconds: writeTimeoutMilliseconds, onClose: { [weak self] id in self?.forget(id) })
            lock.lock(); connections[connection.id] = connection; lock.unlock()
            connection.start(handler: handler)
        }
    }

    private func forget(_ id: UUID) {
        lock.lock(); connections[id] = nil; lock.unlock()
    }

    // MARK: - Socket helpers

    static func address(_ path: String) -> sockaddr_un {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let bytes = Array(path.utf8.prefix(ControlProtocol.maxSocketPathBytes))
        withUnsafeMutableBytes(of: &address.sun_path) { raw in
            raw.copyBytes(from: bytes)
            raw[bytes.count] = 0
        }
        return address
    }

    /// True when something is listening at `path`.
    static func canConnect(to path: String) -> Bool {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return false }
        defer { close(fd) }
        var address = address(path)
        let result = withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(fd, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        return result == 0
    }
}

/// One client connection. Reads lines off the main thread, handles them one
/// at a time on the main actor, and writes replies and pushed events.
final class ControlConnection: @unchecked Sendable {
    let id = UUID()
    private let fd: Int32
    private let queue: DispatchQueue
    private let writeTimeoutMilliseconds: Int32
    private let onClose: @Sendable (UUID) -> Void
    private let lock = NSLock()
    private var source: DispatchSourceRead?
    private var buffer = Data()
    private var closed = false
    private var subscribed = false
    private var continuation: AsyncStream<String>.Continuation?

    init(fd: Int32, queue: DispatchQueue, writeTimeoutMilliseconds: Int32 = 2000, onClose: @escaping @Sendable (UUID) -> Void) {
        self.fd = fd
        self.queue = queue
        self.writeTimeoutMilliseconds = writeTimeoutMilliseconds
        self.onClose = onClose
    }

    var isSubscribed: Bool {
        lock.lock(); defer { lock.unlock() }
        return subscribed && !closed
    }

    func markSubscribed() {
        lock.lock(); subscribed = true; lock.unlock()
    }

    func start(handler: @escaping ControlServer.Handler) {
        let (lines, continuation) = AsyncStream<String>.makeStream()
        lock.lock(); self.continuation = continuation; lock.unlock()
        Task { @MainActor in
            for await line in lines {
                if let reply = await handler(line, self) { self.send(reply) }
            }
        }
        let source = DispatchSource.makeReadSource(fileDescriptor: fd, queue: queue)
        source.setEventHandler { [weak self] in self?.readAvailable() }
        lock.lock(); self.source = source; lock.unlock()
        source.resume()
    }

    /// Writes `line` and a newline. False once the connection is closed.
    /// A line that cannot be written whole within the write timeout closes the
    /// connection: its stream would hold half a line, and a client that has
    /// stopped reading would stall the main actor again on every event.
    @discardableResult
    func send(_ line: String) -> Bool {
        lock.lock()
        guard !closed else { lock.unlock(); return false }
        var bytes = Array((line + "\n").utf8)
        var offset = 0
        while offset < bytes.count {
            let written = bytes.withUnsafeMutableBytes { raw in
                write(fd, raw.baseAddress!.advanced(by: offset), raw.count - offset)
            }
            if written > 0 { offset += written; continue }
            if written < 0, errno == EAGAIN || errno == EINTR {
                var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
                if poll(&pfd, 1, writeTimeoutMilliseconds) > 0 { continue }
            }
            break
        }
        lock.unlock()
        guard offset == bytes.count else { close(); return false }
        return true
    }

    func close() {
        lock.lock()
        guard !closed else { lock.unlock(); return }
        closed = true
        let source = self.source
        let continuation = self.continuation
        self.source = nil
        self.continuation = nil
        lock.unlock()
        continuation?.finish()
        let fd = self.fd
        if let source {
            source.setCancelHandler { Darwin.close(fd) }
            source.cancel()
        } else {
            Darwin.close(fd)
        }
        onClose(id)
    }

    private func readAvailable() {
        var chunk = [UInt8](repeating: 0, count: 16 * 1024)
        while true {
            let count = read(fd, &chunk, chunk.count)
            if count > 0 {
                lock.lock(); buffer.append(contentsOf: chunk[0..<count]); lock.unlock()
                continue
            }
            if count < 0, errno == EAGAIN || errno == EINTR { break }
            // 0 = the client closed; < 0 = an error.
            deliverLines()
            close()
            return
        }
        deliverLines()
    }

    private func deliverLines() {
        lock.lock()
        var lines: [String] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            let lineData = buffer[buffer.startIndex..<newline]
            buffer.removeSubrange(buffer.startIndex...newline)
            var text = String(decoding: lineData, as: UTF8.self)
            if text.hasSuffix("\r") { text.removeLast() }
            if !text.trimmingCharacters(in: .whitespaces).isEmpty { lines.append(text) }
        }
        let overflow = buffer.count > ControlProtocol.maxLineBytes
        let continuation = self.continuation
        lock.unlock()
        for line in lines { continuation?.yield(line) }
        if overflow { close() }
    }
}
