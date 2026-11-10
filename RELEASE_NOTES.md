## PixelSwitch 2.1

### New

- **Ultra accounts have no 5-hour limit, so PixelSwitch stops showing one.** Anthropic removed the 5-hour limit from its Max 20x plan, now called Ultra. For an account on that plan, the plan badge says **Ultra** instead of Max, and the usage card shows only Weekly and Fable. The menu bar's 5-hour items are left out while an Ultra account is active, the widget's 5-hour views are hidden, Settings → Accounts says **No 5-hour limit** where the Session limit used to be, and auto-switch follows the weekly limit alone. In `pixelswitch accounts` the Session column reads `n/a`.
  - If PixelSwitch cannot tell an account's plan, it keeps the 5-hour limit exactly as before. If Anthropic ever reports a real 5-hour reading for an account marked Ultra, PixelSwitch shows it and uses it again.
  - Settings → General still has the "Session (5-hour) default". It still applies to your other accounts.
- **PixelSwitch no longer switches you onto an account that needs signing in again, and it moves you off one that stops working.**
  - Before every switch, PixelSwitch renews the saved login of the account it is about to switch to. If Anthropic refuses it, that account is marked as needing sign-in (**Session expired. Re-authenticate (↻) to fix.**) and auto-switch tries the next account. If Anthropic cannot be reached, the switch goes ahead as before.
  - Every 30 seconds PixelSwitch checks the login Claude Code is using. If Claude Code has signed that account out, PixelSwitch confirms it with a second look and, with auto-switch on, moves you to an account that works within about a minute, so Claude sessions do not sit stuck. It does this even for an account set to Manual only, because you cannot work on a login that is signed out.
  - With auto-switch off, nothing moves: the account is marked as needing sign-in and the log says so once. To fix an account that needs signing in, use the refresh button (↻) on its row in the Accounts tab, or `pixelswitch accounts reauth <account>`.
  - An expired token on its own is not treated as signed out: Claude Code renews those itself. Being offline, a busy server or a slow reply never counts either.

### Fixed

- **The menu bar window is easy to read in light and in dark.** The window, its cards, usage bars and badges were see-through, so whatever was on your desktop showed behind the words. They are now solid, the grey text is stronger, the percentages and badges are darker or lighter as needed, and the active-account outline is easier to see. The new colors were measured against their backgrounds in both light and dark: text at least 4.5 to 1, bars and outlines at least 3 to 1. Colors you chose yourself for the limit bars stay as you chose them.

### Linux

The Linux version is unchanged in 2.1. It does not have the Ultra change or the sign-in check yet. It updates itself from these releases (each update is checked against PixelSwitch's signing key before it is installed).

For Ubuntu 24.04 or 26.04 with GNOME (Python 3.12 or later), install in one line:

```
curl -fsSL https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/install-linux.sh | bash
```

Or check the installer first:

```
curl -fsSLO https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.1/install-linux.sh
sha256sum install-linux.sh   # must print 31bdbeeb70388cf40da5797b881455ef0f7c1489e66ebbb4e07c5b9de03247a0
bash install-linux.sh
```

The new text, "No 5-hour limit", is in English, German, French, Japanese and Simplified Chinese on the Mac.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v2.0...v2.1
