# PixelSwitch Agent Guidelines

**Rules**:
- `project.yml` is the ONLY source of truth. NEVER edit `.pbxproj` or `Info.plist` directly. Run `xcodegen generate` after changes.
- `PixelSwitch.xcodeproj` is disposable (git-ignored).

**App**: Minimalist macOS menu bar app for managing/switching Claude Code accounts.
**Features**: Terminal-free login (Process/Pipe interception), zero-interaction token refresh (`security` CLI workaround), API usage tracking.

**Architecture & Files**:
- **Docs**: `ARCHITECTURE.md` (token flow), `BUILD_GUIDE.md`, `project.yml` (Xcode config).
- **Entry**: `PixelSwitchApp.swift` (MenuBarExtra, lifecycle), `AppState.swift` (@MainActor state).
- **Services**: 
  - `ClaudeService.swift`: Wraps `claude` CLI (auth/status).
  - `KeychainService.swift`: Manages OAuth tokens via `/usr/bin/security`.
  - `*Parser.swift`: Parses `~/.claude/` JSON caches (Activity/Cost/Stats).
- **Models**: `Account.swift`, `*Data.swift` (usage/cost/activity).
- **Views**: `MainMenuView.swift` (dropdown), `SettingsView.swift` (native window), `HiddenWindowView.swift` (LSUIElement keepalive workaround).
