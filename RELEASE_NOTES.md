## PixelSwitch 1.6

### Fixed

- **Auto-switch no longer gets stuck on an account with no active subscription.** When Anthropic refuses an account's usage request with "OAuth authentication is currently not allowed for this organization", that account has no usage to reach a threshold with, so auto-switch used to wait on it forever.
  - Now, if the account you're on is in that state, auto-switch moves you on the next refresh to another account that still has room, chosen the way you set in Settings → General. It doesn't wait for the 5-minute cooldown.
  - It never moves you to an account in that state, and never to a Manual only account.
  - An account whose subscription comes back is used again once PixelSwitch reads its usage.
  - Settings → General explains this under Auto-switch.

With auto-switch off, nothing moves; the account's card still says "No active subscription on this account".

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.5...v1.6
