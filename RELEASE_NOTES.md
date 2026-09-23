## PixelSwitch 1.0.15

### New

- **Double-click any account on the Usage tab to switch to it.** No trip to the Accounts list: point at a card, double-click, done.

It tells you it heard you. The card presses in the moment you double-click, the orange ring moves across to it straight away, and the bottom of the card reads **Switching…** until the switch actually lands, which can take a second or two. Hovering a card you are not signed in to shows **Double-click to switch** and a pointing-hand cursor, so the gesture is not a secret.

Double rather than single, deliberately: the Usage tab is something you read and scroll, and a single click would swap your live Claude login while you were only trying to read a number off it. Double-clicking the account you are already on does nothing, and clicks are ignored while a switch is in flight.

The hint and the status sit on the bottom line of the card rather than next to the address, because real addresses are long enough to truncate there already.

#### Included from 1.0.14 to 1.0.2

- The app clears the identifiers it inherited from the project it was forked from, once it has verified your accounts read back from their new home; whole email addresses, with masking as an opt-in; limit rows in plain readable text; the account you are signed in to ringed in orange; the PixelSwitch mark beside every account; a colour per account; a switch for Fable auto-switching; honest cost totals; auto-switch covering the weekly Fable allowance; MCP logins that survive a switch; a switch that cannot mix up two accounts; and [NOTICE.md](NOTICE.md), which states the licensing position honestly.

### Good to know

- This build is not signed with an Apple Developer ID yet. On a fresh install, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway. Updating in place through Check for Updates does not need this.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.14...v1.0.15
