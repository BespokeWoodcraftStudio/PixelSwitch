# Recon: build, test, release and update pipeline (and how to add a Linux artifact)

Read-only recon, 2026-10-24, repo `/Users/ahamade/Documents/GitHub/PixelSwitch` at `35f8a99` (v1.9, build 30).
Nothing in the repo was edited except this file. No secret value was read, printed or written: the Sparkle key
length was inferred from a CI log, and every signing experiment used a freshly generated throwaway key.

## 0. The facts the spec most needs

1. **One workflow, one job, macOS only.** `.github/workflows/build.yml` has a single job `build` on `macos-26`. It is
   the only thing that publishes. A tag push `v*` runs it; it creates the GitHub release with `softprops/action-gh-release@v2`
   (resolved to v2.6.2, SHA `3bb12739c298aeb8a4eeaf626c5b8d85266b0e65`, in the v1.9 run) and uploads 3 assets:
   `PixelSwitch.dmg`, `appcast.xml`, `latest.json`.
2. **The Sparkle secret `SPARKLE_EDDSA_KEY` is a base64 of a 32-byte Ed25519 SEED.** Evidence: in the v1.9 release run the
   runtime log has no `Note: SPARKLE_EDDSA_KEY decoded to 64 bytes` line (only the echoed script source, log lines 2670 and
   2674), so the silent `32)` branch of `build.yml:261-263` ran. A 32-byte seed turns into a PKCS8 key with a fixed 16-byte
   prefix (section 5.3), and **openssl then produces a byte-identical signature to Sparkle's `sign_update`** (tested).
3. **Same key, same signature format works for Linux with no new secret and no Swift/Mac tool**: `openssl pkeyutl -sign -rawin`
   (OpenSSL 3) or python `cryptography` on the Ubuntu runner. Verified equal to `sign_update` output for a throwaway seed.
4. **Client verification needs only openssl 3 or python3 stdlib.** openssl: `pkeyutl -verify -rawin` with the public key built
   as the PEM `MCowBQYDK2VwAyEA` + `SUPublicEDKey` (plain string concatenation, tested). python3 stdlib has NO Ed25519, so
   an ~80 line RFC 8032 verifier is needed (appendix A, tested: verifies the real published v1.9 DMG against `SUPublicEDKey`
   in 0.03 s and rejects a 1-byte tamper).
5. **The release is invisible to `releases/latest` until the action finalizes it.** softprops creates a DRAFT, uploads every
   asset, then publishes (`github.ts` line 901 `const draft = prerelease === true ? ... : true`, then `finalizeRelease`). So the
   Mac feed flips atomically. A Linux asset uploaded after the Mac job loses that atomicity (Linux clients see a 404 for a
   short window), which is acceptable if Linux clients treat 404 as "no update".
6. **Do not touch `appcast.xml` or `latest.json`, and never make a second "Latest" release.** `SUFeedURL`
   (`project.yml:53`) is `.../releases/latest/download/appcast.xml`. A different release flagged Latest without `appcast.xml`
   breaks every Mac client's updates silently. Linux assets must live in the SAME tagged release with different file names.
7. **`ubuntu-26.04` is a generally available GitHub-hosted label** (runner-images issue 14747), with Swift 6.4,
   Python 3.14.4, OpenSSL 3.5.5-1ubuntu3.7, gh 2.102.0, jq 1.8.1 preinstalled. The laptop has the same Python (3.14.4) and
   the same OpenSSL package (3.5.5-1ubuntu3.7) per `recon/laptop.md`. `ubuntu-latest` moves to 26.04 only in November 2026
   (runner-images issue 14748), so pin `ubuntu-26.04` explicitly.
8. **Repo is PUBLIC and not a fork**, so release assets download with no auth (tested with `curl`, GH_TOKEN unset).
9. **No `concurrency:` group exists**, so a tag-push run and a manual dispatch for the same tag race (documented incident v1.2).
   `main` has no branch protection and no rulesets.

## 1. What was run (all read-only)

| Command | Result |
|---|---|
| `gh repo view BespokeWoodcraftStudio/PixelSwitch --json visibility,isFork,defaultBranchRef` | `{"defaultBranchRef":{"name":"main"},"isFork":false,"visibility":"PUBLIC"}` |
| `gh release list -R ... --limit 3` | `v1.9 Latest 2026-10-16T15:37:20Z`, `v1.8 2026-09-29T19:00:39Z`, `v1.7 2026-09-29T15:17:50Z` |
| `gh release view v1.9 --json assets --jq '.assets[].name'` | `appcast.xml`, `latest.json`, `PixelSwitch.dmg` (sizes 3768, 420, 4433502 bytes) |
| `gh secret list` (names only) | `APPLE_ID`, `APPLE_TEAM_ID`, `APP_SPECIFIC_PASSWORD`, `MAC_CERTS_P12_BASE64`, `MAC_CERTS_P12_PASSWORD`, `SPARKLE_EDDSA_KEY` |
| `gh api repos/.../immutable-releases` | `{"enabled":false,"enforced_by_owner":false}` (v1.9 release JSON also `"immutable": false`) |
| `gh api repos/.../branches/main/protection`, `.../rulesets` | 404 "Branch not protected"; no rulesets |
| `gh api repos/.../actions/permissions/workflow` | `default_workflow_permissions: read`, `can_approve_pull_request_reviews: false` (so the workflow's own `permissions: contents: write` is required) |
| `gh run view 38924948392 --log` (v1.9 release run) | read for step timings, key-length branch, softprops behaviour (log saved in the session scratchpad only) |
| Sparkle 2.9.0 source via `gh api` (`sign_update/main.swift`, `generate_keys/main.swift`, `common_cli/Secret.swift`) and softprops `src/github.ts` at the SHA above | read; cited below |

Experiments (all in the session scratchpad, throwaway keys only): Sparkle 2.9.0 `bin/sign_update` downloaded from the
official release tarball and run against a random 32-byte seed; openssl 3.6.4 (Homebrew), python 3.14.7, python
`cryptography` 47.0.0 on this Mac. Nothing was run on the Linux laptop or on a GitHub runner.

## 2. (a) build.yml, job by job

File: `.github/workflows/build.yml` (381 lines). Workflow `name:` is "Build and Notarize macOS App" (line 1).

### 2.1 Triggers and global settings

| Line | Setting |
|---|---|
| 4-7 | `push` to branch `main`, and `push` of tags `v*` |
| 8-9 | `pull_request` to `main` |
| 10 | `workflow_dispatch` (how branch builds are started: branch pushes do NOT start CI, handover line 88) |
| 12-13 | `permissions: contents: write` for the whole workflow |
| 16, 22 | one job `build`, `runs-on: macos-26` (log: image `macos-26-arm64`, Xcode 26.6 at `/Applications/Xcode_26.6.app`) |
| 25-27 | job `env`: `HAS_APPLE_SECRETS: ${{ secrets.MAC_CERTS_P12_BASE64 != '' }}`, `HAS_SPARKLE_KEY: ${{ secrets.SPARKLE_EDDSA_KEY != '' }}` (strings `'true'`/`'false'`; both are empty on fork PRs, which then get the ad-hoc path) |

There is no `concurrency:` key, no second job, no `needs:`, no matrix, no `if:` on the job.

### 2.2 Steps (v1.9 tag run 38924948392, job 15:33:53Z to 15:37:24Z = about 3 min 31 s)

| # | Lines | Step | Runs when | Does | v1.9 time |
|---|---|---|---|---|---|
| 2 | 30-31 | Checkout (`actions/checkout@v4`) | always | | 2 s |
| 3 | 33-39 | Setup Xcode (`maxim-lobanov/setup-xcode@v1`, `latest-stable`) | always | | 0 s |
| 4 | 41-42 | Unit Tests (credential writing) | always | `bash Tests/run-unit-tests.sh` (section 4); log "706 passed, 0 failed" | 28 s |
| 5 | 44-45 | Install Build Tools | always | `brew install xcodegen create-dmg` | 4 s |
| 6 | 47-48 | Check target names | always | `bash scripts/check-target-names.sh` (needs `xcodegen dump`) | 0 s |
| 7 | 50-51 | Generate Xcode Project | always | `xcodegen generate` from `project.yml` | 0 s |
| 8 | 53-76 | Install Apple Certificate | `HAS_APPLE_SECRETS == 'true'` | decodes `MAC_CERTS_P12_BASE64` into a temp keychain, uses `MAC_CERTS_P12_PASSWORD` | 1 s |
| 9 | 78-89 | Build Universal App | always | `xcodebuild ... ARCHS="arm64 x86_64" ... CODE_SIGNING_ALLOWED=NO build SYMROOT=$(PWD)/build` | 88 s |
| 10 | 91-144 | Sign App | secrets present | inside-out `codesign` of Sparkle helpers, the `pixelswitch` CLI (`--identifier ai.pixelventures.pixelswitch.cli`), widget extension, app; entitlements via `sed` of `$(TeamIdentifierPrefix)` with `APPLE_TEAM_ID`; `codesign --verify --deep --strict` | 2 s |
| 11 | 146-155 | Ad-hoc Sign App | secrets absent | `codesign --force --deep --sign -` | skipped |
| 12 | 157-170 | Create DMG | always | `create-dmg ... PixelSwitch.dmg PixelSwitch.app` in `build/Release` | 36 s |
| 13 | 172-176 | Sign DMG | secrets present | `codesign --sign "Developer ID Application" --timestamp` | 0 s |
| 14 | 178-190 | Notarize DMG | secrets present | `xcrun notarytool submit ... --wait` with `APPLE_ID`, `APPLE_TEAM_ID`, `APP_SPECIFIC_PASSWORD` | 30 s |
| 15 | 192-196 | Staple Ticket to DMG | secrets present | `xcrun stapler staple` | 2 s |
| 16 | 198-205 | Download Sparkle tools | `HAS_SPARKLE_KEY == 'true'` AND `startsWith(github.ref, 'refs/tags/v')` | `curl -sL` of `https://github.com/sparkle-project/Sparkle/releases/download/2.9.0/Sparkle-2.9.0.tar.xz` into `$RUNNER_TEMP/sparkle` (no checksum check) | 2 s |
| 17 | 207-244 | Prepare release notes | tag only | copies committed `RELEASE_NOTES.md` to `$RUNNER_TEMP/release_notes.md` (fallbacks: GitHub generate-notes, `git log`, "Bug fixes and improvements."); renders HTML with `gh api --method POST /markdown -f mode=gfm` (fallback: escaped `<pre>`) into `$RUNNER_TEMP/release_notes.html` | 1 s |
| 18 | 246-335 | Sign DMG with Sparkle EdDSA + generate appcast.xml | Sparkle key AND tag | section 2.4 | 0 s |
| 19 | 337-341 | Upload Artifact (`actions/upload-artifact@v4`) | ALWAYS (also branch and PR builds) | name `PixelSwitch-macOS`, path `build/Release/PixelSwitch.dmg` | 1 s |
| 20 | 343-366 | Generate latest.json | tag only | heredoc, Tauri-style shape with `darwin-aarch64` and `darwin-x86_64` both pointing at the DMG and `"signature": ""` and `"notes": ""` | 1 s |
| 21 | 368-381 | Release (`softprops/action-gh-release@v2`) | tag only | `body_path: ${{ runner.temp }}/release_notes.md`, `fail_on_unmatched_files: false`, `files:` = `build/Release/PixelSwitch.dmg`, `build/Release/latest.json`, `build/Release/appcast.xml`, `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}` | 7 s |

`latest.json` is read by nothing in this repo (`grep -rn latest.json` finds only `build.yml:343-378` and two worklog lines).
Mac clients use only `appcast.xml`.

### 2.3 What gets uploaded where

* **Every trigger** (main push, PR, dispatch, tag): workflow artifact `PixelSwitch-macOS` (the DMG), default 90 day retention.
  `scripts/verify-ci-artifact.sh:8` downloads it with `gh run download <run-id> -n PixelSwitch-macOS`.
* **Tag `v*` only**: the GitHub release for that tag gets `PixelSwitch.dmg`, `latest.json`, `appcast.xml`, with the body
  from `RELEASE_NOTES.md`.
* **How the release is created** (softprops 2.6.2 `src/github.ts`): it looks the tag up; if no release exists it **creates a
  draft** (line 901), uploads each file (asset upload starts all three in parallel; log shows `Uploading` x3, then `Uploaded` x3),
  then `finalizeRelease` publishes it (line 616+) and `make_latest` applies. The v1.9 log also shows
  `Release 415117940 is not yet discoverable by tag v1.9, retrying... (2 retries remaining)` (the eventual-consistency guard,
  `canonicalizeCreatedRelease` lines 830-880, message at 872).
* If a release for the tag already exists it updates it: the body is `workflowBody || existingReleaseBody` (lines 561-566), the
  draft flag is kept (582), and assets with the SAME NAME are deleted and re-uploaded (`overwrite_files` defaults to true,
  lines 321-330). **Assets with other names are never touched**, so Linux assets survive a macOS job re-run.
* A race on asset names is handled inside the action (line 468-490: on 422 `already_exists` it deletes the stale asset and
  retries once) and a race on release creation is handled by `canonicalizeCreatedRelease` (duplicate drafts removed), but
  neither protects the PAIRING of a DMG with its appcast signature (section 3.3).

### 2.4 How the Sparkle EdDSA signature and `appcast.xml` are made (lines 246-335)

* **Secret:** `SPARKLE_EDDSA_KEY` (env on the step, line 249). Repo secret exists (names list above).
* **Tool:** Sparkle 2.9.0 `bin/sign_update`, downloaded at step 16 into `$RUNNER_TEMP/sparkle/bin/sign_update`. It is a macOS
  universal Mach-O (cannot run on a Linux runner).
* **Key handling, lines 254-274:** the secret is base64-decoded only to measure length. 32 bytes: written as is to a temp file.
  64 bytes: truncated to the first 32 (the comment assumes a libsodium "seed + public" blob). Any other length: `exit 1`.
  Evidence the real secret is 32 bytes: no runtime "Note: ... 64 bytes" line in run 38924948392.
* **Signing, line 278:** `sign_update PixelSwitch.dmg -f "$KEY_FILE"`. Output shape
  `sparkle:edSignature="<88 base64 chars = 64 bytes>" length="4433502"`. The step parses the signature with
  `sed -n 's/.*sparkle:edSignature="\([^"]*\)".*/\1/p'` (line 287); the signature covers the raw bytes of the **stapled, notarized
  DMG** (the file as published).
* **Fields:** `VERSION="${GITHUB_REF_NAME#v}"` (293); `BUILD` = first `CURRENT_PROJECT_VERSION: "N"` line of `project.yml`
  via `grep -E '^\s*CURRENT_PROJECT_VERSION: ' ../../project.yml | head -1 | awk -F'"' '{print $2}'` (296); `FILE_LENGTH=$(stat -f%z ...)`
  (301, BSD stat, macOS only); `DOWNLOAD_URL=https://github.com/${GITHUB_REPOSITORY}/releases/download/${GITHUB_REF_NAME}/PixelSwitch.dmg`
  (302); `PUB_DATE=$(date -u +"%a, %d %b %Y %H:%M:%S +0000")` (303).
* **appcast.xml** (written by `echo` lines 312-329, not a heredoc, so the HTML never gets shell-expanded): RSS 2.0 with the
  `sparkle` namespace, `<title>PixelSwitch Updates</title>`, a **single `<item>`** (title "Version X", description CDATA with
  the rendered release notes, with `]]>` neutralised by `sed 's/]]>/]]\&gt;/g'` at 311), `sparkle:version` = build number,
  `sparkle:shortVersionString` = X.Y, `sparkle:minimumSystemVersion` hard-coded `14.0` (324), and
  `<enclosure url=... length=... type="application/octet-stream" sparkle:edSignature=... />`. The feed itself is not signed;
  only the enclosure is. `xmllint --noout appcast.xml` (335) fails the build on malformed XML.
* **Client side:** `project.yml:63` `SUPublicEDKey: rk8t6LzU0KnCEzj6+xCXL5XC0CUSfLwtUneaNWX+5/U=` (32 bytes decoded);
  `project.yml:53` `SUFeedURL`; `:54` `SUEnableAutomaticChecks: true`; `:56` `SUScheduledCheckInterval: 21600` (6 h);
  `:59` `SUAutomaticallyUpdate: true`. `PixelSwitch/Services/UpdateChecker.swift:39-43` starts
  `SPUStandardUpdaterController(startingUpdater: true, ...)`; `:97-102` takes over the install-on-quit so a menu bar app relaunches
  into the update at once unless a switch or sign-in runs (`AutoUpdatePolicy.retryInterval = 60` s).

Quirk worth knowing: line 89 is `SYMROOT=$(PWD)/build`. In bash `$(PWD)` runs a command named `PWD`; it works only because the
runner's disk is case-insensitive and finds `/bin/pwd` (inferred from the documented "CI's disk ignores case" gotcha, not tested).

## 3. (b) The release procedure end to end

### 3.1 Steps

1. On `main` with a clean tree. Write `RELEASE_NOTES.md` (English, user-facing, ends with the footer
   `**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/vPREV...vX.Y`) and **commit it on its own**
   (`docs: Release notes for X.Y`) BEFORE tagging: CI reads it from the tagged commit (`.claude/commands/release.md` step 3;
   `build.yml:219-220`). Current file is the 1.9 notes (22 lines).
2. `echo y | ./scripts/release.sh X.Y` (or `minor`/`major`). From an agent, `echo y |` is required: `read -p "Proceed? [y/N]"`
   (line 100) otherwise waits silently (worklog 2026-09-25: it sat 2 minutes). Versions are two-part only; regex at line 76.
   What the script does:
   * preflight: `project.yml` exists (45), branch is `main` (48-49), no tracked changes (52-54), no untracked files in
     `PixelSwitch/ PixelSwitchWidget/ Shared/ project.yml` only (57-58); **`PixelSwitchCLI/`, `Tests/` and any new Linux
     directory are NOT in that untracked check**;
   * `NEW_BUILD = CURRENT_PROJECT_VERSION + 1` (80); tag must not exist locally (92) or on origin (96);
   * `sed -i ''` (BSD syntax, macOS only) rewrites all 3 `MARKETING_VERSION` and all 3 `CURRENT_PROJECT_VERSION` lines in
     `project.yml` (lines 69-70, 113-114, 145-146: app, widget, CLI) (106-107); `xcodegen generate` (110); re-reads and checks (113-116);
   * commit `chore: Bump to X.Y (build N)` (120), `git tag vX.Y` (123), `git push origin main` (126), `git push origin vX.Y` (127);
   * polls `gh run list -R $REPO --workflow build.yml --branch "$TAG" --event push -L 1` every 5 s, 12 times (141-145); only if
     no run appears does it dispatch by hand with `gh workflow run build.yml -R "$REPO" --ref "$TAG"` (150).
3. The tag push starts the release run (about 3.5 minutes). The `main` push of the bump commit starts a second, non-publishing
   build that is cancelled by hand on every release: `gh run cancel <id> -R BespokeWoodcraftStudio/PixelSwitch` (handover lines
   8 and 87; recent runs list shows cancelled `push main` runs beside each `push vX.Y` success).
4. `scripts/verify-release.sh vX.Y` (Mac only). It checks: release not draft/pre and its assets (15); the live feed at
   `releases/latest/download/appcast.xml` offers the new version (17-18); appcast `length` equals the DMG (20-21); **EdDSA
   verification** of the DMG against `SUPublicEDKey` using the SPKI prefix `302a300506032b6570032100` and `/opt/homebrew/bin/openssl`
   with `pkeyutl -verify -rawin` (22-27); then mounts the DMG and checks `Info.plist` version, helper signature, deep verify,
   `spctl`, `stapler validate`, and two `Localizable.strings` phrases (37-39; those phrases date from 1.3 to 1.6 and are
   stale as a release gate). It runs `set -uo pipefail` WITHOUT `-e` (line 9): a failed check prints but does not stop or
   fail the script, so someone must read the output.
5. Clients: Sparkle checks every 6 h and installs by itself (`SUAutomaticallyUpdate: true`); Settings → About → Check for Updates
   forces it. Last verified release: v1.9 (handover line 8). A branch build can never reach the updater (feed is the latest
   release's appcast only, and a branch build shares the installed build number).

### 3.2 Branch builds (no release)

`gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>`, then `scripts/verify-ci-artifact.sh <run-id>`
(downloads artifact `PixelSwitch-macOS`, mounts it, checks helper signature, deep verify, Gatekeeper, staple, version).
Example: run 38924140452 (branch `fix/usage-429-dead-tokens`, 4 min 46 s) before v1.9.

### 3.3 Races and gotchas (all documented, all still live)

* **Two release builds race** (v1.2, 2026-09-25, worklog WL-2026-09-25-010; handover line 87). Each run notarizes and signs ITS
  OWN DMG; a release holding run A's DMG and run B's `appcast.xml` fails every update with "improperly signed". Cause was
  `release.sh` dispatching by hand while the tag push also started a run (the repo had stopped being a fork). Fix is the poll
  at `release.sh:129-151`. There is still no `concurrency:` group, so a manual re-dispatch of a tag, or a "Re-run all jobs"
  click, can reproduce it. softprops deletes and re-uploads same-named assets one at a time (parallel), so a mismatched pair
  can exist for seconds even on a clean re-run.
* **Any Linux asset set that pairs a tarball with a separate signature file has the same exposure.** Put the signature, length
  and sha256 in ONE small JSON uploaded last (section 5), and add a `concurrency` group to the Linux job.
* **Tag must exist only once and be pushed alone**: never `git push --tags` (`.claude/commands/release.md`).
* **`gh` must be told the repo**: `-R` is not optional (v1.0.10: the fork resolved to its parent, HTTP 403). `release.sh:139` derives it from `origin`.
* **`MARKETING_VERSION` must equal the tag**: the appcast takes `shortVersionString` from the tag (293), the build number from `project.yml` at the tagged commit (296).
* **Notes committed late = wrong notes**: CI uses the file at the tagged commit (219).
* **Branch pushes do not start CI**; `main` pushes do (a docs-only push still costs a macOS build; cancel by hand).
* **`release.sh`, `verify-release.sh`, `verify-ci-artifact.sh` are macOS-only** (`sed -i ''`, `stat -f`, `hdiutil`, `codesign`, `spctl`,
  `PlistBuddy`, `plutil`, Homebrew openssl at a hard-coded path, `xcodegen`). macOS's own `/usr/bin/openssl` is **LibreSSL 3.3.6**
  here, which is why the script hard-codes `/opt/homebrew/bin/openssl`. Do not run them from the Ubuntu laptop.
* **Immutable releases are off** (`enabled:false`). If they are ever turned on, uploading an asset to an already-published
  release fails (softprops has a dedicated error, `github.ts:293-302,445`), which would break the "upload after publish" Linux
  design below. Check before relying on it.
* **`softprops/action-gh-release@v2` floats**; the v1.9 run used 2.6.2. Behaviour above is from that SHA.
* **`-latest` labels move**: `ubuntu-latest` becomes 26.04 in November 2026. Pin.
* **A 404 from `releases/latest/download/<name>` is the normal state** between "release published" and "Linux assets uploaded",
  and permanently if the Linux job fails for a tag.

## 4. (c) Unit tests on a Mac with no Xcode, and the typecheck workaround

### 4.1 `Tests/run-unit-tests.sh` (52 lines)

* Needs only `swiftc` from the Command Line Tools (this Mac: `/Library/Developer/CommandLineTools`, Apple Swift 6.4, target
  `arm64-apple-macosx27.0.0`). `set -euo pipefail`, `cd` to repo root, `OUT=$(mktemp -d)`, trap removes it.
* One `swiftc -swift-version 6 <41 files> -o "$OUT/pixelswitch-unit-tests"` then runs the binary (lines 9-52). The 41 files are
  the pure model/service sources plus test files; `Tests/UnitTests/main.swift` is last and is the entry point: it defines
  `check(_:_:_:)` (counts PASS/FAIL, prints `PASS  name` / `FAIL  name: detail`), runs `runAutoSwitchRulesTests()`,
  `runSignInTests()`, `runControlTests()`, `runAutoUpdateTests()`, `runUsageErrorTests()`, `runUsageGuardTests()`, prints
  `N passed, M failed`, and `exit(failed == 0 ? 0 : 1)` (end of `main.swift`). Test files total 3,569 lines.
* `PIXELSWITCH_KEYCHAIN_TESTS=1` additionally round-trips a throwaway item through `/usr/bin/security` (`main.swift:396`).
* Last known result: **706 passed, 0 failed** (CI run 38924948392, log line 15:34:25Z; handover line 11). **Not re-run today**
  because this task forbids builds; whether Swift 6.4 on macOS 27.0.1 still compiles it is unverified here.
* `Tests/integration/remote-control.sh` is a hand-run check against a real running app (needs the installed CLI); CI does not run it.

### 4.2 Linux portability of that harness (matters for "reproduce Mac behaviour exactly")

Imports of the 41 compiled files: 35 are `Foundation` only. These 6 are NOT portable as written:
`Models/BrandColor.swift` (AppKit, Foundation, SwiftUI), `Models/AccountPalette.swift` (SwiftUI),
`Services/SignInSession.swift` (Combine), `Control/ControlServer.swift` and `PixelSwitchCLI/ControlClient.swift` (Darwin),
`Services/UsageRequestGuard.swift` (CryptoKit). The macOS harness also drives `/usr/bin/security` (keychain tests) and
`Process`. A Linux harness would reuse the test SCENARIOS as the behavioural spec, whichever language the port uses. `check(`
call sites per file (719 in total, against 706 passes at runtime because the keychain block is off by default): 167
`AutoSwitchRulesTests`, 186 `ControlTests`, 144 `SignInTests`, 132 `main.swift` (credential writing), 67 `UsageGuardTests`,
16 `UsageErrorTests`, 7 `AutoUpdateTests`.
The laptop has no `swift` or `swiftc` (`recon/laptop.md` line 43); the `ubuntu-26.04` runner image has Swift 6.4.

### 4.3 `scripts/typecheck.sh` and the workaround

* `typecheck.sh` (35 lines): downloads Sparkle 2.9.0 to `~/Library/Caches/pixelswitch-dev/sparkle-2.9.0` (9-16), then
  `swiftc -typecheck -swift-version 6 -target arm64-apple-macos14.0 -sdk $(xcrun --show-sdk-path) -F <sparkle cache>` over
  every `*.swift` in `PixelSwitch/` and `Shared/` (18-23), then the CLI sources plus `PixelSwitch/Control/ControlProtocol.swift`
  (25-31), then `check-target-names.sh` if `xcodegen` exists (33-35). The widget is skipped (`#Preview` macros need Xcode's
  PreviewsMacros plugin). With `set -e` a failure in the app pass also prevents the CLI pass from running.
* **Why it broke** (handover line 12; investigation `2026-10-16-usage-429.md:227-232`): on Swift 6.4 Command Line Tools the
  SwiftUIMacros plugin is missing, so `PixelSwitchApp.swift` gives 4 baseline errors and swiftc stops, so files sorted after it
  (e.g. `ClaudeService.swift`) are never checked. "A planted error proved it."
* **Workaround** `docs/investigations/2026-10-16-usage-429/scripts/typecheck-reordered.sh <repo-root>` (799 bytes): `LATE` = every
  file matching `@State|@Binding|@Environment|@FocusState|@AppStorage|@StateObject|@ObservedObject|@EnvironmentObject|#Preview`;
  `FILES` = all other `PixelSwitch/ Shared/` files first, then `LATE`; runs the same `swiftc -typecheck` with `-F` the Sparkle
  cache and filters to `^[^ ].*error:` lines (`sort -u`). So all 55 non-view files are checked even though the view files error.
  On 2026-10-16 main and the 1.9 branch both showed the identical 5 errors, all in `SignInView.swift`. The CLI is checked
  separately; that investigation's build notes carry the CLI command.
* **The real compile proof is a CI build**: `gh workflow run build.yml -R BespokeWoodcraftStudio/PixelSwitch --ref <branch>`
  then `scripts/verify-ci-artifact.sh <run-id>`. CI already runs `run-unit-tests.sh` (step 4) before the Xcode build.
* `scripts/check-target-names.sh` (24 lines): `xcodegen dump --type json` then python3 fails if two targets or module names
  differ only by case (CI's disk ignores case; the CLI target is `PixelSwitchCLI` with `productName: pixelswitch`).

## 5. (d) Plan: a Linux artifact in the SAME tagged release, without racing the macOS job

### 5.1 Hard constraints from sections 2 and 3

* Same tag, same release, new file names. Never edit `appcast.xml` or `latest.json`; never create a second Latest release.
* The macOS pipeline is proven (notarization, stapling, appcast); leave `build` untouched so it cannot regress.
* Exactly one writer per asset name. softprops only replaces same-named assets, so Linux names must not collide with
  `PixelSwitch.dmg`, `appcast.xml`, `latest.json`.
* Versions stay two-part and come from the tag; the monotonic comparison number is `CURRENT_PROJECT_VERSION` (as in the appcast).

### 5.2 Recommended design (option C): a Linux build job plus a Linux publish job that runs AFTER the macOS job published

Runner: **`ubuntu-26.04`** (x64). It is the same OS as the test laptop (Ubuntu 26.04.1), with Python 3.14.4, OpenSSL
3.5.5-1ubuntu3.7, `gh`, `jq` preinstalled, Swift 6.4 available if the port is Swift. The laptop's CPU architecture is not
stated in `recon/laptop.md` (confirm with `uname -m`; the Aorus 15P XD is an Intel x86_64 machine to my knowledge, unverified), so
the plan assumes x64; `ubuntu-26.04-arm` also exists if an arm64 build is ever wanted. Standard hosted runners on a public repo cost nothing
(worklog 2026-09-25 says the same for macOS).

```yaml
  # Untested in Actions. Each shell command inside was run locally (section 5.4).
  linux-build:                       # runs on every trigger: gives CI signal on main and PRs, holds no secrets
    runs-on: ubuntu-26.04
    steps:
      - uses: actions/checkout@v4
      - name: Unit tests (Linux)
        run: bash Tests/run-unit-tests-linux.sh        # to be written by the port
      - name: Package
        run: bash scripts/linux/package.sh             # writes dist/PixelSwitch-linux-x86_64.tar.gz (deterministic, see 5.5)
      - uses: actions/upload-artifact@v4
        with:
          name: PixelSwitch-linux                      # PixelSwitch-macOS is taken
          path: dist/PixelSwitch-linux-x86_64.tar.gz

  linux-publish:                     # tags only; starts after the macOS job has finished (release already published)
    needs: [build, linux-build]      # `build` is the macOS job id
    if: startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-26.04
    concurrency:
      group: linux-assets-${{ github.ref_name }}
      cancel-in-progress: false      # a duplicate run for the same tag waits instead of interleaving
    env:
      GH_TOKEN: ${{ github.token }}                    # workflow-level `contents: write` (line 13) covers `gh release upload`
      SPARKLE_EDDSA_KEY: ${{ secrets.SPARKLE_EDDSA_KEY }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/download-artifact@v4
        with: { name: PixelSwitch-linux, path: dist }
      - name: Sign and write the Linux feed        # scripts/linux/sign-and-feed.sh, see 5.3 and 5.6
        run: bash scripts/linux/sign-and-feed.sh dist/PixelSwitch-linux-x86_64.tar.gz "$GITHUB_REF_NAME"
      - name: Upload (tarball first, feed LAST)
        run: |
          gh release upload "$GITHUB_REF_NAME" dist/PixelSwitch-linux-x86_64.tar.gz --clobber
          gh release upload "$GITHUB_REF_NAME" dist/latest-linux.json --clobber
      - name: Verify from the public URL (no token)
        run: env -u GH_TOKEN bash scripts/verify-release-linux.sh "$GITHUB_REF_NAME"
```

Why this shape:
* `needs: build` means the macOS job (including its Release step) is finished, so the release EXISTS and is published. The
  Linux job never creates a release, so there is **no create race**, and it never writes a Mac asset name.
* `needs` is success-gated: a failed macOS build means no Linux publish; a failed Linux build leaves the already-published Mac
  release untouched (the Linux job goes red; the release lacks Linux assets).
* **The feed is uploaded last**, and it carries the tarball's version, build, url, length, sha256 and signature in ONE file, so
  a Linux client can never pair a tarball from one run with a signature from another. A feed that exists implies its tarball exists.
* `concurrency` serializes the Linux publish for a tag. It does not serialize the macOS job; fixing that is a separate
  hardening (a `concurrency: group: release-${{ github.ref }}, cancel-in-progress: false` on `build` would, but touches the proven job; not recommended without the founder's word).
* `linux-build` has no secrets, so PR and fork runs are safe; `linux-publish` is skipped off-tag.

Alternatives considered:

| Option | Shape | For | Against |
|---|---|---|---|
| **C (recommended)** | Linux jobs as above, upload after macOS publishes | zero change to the Mac job; no create race; Linux failure cannot block the Mac release | Linux assets appear 1 to 3 minutes after the Mac release; breaks if immutable releases are enabled (currently `false`) |
| A: one publish job | macOS and Linux jobs upload workflow artifacts; a third job `needs: [build, linux-build]` downloads both and runs the single softprops step | one writer; Linux assets appear atomically with the release; safe under immutable releases | moves `Prepare release notes`, `latest.json`, `appcast.xml` and the DMG into artifacts and re-points the Release step; a Linux failure would block the Mac release unless guarded with `if: !cancelled()`; the largest change to a pipeline that works |
| B: add Linux files to the existing softprops `files:` and make `build` `needs: linux-build` | smallest diff | atomic, one writer | delays the whole macOS job until Linux finishes, and couples the two (a Linux failure blocks the Mac release, though `fail_on_unmatched_files: false` would tolerate missing files if `needs` is made non-blocking) |
| D: Linux asset in a separate/perpetual release | stable feed URL | | creates a second release; if it is flagged Latest it breaks the Mac feed. Rejected. |

### 5.3 Signing the Linux artifact with the same key (the secret's exact format)

Sparkle 2.9.0 source, quoted facts:
* `common_cli/Secret.swift:10-12`: `secretUsesRegularSeed` is `secret.count == 32`; `secretUsesOldHashedSeed` is `64 + 32` (96) bytes.
* `Secret.swift` comment: "For newer generated keys, secret is the private seed. For older generated keys, secret is the
  private orlp/Ed25519 key concatenated with the public key." 32 bytes → `ed25519_create_keypair(&pub, &priv, seed)`.
* `generate_keys/main.swift:166`: `-x` exports `secret.base64EncodedString()`; "if the private key is generated in the new
  format (i.e. the key file after base64 decoding is 32 bytes), then the exported key file is the base64 encoding of the private
  seed. The seed can be used to create the private/public keypair with other tools that support EdDSA signing."
* `generate_keys/main.swift:273-276`: new keys store only the 32-byte seed.
* `sign_update/main.swift:80-82` prints "Imported key must be 64 bytes or 96 bytes" but that text is stale: the code accepts
  32 or 96 (Secret.swift), and **a 64-byte file is rejected** (tested). So `build.yml`'s `64)` branch (lines 265-268) is based on
  a wrong assumption about Sparkle formats; it never ran for this repo.

| Secret decodes to | Meaning | Usable with openssl / python? |
|---|---|---|
| **32 bytes (this repo's secret)** | RFC 8032 Ed25519 seed (orlp `ed25519_create_keypair` = SHA-512 of the seed, clamped, standard) | **Yes.** PKCS8 DER = `302e020100300506032b657004220420` + 32 seed bytes |
| 64 bytes | not a Sparkle format; `sign_update` rejects it | only if it is libsodium seed+public (what build.yml assumes); not the case here |
| 96 bytes (old generate_keys) | orlp EXPANDED private key (64) + public (32): the seed is not recoverable | **No.** openssl cannot import an expanded scalar. The only route would be a new key, which changes `SUPublicEDKey` and strands every installed Mac |

Public half: `project.yml:63` `SUPublicEDKey` is base64 of the 32-byte raw public key. SPKI DER = `302a300506032b6570032100` + 32 bytes
(that is exactly `verify-release.sh:24`).

**Tested equivalence (throwaway seed, Sparkle 2.9.0 `sign_update`, openssl 3.6.4, python cryptography 47.0.0, Mac, 2026-10-24):**

| Check | Result |
|---|---|
| `sign_update payload -f seed.b64` signature decodes to | 64 bytes (88 base64 chars) |
| openssl: PKCS8 from seed, `pkeyutl -sign -rawin` | **byte-identical** to Sparkle's signature (Ed25519 is deterministic) |
| python `cryptography` `Ed25519PrivateKey.from_private_bytes(seed).sign(data)` | identical |
| Same file signed twice | identical |
| `sign_update -p` | prints only the signature |
| 33-byte or 64-byte key file | `sign_update` errors; 96-byte accepted (old format) |
| trailing newline in the key file, and stdin form `--ed-key-file -` | both accepted |
| tampered file | openssl: `Signature Verification Failure`, exit 1; `sign_update --verify` exit 1 |

**CI signing snippet for the Linux runner (tested here, with the secret supplied as an env var exactly as in build.yml):**

```bash
sign_file() {   # sign_file <file>  ->  base64 Ed25519 signature on stdout
  local f="$1" key; key=$(mktemp); chmod 600 "$key"
  python3 - "$key" <<'PY'
import base64, os, sys
raw = base64.b64decode(os.environ["SPARKLE_EDDSA_KEY"].strip(), validate=True)
if len(raw) == 64:      # parity with build.yml (libsodium seed||public); Sparkle itself never produces this
    raw = raw[:32]
if len(raw) != 32:
    sys.exit("SPARKLE_EDDSA_KEY must decode to 32 bytes (Sparkle seed); got %d" % len(raw))
open(sys.argv[1], "wb").write(bytes.fromhex("302e020100300506032b657004220420") + raw)
PY
  [ $? -eq 0 ] || { rm -f "$key"; return 1; }
  openssl pkeyutl -sign -rawin -keyform DER -inkey "$key" -in "$f" | base64 | tr -d '\n'
  local rc=${PIPESTATUS[0]}; rm -f "$key"; return $rc
}
```
Tested outputs: equal to `sign_update` for the same seed; a 33-byte secret exits 1 ("fails closed"). It uses python3 for the
DER (no `xxd` needed; `xxd -r -p` also worked in the first experiment). Do NOT `echo` the decoded seed or any re-encoding of it:
GitHub masks the exact secret string, not derived forms.

Security note for the spec: using the one key for both platforms means anything that can run in `linux-publish` can sign a Mac DMG,
and a leak of either platform's signing path exposes both. The founder asked for the same key; a second key
(`LINUX_ED25519_KEY` plus a second embedded public key in the Linux client) would isolate them at the cost of one more secret.

### 5.4 How a Linux client verifies with only openssl 3 or python3 stdlib

The signature is plain Ed25519 over the **raw bytes of the downloaded file** (pure Ed25519, not prehashed): the same contract
`verify-release.sh:27` uses (`-rawin`).

**openssl 3 (tested with 3.6.4; `-rawin` exists since OpenSSL 3.0; the laptop and runner have 3.5.5, not run there):**

```bash
PUB='rk8t6LzU0KnCEzj6+xCXL5XC0CUSfLwtUneaNWX+5/U='     # SUPublicEDKey from project.yml:63, baked into the Linux app
# A 12-byte SPKI prefix is a multiple of 3, so its base64 ("MCowBQYDK2VwAyEA") concatenates with the key's base64:
printf -- '-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEA%s\n-----END PUBLIC KEY-----\n' "$PUB" > pub.pem
printf '%s' "$SIG_B64" | base64 -d > sig.bin
openssl pkeyutl -verify -pubin -inkey pub.pem -rawin -in PixelSwitch-linux-x86_64.tar.gz -sigfile sig.bin
# prints "Signature Verified Successfully", exit 0; on any mismatch prints "Signature Verification Failure", exit 1
```
Tested: the real published v1.9 DMG verifies against the real `SUPublicEDKey` with exactly this PEM (no `xxd`, no escapes).
`/usr/bin/openssl` on macOS is LibreSSL and lacks this; irrelevant for Ubuntu.

**python3 stdlib only:** the standard library has no Ed25519 (checked on Python 3.14.7: not in
`hashlib.algorithms_available`, `ssl`, `hmac`, or any stdlib module name), so use the RFC 8032
reference verifier in appendix A (about 80 lines, `hashlib.sha512` plus big-int maths, no packages). Tested: verifies the real v1.9
DMG (4,433,502 bytes) against `SUPublicEDKey` in 0.03 s and the throwaway-key payload; returns `BAD SIGNATURE` after a 1-byte
change. The laptop also has `python3-cryptography 46.0.5` (`recon/laptop.md` line 80) if the "stdlib only" rule is relaxed:
`Ed25519PublicKey.from_public_bytes(base64.b64decode(PUB)).verify(sig, data)`.

Client rules to put in the spec: verify BEFORE extracting or executing; refuse if `build` in the feed is not greater than the
installed build; read the version again from inside the verified tarball and refuse a downgrade (Sparkle's appcast is unsigned,
so feed fields alone are not trustworthy); treat HTTP 404 on the feed as "no update", not an error.

### 5.5 Linux feed and asset naming (proposal)

* Tarball: `PixelSwitch-linux-x86_64.tar.gz` (stable name, so `releases/latest/download/PixelSwitch-linux-x86_64.tar.gz` works,
  like `PixelSwitch.dmg`). Optionally also a versioned copy; not needed.
* Feed: `latest-linux.json`, uploaded last:
  `{"version":"1.10","build":31,"pub_date":"2026-...Z","url":"https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/download/v1.10/PixelSwitch-linux-x86_64.tar.gz","length":N,"sha256":"...","signature":"<base64 64 bytes>","min_os":"ubuntu-26.04"}`.
  `build` from the same grep as `build.yml:296` (GNU grep supports `\s`); `version` from `${GITHUB_REF_NAME#v}`.
* Client feed URL: `https://github.com/BespokeWoodcraftStudio/PixelSwitch/releases/latest/download/latest-linux.json`
  (302 to `/releases/download/vX.Y/latest-linux.json`, tested for `appcast.xml`).
* Deterministic packaging (untested): `tar --sort=name --owner=0 --group=0 --numeric-owner --mtime=@$SOURCE_DATE_EPOCH | gzip -n`.
  Because Ed25519 is deterministic, identical bytes give identical signatures, so an accidental second run is idempotent.
* If "latest has no Linux feed" must be survivable (Linux job failed for a tag), the client can fall back to
  `GET https://api.github.com/repos/BespokeWoodcraftStudio/PixelSwitch/releases?per_page=10` and take the newest release that has
  the feed asset (unauthenticated limit observed: `x-ratelimit-limit: 60` per hour per IP).

### 5.6 Other pipeline edits the Linux work implies

* `scripts/linux/sign-and-feed.sh` (sign_file above + JSON via python3) and `scripts/verify-release-linux.sh vX.Y` (portable bash:
  `curl -fsSL`, `openssl`, `python3`; no `gh`, no macOS tools) mirroring `verify-release.sh:16-27`: feed present, `length` equals file,
  sha256 equals, signature verifies against `SUPublicEDKey` read from `project.yml`.
* `scripts/release.sh:57` untracked-files check should add the new Linux source directory (and ideally `PixelSwitchCLI/`).
  Its closing message (line 154-155) could mention the Linux job.
* `.claude/commands/release.md` ("CI then builds/notarizes...") and `docs/SESSION-HANDOVER.md` line 80 need a sentence on the Linux jobs.
* Optional: rename the workflow ("Build and Notarize macOS App", line 1). Lookups use the file name `build.yml`, so a rename is safe.
* A Linux fix still needs a new two-part version (no patch releases, `AGENTS.md`), which also re-releases macOS.

## 6. (e) Are release assets publicly downloadable without auth?

**Yes.** `visibility: PUBLIC`, `isFork: false`. Tested with `GH_TOKEN` and `GITHUB_TOKEN` unset:

| Request | Result |
|---|---|
| `curl -L .../releases/download/v1.9/PixelSwitch.dmg` | HTTP 200, 4,433,502 bytes, via a signed redirect to `release-assets.githubusercontent.com/...` (short-lived URL; `-L` required) |
| `curl -L .../releases/latest/download/appcast.xml` | HTTP 200, 3,768 bytes; first hop is `302` to `/releases/download/v1.9/appcast.xml` |
| `curl -L .../releases/latest/download/latest.json` | HTTP 200, 420 bytes |
| `curl https://api.github.com/repos/BespokeWoodcraftStudio/PixelSwitch/releases/latest` | HTTP 200, `tag_name v1.9`, three assets with `browser_download_url`; `x-ratelimit-limit: 60` |

The Mac feed depends on this (`SUFeedURL` is fetched anonymously by Sparkle). If the repo ever went private, Sparkle updates and
any Linux client using these URLs would stop. Whether the `/releases/latest/download/` web redirect counts against the REST API
limit was not measured.

## 7. Not verified / open

* `Tests/run-unit-tests.sh` was not re-run today (no builds). `typecheck.sh` was not run. Nothing was built.
* The real `SPARKLE_EDDSA_KEY` and the keychain item `https://sparkle-project.org` / account `pixelswitch` were not read. The
  32-byte conclusion comes from the v1.9 CI log plus Sparkle's source; the match between that secret and `SUPublicEDKey` is shown
  indirectly (the published DMG's signature verifies against `SUPublicEDKey`).
* `openssl pkeyutl -rawin` was run on OpenSSL 3.6.4 only; the laptop and runner image carry 3.5.5. `-rawin` is documented from 3.0.
* The YAML in 5.2 was not executed in GitHub Actions; the shell/python inside it was executed locally on a Mac with throwaway keys.
* GNU tar determinism flags were not run (this Mac has bsdtar).
* Whether the Linux port is Swift or Python is undecided in this lane. The laptop recon shows Python 3.14.4 + PyGObject and no
  Swift, which favours a Python app; the pipeline plan above is language-neutral.
* `docs/SESSION-HANDOVER.md` is dated 2026-10-16; the repo `HEAD` is `35f8a99` and the working tree is otherwise clean apart from
  the untracked `docs/investigations/2026-10-24-linux-and-split-thresholds/` folder.

## Appendix A. Pure-stdlib Ed25519 verifier (RFC 8032 section 6), tested

Usage: `python3 ed25519_verify.py <pubkey-b64-32-bytes> <signature-b64-64-bytes> <file>`; exit 0 and `OK` if the signature over the
raw bytes of `<file>` verifies, exit 1 and `BAD SIGNATURE` otherwise. Tested results: real v1.9 DMG + `SUPublicEDKey` → `OK`
(0.03 s); throwaway payload → `OK`; tampered payload → `BAD SIGNATURE`.

```python
#!/usr/bin/env python3
import base64, hashlib, sys

P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

def _inv(x): return pow(x, P - 2, P)

def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % P
    B = (a[1] + a[0]) * (b[1] + b[0]) % P
    C = 2 * a[3] * b[3] * D % P
    Dd = 2 * a[2] * b[2] % P
    E, F, G, H = B - A, Dd - C, Dd + C, B + A
    return (E * F % P, G * H % P, F * G % P, E * H % P)

def _mul(s, pt):
    r = (0, 1, 1, 0)
    while s > 0:
        if s & 1: r = _add(r, pt)
        pt = _add(pt, pt)
        s >>= 1
    return r

def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0

def _recover_x(y, sign):
    if y >= P: return None
    x2 = (y * y - 1) * _inv(D * y * y + 1) % P
    if x2 == 0: return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0: x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0: return None
    if (x & 1) != sign: x = P - x
    return x

_gy = 4 * _inv(5) % P
_gx = _recover_x(_gy, 0)
BASE = (_gx, _gy, 1, _gx * _gy % P)

def _decompress(s):
    y = int.from_bytes(s, "little"); sign = y >> 255; y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)

def verify(public, message, signature):
    if len(public) != 32 or len(signature) != 64: return False
    A = _decompress(public); R = _decompress(signature[:32])
    if A is None or R is None: return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q: return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % Q
    return _equal(_mul(s, BASE), _add(R, _mul(h, A)))

if __name__ == "__main__":
    pub = base64.b64decode(sys.argv[1]); sig = base64.b64decode(sys.argv[2])
    data = open(sys.argv[3], "rb").read()
    ok = verify(pub, data, sig)
    print("OK" if ok else "BAD SIGNATURE")
    sys.exit(0 if ok else 1)
```
