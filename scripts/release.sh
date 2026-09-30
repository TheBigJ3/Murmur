#!/usr/bin/env bash
# Cut a Murmur release: bump VERSION, update CHANGELOG, commit, and tag vX.Y.Z.
# Does not push; prints the push command instead.
#
# Usage: scripts/release.sh patch|minor|major
set -euo pipefail

PROMPT_MARKER='<!-- murmur:map-prompt -->'

die() { echo "error: $*" >&2; exit 1; }

part="${1:-}"
case "$part" in
  patch|minor|major) ;;
  *) echo "usage: $0 patch|minor|major" >&2; exit 1 ;;
esac

cd "$(git rev-parse --show-toplevel)"

[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty; commit or stash first"

prompt=prompts/map.md
grep -q '[^[:space:]]' "$prompt" 2>/dev/null || die "$prompt is empty"
! grep -qx 'PASTE MAPPING PROMPT HERE' "$prompt" || die "$prompt still contains the placeholder"
[[ "$(head -n 1 "$prompt")" == "$PROMPT_MARKER" ]] \
  || die "$prompt must start with the line: $PROMPT_MARKER"

grep -qx '## \[Unreleased\]' CHANGELOG.md || die "CHANGELOG.md has no '## [Unreleased]' heading"

current="$(tr -d '[:space:]' < VERSION)"
[[ "$current" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]] || die "VERSION '$current' is not X.Y.Z"
major="${BASH_REMATCH[1]}" minor="${BASH_REMATCH[2]}" patch="${BASH_REMATCH[3]}"
case "$part" in
  major) new="$((major + 1)).0.0" ;;
  minor) new="$major.$((minor + 1)).0" ;;
  patch) new="$major.$minor.$((patch + 1))" ;;
esac
tag="v$new"

git rev-parse -q --verify "refs/tags/$tag" >/dev/null && die "tag $tag already exists"

echo "$new" > VERSION

# Notes under [Unreleased] become the new version's section.
tmp="$(mktemp)"
awk -v heading="## [$new] - $(date +%Y-%m-%d)" '
  $0 == "## [Unreleased]" { print; print ""; print heading; next }
  { print }
' CHANGELOG.md > "$tmp" && mv "$tmp" CHANGELOG.md

tmp="$(mktemp)"
sed -E "s/^version = \"[^\"]*\"/version = \"$new\"/" runner/pyproject.toml > "$tmp" \
  && mv "$tmp" runner/pyproject.toml

git add VERSION CHANGELOG.md runner/pyproject.toml
git commit -q -m "Release $tag"
git tag -a "$tag" -m "Murmur $tag"

branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Released $current -> $new (commit + tag $tag created locally)."
echo "Push with:"
echo "  git push origin $branch $tag"
