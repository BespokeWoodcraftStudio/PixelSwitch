## PixelSwitch 1.0.1

### Fixed

- **Running Claude Code sessions now follow a switch.** A running session only re-reads its login when Claude Code's fallback file `~/.claude/.credentials.json` changes, or when that file does not exist. On a Mac where the file had been left behind, sessions that were already open kept billing the old account for hours after a switch. PixelSwitch now bumps that file's date after every switch (it never creates, rewrites or deletes it), so open sessions move to the new account on their next request.
- **The credential is kept off the command line when it fits.** The login is now handed to `/usr/bin/security` on stdin instead of as a command-line argument, so other programs listing processes cannot see it. A login that also carries MCP server tokens is usually too long for that route (about 2,000 characters is the limit); it is then passed as hex on the command line, which is what Claude Code itself does.
- **Safer write.** The Keychain item is now updated in place instead of deleted and re-added, so there is no moment when it is missing, and PixelSwitch reads it back and compares it byte for byte before it reports the switch as done.

#### Included from v1.0

- **New name, icon and About page.** PixelSwitch by Pixel Ventures, based on CCSwitcher by Xueshi Qiao.
- **Much lighter on memory.** Cost and activity read only recent Claude Code history, the last 24 hours by default. Change it in Settings → General → Usage history window.
- **Brings your CCSwitcher accounts with it.** On first launch PixelSwitch copies CCSwitcher's accounts and settings. Quit CCSwitcher before opening PixelSwitch.
- **Its own update feed**, signed with the PixelSwitch update key.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.
- Desktop widgets need a Developer ID signed build, so they do not load in this release.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0...v1.0.1
