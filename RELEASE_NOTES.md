## PixelSwitch 1.9

### Fixed

- **"API Rate Limited. Try again later." no longer appears on your accounts' cards, and PixelSwitch no longer causes it.** Claude Code was never rate limited; only PixelSwitch's own usage lookup was. Anthropic answers a few refused lookups on the same expired or revoked login with an hour-long "too many requests", and PixelSwitch kept sending those logins. Now:
  - It never sends a login it knows is expired or was already refused. A saved login that has expired is renewed first, then asked once.
  - If the login Claude Code is using has expired while you weren't working, PixelSwitch sends nothing. The card keeps its last numbers and says "Waiting for Claude Code to renew this login. It renews the next time a session runs." The next time a session runs, the account reads again by itself.
  - After any refused lookup it leaves that login alone for 20 minutes, however many accounts you have. Before, parking one account made PixelSwitch check the others more often, which kept the problem going.
  - An account whose saved login Anthropic has refused (you need to sign in again) is no longer asked about at all, can't be switched to, and its card says "Session expired. Re-authenticate (↻) to fix." Before, it was asked about on every turn and looked switchable.
- **Signing in again, switching to an account or Claude Code renewing a login now clears the old message at once.** Before, a card could stay blank or bannered for up to an hour after you had fixed the account, because the wait was kept for the account, not for the login.
- **When Anthropic really does pause a reading, the card says so plainly.** "Usage reading paused until 2:48 PM. Claude Code itself is not affected." The last reading stays on the card, and the message never covers an expired or re-sign message.
- **Switching to an account no longer sends an expired login to Anthropic first.** It checks the expiry and renews the saved login before asking whose it is.

### Changed

- PixelSwitch no longer runs `claude auth status` to try to refresh the login Claude Code is using. In 13 tries it never refreshed anything; it only asked again with the same expired login.

The new messages are in English, German, French, Japanese and Simplified Chinese. The full explanation, with the measurements, is in `docs/investigations/2026-10-16-usage-429.md`.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.8...v1.9
