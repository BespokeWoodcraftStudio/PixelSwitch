# PixelSwitch Agent Guidelines

**Start here:** `docs/SESSION-HANDOVER.md` (current state, what is waiting, deferred defects, gotchas), then `docs/worklog/INDEX.md`.

**Rules**:
- `project.yml` is the ONLY source of truth. NEVER edit `.pbxproj` or `Info.plist` directly. Run `xcodegen generate` after changes.
- `PixelSwitch.xcodeproj` is disposable (git-ignored).
- Versions are two-part: 1.1, 1.2, 1.3, then 2.0, 2.1. Never three-part (no 1.0.17), no patch releases. Release with `/release` or `scripts/release.sh minor|major|<X.Y>`.
- Releases are Developer ID signed and notarized by CI (`.github/workflows/build.yml`, Team LM3P28DNVQ). The signing secrets live in GitHub repo secrets; the identity's only durable copy is the founder's login keychain and password manager.

**App**: Minimalist macOS menu bar app for managing/switching Claude Code accounts.
**Features**: Sign-in links you control (PixelSwitch runs `claude auth login` with `BROWSER` pointed at a per-sign-in capture helper and shows both links with Open / Open in / Copy; nothing opens by itself; `SignInSession`, `SignInWindowController`), zero-interaction token refresh (`security` CLI workaround), API usage tracking.

**Architecture & Files**:
- **Docs**: `ARCHITECTURE.md` (token flow), `BUILD_GUIDE.md`, `project.yml` (Xcode config).
- **Entry**: `PixelSwitchApp.swift` (MenuBarExtra, lifecycle), `AppState.swift` (@MainActor state).
- **Services**: 
  - `ClaudeService.swift`: Wraps `claude` CLI (auth/status).
  - `KeychainService.swift`: Manages OAuth tokens via `/usr/bin/security`.
  - `*Parser.swift`: Parses `~/.claude/` JSON caches (Activity/Cost/Stats).
- **Models**: `Account.swift`, `*Data.swift` (usage/cost/activity).
- **Views**: `MainMenuView.swift` (dropdown), `SettingsView.swift` (native window), `HiddenWindowView.swift` (LSUIElement keepalive workaround).
- **Remote control** (`PixelSwitch/Control/`, `PixelSwitchCLI/`): the `pixelswitch` tool (`Contents/Helpers/pixelswitch`, a `type: tool` target in `project.yml`, signed in CI) talks to the app over a user-only Unix socket (`~/Library/Application Support/PixelSwitch/control.sock`, newline-delimited JSON-RPC, `ControlProtocol.swift` shared by both). `ControlAPI` (pure, unit-tested) → `AppController` → `AppState`; `SettingsStore` owns every setting's side effect; `pixelswitch mcp` is an MCP server (20 tools; modern 2026-07-28 and legacy handshake). Protocol stays 2; 21 methods and 24 settings keys since 2.0 (`accounts.setThresholds`, `accounts.setPriority`, `autoSwitch.sessionThreshold`, `autoSwitch.weeklyThreshold`), all additions optional and announced in `status.features`. Priority is a stored 1-99 number, shown as `P1`, never an account reference. No token ever crosses the socket; no TCP listener.

**Linux (2.0)**: `linux/` is a Python 3.12+ port (stdlib daemon `pixelswitchd` as a systemd user service, the same
`pixelswitch` CLI and MCP, a GTK 3 tray over StatusNotifierItem, a GTK 4/libadwaita settings window, signed self-updates
with rollback). It must behave exactly like the Mac on the shared rules: `Tests/conformance/autoswitch-vectors.json`
runs on both engines. Start at `linux/README.md`; tests `python3 -m unittest discover -s linux/tests -t linux`.
Contract for both platforms: `docs/superpowers/specs/2026-10-24-2.0-spec.md`.

**Rules learned in 2.0** (details in `docs/SESSION-HANDOVER.md` gotchas): this Mac cannot compile SwiftUI view files, so a
view change needs a CI branch build before main; wait on CI with an exit status that follows the run's conclusion and
poll no faster than every 120 s (the GitHub API quota is shared with the founder's other sessions); never install,
upgrade or relink software on the founder's machines; test Linux changes on his laptop only under spec section 5.7
(scratch HOME, test mode, fabricated logins).
