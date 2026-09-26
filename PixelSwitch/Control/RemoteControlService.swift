import AppKit
import Combine

private let controlLog = FileLog("Control")

/// Starts remote control at launch and stops it at quit: the socket server,
/// the API behind it, and the events pushed to `pixelswitch watch`. Always on
/// (the founder's answer, 2026-09-25): anything running as this Mac's user can
/// use it; nothing outside this Mac can reach it.
@MainActor
final class RemoteControlService: ObservableObject {
    static let shared = RemoteControlService()

    enum State: Equatable {
        case stopped
        case listening(path: String)
        case failed(String)
    }

    @Published private(set) var state: State = .stopped

    private var server: ControlServer?
    private var api: ControlAPI?
    private var hub: ControlEventHub?
    private var quitObserver: NSObjectProtocol?

    private init() {}

    func start(appState: AppState, updateChecker: UpdateChecker) {
        guard server == nil else { return }
        SettingsStore.shared.appState = appState
        let api = ControlAPI(controller: AppController(appState: appState, updateChecker: updateChecker),
                             log: { controlLog.info($0) })
        let server = ControlServer(
            path: ControlProtocol.socketPath(),
            handler: { line, connection in
                await api.handle(line, onSubscribe: { connection.markSubscribed() })
            },
            log: { controlLog.info($0) }
        )
        do {
            try server.start()
        } catch {
            let message = (error as? ControlServer.StartError)?.description ?? error.localizedDescription
            controlLog.error("[control] Not started: \(message)")
            state = .failed(message)
            return
        }
        self.api = api
        self.server = server
        hub = ControlEventHub(appState: appState) { [weak server] event in
            guard let server, let params = try? JSONValue.encode(event) else { return }
            let line = RPCNotification(method: ControlEvent.notificationMethod, params: params).line
            server.subscribers().forEach { $0.send(line) }
        }
        state = .listening(path: server.path)
        quitObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification, object: nil, queue: .main
        ) { _ in
            MainActor.assumeIsolated { RemoteControlService.shared.stop() }
        }
    }

    func stop() {
        server?.stop()
        server = nil
        api = nil
        hub = nil
        state = .stopped
    }
}

/// Pushes what changes in the app to every `events.subscribe` connection.
@MainActor
final class ControlEventHub {
    private var subscriptions = Set<AnyCancellable>()
    private var signInSubscription: AnyCancellable?
    private let send: @MainActor (ControlEvent) -> Void

    init(appState: AppState, send: @escaping @MainActor (ControlEvent) -> Void) {
        self.send = send

        appState.$activeAccount
            .map { $0?.id }
            .removeDuplicates()
            .dropFirst()
            .sink { [weak self, weak appState] id in
                let email = appState?.accounts.first(where: { $0.id == id })?.email
                self?.emit("activeAccountChanged", ["accountId": id.map { .string($0.uuidString) } ?? .null,
                                                    "email": email.map(JSONValue.string) ?? .null])
            }
            .store(in: &subscriptions)

        appState.$lastUsageRefresh
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] _ in self?.emit("usageUpdated", [:]) }
            .store(in: &subscriptions)

        appState.$lastAutoSwitch
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] record in
                self?.emit("autoSwitched", ["from": .string(record.from.uuidString), "to": .string(record.to.uuidString),
                                            "limit": .string(record.limit.rawValue), "trigger": .string(record.trigger.rawValue)])
            }
            .store(in: &subscriptions)

        appState.$currentSignIn
            .sink { [weak self] session in self?.follow(session) }
            .store(in: &subscriptions)

        appState.$errorMessage
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] message in self?.emit("error", ["message": .string(message)]) }
            .store(in: &subscriptions)
    }

    /// One `signInChanged` per change of state or link, read after the change lands.
    private func follow(_ session: SignInSession?) {
        guard let session else { signInSubscription = nil; return }
        signInSubscription = session.objectWillChange
            .receive(on: DispatchQueue.main)
            .map { [weak session] _ in session.map(ControlSnapshots.signInInfo) }
            .compactMap { $0 }
            .removeDuplicates()
            .sink { [weak self] info in
                guard let data = try? JSONValue.encode(info) else { return }
                self?.emit("signInChanged", ["signIn": data])
            }
    }

    private func emit(_ type: String, _ data: [String: JSONValue]) {
        send(ControlEvent(type: type, at: Date(), data: .object(data)))
    }
}
