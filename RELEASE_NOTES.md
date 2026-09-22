## PixelSwitch 1.0.7

### Fixed

- **The account's colour is just the line now.** 1.0.6 still tinted the whole card, faintly, which read as a coloured box. The card is plain again: the account's colour appears only as the bar down its left edge and on the account's icon. That is enough to tell whose numbers you are looking at, and nothing sits behind the text.

#### Included from 1.0.6

- Ten colours, spread around the colour wheel so no two look alike, repeating only past ten accounts. An account keeps its colour between launches.
- **You can turn it off**: Settings → Account display → "Give each account its own color".

#### Included from 1.0.5 to 1.0.2

- The app's own mark in the panel header and menu bar; cost totals that only claim the history actually kept; Session, Weekly and Fable each with their own colour and symbol; auto-switch covering Fable; MCP logins that survive a switch.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.6...v1.0.7
