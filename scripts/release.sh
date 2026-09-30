#!/usr/bin/env bash
# Cut a Murmur release: bump VERSION, update CHANGELOG, add the version to
# migrations/index.json, commit, and tag vX.Y.Z. Requires migrations/vX.Y.Z.md.
# Does not push; prints the push command instead.
#
# Usage: scripts/release.sh patch|minor|major
set -euo pipefail

PROMPT_MARKER='<!-- murmur:map-prompt -->'
MIGRATION_MARKER='<!-- murmur:migration -->'
MIGRATION_SECTIONS=(
  "Summary"
  "Breaking changes"
  "Manual steps required"
  "Steps to migrate a project from the previous version"
  "How to verify"
)

die() { echo "error: $*" >&2; exit 1; }

# First non-blank line of a "## <name>" section in a Markdown file.
section_first_line() {
  awk -v h="## $2" '$0 == h { f = 1; next } f && /^## / { exit } f && NF { print; exit }' "$1"
}

# check: validate migrations/index.json can take <version>. write: append it.
index_update() {
  python3 - "$1" "$2" <<'PY'
import json, sys

mode, new = sys.argv[1], sys.argv[2]
path = "migrations/index.json"
key = lambda v: tuple(int(n) for n in v.split("."))
try:
    with open(path) as f:
        data = json.load(f)
    versions = data["versions"]
except (OSError, ValueError, KeyError, TypeError) as e:
    sys.exit(f"error: cannot read {path}: {e}")
if new in versions:
    sys.exit(f"error: {new} is already in {path}")
if versions and key(versions[-1]) >= key(new):
    sys.exit(f"error: {new} is not newer than {versions[-1]}, the last version in {path}")
if mode == "write":
    versions.append(new)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
PY
}

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

migration="migrations/$tag.md"
[[ -f "$migration" ]] || die "$migration is missing; copy migrations/TEMPLATE.md and fill it in"
[[ "$(head -n 1 "$migration")" == "$MIGRATION_MARKER" ]] \
  || die "$migration must start with the line: $MIGRATION_MARKER"
grep -qxF "# Migration to $tag" "$migration" || die "$migration must have the heading: # Migration to $tag"
! grep -q 'TODO' "$migration" || die "$migration has unfilled template sections (TODO)"
for section in "${MIGRATION_SECTIONS[@]}"; do
  grep -qxF "## $section" "$migration" || die "$migration is missing the section: ## $section"
  [[ -n "$(section_first_line "$migration" "$section")" ]] || die "$migration: section '$section' is empty"
done
for section in "Breaking changes" "Manual steps required"; do
  section_first_line "$migration" "$section" | grep -Eiq '^(yes|no)([^a-z]|$)' \
    || die "$migration: section '$section' must start with yes or no"
done

command -v python3 >/dev/null || die "python3 is required to update migrations/index.json"
index_update check "$new"

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

index_update write "$new"

git add VERSION CHANGELOG.md runner/pyproject.toml migrations/index.json
git commit -q -m "Release $tag"
git tag -a "$tag" -m "Murmur $tag"

branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Released $current -> $new (commit + tag $tag created locally)."
echo "Push with:"
echo "  git push origin $branch $tag"
