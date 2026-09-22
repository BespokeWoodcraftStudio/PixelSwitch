## PixelSwitch 1.0.12

### Changed

- **PixelSwitch's own name on its own keychain item.** The item holding every saved account was still called `me.xueshi.ccswitcher.backups`, the identifier of the app this one was forked from, so macOS put another app's name in front of you every time it asked permission to use *your* accounts. It is now `ai.pixelventures.pixelswitch.backups`.
- **The stray folder is gone.** The app created `~/.ccswitcher` in your home directory on every launch and almost always left it empty. It now uses `~/.pixelswitch`, and removes the old folder if, and only if, nothing is inside it.
- **The account list moved to PixelSwitch's own preference key.**

**Nothing is lost, and this is reversible.** Each old name is still read, and never written to or deleted. On the first launch after updating, PixelSwitch finds your accounts under the old names and copies them across; if a read fails for any reason, it refuses rather than starting from an empty store. Roll back to an older build and it finds every account exactly where it left them.

**macOS will ask once more** whether PixelSwitch may use its saved account credentials, because the item it is asking about is genuinely a new one. Click **Always Allow**. This is the last time the dialog will name anything other than PixelSwitch.

### Removed

- 564 lines of a superseded parse cache that had no callers left.
- The comparisons to the project this was forked from, throughout the README. The credit remains, in Credits, where it belongs.

### Added

- **[NOTICE.md](NOTICE.md), which states the licensing position honestly.** PixelSwitch has no license and cannot offer one: the upstream project publishes no license, and most of this code is still other people's. The file gives the measured line counts, names everyone with a copyright interest, and says plainly what would have to change. A repository with no `LICENSE` usually just looks careless; this one now explains itself.

#### Included from 1.0.11 to 1.0.2

- Whole email addresses, with masking as an opt-in; limit rows in plain readable text; the account you are signed in to ringed in orange; the PixelSwitch mark beside every account; a colour per account; a switch for Fable auto-switching; honest cost totals; auto-switch covering the weekly Fable allowance; MCP logins that survive a switch; a switch that cannot mix up two accounts.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.11...v1.0.12
