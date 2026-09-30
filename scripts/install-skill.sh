#!/usr/bin/env bash
# Install the murmur-map Claude Code skill with a version baked in.
#
# Usage: scripts/install-skill.sh [X.Y.Z]
#   No argument: installs the version in VERSION.
#   X.Y.Z:       installs that released version (its template is read from tag vX.Y.Z).
#
# Env: MURMUR_REPO   GitHub "owner/name" to fetch prompts from (default: TheBigJ3/murmur)
#      SKILLS_DIR    Skills directory (default: ~/.claude/skills)
set -euo pipefail

REPO="${MURMUR_REPO:-TheBigJ3/murmur}"
SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
DEST="$SKILLS_DIR/murmur-map/SKILL.md"

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

die() { echo "error: $*" >&2; exit 1; }

version="${1:-$(tr -d '[:space:]' < VERSION)}"
version="${version#v}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid version '$version' (expected X.Y.Z)"

tag="v$version"
if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  template="$(git show "$tag:skill/SKILL.md.template")"
else
  [[ $# -eq 0 ]] || die "tag $tag not found locally (try: git fetch --tags)"
  echo "warning: tag $tag does not exist yet; the skill will fail until it is released and pushed." >&2
  template="$(cat skill/SKILL.md.template)"
fi

mkdir -p "$(dirname "$DEST")"
printf '%s\n' "$template" \
  | sed -e "s|{{VERSION}}|$version|g" -e "s|{{REPO}}|$REPO|g" > "$DEST"

echo "Installed murmur-map skill v$version ($REPO) -> $DEST"
