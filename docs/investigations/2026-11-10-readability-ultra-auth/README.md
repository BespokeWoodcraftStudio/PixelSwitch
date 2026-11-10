# 2.1 investigation: readable colours, Ultra accounts, sign-in expiry

Everything behind PixelSwitch 2.1 (built and merged on `main` on 2026-11-10; release: pending). The founder's four
requests, verbatim, and every decision with its reason: `docs/decisions/2026-09-24-remote-control-answers.md`, the last
section. The stories: worklog WL-2026-11-10-001 (colours), WL-2026-11-10-002 (sign-in expiry), WL-2026-11-10-003 (Ultra)
and WL-2026-11-10-004 (the session and its lessons). The page he checks by eye:
`docs/decisions/pixelswitch-hand-checks-2.1-2026-11-10.html`.

The files here are the lanes' own reports, copied as they were written. Paths inside them point at a scratchpad that no
longer exists; the commit shas below are the stable references. Line numbers in them were true at the sha each file names.

| File | What it is |
|---|---|
| `recon-colors.md` | Read-only report on why the popover was hard to read: it was see-through twice over (`NSPopover` plus `.ultraThinMaterial`) under low-alpha card fills, grey text at 3.95:1 on white, bar colours used as text at 2.0:1 to 3.2:1. Every place a background, material or tint is set, the token table, and the measured contrast of each proposed pair |
| `recon-ultra.md` | Read-only report for the Ultra change: how the plan is read and stored, every place a 5-hour limit is shown or acted on, the engine's handling of a missing window (already correct, pinned by two conformance vectors), the API facts (`five_hour: null` on every read, 2,463 reads on 7 accounts), and the repo being 104 commits behind at the start |
| `recon-auth.md` | Read-only report for the sign-in expiry work: how Claude Code blanks a dead login, how PixelSwitch handled a dead live login (it did not), the 2026-11-10 10:40Z case from the app log, and the proposal the lane built from |
| `review-auth.md` | Deciding review of AUTH-EXPIRY (f519f40 and b345bfa, merged 8cf6e27): SHIP, 0 P0, 0 P1, 4 P2 (F1 to F4), 9 P3. Settles from Claude Code's own bundle what a blanked login looks like and that the watch cannot race Claude Code's refresh |
| `rereview-auth.md` | Re-review of AUTH-FIXES (6ee54f6, merged 81b021c): SHIP, F1 to F3 fixed, F4 partly fixed; five new P3 (N1 to N3 and two smaller). N2 and N3 are what the lane `lane/auth-fixes-2` closes |
| `review-ultra.md` | First-pass review of ULTRA (b0e9521, merged 4b2e87b): SHIP-WITH-FIXES, 3 P2 and 4 P3, all fixed by 65c0e82 except the items the handover lists as follow-ups |
| `evidence/limits-light.png`, `limits-dark.png` | The Usage card as rendered from the real views after the COLOURS change (example data), light and dark |
| `evidence/accounts-light.png`, `accounts-dark.png` | Four account cards (Max and Pro, one active) after the COLOURS change, light and dark |
| `evidence/ultra-light.png`, `ultra-dark.png` | An Ultra card (no Session row) above a Max card (Session, Weekly, Fable), light and dark |

These renders come from `Tools/screenshots/make_screenshots.swift` through `ImageRenderer`. They cannot draw the real
popover frame (the arrow, corner radius and edge) and there is no "before" render, so nothing here is marked in red and
green. A before and after of the popover itself was not made.

## The commits

| Sha | Card | What |
|---|---|---|
| `53bde1c` | COLOURS | Opaque popover, solid card, tile and track fills, text tokens fixed, badge text `#111111`. Measured on the rendered pixels: every body pair at least 4.5:1, every graphic at least 3:1, both appearances |
| `f519f40`, `b345bfa` | AUTH-EXPIRY | New `Services/AuthHealth.swift` (`AuthHealth`, `LiveLoginWatch`, `DeadLoginPolicy`, `TargetRenewalPolicy`); a 30-second watch timer; a Manual only account with a dead login is left, like the 1.6 `noSubscription` rule |
| `b0e9521` | ULTRA | New `Models/AccountPlan.swift`; Max 20x resolves to Ultra; no Session row, no session menu-bar modules, widget session views hidden; CLI Session cell `n/a`; `AccountInfo.hasSessionLimit` (optional), revision 3 to 4, protocol version stays 2 |
| `6ee54f6` | AUTH-FIXES | Every switch target is renewed first (skipped when this app renewed it within 300 s with the same token fingerprint); a refusal flags the account and the next target is tried; a spent pair is never written live; the watch resets on every sign-in; dead code removed |
| `65c0e82` | ULTRA-FIXES | The API's data wins (a real `five_hour` reading is never dropped; `Account.sessionLimitObserved`); relaxed rule 9 is a no-op for an Ultra account (`AutoSwitchSettings.effectiveRule`); a known tier is never overwritten by `max_other` or `other`; `PlanBackfillLedger` retries an unreadable store at most 3 times per launch |
| `3ff3ce2` (merged `fb7d296`) | AUTH-FIXES-2 | In 2.1: rotation safety (persist the rotated pair before the identity lookup, a 15 second timeout on the renewal request, a settle result dropped when the token changed, a comment fix). It merged while the records lane ran, so the test numbers below do not include it |
| `e538ad5` (on `lane/ultra`, NOT merged) | ULTRA | MCP descriptions say an Ultra account has no 5-hour limit. Left unmerged on purpose: `linux/tests/test_mcp.py` pins the Linux MCP text to `PixelSwitchCLI/MCPServer.swift`, so it must land together with the matching `linux/pixelswitch/mcp.py` edit |

## What was verified, and what was not

- `bash Tests/run-unit-tests.sh` on the release tree (`main` at `f71f451` minus the docs, which holds every lane including
  `3ff3ce2`): 1763 passed, 0 failed, conformance 150/150. An earlier run at `4294327` (and the records lane's own tree, cut from
  it, without `3ff3ce2`) printed 1733; that number is superseded. Linux suite: 1670 OK, 47 skipped. A real Debug and a real Release `xcodebuild` also succeeded.
- A real Debug build (`xcodegen generate`, then `xcodebuild ... CODE_SIGNING_ALLOWED=NO build`) succeeded for the app, the
  widget and the CLI at `65c0e82`. `scripts/typecheck-reordered.sh` does NOT check the function bodies of `AppState.swift`;
  the real build is the proof.
- Not run: the live watch against a real dead login (no way to cause one on purpose without signing the founder's Claude
  Code out), `backfillPlans` against real saved logins, the real popover frame, the Linux port.
