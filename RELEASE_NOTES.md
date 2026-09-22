## PixelSwitch 1.0.3

### Improved

- **You can tell the three limits apart at a glance.** Each limit on an account's card now carries its own colour and symbol that never change: a green clock for **Session**, a blue calendar for **Weekly**, and a purple sparkle for **Fable**, each on a matching tinted row. Before this, the bars were coloured only by how full they were, so Weekly and Fable were both blue, and both turned red once they were nearly out, which made a card hard to read at a glance. The bar itself still fills up and turns red as a limit runs out; that meaning has not changed. Fable also has its own colour in Settings now, next to the Session and Weekly ones.
- **Every limit says how much is left**, not just Fable, next to its name.
- **Reset times read properly.** A reset more than a day away now says "Resets Mon 11:00 AM" instead of "Resets in Mon 11:00 AM".

#### Included from 1.0.2

- **Auto-switch covers Fable.** When the active account's Fable use reaches your threshold, PixelSwitch switches to the account with the most Fable left, choosing only an account that is also clear of its session and weekly limits. If none has Fable to spare, it stays put.
- **How much Fable is left, for every account**, on the Usage tab and optionally in the menu bar.
- **Your MCP server logins survive an account switch.** A switch used to put back an old copy of them, so servers like Stripe asked you to sign in again.
- **A switch can no longer mix up two accounts:** ownership of a login is proven with Anthropic rather than read from a file that running sessions rewrite.
- **Running Claude Code sessions follow a switch**, instead of billing the old account for hours.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.
- A connector you added on claude.ai belongs to that Claude account, not to this Mac, so those still differ between accounts. Only the MCP servers configured on this Mac are kept.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.2...v1.0.3
