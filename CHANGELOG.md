# Changelog

All notable changes to Murmur are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Add notes under `[Unreleased]`;
`scripts/release.sh` turns them into a versioned section.

## [Unreleased]

## [0.3.0] - 2026-09-29

- Murmur ships as portable [Agent Skills](https://agentskills.io) that work in Claude Code, Codex, Gemini CLI, Cursor, GitHub Copilot and other agents. The `murmur-map` skill is now `murmur-start`.
- New `murmur-update` skill: shows the changelog and project migrations up to the latest release, then updates the installed skills after you confirm. `murmur-start` never updates itself.
- New `install.sh` installs, updates and removes the skills, into `~/.agents/skills`, `~/.claude/skills` or any directory. It replaces `scripts/install-skill.sh` and removes the old `murmur-map` skill. Run it once to upgrade from v0.2.0 or earlier.
- The mapping prompt and migrations ship inside `murmur-start`, so mapping no longer fetches them from GitHub.
- `scripts/release.sh` writes the version into every skill and checks skill names against the Agent Skills rules.
- New `murmur validate` command in the runner: checks `.murmur/loadgraph.json` against its schema and the mapping prompt's rules (probabilities and persona shares summing to 1, edges to real nodes and `exit`, flags that are set, placeholders that resolve, protected skips) and reports every problem at once.
- The mapping prompt states every rule `murmur validate` checks, including plain JSON with no comments and exactly one of `generate` or `value` per test rule.

## [0.2.0] - 2026-09-29

- The skill writes `.murmur/README.md` in place of `.murmur/skips.md`. Projects mapped by v0.1.0 have their `skips.md` moved into it.
- The skill writes `.murmur/manifest.json`, a record of the Murmur version, run history, every file and line Murmur added, its environment variables, its test data and the settings it relies on outside the repo.
- The skill reads the project's manifest and migrates projects mapped by an older version, one release at a time, before running the mapping prompt. It stops when the project is newer than the skill.
- Migrations: `migrations/index.json` lists every release, and each release has a `migrations/vX.Y.Z.md` describing how to bring a project up to it.
- `scripts/release.sh` requires a filled in migration file for the new version and appends the version to `migrations/index.json`.

## [0.1.0] - 2026-09-29

- Initial project skeleton: skill template, install and release scripts, runner and schema placeholders.
- API mapping prompt: finds the API and existing test credential rules, writes `.murmur/loadgraph.json` and `.murmur/skips.md`, and implements key protected, dev only skip endpoints.
