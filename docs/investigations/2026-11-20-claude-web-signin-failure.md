# Claude web window: sign-in failure, 2026-11-20 (read-only diagnosis)

Author: Claude (diagnosis subagent). Date: 2026-11-20. Status: findings, no code changed.

## What he hit
He clicked "Limit resets" on claude@bespokewoodcraftstudio.com (account 472C6043). The window loaded claude.ai/settings/usage, claude.ai redirected it to /login, and it stayed there. Nothing was reset. Nothing on claude.ai was clicked or scripted by PixelSwitch.

## 1. Log reconstruction (PixelSwitch-app.log, UTC; PST = UTC-8; DEADBEEF lines ignored)
Only 12 [ClaudeWeb] lines exist for Nov 19-20, all on Nov 20:
- 15:34:25 (07:34 PST) 472C6043 window opened. 15:34:25 allow claude.ai/settings. 15:34:27 allow claude.ai/login.
- Then 51 s with NO logged navigation, popup, block, browser hand-off or load failure. 15:35:18 window closed.
- 15:35:25 E051B6B1 (a different account) window opened, same two allows (settings, then login at 15:35:26).
- 15:35:32 pop-up opened for accounts.google.com/o (Google OAuth), allowed; 15:35:32 one more main-frame allow on accounts.google.com/(non-plain path). 15:35:35 pop-up closed (2.9 s after open). 15:35:36 window closed.
- No "pop-up closed by the page" line, so the page did not close the pop-up itself (that line is logged in ClaudeWebPopupController.swift:77); the pop-up window was closed by the window-close path. Nothing was bounced to the default browser, blocked, or failed to load. No Cloudflare/hcaptcha challenge was refused (embedded frames are allowed any https host, ClaudeWebPolicy.swift:56-58).
- Which account is which: the Keychain log lines tie 472C6043 to claude@bespokewoodcraftstudio.com. On the Google attempt he was in E051B6B1's window, not the account he named. The log cannot show what Google displayed (paths are redacted by design).

## 2. Is the sign-in persisted?
Yes, the store is persistent; it simply never held a sign-in.
- UserDefaults key ai.pixelventures.pixelswitch.claudeWeb.dataStoreIDs maps 472c6043... -> 6957DFD8-0D88-4222-82E5-316EF6D1EA7A and e051b6b1... -> AE91DC05-16CA-4A78-85F8-06FA425F4839. Not sandboxed (no Container); stores are under ~/Library/WebKit/ai.pixelventures.pixelswitch/WebsiteDataStore/<uuid lowercase>.
- Both folders were created 07:34/07:35 today, i.e. first use. Cookies.binarycookies on disk (names only, parsed read-only):
  - 6957dfd8 (claude@bespoke): 11 cookies, all written 15:34:25-15:35:16 UTC: claude.ai _dd_s_v2, anthropic-device-id, activitySessionId; .claude.ai cf_clearance, __cf_bm, _fbp, user-sidebar-visible-on-load, CH-prefers-color-scheme, ajs_anonymous_id, __ssid; .hcaptcha.com __cf_bm. No session cookie of any kind (no sessionKey, no lastActiveOrg). It is an anonymous visitor jar.
  - ae91dc05: same plus return-to (claude.ai), __Host-GAPS (accounts.google.com), hmt_id (api.hcaptcha.com), written 15:35:26-15:35:32. Still no claude.ai session cookie, so the Google flow did not complete.
- LocalStorage and IndexedDB folders are empty in 6957dfd8.

## 3. Would each route complete in this window?
No web view configuration sets a user agent (grep for userAgent in PixelSwitch/ClaudeWeb: none), so this is WKWebView's default UA from a third-party app.
a) Continue with Google. Google's policy ([developers.googleblog.com, 2021](https://developers.googleblog.com/2021/06/upcoming-security-changes-to-googles-oauth-2.0-authorization-endpoint.html); [Auth0 summary](https://auth0.com/blog/google-blocks-oauth-requests-from-embedded-browsers/)) names embedded WKWebView as non-compliant and shows 403 disallowed_useragent. I did not capture the Google page itself, so "it showed that error" is strong inference (the pop-up loaded Google, set __Host-GAPS, ran 3 s, and was closed with no session), not log-proven. A fresh jar is also why Google does not know his account: this store has never held a Google login, and his Chrome/Safari Google sessions are not shared with it.
b) Continue with Apple. Not attempted in the log. It is allowed by policy (appleid.apple.com, idmsa.apple.com) and Apple permits web views for its web flow, but it needs his Apple ID password plus two-factor in a jar that has never seen it. Untested.
c) Email. claude.ai emails a magic link; a six-digit code appears only if the link is opened on a different device than the one that requested it ([Claude help: log in](https://support.claude.com/en/articles/13189465-log-in-to-your-claude-account)). Clicking the link in Gmail opens the default browser (Chrome), which signs Chrome in, not this window. The window only signs in if the link is pasted into the "Paste sign-in link" button (ClaudeWebWindowController.swift:130, 344; accepted by validatePastedLink, ClaudeWebPolicy.swift:112). He would have to know that. The 51 s idle on the first window fits someone requesting an email link or looking at the options.
d) Password autofill. A third-party WKWebView does not get Safari's iCloud Keychain/Passwords autofill or Chrome's saved passwords (those belong to the browsers). Confirmed by design and by the empty-jar evidence; I did not run an autofill test. So "it does not remember my login" is expected: this window is a separate browser profile per account.

## 4. Anything that loses a completed sign-in?
No defect found. Store is WKWebsiteDataStore(forIdentifier:) (ClaudeWebSessionStore.swift:15-18), persistent; identifier is created once and saved (ClaudeWebStoreMap.swift:55-68); popups reuse the opener configuration so share the store (ClaudeWebPopupController.swift:7-9, ClaudeWebWindowController.swift:196); cookies are cleared only by the explicit sign-out (ClaudeWebSessionStore.swift:46-74). Nothing signed in, so there was nothing to lose.

## Root causes
1. The window is a brand-new browser profile with no login, so every first use lands on claude.ai's login page. The Limit resets button gives no sign-in path that works.
2. Google sign-in (his obvious choice) is blocked or at least hostile in embedded web views; Apple needs a full fresh login; email needs a pasted link he was never told about, because clicking the link opens Chrome.
3. Nothing in the app tells him which route works, and the failed Google pop-up closes silently.

## Can a window ever give a no-login reset?
No. A separate in-app web view will always need its own claude.ai sign-in once per account, and the easiest route (Google) is the one Google blocks. A no-login reset would need his existing browser session (Chrome, via the Chrome MCP or opening the usage URL in the default browser, where he is already signed in), or the existing OAuth credential in Keychain if claude.ai accepted it for the web (not established). Within the founder's "never script claude.ai" order, the only non-login option is "open claude.ai/settings/usage in the default browser" (needs the right profile for each account). Email-link sign-in with the paste box works once per account and then persists.

## Open unknowns
Exact Google page shown in the pop-up (log redacts paths); whether he tried email on 472C6043 during the 51 s idle.
