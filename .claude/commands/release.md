Release a new version of PixelSwitch. This ensures the version is synced everywhere: `MARKETING_VERSION` in project.yml, git tag, and the commit message.

## Instructions

When the user invokes `/release`, follow these steps EXACTLY. Do NOT skip or improvise.

### 1. Pre-flight checks

- Verify you're on the `main` branch
- Verify working tree is clean (no uncommitted changes)
- If there ARE uncommitted changes, ask the user whether to commit them first

### 2. Determine version

- Read the current `MARKETING_VERSION` from `project.yml`
- Ask the user what the new version should be, or accept it as an argument (e.g., `/release 1.3` or `/release minor`)
- Versions are **two-part** (founder's rule, 2026-09-24): 1.1, 1.2, 1.3 ... then 2.0, 2.1. Never three-part, and there are no patch releases: every release is a new minor.
- `minor`: bump 1.2 → 1.3 (from the old 1.0.16 it gives 1.1)
- `major`: bump 1.2 → 2.0

### 3. Write & commit `RELEASE_NOTES.md` (REQUIRED — do this BEFORE tagging)

`RELEASE_NOTES.md` (repo root) is the **single source of truth** for release notes. CI reads it from the tagged commit and uses it for BOTH the GitHub Release body AND the Sparkle update dialog (`<description>` in `appcast.xml`). It must therefore be committed *before* the release script creates the tag — there is no manual `gh release edit` step anymore.

- Overwrite `RELEASE_NOTES.md` with the notes for this version (it always reflects the latest release; it's fine that it's overwritten each time).
- Write the notes in English.
  - End the file with a footer line: `**Full Changelog**: https://github.com/BespokeWoodcraftStudio/PixelSwitch/compare/vPREV...vX.Y` (CI does not auto-append one).
  - Keep it user-facing and concise — lead with what changed for the user, not internal implementation.
  - Derive content from the commits in `vPREV..HEAD` (`git log --oneline vPREV..HEAD`).
  - This applies to every release, including fix-only ones (a short "Bug fix" entry is fine).
- Commit it on its own: `git add RELEASE_NOTES.md && git commit -m "docs: Release notes for X.Y"`. This commit must land **before** running the release script so the tag includes it.

### 4. Run the release script

Run `./scripts/release.sh <version>` which handles everything:
- Updates `MARKETING_VERSION` in project.yml (all occurrences)
- Increments `CURRENT_PROJECT_VERSION` (build number)
- Runs `xcodegen generate`
- Commits with message: `chore: Bump to X.Y (build N)`
- Creates git tag `vX.Y`
- Pushes the commit to `main`
- Pushes ONLY the specific tag (never `git push --tags`)

CI then builds/notarizes and **automatically** publishes `RELEASE_NOTES.md` to the GitHub Release body and embeds it (rendered to HTML) in the Sparkle appcast. No manual notes step after tagging.

### Critical rules

- **NEVER use `git push --tags`** — it pushes ALL local tags including stale ones. Always push the specific tag: `git push origin vX.Y`
- **MARKETING_VERSION must match the git tag** — if the tag is `v1.3`, MARKETING_VERSION must be `"1.3"`. They are the same value. No exceptions.
- **Verify before pushing** — check that the tag doesn't already exist locally or on remote before creating it
