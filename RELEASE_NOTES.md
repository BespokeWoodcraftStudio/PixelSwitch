## PixelSwitch 1.0.6

### Improved

- **Account colours you can actually read.** 1.0.5 washed a whole card in the account's colour, which made some text hard to read and left several colours looking alike. The colour is now an accent instead: a solid bar down the edge of the card and the account's icon, with only a whisper of tint on the card itself. Text contrast measures at least 13:1 on every colour in both light and dark, where 7:1 is the strictest common standard.
- **Ten colours that are properly different**, spread around the colour wheel by eye rather than in even steps, which had bunched amber, ochre and coral together. They repeat only past ten accounts.
- **You can turn it off.** Settings → Account display → **"Give each account its own color"**. Off gives you the plain cards.

#### Included from 1.0.5 and 1.0.4

- An account keeps its colour between launches, because the colour comes from the account itself rather than its position in the list.
- The app's own mark in the panel header and the menu bar; Settings says "Show PixelSwitch logo in menu bar".
- Cost totals that only claim the history actually kept.

#### Included from 1.0.3 and 1.0.2

- Session, Weekly and Fable each have their own colour and symbol, and every row says how much is left.
- Auto-switch covers Fable; MCP server logins survive a switch; a switch cannot mix up two accounts; running sessions follow a switch.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.5...v1.0.6
