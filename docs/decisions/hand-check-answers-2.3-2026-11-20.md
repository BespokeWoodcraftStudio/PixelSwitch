# Founder's answers to the 2.3 hand checks (November 20, 2026)

Pasted in chat on November 20, 2026, about 9:15 AM PST, verbatim:

```
PIXELSWITCH: HAND CHECKS FOR 2.3 (Nov 20, 2026)

1. Does PixelSwitch say version 2.3 after it updates? [version]
   ANSWER: A. Passed: it says Version 2.3 (the recommended one)

2. Does the header show ahmed@pixelventures.ai once, not twice? [header]
   ANSWER: A. Passed: the address shows once (the recommended one)

3. Does the popover have only Usage and Costs, with bigger icons that explain themselves on hover? [popover]
   ANSWER: A. Passed: two tabs, five bigger icons, a label on each (the recommended one)
   COMMENT: works sometimes

4. Do Add current account and Sign in a new account work from the bottom icons? [add-sign-in]
   ANSWER: A. Passed: both icons worked (the recommended one)

5. Does Settings → Accounts have Switch, Re-sign and a "..." menu with Edit label and Remove, and do long addresses show in full? [settings-accounts]
   ANSWER: A. Passed: all buttons are there and every address shows in full (the recommended one)
   COMMENT: Yes but why did you put it behind 3 dots?  why not just have 3 icons?

6. On beeco1@pixelventures.ai, does Limit resets open claude.ai's usage page in a Chrome profile? [reset-beeco1]
   ANSWER: C. It did not work
   COMMENT: It takes me to the claude login page in a new window

7. On ahmed@bespokewoodcraftstudio.com, does Limit resets open your existing Chrome profile of that name? [reset-bws]
   ANSWER: C. It did not work

8. Does an expired card say "Click Re-sign to fix.", and does Re-sign work? [expired]
   ANSWER: A. Passed: the message and Re-sign both worked (the recommended one)

(8 of 8 answered)
```

Earlier the same morning, after updating to 2.3 (verbatim): "i updated to the newer version and now its 100% broken, it does NOT show up in my menu bar anymore"

## What we found and are doing (2.4)

- **Launch crash (his message above).** 2.3's one-time cleanup of the 2.2 window's data called WebKit's
  `WKWebsiteDataStore.remove(forIdentifier:)`, which crashed in WebKit on every launch (crash report
  PixelSwitch-2026-11-20-090543.ips). Unblocked by deleting the trigger key
  `ai.pixelventures.pixelswitch.claudeWeb.dataStoreIDs`; 2.4 removes every WebKit call.
- **Check 7 (and the cause of check 6's experience on ahmed@pixelventures.ai).** The log line at 17:11:14Z says no Chrome
  profile was signed in to ahmed@pixelventures.ai, yet Chrome's profile list has "Profile 1" for that address, so the
  matcher failed and a new empty profile ("PixelSwitch ahmed@pixelventures.ai") was opened on the login page. A bug; fixed in 2.4.
- **Check 6, beeco1.** beeco1 has no Chrome profile of its own, so by design (round-2 option A) it opens a new
  PixelSwitch Chrome profile where he signs in to claude.ai once; Chrome keeps the sign-in after that.
- **Check 5 comment.** Settings rows get plain icons for Switch, Re-sign, Edit label and Remove instead of the "…" menu.
- **Check 3 comment "works sometimes".** The hover labels used the system tooltip, which a menu bar popover shows
  unreliably; 2.4 shows its own label on hover.
