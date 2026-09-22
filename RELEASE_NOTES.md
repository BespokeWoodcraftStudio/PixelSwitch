## PixelSwitch 1.0.4

### Fixed

- **The app's own icon, everywhere.** The panel header and the menu bar still showed the head glyph inherited from CCSwitcher. Both now use the PixelSwitch mark: the panel shows the app icon, and the menu bar shows the switch mark as a template image, so macOS colours it correctly on a light or dark bar. It still turns brand-coloured while a double-usage promotion is running, which is what the old filled glyph signalled. The Settings toggle now says **"Show PixelSwitch logo in menu bar"**.
- **The cost totals no longer claim history that was never kept.** "Last 7 Days" and "Last 30 Days" were fixed labels, so on a few days of data they showed the same figure and implied a month of history. PixelSwitch only reads session files touched inside your usage history window (Settings → General, 24 hours by default) and drops the rest, which is exactly what keeps its memory small. Each card now appears only when its period is genuinely covered; otherwise a single card states the real span, such as "All 5 days" or "Today only", and a line underneath says how far back your window reaches and where to change it.

#### Included from 1.0.3

- **You can tell Session, Weekly and Fable apart at a glance**: each has its own colour and symbol that never change, on a matching tinted row, while the bar itself still fills and turns red as a limit runs out. Every row says how much is left.

#### Included from 1.0.2

- **Auto-switch covers Fable**, moving you to the account with the most Fable left, and only to one that is also clear of its session and weekly limits.
- **Your MCP server logins survive an account switch**, and a switch can no longer mix up two accounts.
- **Running Claude Code sessions follow a switch.**

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.3...v1.0.4
