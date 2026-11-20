## PixelSwitch 2.2

### New

- **A Re-sign button on an account card whose sign-in has expired.** When a card in the main window says the account's sign-in expired, a **Re-sign** button now sits on it. It does the same thing as the refresh button (↻) in the Accounts tab: it opens the sign-in panel for that account. An account with no subscription gets no Re-sign button, because signing in again would not help.
- **Each plan has its own chip color.** Ultra is orange, Max blue, Pro gold, Team green, Enterprise violet and an unknown plan grey. An account with no subscription shows a grey **No plan** chip. If all your accounts are Ultra, they all stay orange.
- **A Limit resets button on every account card.** It opens that account's own claude.ai usage page in a PixelSwitch window, so you can see the account's limit resets and use one yourself on claude.ai's own button. PixelSwitch never uses a reset for you.
  - Each account has its own saved claude.ai sign-in, set up once. The window shows **Sign in as** the account's email while you are signed out, and you can sign out of claude.ai from the window.
  - If claude.ai emails you a sign-in link instead of signing you in, paste it into the **Paste sign-in link** field. **Open in browser** opens the same page in your browser.
  - If the page does not load, a banner offers **Reload**.
  - Removing an account also deletes its saved claude.ai sign-in.
  - Why a window and not a button that resets directly: Anthropic's server offers resets only to claude.ai and to Claude Code, and answered "not eligible" to PixelSwitch for every saved account. PixelSwitch does not pretend to be either of them.
  - Not yet proven: whether "Continue with Google" works inside this window. If Google refuses it, use claude.ai's email option and paste the emailed link into **Paste sign-in link**.

### Fixed

- **The "Updated N min ago" line at the bottom of a card shows whole minutes and is never cut off.** It no longer shows seconds, so it no longer flickers, and when space is short the **Double-click to switch** hint moves into a tooltip. "Double-click to switch" and "Switching…" are now translated into German, French, Japanese and Simplified Chinese.

### Linux

The Linux version is unchanged in 2.2. It does not have the Ultra change, the sign-in check or the new main-window buttons yet. It updates itself from these releases (each update is checked against PixelSwitch's signing key before it is installed).

For Ubuntu 24.04 or 26.04 with GNOME (Python 3.12 or later), install in one line:

```
curl -fsSL https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/install-linux.sh | bash
```

Or check the installer first:

```
curl -fsSLO https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v2.2/install-linux.sh
sha256sum install-linux.sh   # must print 31bdbeeb70388cf40da5797b881455ef0f7c1489e66ebbb4e07c5b9de03247a0
bash install-linux.sh
```

The new texts are in English, German, French, Japanese and Simplified Chinese on the Mac.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v2.1...v2.2
