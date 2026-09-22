## PixelSwitch 1.0.9

### Fixed

- **The PixelSwitch mark beside every account.** Each account still showed the head glyph inherited from CCSwitcher, on its card and in the Accounts list. Both now show the PixelSwitch mark, in that account's colour.
- **The pictures in the README no longer use real email addresses.** They were rendered with the author's own accounts; they now use invented ones on `example.com`, the domain reserved for documentation.

### New

- **The README shows what auto-switch does**: the account that has run out of Fable, and the account PixelSwitch moves to, drawn with the same cards the app draws.

#### Included from 1.0.8

- **Fable auto-switching has its own switch**: Settings → Auto-switch → "Also switch when Fable runs out", on by default. Off leaves Fable as a reading while the 5-hour and weekly limits keep switching.

#### Included from 1.0.7 to 1.0.2

- A colour per account, shown as the bar down the edge of its card; the app's own mark in the panel header and menu bar; cost totals that only claim the history actually kept; Session, Weekly and Fable each with their own colour and symbol; auto-switch covering Fable; MCP logins that survive a switch; a switch that cannot mix up two accounts.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.8...v1.0.9
