## PixelSwitch 1.3

### New

- **Update automatically.** Tick **Settings → About → Update automatically**, and PixelSwitch looks for a new version every 6 hours, downloads it in the background, and restarts into it by itself.
  - It never restarts in the middle of an account switch or a sign-in: it waits until that is done.
  - Leave the box unticked and nothing changes: PixelSwitch still checks, and asks before installing.
  - It can also be turned on from the command line, with `pixelswitch settings set updates.automatic on`, or by Claude through the MCP server.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.2...v1.3
