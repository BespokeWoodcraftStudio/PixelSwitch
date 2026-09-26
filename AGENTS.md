# PixelSwitch Agent Guidelines

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
