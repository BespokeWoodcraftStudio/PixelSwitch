## PixelSwitch 1.0.8

### New

- **Fable auto-switching has its own switch.** Settings → Auto-switch → **"Also switch when Fable runs out"**, on by default. Turn it off and Fable stays a reading you can see, while the 5-hour and weekly limits keep moving you to an account with room, exactly as before. Nothing else about auto-switch changes.
- **A proper README**, with pictures of the app and an explanation of what every colour and bar means.

#### Included from 1.0.7 and 1.0.6

- Each account has its own colour, shown as the bar down the edge of its card and on its icon. Ten colours, repeating only past ten accounts, and each account keeps its colour between launches. Turn it off in Settings → Account display.

#### Included from 1.0.5 to 1.0.2

- The app's own mark in the panel header and menu bar; cost totals that only claim the history actually kept; Session, Weekly and Fable each with their own colour and symbol; auto-switch covering Fable; MCP logins that survive a switch; a switch that cannot mix up two accounts.

### Good to know

- This build is not signed with an Apple Developer ID yet. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.0.7...v1.0.8
