## PixelSwitch 1.5

### New

- **Manual only.** Each account's threshold menu in **Settings → Accounts** now ends with **Manual only (0%)**.
  - Auto-switch never moves you to that account, but you can still switch to it yourself.
  - While you're on it, auto-switch moves you off only at your default threshold, and never brings you back to it.
  - Setting it on the account you're using doesn't move you off.
  - In the popover, those accounts show a quiet "Manual only" line.
  - If every other account is Manual only, Settings warns you that auto-switch has nowhere to go.
- **Per-account thresholds from 1% to 100%.** The menu adds 10%, 20%, 30% and 40%, and the stepper reaches every whole number down to 1%.
  - Below 20%, auto-switch takes an account while it is at or under half its threshold, so even a 5% account is still used.
  - Hover over an account's threshold to see exactly when auto-switch leaves it and when it takes it.
  - Thresholds from 50% to 100%, and the default, work exactly as before.
- **From the command line and Claude:**
  - `pixelswitch accounts threshold <account> manual` (or `0`), or any number from 1 to 100.
  - `pixelswitch accounts` marks Manual only accounts.
  - The MCP tools accept 0 to 100.

If you use `pixelswitch` right after updating while the old version is still running, it asks you once to quit and reopen PixelSwitch.

Manual only and thresholds below 50% need 1.5 or later. An older version would read them as 50%.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.4...v1.5
