## PixelSwitch 1.0.5

### New

- **Every account has its own colour.** Each account's card on the Usage tab, and its row on the Accounts tab, now carries a colour of its own, so you know whose numbers you are looking at before you read the address. There are ten colours; with more accounts than that they repeat. An account keeps its colour between launches, because the colour comes from the account itself rather than from its position in the list, and the accounts on screen are always given different colours.
- The limit rows inside a card sit on their own neutral inset, so Session, Weekly and Fable stay just as readable on a coloured card as on a plain one.

#### Included from 1.0.4

- **The app's own mark** in the panel header and the menu bar, and Settings says "Show PixelSwitch logo in menu bar".
- **Cost totals that only claim history that exists:** a "Last 7 days" or "Last 30 days" card appears only when that much is kept, otherwise a single card states the real span, with a line naming your usage history window.

#### Included from 1.0.3 and 1.0.2

- Session, Weekly and Fable each have their own colour and symbol, and every row says how much is left.
- Auto-switch covers Fable; MCP server logins survive a switch; a switch cannot mix up two accounts; running sessions follow a switch.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.4...v1.0.5
