## PixelSwitch 1.0.13

### Changed

- **PixelSwitch now cleans up after itself.** 1.0.12 moved your accounts onto PixelSwitch's own keychain item and preference key, and deliberately left the old ones behind as a way back to an earlier build. This release removes them, once and only once the new ones have been read back and proven to hold exactly the same accounts.

Three reasons it is better this way. The old keychain item was a **second copy of every account's OAuth token**, sitting there indefinitely with nothing maintaining it. An older build that found it would carry on writing to it, so the two stores would drift apart and whichever build ran last would look wrong. And it kept another app's name in your keychain, which is what it was called.

Removed on first launch, after verification: the old keychain item, the old accounts preference key, the stale migration flag, and the empty `~/.ccswitcher` folder. **If the read-back does not match, nothing is deleted** and the app carries on exactly as before.

### If you are updating from an older build

Everything happens by itself. Your accounts move across, get verified, and the old copies are cleared. macOS asks once whether PixelSwitch may use its saved credentials, because the item it is asking about is a new one: click **Always Allow**.

Updating through **Check for Updates** works and has been tested end to end on an unsigned build, from 1.0.9 to the current release.

#### Included from 1.0.12 to 1.0.2

- PixelSwitch's own keychain item, home folder and preference key; whole email addresses, with masking as an opt-in; limit rows in plain readable text; the account you are signed in to ringed in orange; the PixelSwitch mark beside every account; a colour per account; a switch for Fable auto-switching; honest cost totals; auto-switch covering the weekly Fable allowance; MCP logins that survive a switch; a switch that cannot mix up two accounts; and [NOTICE.md](NOTICE.md), which states the licensing position honestly.

### Good to know

- This build is not signed with an Apple Developer ID yet. On a fresh install, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway. Updating in place through Check for Updates does not need this.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.12...v1.0.13
