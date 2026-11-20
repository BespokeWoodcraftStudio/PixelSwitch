# Chrome --profile-email live test, 2026-11-20

Chrome 158, Chrome already running with the founder's windows. Launch: `/usr/bin/open -na "Google Chrome" --args <flags> "data:text/html,<title>PSTESTn</title>...#ps-test-n"` (about:blank was rewritten to chrome://newtab, so a data: URL was used). Profile that received the tab was read from the window title in System Events (Chrome appends the profile name) and from Local State `last_used`.

| # | Flags | Profile that opened |
|---|---|---|
| 1 | `--profile-email=ahmed@bespokewoodcraftstudio.com` | Profile 2 (correct) |
| 2 | `--profile-email=ahmed@pixelventures.ai` | Profile 1 (correct) |
| 3 | `--profile-email=AHMED@PixelVentures.ai` | Profile 1 (match is case-insensitive) |
| 4 | `--profile-email=beeco1@pixelventures.ai` (no match) | Opened in the LAST-USED profile, no picker, no error, no new profile. First run: Profile 1; re-run after last_used was Profile 2: Profile 2. DANGEROUS: lands in a different account's profile. |
| 5 | `--profile-email=beeco1@pixelventures.ai --profile-directory="PixelSwitch beeco1@pixelventures.ai"` | `--profile-directory` won; Chrome CREATED the directory "PixelSwitch beeco1@pixelventures.ai" (shown as "Person 2", no signed-in user). Email ignored. |
| 6 | `--profile-directory="Profile 2" --profile-email=ahamade@gmail.com` | Profile 2: `--profile-directory` wins over the email (gmail is Default) |

Note: tests 1-3 opened a tab in an existing window of the matched profile; test 2 also started a Profile 1 window ("Markdown Reader - Options", an extension startup page, 1 tab, id 2106188621) which was NOT closed (no test fragment). Test windows/tabs with `ps-test-` were all closed (0 remain).

## Profile directories created
- "PixelSwitch beeco1@pixelventures.ai" (Person 2) by test 5. Not deleted.
- "PixelSwitch ahmed@pixelventures.ai" (Person 1) already existed before the tests.

## Documented behaviour (sources, partial)
Chromium source (not user docs) shows `kProfileEmail` is handled at startup: lookup finds a profile by matching the email to profile attribute `user_name`; if none matches and the value is non-empty, the picker/create path (`FromStartupWithEmail`, a `--create-profile-email-if-not-exists` switch) applies. Our live result differs: with Chrome already running, an unmatched email silently used the last-used profile. No source found documenting combination with `--profile-directory`; test 6 and 5 show the directory wins.
- https://chromium.googlesource.com/chromium/src/+/205191a32c9db91062c5342e901b2da2d67a49e5/chrome/browser/ui/profiles/profile_picker.cc
- https://chromium.googlesource.com/chromium/src.git/+/main/chrome/browser/ui/startup/startup_browser_creator.cc
- https://chromium.googlesource.com/chromium/src/+/lkgr/chrome/browser/ui/profiles/profile_picker_unittest.cc

## Recommendation
1. Never send `--profile-email` alone for an email PixelSwitch cannot confirm exists: unmatched emails open the last-used profile (wrong account).
2. Matched email: `--profile-email=<email>` alone is reliable (exact, case-insensitive) but PixelSwitch cannot know it matches (Local State unreadable to it). So do not rely on it for safety.
3. Safe design: PixelSwitch owns a dedicated directory per account, `--profile-directory="PixelSwitch <email>"` (explicit directory always wins and is created if absent, as test 5 showed). Never pass `--profile-email` with it. For the founder's existing signed-in profiles, store a one-time email-to-directory mapping (Default / Profile 1 / Profile 2) in PixelSwitch's own settings (the founder, or a helper run from a shell that can read Local State, seeds it); otherwise use the PixelSwitch-owned directory, where the user signs in once.
4. Unmapped account: use its own PixelSwitch directory (fresh, signed out), never the email flag.
