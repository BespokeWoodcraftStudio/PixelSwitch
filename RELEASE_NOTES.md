## PixelSwitch 1.0.2

### New

- **Auto-switch now covers Fable.** Fable has its own weekly allowance, and it can run out long before your weekly limit does. When the active account's Fable use reaches your auto-switch threshold, PixelSwitch now switches to the account with the most Fable left. It only picks an account that is also clear of its session and weekly limits, so it never lands on one it would have to leave again at once. If no account has Fable to spare, it stays put. Session and weekly are still checked first, and when they trigger a switch PixelSwitch now prefers an account that also has Fable to spare, so one switch is enough.
- **How much Fable is left, for every account.** Each account's card on the Usage tab now has a "Weekly (Fable)" bar under Weekly, showing how much of that account's weekly Fable allowance is left and when it resets. The numbers come from the same usage request PixelSwitch already makes, so nothing extra is sent. You can also add a "FABLE" bar to the menu bar in Settings, for the active account. Accounts without a separate Fable allowance show no Fable bar and are never chosen for a Fable switch.

### Fixed

- **Your MCP server logins survive an account switch.** Claude Code keeps the logins for MCP servers (Stripe, Supabase, Linear and the rest) in the same Keychain item as your Claude account login. Until now a switch put back the whole item as it was the last time that account was active, so MCP logins made or renewed since then were replaced by older copies, and those servers asked you to sign in again. Measured on one Mac: a switch took the stored MCP logins from 48 entries to 8. PixelSwitch now takes only the Claude account login from the account you switch to, and keeps this Mac's MCP logins exactly as they are.
- **A switch can no longer mix up two accounts.** PixelSwitch used to decide whose login was live from `~/.claude.json`, but running Claude Code sessions rewrite that file from memory. So it could name one account while the Keychain held another's login, and a switch then saved one account's login under the other; switching to that account later used the wrong login while claiming the right one. PixelSwitch now works out whose login is live from the login itself (and, when needed, by asking Anthropic's servers), backs up a login only under the account it belongs to, and refuses a switch whose saved login belongs to someone else.
- **Repairs a mix-up it finds.** If the same login is saved under two accounts, PixelSwitch keeps it only under the account it belongs to and asks you to re-authenticate the other. It also never renews a saved login that belongs to a different account, which could have signed that account's sessions out.
- **Stays in step with Claude Code.** On every refresh PixelSwitch follows the account whose login is actually live, corrects `~/.claude.json` if a session left the wrong account there, and keeps that account's saved login current, so switching back never restores an out-of-date one.
- **No more half-finished switches.** If `claude auth status` stops answering, PixelSwitch gives up on it after 30 seconds and checks the stored login directly, instead of stalling with the switch half done.

#### Included from v1.0.1

- **Running Claude Code sessions follow a switch.** PixelSwitch bumps the date on `~/.claude/.credentials.json` after every switch (it never creates, rewrites or deletes it), which is Claude Code's own signal to re-read the login. Open sessions used to keep billing the old account for hours.
- **The credential is kept off the command line when it fits**, and the Keychain item is updated in place and read back byte for byte before a switch counts as done.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.
- A connector you added on claude.ai belongs to that Claude account, not to this Mac, so those still differ between accounts. Only the MCP servers configured on this Mac are kept.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.1...v1.0.2
