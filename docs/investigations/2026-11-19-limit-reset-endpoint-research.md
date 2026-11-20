# Limit reset endpoint research (2026-11-19)

Status: research only. No reset was triggered. No POST/PUT/PATCH/DELETE was sent. Only one GET was sent (status below). Tokens are never recorded here ("redacted").

## Verdict

Feasible with the OAuth credentials PixelSwitch already holds: LIKELY YES for listing (proven live), PROBABLY YES for triggering (proven only by reading the code, not by trying).
No claude.ai web session or cookie is needed. Claude Code itself ships this feature as the hidden slash command `/limit-reset`, and it uses only the OAuth bearer token.

## Source of the finding

Claude Code CLI 2.1.327, binary `/Users/ahmed/.local/share/claude/versions/2.1.327` (Bun bundle; JS is readable with `strings`). The feature's internal codename is "cedar_ember". Feature flag: `tengu_cedar_ember` (server-side GrowthBook; `/limit-reset` is hidden and enabled only when the flag is on and the account has a claude.ai login).

## Endpoint 1: list available resets (GET, proven live)

- `GET https://api.anthropic.com/api/oauth/usage?cedar_ember=1&skip_spend=1`
- Headers: `Authorization: Bearer <OAuth access token>`, `anthropic-beta: oauth-2025-04-20` (identical to PixelSwitch's `ClaudeService.getUsageLimits`, which already calls `/api/oauth/usage` without the `cedar_ember=1` query).
- Required scope: `user:profile` (Claude Code checks `scopes` contains it before asking). The login on this Mac has it.
- Live test, one account (the current Claude Code login, Max plan): HTTP 200. The response already had a top-level `cedar_ember` key. For that account it read: `eligible=false`, `ineligible_reason="surface"`, `grants=[]`, so this account had nothing to list. Shape confirmed from the live call (types only):
  `cedar_ember: { eligible: bool, ineligible_reason: str|null, at_limit: bool, exhausted: [str], grants: [..], next_grant_id: str|null, weekly_resets_at: str|null, cooldown_until: str|null, event_props: obj|null }`
- Each grant (from the Claude Code schema, not yet seen live because this account had none):
  `{ id: "[a-z0-9_-]{1,40}", label: str, resets_total: int, resets_left: int, starts_at: iso|null, ends_at: iso|null (the "Expires" date), clears: [limit keys], paused: bool, usable_now: bool, use_requires_limit: bool, percent_used: {limitKey: 0-100}, blocking: [limit keys], type: "session"|"weekly"|"full" (optional), gives_back_weekly: bool, unavailable_reason: "cooldown"|"blocked_five_hour"|"blocked_seven_day"|"blocked_opus"|"blocked_sonnet"|"blocked_other"|"cannot_clear"|"needs_limit"|"unavailable"|null }`
- `limit keys` are: five_hour, seven_day, seven_day_overage_included, seven_day_opus, seven_day_sonnet, seven_day_cowork, seven_day_omelette, seven_day_oauth_apps.
- `ineligible_reason` values: config_off, tier, seat, mobile, surface, cli_version, no_grant, tenure, other_experiment, unavailable, unknown.
- Other new top-level keys in the same response (names only): tangelo, omelette_promotional, nimbus_quill, cinder_cove, copper_kite, brass_thimble, harbor_lantern, maple_sundial, wattle_ember, promo_credits, amber_ladder, granite_teacup, amber_cistern(_offer), juniper_tide, starry_waterfall, amber_gauge, pace, indicator, spend, weekly_scoped_shares, seven_day_breakdown. Not investigated.

Does the existing PixelSwitch call carry reset fields? Its decoder (`PixelSwitch/Models/UsageData.swift`) ignores `cedar_ember`. The plain URL (no `cedar_ember=1`) probably does not return the block; the live call with the query parameter did. Not tested without the parameter.

## Endpoint 2: use a reset (POST, NOT tried, from code)

- `POST https://api.anthropic.com/api/organizations/{organizationUuid}/reset_rate_limits` (host inferred from Claude Code's shared API client; the path is exact).
- Body (JSON): `{"program":"cedar_ember","grant_id":"<grant.id>","request_id":"<random id, [A-Za-z0-9_-]{1,64}>"}`
- Auth: same OAuth bearer, `Content-Type: application/json`, 25 s timeout. Claude Code refreshes the OAuth token on 401 and retries.
- `organizationUuid` is the OAuth account's org id (Claude Code stores it as `organizationUuid`; PixelSwitch reads the same from `/api/oauth/profile`, so it is likely already available or one GET away).
- Only the grant that is `next_grant_id` and `usable_now` is accepted (reasons `not_next_grant`, `grant_id_required`, `unknown_grant`).
- `request_id` is an idempotency key: Claude Code reuses the same one if a claim was unconfirmed, so retries never spend a second reset. PixelSwitch must do the same.
- Response: `{ result: "reset"|"already_used"|"not_limited"|"cooldown"|"ineligible"|"unavailable", reason: str|null, resets_left: int|null, cleared: [limit keys], weekly_resets_at: iso|null, cooldown_until: iso|null }`. HTTP 429 = rate limited, 401/403 = auth. Claude Code treats a success it cannot confirm as "unconfirmed" and re-reads status before trusting it.

## Second program: 5-hour reset (juniper_tide), also OAuth

- `POST /api/organizations/{orgUuid}/reset_rate_limits` with body `{"program":"juniper_tide"}` (no grant). Response `{result, next_available_at}`. Meaning: reset the 5-hour limit now, once a week, and it still counts against the weekly limit. Flag `tengu_nifty_lemur`. This is the "Reset 5-hour" button, not the claude.ai "Limit resets" cards.

## Full reset vs Fable reset

Use the grant fields, not the label text:
- Fable reset: `clears` contains `seven_day_overage_included` and `gives_back_weekly=true` (Claude Code names it "Fable"; "frees up only what Fable used this week"). It needs Fable usage to exist (`percent_used` for that key above 0; otherwise `unavailable_reason` is like `cannot_clear` or `needs_limit`, which matches the greyed "no Fable usage to reset" button).
- Full reset: `type="full"`, or `clears` includes `seven_day` (and five_hour). The weekly reset day does not change.
- `type="session"`: clears five_hour only.
- When available: `usable_now=true`, `paused=false`, `resets_left>0`, now before `ends_at`, and `cooldown_until` empty. `use_requires_limit=true` means the account must be at a limit first; Claude Code asks "use it anyway?" when it is not (the web Fable card is not gated that way).

## What is proven and what is not

Proven: the listing GET works with the OAuth token PixelSwitch holds, returns the `cedar_ember` block, and the POST contract is exactly known from the shipped client.
Not proven:
1. A live grant (this Mac's current account has none; ineligible_reason "surface"). The grant field values above come from the schema, not a live sample.
2. That the server accepts a claim from a non-Claude-Code client. `event_props.surface` has `claude_ai | claude_code_cli | unknown`, and `ineligible_reason="surface"` exists, so the server may restrict some grants to one surface. Possibly the two accounts the owner expects to hold a full reset show `eligible=true` and the others show "surface". Needs one listing run per account.
3. The `/limit-reset` command is behind a server flag (`tengu_cedar_ember`) per account; a flag-off account might be refused on POST (`config_off`).
4. Whether a PixelSwitch-held token has `user:profile`. Tokens from older sign-ins may lack it; PixelSwitch tokens that predate the scope would need a re-sign-in.
5. Step 3 (claude.ai web app network capture) NOT done: the Claude in Chrome extension was not connected (tabs_context_mcp: "Browser extension is not connected"), so the claude.ai page's own request was not observed. Likely the web button hits the same `reset_rate_limits` path with the session cookie, but that is unverified.

## What PixelSwitch needs (if built)

1. Per account: add `cedar_ember=1` to its usage URL (or one extra listing call) and decode `cedar_ember` leniently (as it does `limits`).
2. Org id per account (from `/api/oauth/profile`, already called) for the POST.
3. A Reset button per account that is enabled only when a grant has `usable_now`, `resets_left>0`, not `paused`; shows "Fable" vs "Full" per the rules above; asks for confirmation (irreversible, and Fable reset is once per grant use).
4. Persist a `request_id` per attempt, reuse it on retry, then re-list before reporting success.
5. Respect `UsageRequestGuard` (429 with Retry-After 3600 hits the same host).
6. Do a first-run read-only listing for every account and record which are `eligible`, before writing the POST code.

## Open questions

- Which of the owner's accounts return `eligible=true` and a full grant? (One GET per account.)
- Does the server reject a claim whose caller is not Claude Code, given the `surface` field? Safest test: owner clicks one real reset, or run `/limit-reset` inside Claude Code on an account with a grant (hidden command; it may not appear if the flag is off).
- Does the plain `/api/oauth/usage` URL omit `cedar_ember`?
- Exact base URL for the POST (inferred `api.anthropic.com`).
