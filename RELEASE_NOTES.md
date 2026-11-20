## PixelSwitch 2.4

**If PixelSwitch 2.3 never appears in your menu bar, download and install 2.4 from the release page by hand; 2.3 can crash before it can update itself.**

### Fixed

- **The crash at launch is gone.** PixelSwitch 2.3 crashed every time it started on a Mac that had opened the 2.2 Limit resets window, so it never showed in the menu bar. The clean-up of that window's leftovers no longer uses WebKit at all, and the app was started with the broken 2.3 state in place and stayed up.
- **Limit resets opens the right Chrome profile.** macOS does not let PixelSwitch read Chrome's list of profiles, and Chrome opens the last-used profile (some other account's) when it is given an email it cannot match. So PixelSwitch no longer sends an email alone. Each account now has a stored Chrome profile choice, and an account without one gets its own PixelSwitch profile, where you sign in to claude.ai once.
- **Hover labels show every time.** The footer icons and the icons in Settings now draw their own label when you rest the pointer on them. The system tooltip showed only sometimes in a menu bar popover.

### New

- **Five icons on every account row in Settings → Accounts:** Switch, Re-sign, Edit label, Chrome profile (a globe whose menu shows the choice for that account) and Remove. The "..." menu is gone.
- PixelSwitch still only opens claude.ai's usage page. You click claude.ai's own **Reset** button yourself, and PixelSwitch never uses a reset for you.

### Linux

The Linux version has no changes in this release. It updates itself from these releases (each update is checked against PixelSwitch's signing key before it is installed).

For Ubuntu 24.04 or 26.04 with GNOME (Python 3.12 or later), install in one line:

```
curl -fsSL https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/install-linux.sh | bash
```

Or check the installer first:

```
curl -fsSLO https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.4/install-linux.sh
sha256sum install-linux.sh   # must print 31bdbeeb70388cf40da5797b881455ef0f7c1489e66ebbb4e07c5b9de03247a0
bash install-linux.sh
```

The new texts are in English, German, French, Japanese and Simplified Chinese on the Mac.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v2.3...v2.4
