## PixelSwitch 1.0.11

### Changed

- **Email addresses are shown in full.** Cards used to read `cla*@*.com`, which meant two accounts on the same domain were told apart by guessing. You now see the whole address everywhere it appears: the panel header, the Usage cards, the Accounts list, the menu bar modules and the widgets.
- **Masking is now something you turn on**, in Settings → Account display → "Hide part of each email address". It is genuinely useful right before you share your screen or send a screenshot, so it stays one switch away rather than being removed.

This flips the old default, and it flips it for existing installs too. The old setting was stored as "off" on machines that had never touched it, so reusing it would have left everyone masked and changed nothing; the setting has a new name, and the new default reaches every install. If you preferred the masked view, turn it back on in Settings and it stays on.

#### Included from 1.0.10 to 1.0.2

- Limit rows in plain, readable text, with the colour carried by the chip, the row tint and the bar; the account you are signed in to ringed in bright orange; the PixelSwitch mark beside every account; a colour per account down the edge of its card; a switch for Fable auto-switching; cost totals that only claim the history actually kept; Session, Weekly and Fable each with their own colour and symbol; auto-switch covering the weekly Fable allowance; MCP logins that survive a switch; a switch that cannot mix up two accounts.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.10...v1.0.11
