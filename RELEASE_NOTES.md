## PixelSwitch 1.7

### Fixed

- **Auto-switch no longer stays on an account that is used up.** When the account you're on reaches 100% of its 5-hour or weekly limit, Claude Code can't use it until that limit resets.
  - Before, auto-switch only moved you to an account at least 10 points under its own threshold. If every account was nearly full (92–99% of its week against a 98% threshold), it stayed on the empty one.
  - Now, once the account you're on is used up, any other account with at least a point of room under its own threshold will do. It's chosen the way you set in Settings → General.
  - When an account 10 points under exists, it's chosen exactly as before.
  - Running out of Fable alone doesn't count, because the other models still work.
  - Settings → General says so under Auto-switch.
- **A failed usage reading now says what happened, with a Retry button.** An error from Anthropic's side used to show "The operation couldn't be completed (… UsageError error 0.)". Now each kind of failure has its own message:
  - Anthropic's service is down: "Anthropic's usage service isn't responding (HTTP 503). Your account is fine; PixelSwitch keeps trying." It also has a **Status** link to status.claude.com.
  - You're offline: "Can't reach Anthropic. Check your internet connection."
  - Anything else: "Unexpected reply from Anthropic (HTTP …)".
- **The last reading stays on the card during an outage or while you're offline**, the way it already did for a rate limit. The card shows the bars and their "Updated … ago" time, with the problem under them.
- **Retry** on the card takes a fresh reading of that account straight away. It's a real button, so clicking it never switches accounts (double-clicking a card still does).

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.6...v1.7
