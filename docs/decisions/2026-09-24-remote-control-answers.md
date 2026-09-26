# Remote control: founder's answers and added requirements

Page: [pixelswitch-remote-control-2026-09-24.html](pixelswitch-remote-control-2026-09-24.html). Answered 2026-09-25. Recorded verbatim.

## Answers

```
PIXELSWITCH: FOUNDER QUESTIONS, REMOTE CONTROL (2026-09-24)

1. How does the other Mac run work on this one today? [how-reach]
   ANSWER: It runs commands on this Mac (SSH, or a tool like it) (the recommended one)

2. What is the AI allowed to do? [permissions]
   ANSWER: Always on, everything allowed, no switch in Settings
   COMMENT: Should be able to do anything I ask it to do. Anything I can do on this computer, I should be able to do it from the other computer, and AI should be able to automate that.

3. How should the AI add an account? [adding-accounts]
   ANSWER: Both: save the current account, and start a new sign-in that I finish in the browser (the recommended one)

4. What should the AI plug into? [shape]
   ANSWER: Both: shell commands and an MCP mode (the recommended one)

(4 of 4 answered)
```

## Added requirements, same message

```
Some additional features I want to add to the product:
1. I want to add priority for which account gets selected next because maybe I have a specific order of accounts I want to have selected. I want to be able to define my own priority and I want to be able to have it select priority based on accounts resetting soonest. Let's say I have an account that is resetting in 12 hours for the whole week and I have 5% usage available. I want to be able to have it automatically select that one so that it finishes the usage on it, so it doesn't get reset and I waste that additional usage. Or, for example, I want to set my own priority and I want it to go account 1, then account 4, then account 2, etc. I want to be able to do that.
2. I want to be able to set an individual threshold to switch per account. Maybe I want the limits to be 70% for account A and 90% for account B. I want to be able to do that.
3. When adding a new account, I want to have the functionality where I can either:
1. Click a button which would open the link in a web browser, and then I do the authentication.
2. Have a copy-and-paste button. I copy it, then I can go and open a different browser than my default browser where I'm already authenticated with the different account, and then it finishes the authentication.  The reason is that if I have multiple accounts, maybe I have multiple web browsers I'm logged into Claude from. Maybe I have Safari, I'm logged into account A, and in Chrome I'm logged into account B, and in Firefox I'm logged into account C. Because of this, I want to be able to click a copy button, then I go and paste it into the right web browser, and I don't have to log in from that browser because I'm already logged in.  I want those features added to the product. Mind you, all this that you're adding needs to work through the GUI that's currently in place or through the CLI/MCP that you're building.
```

## What follows from them

- Answer 2 overrides the recommendation: no master switch and no permission levels. Anything that can run a command as this Mac's user can do everything, including removing accounts. The control channel still never leaves this Mac (answer 1) and never returns login tokens.
- Every new feature above is reachable from both the GUI and the CLI/MCP.

## Design review, answered 2026-09-25

Page: [pixelswitch-design-review-2026-09-25.html](pixelswitch-design-review-2026-09-25.html). Recorded verbatim.

```
PIXELSWITCH: FOUNDER QUESTIONS, DESIGN REVIEW (2026-09-25)

1. Approve the design [approve-design]
   ANSWER: Approve as written (the recommended one)

2. Should PixelSwitch switch early to use quota that is about to reset? [switch-early]
   ANSWER: Yes, switch early under that rule, only when "Resets soonest" is chosen (the recommended one)
   COMMENT: Feature that should be enabled or disabled Via s switch in the configuration settings

(2 of 2 answered)
```

What follows: the design at docs/superpowers/specs/2026-09-25-remote-control-and-auto-switch-design.md is APPROVED. Early switching (A3) is option (a), with its own on/off switch in Settings → General → Auto-switch, shown whenever "Resets soonest" is picked, on by default, and also settable from the CLI/MCP as `autoSwitch.drainEarly`.

## Build plans A, B and C, answered 2026-09-25

Page: [pixelswitch-plans-abc-2026-09-25.html](pixelswitch-plans-abc-2026-09-25.html). Recorded verbatim.

```
PIXELSWITCH: FOUNDER QUESTIONS, BUILD PLANS A, B AND C (2026-09-25)

1. Approve the plans for Parts A, B and C [approve-plans]
   ANSWER: Approve; build A, then B, then C, in this session (the recommended one)

2. What should "Most room left" mean once accounts have their own thresholds? [most-room]
   ANSWER: Rank by the room left under each account's own threshold (the recommended one)

3. When do you want to test by hand? [manual-checks]
   ANSWER: Once, on one build with all three parts (the recommended one)

4. Install Xcode on this Mac? [xcode]
   ANSWER: Not needed; keep building on GitHub (the recommended one)
   COMMENT: We can revisit Xcode in the future. Right now, if it's slower, it's okay. I'm not in a hurry on this app.

(4 of 4 answered)
```

What follows:
- Build A, then B, then C, in this session, single lane.
- "Most room left" ranks by the room left under each account's own threshold (a change to Plan A Task 3, made with a test).
- One round of hand checks, on one build with all three parts, after Part C.
- No Xcode for now; GitHub builds the app. Revisit later.

## Release 1.2 before the hand checks, 2026-09-25

In chat, after the test DMG was handed over. He asked:

> why can't I just use the updater from the PixelSwitch version I'm already running?

The answer given: the updater reads only the latest release's appcast. A branch build is not in it and carries the same build number (22) as 1.1. Publishing it would release it to every user before the hand checks.

He replied, verbatim:

> nobody has the software but me. I'm the only one using it.

What follows:
- Taken as a go to release 1.2 now, so his running 1.1 updates itself.
- The hand checks still happen, on the released 1.2; anything they find ships in 1.3.
- The stacked branches part-a, part-b and part-c were fast-forwarded into main. v1.2 (build 23) was tagged, and CI run 36221957278 publishes it.
- The test DMG was removed from Downloads.
- Standing consequence: PixelSwitch has one user, the founder. "Don't release before the hand checks" protects nobody, so releasing through the updater and checking afterwards is fine.

## Update automatically on by default, 2026-09-26

In chat, after 1.3 shipped with the box unticked by default. Verbatim:

> I notice that by default it's not checked. You should have it checked by default.  What I mean is the checkbox for "Update automatically."

What follows:
- `SUAutomaticallyUpdate: true` is in the app's Info.plist (project.yml). Sparkle uses it until someone unticks the box, which saves NO in user defaults, and that wins.
- Released as 1.4. His own Mac already had the box ticked (SUAutomaticallyUpdate = 1 in his defaults, read 2026-09-26), so his 1.3 should install 1.4 by itself within about 6 hours. That is the first end-to-end proof of automatic updates.

## Per-account thresholds down to 0% ("Manual only"), 2026-09-26

In chat, verbatim:

> okay, another update: I want to make it so the amount that you can set for the limit per account can go below 50%. I want it to be able to go all the way down to 0%, and at 0%, it's disabled from being switched to.  And maybe sometimes you don't want accounts to be used, so you want them disabled. Changing it to 0% would basically do that.  A quick way to do it would be to have a checkbox next to it or something that says "Disable auto switch," but something like that. Come up with a better idea.

How the design was chosen:
- A design workflow ran three independent designs (clarity first, engine correctness first, smallest change), two judges (founder lens, engine lens) and one synthesis. Run wf_4927df27-02c.
- Both judges picked the clarity design; the synthesis grafted the engine design's single exclusion rule and the minimal design's label reuse.

What was built (release 1.5):
- **"Manual only (0%)"** is the named last item of each account's threshold menu. There is no checkbox: one control, one stored value (`switchThreshold == 0`).
  - Auto-switch never moves you to it. `AutoSwitchEngine.ceiling` returns nil for 0, inside the one rule that ranking and verification share.
  - You can still switch to it by hand.
  - While it is active, auto-switch moves you off only at the default threshold and never early-drains it.
  - Nothing brings you back to it.
- **1–100%** per account. The stepper stops at 1, so nobody slides into Manual only by accident.
  - Below 20%, the room a target needs is half its threshold, so 5% is not a hidden second "never".
  - It is identical to 1.4 for 20–100% (pinned by loop tests).
- The global default stays 50–100.

Decisions NOT to do something (so a later session does not "fix" them):
- **No "Disable auto switch" checkbox.** It means two controls for one decision, allows a contradictory state ("disabled" but "Switch at 70%"), and the words could equally mean "never switch away from it".
- **Setting Manual only on the account you are using does not move you off it.** A settings change should not pull a live login out from under running sessions. The Settings row and the CLI say when you will be moved.
- **A deliberate manual switch to a Manual only account is not undone after the 5-minute cooldown.** The rejected alternative ("0 is reached at any reading") would rewrite the live login about 5 minutes after every manual pick.
- **No remembered previous threshold** when choosing Manual only. A hidden value would come back into force later; one click on Default or a number turns it back on.
- **No CLI aliases "off", "never" or "disable".** They read as "never switch away". Only `manual` and `0`.
- **No "Manual only" badge beside the name in the popover.** The address already truncates there (recorded in AccountSwitcherView and UsageDashboardView); a separate quiet line is used instead.
- **The global default cannot go below 50.** A 0% default would make every account Manual only.
