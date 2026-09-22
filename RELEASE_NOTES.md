## PixelSwitch 1.0.2

### Fixed

- **Your MCP server logins survive an account switch.** Claude Code keeps the logins for MCP servers (Stripe, Supabase, Linear and the rest) in the same Keychain item as your Claude account login. Until now a switch put back the whole item as it was the last time that account was active, so MCP logins made or renewed since then were replaced by older copies, and those servers asked you to sign in again. Measured on one Mac: a switch took the stored MCP logins from 48 entries to 8. PixelSwitch now takes only the Claude account login from the account you switch to, and keeps this Mac's MCP logins exactly as they are.

#### Included from v1.0.1

- **Running Claude Code sessions follow a switch.** PixelSwitch bumps the date on `~/.claude/.credentials.json` after every switch (it never creates, rewrites or deletes it), which is Claude Code's own signal to re-read the login. Open sessions used to keep billing the old account for hours.
- **The credential is kept off the command line when it fits**, and the Keychain item is updated in place and read back byte for byte before a switch counts as done.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.
- A connector you added on claude.ai belongs to that Claude account, not to this Mac, so those still differ between accounts. Only the MCP servers configured on this Mac are kept.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.1...v1.0.2
