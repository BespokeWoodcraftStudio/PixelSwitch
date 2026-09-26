#!/bin/bash
set -euo pipefail

# PixelSwitch Release Script
# Ensures version is synced across: project.yml MARKETING_VERSION, git tag, and commit.
#
# Usage:
#   ./scripts/release.sh <version>
#   ./scripts/release.sh minor|major
#
# Versions are two-part (1.1, 1.2, 2.0), never three-part.
#
# Examples:
#   ./scripts/release.sh 1.3
#   ./scripts/release.sh minor    # 1.2 -> 1.3   (also 1.0.16 -> 1.1)
#   ./scripts/release.sh major    # 1.2 -> 2.0

PROJECT_YML="project.yml"

# --- Helpers ---

die() { echo "ERROR: $1" >&2; exit 1; }

get_current_version() {
    grep 'MARKETING_VERSION:' "$PROJECT_YML" | head -1 | sed 's/.*"\(.*\)".*/\1/'
}

get_current_build() {
    grep 'CURRENT_PROJECT_VERSION:' "$PROJECT_YML" | head -1 | sed 's/.*"\(.*\)".*/\1/'
}

bump_version() {
    local current="$1" type="$2"
    IFS='.' read -r major minor _ <<< "$current"
    case "$type" in
        major) echo "$((major + 1)).0" ;;
        minor) echo "$major.$((minor + 1))" ;;
        *) die "Unknown bump type: $type" ;;
    esac
}

# --- Preflight checks ---

# Must be in repo root
[ -f "$PROJECT_YML" ] || die "Must be run from the project root (project.yml not found)"

# Must be on main branch
BRANCH=$(git branch --show-current)
[ "$BRANCH" = "main" ] || die "Must be on main branch (currently on '$BRANCH')"

# Must have clean working tree
if ! git diff --quiet || ! git diff --cached --quiet; then
    die "Working tree is dirty. Commit or stash changes first."
fi

# Must have no untracked files in source directories
UNTRACKED=$(git ls-files --others --exclude-standard PixelSwitch/ PixelSwitchWidget/ Shared/ project.yml)
[ -z "$UNTRACKED" ] || die "Untracked source files found:\n$UNTRACKED"

# --- Determine version ---

CURRENT_VERSION=$(get_current_version)
CURRENT_BUILD=$(get_current_build)

if [ $# -ne 1 ]; then
    echo "Current: v${CURRENT_VERSION} (build ${CURRENT_BUILD})"
    echo ""
    echo "Usage: $0 <version|minor|major>"
    exit 1
fi

INPUT="$1"

case "$INPUT" in
    minor|major) NEW_VERSION=$(bump_version "$CURRENT_VERSION" "$INPUT") ;;
    [0-9]*.[0-9]*) [[ "$INPUT" =~ ^[0-9]+\.[0-9]+$ ]] || die "Version must be two-part, like 1.1 or 2.0 (got $INPUT)"; NEW_VERSION="$INPUT" ;;
    *) die "Invalid argument: $INPUT (expected a version like 1.1, or minor/major)" ;;
esac

NEW_BUILD=$((CURRENT_BUILD + 1))
TAG="v${NEW_VERSION}"

# --- Validate ---

echo "Release plan:"
echo "  Version:  ${CURRENT_VERSION} -> ${NEW_VERSION}"
echo "  Build:    ${CURRENT_BUILD} -> ${NEW_BUILD}"
echo "  Tag:      ${TAG}"
echo ""

# Check tag doesn't already exist locally or remotely
if git tag -l "$TAG" | grep -q .; then
    die "Tag $TAG already exists locally. Delete it first: git tag -d $TAG"
fi

if git ls-remote --tags origin "$TAG" | grep -q .; then
    die "Tag $TAG already exists on remote. Choose a different version."
fi

read -p "Proceed? [y/N] " confirm
[[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }

# --- Execute ---

# 1. Update MARKETING_VERSION and CURRENT_PROJECT_VERSION in project.yml (all occurrences)
sed -i '' "s/MARKETING_VERSION: \"${CURRENT_VERSION}\"/MARKETING_VERSION: \"${NEW_VERSION}\"/g" "$PROJECT_YML"
sed -i '' "s/CURRENT_PROJECT_VERSION: \"${CURRENT_BUILD}\"/CURRENT_PROJECT_VERSION: \"${NEW_BUILD}\"/g" "$PROJECT_YML"

# 2. Regenerate Xcode project
xcodegen generate

# 3. Verify the update
VERIFY_VERSION=$(get_current_version)
VERIFY_BUILD=$(get_current_build)
[ "$VERIFY_VERSION" = "$NEW_VERSION" ] || die "Version update failed: expected $NEW_VERSION, got $VERIFY_VERSION"
[ "$VERIFY_BUILD" = "$NEW_BUILD" ] || die "Build update failed: expected $NEW_BUILD, got $VERIFY_BUILD"

# 4. Commit
git add "$PROJECT_YML"
git commit -m "chore: Bump to ${NEW_VERSION} (build ${NEW_BUILD})"

# 5. Create tag on this exact commit
git tag "$TAG"

# 6. Push commit and ONLY this specific tag (never --tags)
git push origin main
git push origin "$TAG"

# The tag push starts the release build by itself: the repository is no longer
# a GitHub fork (it was on 2026-09-21, when pushes started nothing). Start one
# by hand ONLY if no run for this tag appears. Two release builds race to upload
# the DMG and the appcast, and a DMG from one with the appcast's signature from
# the other fails every update (v1.2, 2026-09-25: both ran; this time the later
# one uploaded all three files).
#
# `-R` is not optional. Without it the gh CLI resolved the old fork to its
# PARENT and the dispatch came back "HTTP 403" (v1.0.10, 2026-09-22). Read from
# the origin remote rather than hardcoded, so a rename cannot break it silently.
REPO=$(git remote get-url origin | sed -E 's#.*github\.com[:/]##; s#\.git$##')
RUN=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    sleep 5
    RUN=$(gh run list -R "$REPO" --workflow build.yml --branch "$TAG" --event push -L 1 --json databaseId --jq '.[0].databaseId // empty')
    [ -n "$RUN" ] && break
done
if [ -n "$RUN" ]; then
    echo "The tag push started the release build: https://github.com/${REPO}/actions/runs/${RUN}"
else
    echo "No build started from the tag push within a minute; starting it by hand."
    gh workflow run build.yml -R "$REPO" --ref "$TAG"
fi

echo ""
echo "Released ${TAG} (build ${NEW_BUILD})"
echo "GitHub Actions will build and publish the release. Watch it with: gh run list -R ${REPO} -L 1"
