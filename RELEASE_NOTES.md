## PixelSwitch 1.8

### Changed

- **Auto-switch now switches at your number.** With a 98% threshold, when the account you're on reaches 98%, PixelSwitch moves you to any account below 98% (at least a point under its own threshold), choosing it the way you set in Settings → General.
  - Before, the other account had to be 10 points under its threshold (88% or less at 98%). On a week when every account was nearly full, that meant no switch until your session ran out.
  - This covers the 5-hour, weekly and Fable limits. With the Fable switch off, Fable is still only a reading.
  - It can't bounce you back and forth: it leaves an account only at or over its threshold, moves only to one under it, and waits at least 5 minutes between automatic switches.
  - It still never moves you to a Manual only account, to one with no subscription, or to one it can't read. The chosen account is re-checked with a fresh reading first.
  - An account with its own threshold (Settings → Accounts) is judged against its own number.
- Settings → General and the Settings → Accounts tooltips say so. 1.7's separate line about a used-up account is gone, because the main rule now covers it.

Your accounts, settings and saved logins carry over as they are.

**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/v1.7...v1.8
