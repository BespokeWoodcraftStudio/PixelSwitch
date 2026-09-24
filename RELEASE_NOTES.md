## PixelSwitch 1.1

### Changed

- **PixelSwitch is now signed and notarized by Apple.** It opens with a normal double-click and no security warning. You no longer need to run `xattr -dr com.apple.quarantine` or use Privacy & Security → Open Anyway on a fresh install.
- **Desktop widgets now work.** macOS only loads widgets from a signed app, so they could not appear in the unsigned 1.0.x releases. In 1.1, right-click your desktop, choose **Edit Widgets**, and search for PixelSwitch: small, medium and large widgets, plus a circular ring, show your account usage, costs and activity.
- **Version numbers are shorter.** From here on releases are numbered 1.1, 1.2, 1.3, then 2.0, and so on, instead of 1.0.16.

There are no other changes to how the app works. Your accounts, settings and saved tokens carry over as they are.

#### Included from 1.0.16

- **The Accounts tab is readable again.** Each row is now two lines and never wraps: your address (or your own label, if you have set one) on top, and **Max · Claude Code** underneath. The organisation name is gone, because it never said anything the address did not already say.
- Double-click an account on the Usage tab to switch to it. Also in the 1.0 series: whole email addresses with masking as an opt-in, a colour per account, honest cost totals, a switch for Fable auto-switching, MCP logins that survive a switch, and a switch that cannot mix up two accounts.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.16...v1.1
