# Limit reset grants, per saved account (2026-11-19)

Read-only probe. One GET per account to the usage endpoint with `?cedar_ember=1&skip_spend=1` (headers: bearer token, `anthropic-beta: oauth-2025-04-20`). No POST, no refresh, no keychain or repo writes. Tokens never printed or stored. Background: `2026-11-19-limit-reset-endpoint-research.md`.

Tokens come from PixelSwitch's single keychain item (service `ai.pixelventures.pixelswitch.backups`, account `all-accounts`, JSON `{accountId: {token, oauthAccount}}`; `token` is a credentials JSON whose `claudeAiOauth` holds `accessToken`, `expiresAt`, `scopes`, `subscriptionType`, `rateLimitTier`). Seven accounts saved. None were expired.

## Result

| Account | HTTP | eligible | ineligible_reason | Full reset | Fable reset |
|---|---|---|---|---|---|
| claude@pixelventures.ai | 200 | false | surface | none (grants=[]) | none |
| beeco1@pixelventures.ai | 200 | false | surface | none | none |
| ahmed@bespokewoodcraftstudio.com | 200 | false | surface | none | none |
| ahamade@gmail.com | 200 | false | surface | none | none |
| ahmed@pixelventures.ai | 403 | unknown | unknown | unknown | unknown |
| beeco2@pixelventures.ai | 200 | false | surface | none | none |
| claude@bespokewoodcraftstudio.com | 200 | false | surface | none | none |

Every 200 returned the identical `cedar_ember` object:
`{eligible:false, ineligible_reason:"surface", at_limit:false, exhausted:[], grants:[], next_grant_id:null, weekly_resets_at:null, cooldown_until:null, event_props:null}`.
No grant exists on any account, so there are no `label/type/clears/gives_back_weekly/usable_now/paused/resets_left/ends_at/percent_used/unavailable_reason/use_requires_limit` values to record (and no ids).

## Findings

1. Every readable account says ineligible with reason "surface". This is the case the research doc feared: the server decides per calling surface, and a plain OAuth caller (not Claude Code CLI, not claude.ai) gets no grants listed. The owner's expected full resets on "Bco 1" and "Bco 2" (beeco1 and beeco2 at pixelventures.ai, mapped by email) and a Fable reset everywhere are NOT visible through this call.
2. This does not prove the grants are absent, only that this request, as sent, is not shown them. Untested next steps (each needs a decision; none done here): the same GET with Claude Code's own client headers (user agent and app header), or `claude /limit-reset` inside Claude Code on one account (hidden command, not run per the rules), or capture what the claude.ai web page requests.
3. ahmed@pixelventures.ai returned HTTP 403 with an error envelope (`type`, `error`, `request_id`; message text not captured and not retried, per the one-GET rule). Its access token was still valid (expiresAt about 3.3 hours ahead) and has the same scopes, so the 403 is a server-side refusal for that account, not an expiry. Worth one manual look at the message.
4. The plain URL PixelSwitch already calls (no query), checked once on ahmed@bespokewoodcraftstudio.com: HTTP 200 and it DOES include a top-level `cedar_ember` key (also `limits`, `extra_usage`, `juniper_tide`, `spend`, `pace`, and others). So no extra call is needed to read it; `cedar_ember=1` may only change what gets populated. The research doc's guess that the plain URL omits it was wrong.

## Scopes (names only), all accounts

`user:file_upload`, `user:inference`, `user:mcp_servers`, `user:plugins`, `user:profile`, `user:sessions:claude_code`. claude@bespokewoodcraftstudio.com also has `user:privacy_consents:write`. All have `user:profile`, so a missing scope is not the cause.

## Plan and tier data (for the plan-chip fix)

Stored token (`claudeAiOauth`): `subscriptionType`, `rateLimitTier`, `subscriptionTypeSource`. Stored profile (`oauthAccount` from ~/.claude.json): `organizationType`, `organizationRole`, `billingType`, `hasExtraUsageEnabled`, `subscriptionCreatedAt`, `seatTier`, `organizationName`.

| Account | subscriptionType | rateLimitTier | organizationType | billingType | extra usage | seatTier |
|---|---|---|---|---|---|---|
| claude@pixelventures.ai | max | default_claude_max_20x | claude_max | stripe_subscription | false | null |
| beeco1@pixelventures.ai | max | default_claude_max_20x | claude_max | stripe_subscription | false | null |
| ahmed@bespokewoodcraftstudio.com | max | default_claude_max_20x | claude_max | stripe_subscription | false | null |
| ahamade@gmail.com | max | default_claude_max_20x | claude_max | stripe_subscription | false | null |
| ahmed@pixelventures.ai | max | default_claude_max_20x | claude_max | stripe_subscription | false | null |
| beeco2@pixelventures.ai | max | default_claude_max_20x | claude_max | stripe_subscription | false | null |
| claude@bespokewoodcraftstudio.com | max | default_claude_max_20x | claude_max | stripe_subscription | true | null |

All seven are Max 20x. The tier string `default_claude_max_20x` carries the multiplier ("20x"); `subscriptionType` alone gives only "max". The usage response itself carried no plan field.
