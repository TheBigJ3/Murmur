#!/usr/bin/env bash
# Install, update or remove the Murmur agent skills.
#
#   curl -fsSL https://raw.githubusercontent.com/TheBigJ3/murmur/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/TheBigJ3/murmur/main/install.sh | bash -s -- --agent claude
#
# Options:
#   --version X.Y.Z   install that release (default: the latest release)
#   --agent NAME      install for an agent; repeatable:
#                       agents  ~/.agents/skills (Codex, Gemini CLI, Cursor, Copilot and others)
#                       claude  ~/.claude/skills (Claude Code)
#                       all     both
#   --dir PATH        install into any other skills directory; repeatable
#   --from PATH       install from a local Murmur checkout instead of downloading
#   --uninstall       remove the Murmur skills from every directory they are installed in
#
# Without --agent or --dir, it reuses the directories of the last install. On a first
# install it uses ~/.agents/skills, plus ~/.claude/skills when ~/.claude exists.
# Directories given with --agent or --dir replace the previous set, and Murmur skills
# are removed from any directory no longer in it.
#
# Env: MURMUR_REPO   GitHub "owner/name" to install from (default: TheBigJ3/murmur)
#      MURMUR_HOME   where the install record is kept (default: ~/.murmur)
set -euo pipefail

REPO="${MURMUR_REPO:-TheBigJ3/murmur}"
MURMUR_HOME="${MURMUR_HOME:-$HOME/.murmur}"
STATE="$MURMUR_HOME/install"

die() { echo "error: $*" >&2; exit 1; }
say() { echo "murmur: $*"; }

version="" from="" uninstall=0
dirs=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) [[ $# -ge 2 ]] || die "--version needs a value"; version="${2#v}"; shift 2 ;;
    --agent)
      [[ $# -ge 2 ]] || die "--agent needs a value"
      case "$2" in
        agents) dirs+=("$HOME/.agents/skills") ;;
        claude) dirs+=("$HOME/.claude/skills") ;;
        all) dirs+=("$HOME/.agents/skills" "$HOME/.claude/skills") ;;
        *) die "unknown agent '$2' (use agents, claude or all, or --dir PATH)" ;;
      esac
      shift 2 ;;
    --dir) [[ $# -ge 2 ]] || die "--dir needs a value"; dirs+=("$2"); shift 2 ;;
    --from) [[ $# -ge 2 ]] || die "--from needs a value"; from="$2"; shift 2 ;;
    --uninstall) uninstall=1; shift ;;
    -h|--help)
      if [[ -f "$0" ]]; then sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'
      else echo "see https://github.com/$REPO/blob/main/install.sh for options"; fi
      exit 0 ;;
    *) die "unknown option '$1' (see --help)" ;;
  esac
done

# Lines "key=value" from the install record, for one key.
state_get() { [[ -f "$STATE" ]] && sed -n "s/^$1=//p" "$STATE" || true; }

# Removes a skill directory only if it is a Murmur skill, never a user's own skill that
# happens to share the name.
remove_skill() {
  if [[ -f "$1/SKILL.md" ]] && grep -q '^  murmur-version:' "$1/SKILL.md"; then
    rm -rf "$1"
  fi
}

prev_dirs=() prev_skills=()
while IFS= read -r line; do [[ -n "$line" ]] && prev_dirs+=("$line"); done < <(state_get dir)
while IFS= read -r line; do [[ -n "$line" ]] && prev_skills+=("$line"); done < <(state_get skill)

if [[ $uninstall -eq 1 ]]; then
  [[ -f "$STATE" ]] || die "no Murmur install recorded in $STATE"
  for d in ${prev_dirs[@]+"${prev_dirs[@]}"}; do
    for s in ${prev_skills[@]+"${prev_skills[@]}"}; do remove_skill "$d/$s"; done
  done
  prev_version="$(state_get version)"
  rm -f "$STATE"
  say "removed Murmur v$prev_version from: ${prev_dirs[*]:-nothing}"
  exit 0
fi

command -v curl >/dev/null || [[ -n "$from" ]] || die "curl is required"
command -v tar >/dev/null || [[ -n "$from" ]] || die "tar is required"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

latest_version() {
  local json v
  if json="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null)"; then
    v="$(printf '%s\n' "$json" | sed -nE 's/.*"tag_name": *"v?([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' | head -n 1)"
  elif json="$(curl -fsSL "https://api.github.com/repos/$REPO/tags?per_page=100")"; then
    v="$(printf '%s\n' "$json" | sed -nE 's/.*"name": *"v([0-9]+\.[0-9]+\.[0-9]+)".*/\1/p' \
      | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)"
  else
    die "cannot reach the GitHub API for $REPO"
  fi
  [[ -n "$v" ]] || die "no release found for $REPO"
  echo "$v"
}

# The source tree: a local checkout or the release tarball.
if [[ -n "$from" ]]; then
  [[ -d "$from" ]] || die "--from: $from is not a directory"
  src="$(cd "$from" && pwd)"
  [[ -d "$src/skills" ]] || die "$src has no skills/ directory"
else
  [[ -n "$version" ]] || version="$(latest_version)"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "invalid version '$version' (expected X.Y.Z)"
  say "downloading $REPO v$version"
  curl -fsSL "https://github.com/$REPO/archive/refs/tags/v$version.tar.gz" | tar -xz -C "$tmp" \
    || die "cannot download v$version of $REPO"
  src="$(find "$tmp" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [[ -d "$src/skills" ]] || die "v$version predates install.sh, which installs v0.3.0 and later"
fi

skills=()
for d in "$src"/skills/*/; do
  [[ -f "$d/SKILL.md" ]] && skills+=("$(basename "$d")")
done
[[ ${#skills[@]} -gt 0 ]] || die "no skills found in $src/skills"

# The version the skills themselves carry.
skill_version="$(sed -nE 's/^  murmur-version: *"([^"]*)".*/\1/p' "$src/skills/${skills[0]}/SKILL.md" | head -n 1)"
if [[ -n "$from" ]]; then
  version="$skill_version"
  say "installing from $src (skills say v$version; unreleased changes there are included)"
elif [[ "$skill_version" != "$version" ]]; then
  die "the v$version release carries skills marked v$skill_version"
fi

if [[ ${#dirs[@]} -eq 0 ]]; then
  if [[ ${#prev_dirs[@]} -gt 0 ]]; then
    dirs=("${prev_dirs[@]}")
  else
    dirs=("$HOME/.agents/skills")
    [[ -d "$HOME/.claude" ]] && dirs+=("$HOME/.claude/skills")
  fi
fi

# Absolute paths, without duplicates.
abs_dirs=()
for d in "${dirs[@]}"; do
  mkdir -p "$d"
  d="$(cd "$d" && pwd)"
  case " ${abs_dirs[*]:-} " in *" $d "*) ;; *) abs_dirs+=("$d") ;; esac
done

for d in "${abs_dirs[@]}"; do
  for s in "${skills[@]}"; do
    rm -rf "$d/.$s.new"
    cp -R "$src/skills/$s" "$d/.$s.new"
    rm -rf "$d/$s"
    mv "$d/.$s.new" "$d/$s"
  done
  # Skills an older release had and this one does not.
  for s in ${prev_skills[@]+"${prev_skills[@]}"}; do
    case " ${skills[*]} " in *" $s "*) ;; *) remove_skill "$d/$s" ;; esac
  done
done

# Directories dropped from the set.
for d in ${prev_dirs[@]+"${prev_dirs[@]}"}; do
  case " ${abs_dirs[*]} " in *" $d "*) continue ;; esac
  for s in ${prev_skills[@]+"${prev_skills[@]}"}; do remove_skill "$d/$s"; done
  say "removed Murmur skills from $d"
done

# The single murmur-map skill that v0.1.0 and v0.2.0 installed for Claude Code.
legacy="$HOME/.claude/skills/murmur-map"
if [[ -f "$legacy/SKILL.md" ]] && grep -q '^Murmur skill version:' "$legacy/SKILL.md"; then
  rm -rf "$legacy"
  say "removed the old murmur-map skill; use murmur-start instead"
fi

mkdir -p "$MURMUR_HOME"
{
  echo "repo=$REPO"
  echo "version=$version"
  echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  for d in "${abs_dirs[@]}"; do echo "dir=$d"; done
  for s in "${skills[@]}"; do echo "skill=$s"; done
} > "$STATE"

say "installed v$version (${skills[*]}) into:"
for d in "${abs_dirs[@]}"; do echo "  $d"; done

case " ${abs_dirs[*]} " in *" $HOME/.agents/skills "*)
  case " ${abs_dirs[*]} " in *" $HOME/.claude/skills "*)
    say "note: Cursor and VS Code read both ~/.agents/skills and ~/.claude/skills, so they list each Murmur skill twice" ;;
  esac ;;
esac
