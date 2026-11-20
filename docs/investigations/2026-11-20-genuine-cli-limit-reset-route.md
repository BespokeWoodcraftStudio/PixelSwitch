# Genuine Claude Code CLI as the limit-reset route (2026-11-20)

Status: read-only research. Nothing was run that can use a reset: no `/limit-reset`, no interactive `claude`, no POST, no altered headers. Sources: the installed CLI bundle `/Users/ahmed/.local/share/claude/versions/2.1.327` (Mach-O with a readable embedded JS bundle; offsets below are byte offsets into that file), `claude --version` / `claude --help`, the CLI's own `~/.claude.json` caches, and PixelSwitch source. No token or cookie was printed. Builds on `2026-11-19-limit-reset-endpoint-research.md`, `-grants-per-account.md` and `-legitimate-routes.md`. Account names below are short names (the address is in `oauthAccount.emailAddress` of `~/.claude.json`).

## Answer in one paragraph

A design exists where PixelSwitch only launches and steers the genuine `claude` and the genuine client makes every request. There is no headless route: `/limit-reset` is a TUI-only command, so PixelSwitch would have to drive `claude` inside a pseudo-terminal. It is UNPROVEN that Anthropic's server offers any reset to the CLI surface for any of our accounts. For the one account whose CLI state is on disk (the currently active one, the "ahmed" account on the pixelventures.ai domain) the evidence says no: the CLI's feature gate for resets is not set and the CLI's own cached usage response carries `cedar_ember: null`. The decisive test is a single, safe step by the founder as beeco1 (section 6).

## 1. Is the genuine CLI offered resets? Cached state

- Active account now: the pixelventures.ai "ahmed" account (Max, Stripe billing), per `oauthAccount` in `~/.claude.json`. (Its full address is given in the hand-off message, not repeated in this file.)
- The CLI does fetch the listing outside `/limit-reset`, in two places (bundle offsets 318316000-318330000):
  - On a rate-limit wall (`se()` -> `kkr(..., {surface})`, read `at_wall`), when the gate is on.
  - A background "do you hold an unused reset" check (`Oe`, around 318327000): `kkr(e, {read:"cedar_ember"})`, only when the login source is claude.ai, the mode is the interactive CLI, and the gate is on. It writes `unusedLimitResetStatusCache` to `~/.claude.json` as `{accountUuid, holdsUnusedReset: bool, checkedAt, validUntil}` (valid up to 3 days). It stores ONLY a yes/no, never grants or ids.
- `~/.claude.json` for the active account:
  - `unusedLimitResetStatusCache`: absent (never written).
  - `cachedUsageUtilization` (the CLI's last `/api/oauth/usage` response, fetched 2026-11-20 07:30 local): has a `cedar_ember` key whose value is **null**, and `juniper_tide` is null too. So for the CLI's own identity the server served no reset block at all for this account.
  - There is no `~/.claude/debug/` folder and no other cache under `~/.claude` that mentions `cedar_ember`.
- Only the active account has any CLI-side cache. `cachedUsageUtilization` is keyed by `accountUuid` and wiped on a switch (`t2o`, offset 293677900). Nothing is cached for beeco1, beeco2 or the others.
- Eligible / ineligible_reason / grant types for the active account: **not served** (null block). Nothing to report for the others without running the CLI as them.

## 2. How the server decides surface; what the CLI sends

- Both the listing GET and the reset POST go through the CLI's shared HTTP client (`zt`). Per request headers: `Authorization: Bearer <OAuth access token>`, `anthropic-beta: oauth-2025-04-20` (`_u`, offset 283302033), `Content-Type: application/json` on the POST, plus the client's standard identity headers from `Au()` (offset 289875073): `x-app: cli` (`cli-bg` for background sessions), `User-Agent: claude-cli/<version> (external, <entrypoint>)`, `X-Claude-Code-Session-Id: <session uuid>`. Timeouts: GET 5 s, POST 25 s.
- The listing URL is `/api/oauth/usage?cedar_ember=1&skip_spend=1` (offset 140561580, built by `Se()` at 293656824). At a wall it is `?at_wall=1&starry_waterfall=<cli_menu|...>&skip_spend=1`. The CLI sends no `surface` parameter for the cedar_ember read. So the server infers the surface from request identity (headers/User-Agent), which is exactly what PixelSwitch will not imitate.
- Expected values: `event_props.surface` is parsed as `claude_ai | claude_code_cli | unknown` (schema `fe`, offset 318316600). `ineligible_reason` is parsed as `config_off, tier, seat, mobile, surface, cli_version, no_grant, tenure, other_experiment, unavailable, unknown`.
- Does the CLI treat `ineligible_reason: "surface"` specially? **No.** It is only logged to telemetry. Any ineligible block ends in the generic text "No reset to use · you're not at a usage limit right now" (`An()`/`De`+`zn`). So the CLI code gives no message like "only on claude.ai". That `claude_code_cli` exists as a surface value in the schema, and that the CLI ships the whole flow, strongly suggests the CLI is an intended surface, but it proves nothing about our grants.
- The POST: `POST /api/organizations/{organizationUuid}/reset_rate_limits` with `{program:"cedar_ember", grant_id, request_id}` (`Ays`, offset 318322476). `organizationUuid` comes from `oauthAccount` in the CLI's global config (`En()?.organizationUuid`), so a CLI run as another account needs that account's `oauthAccount` block in its config.

## 3. The gate

- Server flag `tengu_cedar_ember` (GrowthBook), read as `b("tengu_cedar_ember", {})` and treated on only if the object has `enabled === true` (`mSe()`, offset 296854058). Text, kinds and version also come from this object.
- Cached locally in `~/.claude.json` under `cachedGrowthBookFeatures` (1471 entries, refreshed 2026-11-20 07:32).
- Value for the active account: **`tengu_cedar_ember` is not present in the cache**, so the CLI uses the default `{}`, so `mSe()` is false. The sibling 5-hour program flag `tengu_nifty_lemur` is cached as `{"enabled": false, "version": 0}`.
- The command is registered `type:"local-jsx"`, `isHidden:true`, `isEnabled: Pt() && (EDe() || mSe())` (offset 296857503): it only exists when the account is a claude.ai subscriber AND one of the two flags is on. With both off, as on the active account, typing `/limit-reset` finds no such command.

## 4. The `/limit-reset` flow, exactly

- Argument parse (`z()`, offset 338846000): empty = no kind; `5-hour|5h|five-hour` = session; otherwise one of `session, weekly, fable, full`. Anything else prints "That isn't a reset name · try /limit-reset fable, full, 5-hour or weekly, or just /limit-reset to see the resets you have".
- If the cedar_ember flag is OFF but the 5-hour flag is on, any argument runs the 5-hour program (`v$t`). That path claims with NO confirm dialog, but only while the account is at a rejected 5-hour wall and the server marks it available.
- Flag ON, not at a wall (`Qr` -> `Zr`, offset 318349303): one GET (`read:"cedar_ember"`), then:
  - no usable grant: prints "No reset to use · you're not at a usage limit right now" (or "Your reset can't be used right now ...", "That reset was already used ...").
  - one offer kind, or an argument that matches one: goes straight to the confirm dialog (so `/limit-reset full` skips the list).
  - several kinds and no argument: a "Which reset do you want to use?" list; selecting one opens the confirm dialog. Unusable kinds are listed greyed with the reason.
  - an argument naming a kind that is not offered: prints "Your {kind} can't be used right now · {reason} · you can use ...", and does nothing.
- Flag ON and at a wall: the same, but offers are filtered to what refills the limit that was hit.
- Confirm dialog (`xKe`, offset 338225900): title "Use a {kind}?", body "Refills your {limits} now · {returns} · your weekly reset day stays {week}", detail "{n} left · use by {date}", options "Yes, use my {kind}" / "No, keep it". **The default focus is Yes** (`focus` defaults to `"confirm"`, Yes listed first; `$n` at offset 308079680). Esc = "No, keep it" (prints "Resets kept · run /limit-reset when you want one · first expires ..."). Enter on the default means Yes. There is an accidental-input guard window after the dialog opens (`refuseInput` / `windowMs`): early keys are refused, so a script must wait. In screen-reader mode it becomes a "y. Yes / n. No / Enter y/n:" text prompt instead.
- Result strings: success "Limits reset" / "Weekly limits reset" / "Fable reset used · what Fable used is back in your weekly limit" / "5-hour limit reset", then " · your weekly reset day stays {date} · {n} left" and "send a message to continue" if the session did not auto-continue. Failures: "Couldn't reset your limits · nothing was used · try again in a moment", "Couldn't confirm the reset went through ...", "Your earlier reset went through after all", "Your limits are clear", "This reset isn't available any more · nothing was used", "Couldn't reset your limits with this login · run /login, then try again". The request id is a fresh UUID, reused on a retry of an unsettled claim, so a retry cannot spend a second reset.
- Non-interactive path: **none found.** `/limit-reset` is `local-jsx` with no `supportsNonInteractive`; the CLI has no `limit-reset` subcommand (`claude --help` Commands: agents, attach, auth, auto-mode, doctor, gateway, import, install, logs, mcp, plugin, purge, respawn, rm, setup-token, stop, ...); no MCP tool or SDK control request for it exists in the bundle (only the TUI components and the claim function inside the rate-limit module). So print mode / stream-json cannot run it. Only a pseudo-terminal can.
- Safety note for any test: bare `/limit-reset` can fall into the 5-hour path with no dialog (`l===undefined && EDe() && no cedar offer`), and claims immediately if the account is at a rejected 5-hour wall and the server marks that reset available. Never run it bare while at a 5-hour limit.

## 5. Running the genuine CLI as a chosen account

- Credentials on macOS: Keychain generic password, service `"Claude Code" + OAUTH_FILE_SUFFIX("") + "-credentials" + suffix`, where suffix is empty when `CLAUDE_CONFIG_DIR` is unset, else `-` + the first 8 hex of the SHA-256 of the config dir path (`_9()`, offset 286182450). So the default is `Claude Code-credentials` (confirmed present in this Mac's keychain; same constant PixelSwitch uses, `KeychainService.swift:60`). `CLAUDE_SECURESTORAGE_CONFIG_DIR` can override. Fallback file: `<config dir>/.credentials.json` (default `~/.claude`).
- `HOME` alone is NOT enough and is dangerous: with `CLAUDE_CONFIG_DIR` unset the service name stays `Claude Code-credentials`, so a process with only a different HOME would still read (and may refresh and rewrite) the LIVE account's login. Isolation must use `CLAUDE_CONFIG_DIR`.
- PixelSwitch today swaps the live login by writing the target's token into that one keychain item and its `oauthAccount` into `~/.claude.json`, then bumping `~/.claude/.credentials.json` mtime (`ClaudeService.switchAccount`, steps 1-4, lines ~520-640; `ClaudeCredentialsFile`). Running sessions follow the switch. It never sets `CLAUDE_CONFIG_DIR`; subprocesses get only a PATH fix and `HOME` (`ClaudeProcessEnvironment`).
- Can PixelSwitch run one isolated `claude` as account X without switching? **Yes, in principle, using a temporary config dir:** create `$TMP/cfg` (0700); write X's `claudeAiOauth` credential from PixelSwitch's backup to `$TMP/cfg/.credentials.json` (0600); write `$TMP/cfg/.claude.json` with X's saved `oauthAccount` (carries `accountUuid` and `organizationUuid`, which the claim needs) plus onboarding-done flags; launch with `CLAUDE_CONFIG_DIR=$TMP/cfg`. The keychain service becomes `Claude Code-credentials-<hash>` (a separate item), so the live account and the 32 running Claude processes are untouched. No switch and no switch-back is needed. The global-config location under `CLAUDE_CONFIG_DIR` (`<dir>/.claude.json`) is inferred, not read from code.
- Cost and risks of isolation (unproven): (a) the copy may refresh and rotate the refresh token; if X is not the live account PixelSwitch must read the rotated credential back and update X's backup, exactly as its existing renew path does, or X's saved login goes stale. (b) If X IS the live account, run with the normal environment instead (no copy), so the live token is not forked. (c) A brand-new config dir triggers first-run onboarding, the folder-trust prompt and possibly a login screen; these need pre-seeding or scripted answers. (d) Deleting the temp dir and any hashed keychain item afterwards.

## 6. Ranked recommendation

1. **Build "Reset" as a PTY-driven genuine CLI, but prove the route first (the one-step test below).** Design if it passes:
   - Listing before any click: PixelSwitch cannot read the offers honestly itself (its own call is answered "surface"). It asks the genuine CLI: spawn `claude` in a pseudo-terminal as account X (isolated config dir; live environment if X is active), type `/limit-reset full`, `/limit-reset fable`, `/limit-reset weekly` one at a time (explicit kinds avoid the unconfirmed 5-hour path), read the screen, always press Esc, quit. A dialog "Use a {kind}?" with "{n} left · use by {date}" means a reset is available; other texts are the reasons. Cache the result per account with a timestamp and a "Check resets" refresh button; never auto-poll. For the ACTIVE account PixelSwitch can also read `unusedLimitResetStatusCache.holdsUnusedReset` from `~/.claude.json` for free, but only when the CLI has written it.
   - On the founder's click ("Use Fable reset for beeco1" with its own confirm sheet in PixelSwitch): spawn the same way, type `/limit-reset <kind>`, wait out the input guard, press Enter on the default Yes only after a screen match on "Use a {kind}?" for the exact kind and account; capture the result line; kill the process; write back rotated credentials; re-list. If any screen is unrecognised, press Esc and report nothing was used.
2. **Fallback if screen-driving proves brittle:** a "Reset in Terminal" button that opens Terminal.app with the isolated environment and `claude` already started, so the founder types `/limit-reset full` and presses Enter himself. Still the genuine client, still explicit; no screen scraping, one manual keystroke.
3. **Do not build** any headless claim, any print-mode or SDK route (none exists), or any PixelSwitch request to the reset endpoints. This is consistent with the 2026-11-19 decision in `docs/decisions/2026-09-24-remote-control-answers.md` (a reset is only ever used by his click, through a genuine client).

### What remains unproven

- That the server ever offers a grant to the CLI surface for beeco1 (or any account). The only CLI-side evidence, the active account, shows no block and no flag.
- That `tengu_cedar_ember` is on for beeco1 or the other accounts (the cache only covers the active one).
- Whether the PTY steering, onboarding pre-seed and credential write-back work reliably in a temporary config dir.
- The input-guard window length (value not extracted).

### One safe step the founder can do himself (proves or kills the whole route)

In Claude Code on the Mac, signed in as **beeco1** (switch with PixelSwitch first, then start a NEW Claude Code session): type `/limit-reset full` and press Enter, and only look.
- "Unknown command" / it is not in the slash menu: the server flag is off for that account. The route is dead for it, with no cost.
- A dialog titled "Use a full reset?" appears: the genuine CLI is offered the reset. **Press Esc** (it says "Resets kept"). Do not press Enter or `y`: the cursor starts on "Yes, use my full reset".
- A plain line such as "No reset to use · you're not at a usage limit right now": the gate is on but no grant is served to the CLI surface. Route dead on this surface.

Do not type bare `/limit-reset` while the account is at a 5-hour limit (section 4 safety note). To see the other kinds, repeat with `fable` or `weekly`.

## Files read

`~/.local/share/claude/versions/2.1.327` (bundle), `~/.claude.json` (selected keys only), `~/.claude/` (cache search), `PixelSwitch/Services/KeychainService.swift`, `ClaudeService.swift`, `ClaudeCredentialsFile.swift`, `ClaudeProcessEnvironment.swift`, `docs/decisions/2026-09-24-remote-control-answers.md`, the three 2026-11-19 investigation notes.
