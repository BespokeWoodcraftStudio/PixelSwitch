## PixelSwitch 1.0

The first release of PixelSwitch, the Pixel Ventures version of [CCSwitcher](https://github.com/XueshiQiao/CCSwitcher) by Xueshi Qiao.

### New

- **New name, icon and About page.** PixelSwitch by Pixel Ventures.
- **Much lighter on memory.** Cost and activity now read only recent Claude Code history, the last 24 hours by default, instead of every transcript on disk. On a heavy-use Mac with about 10,000 transcript files, steady memory fell from about 860 MB to about 200 MB and the 3.9 GB spike at launch is gone. Change it in Settings → General → Usage history window.
- **Brings your CCSwitcher accounts with it.** On first launch PixelSwitch copies CCSwitcher's accounts and settings, so you do not sign in again. Quit CCSwitcher before opening PixelSwitch.
- **Its own update feed.** Check for Updates reads PixelSwitch releases only, signed with the PixelSwitch update key.

### Good to know

- This build is not signed with an Apple Developer ID. The first time you open it, clear the download flag with `xattr -dr com.apple.quarantine /Applications/PixelSwitch.app`, or use System Settings → Privacy & Security → Open Anyway.
- Desktop widgets need a Developer ID signed build, so they do not load in this release.
