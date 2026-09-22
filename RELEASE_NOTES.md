## PixelSwitch 1.0.10

### Fixed

- **You can read the limit rows now.** "Session", "Weekly" and "Fable" were drawn in each limit's own colour, on a row tinted with that same colour, so the words disappeared into their own background: measured at **1.9:1** for Session, where 4.5:1 is the readable minimum. Every label and figure in a row is now plain text, measured at **12.9:1 or better** in both light and dark appearance. The colour moved to the chip, the row tint and the bar, which are shapes rather than type.
- A used-percentage still keeps its colour while a limit is nearly gone, because red on a green, blue or purple tint is the opposite of camouflage, and that is the one warning worth shouting.

### New

- **The account you are signed in to is ringed in bright orange, all the way round its card** — on the Usage tab and in the Accounts list. The small green "Active" badge is still there, but a badge has to be hunted for and a ring does not.

#### Included from 1.0.9 to 1.0.2

- The PixelSwitch mark beside every account; a colour per account down the edge of its card; a switch for Fable auto-switching; cost totals that only claim the history actually kept; Session, Weekly and Fable each with their own colour and symbol; auto-switch covering the weekly Fable allowance; MCP logins that survive a switch; a switch that cannot mix up two accounts.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.9...v1.0.10
