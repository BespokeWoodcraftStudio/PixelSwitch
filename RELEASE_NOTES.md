## PixelSwitch 1.0.16

### Fixed

- **The Accounts tab is readable again.** Each row led with the organisation name, which Anthropic returns as "*your address*'s Organization", and then printed your address a second time underneath. On a 360pt panel with a Switch button and two icons beside it, that left the text about 130pt wide, so it wrapped mid-word: `ahmed@be-spokewood-craftstudio.-com's Organization`. Even the **Max** badge split into "Ma" and "x".

A row is now two lines and never wraps:

- **your address** (or your own label, if you have set one), truncated with an ellipsis if it is long
- **Max · Claude Code** underneath, quietly

The organisation name is gone. It never said anything the address did not already say, at three times the width.

Also fixed along the way: the text column and the spacer beside it were both trying to expand, so they split the row between them and the address truncated at about half the width actually available. A seventeen-character address was being cut short with 50pt of empty row next to it.

#### Included from 1.0.15 to 1.0.2

- Double-click an account on the Usage tab to switch to it, with the card pressing in, the orange ring moving across and a "Switching…" label; the app clears the identifiers it inherited from the project it was forked from; whole email addresses, with masking as an opt-in; limit rows in plain readable text; the account you are signed in to ringed in orange; a colour per account; a switch for Fable auto-switching; honest cost totals; auto-switch covering the weekly Fable allowance; MCP logins that survive a switch; a switch that cannot mix up two accounts; and [NOTICE.md](NOTICE.md), which states the licensing position honestly.

### Good to know

- This build is not signed with an Apple Developer ID yet. On a fresh install, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway. Updating in place through Check for Updates does not need this.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.15...v1.0.16
