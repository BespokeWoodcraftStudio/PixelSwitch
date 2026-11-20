## PixelSwitch 2.3

### New

- **Limit resets now opens claude.ai in your own Chrome.** The button on each account card opens that account's claude.ai usage page in the Chrome profile that belongs to the account. PixelSwitch finds the profile by the Google account Chrome shows for it. If no profile matches, PixelSwitch creates one named "PixelSwitch <email>" the first time, and you sign in to claude.ai there once. If Chrome is not installed, it opens your default browser. PixelSwitch only opens the page: you click claude.ai's own **Reset** button yourself, and PixelSwitch never uses a reset for you.
  - The claude.ai window built into PixelSwitch 2.2 is gone, and the data it left behind is removed the first time 2.3 starts. That window could not remember your sign-in, and Google does not allow signing in inside an app's own window.
  - Why not a one-click reset: Anthropic offers resets only to claude.ai and to Claude Code. PixelSwitch does not pretend to be either of them.
- **The Accounts tab is gone from the popover.** The popover now has Usage and Costs. Switch, Re-sign, Edit label and Remove are in **Settings → Accounts**, which opens on the tab you used last and is wider so long email addresses fit.
- **Bigger icons at the bottom of the popover, each with a label when you hover.** They are Add current account, Sign in a new account, Refresh, Settings and Quit. While a sign-in runs, the popover shows "Signing in…" with a **Show sign-in window** button. Add and Remove are off while a switch or a sign-in is running.

### Fixed

- **Your email shows once.** When your organization name is Anthropic's default ("<email>'s Organization") or missing, the popover header, the menu bar text and the widget show the email once instead of twice. A real organization name (a Team, for example) still shows above the email, and a label you chose still shows above the email. The name in the header stays on one line.
- **The expired message points at the right button.** It now says "Click Re-sign to fix." The old "(↻)" pointed at the Refresh icon.
- **An expired account can no longer be switched to by double-clicking it.** This is the same rule Settings already used for its Switch button.

### Linux

The Linux version gets the new "Click Re-sign to fix." wording. Nothing else changed there. It updates itself from these releases (each update is checked against PixelSwitch's signing key before it is installed).

For Ubuntu 24.04 or 26.04 with GNOME (Python 3.12 or later), install in one line:

```
curl -fsSL https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/install-linux.sh | bash
```

Or check the installer first:

```
curl -fsSLO https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.3/install-linux.sh
sha256sum install-linux.sh   # must print 31bdbeeb70388cf40da5797b881455ef0f7c1489e66ebbb4e07c5b9de03247a0
bash install-linux.sh
```

The new texts are in English, German, French, Japanese and Simplified Chinese on the Mac.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v2.2...v2.3
