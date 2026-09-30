# Changelog

All notable changes to Murmur are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Add notes under `[Unreleased]`;
`scripts/release.sh` turns them into a versioned section.

## [Unreleased]

- The skill writes `.murmur/README.md` in place of `.murmur/skips.md`. Projects mapped by v0.1.0 have their `skips.md` moved into it.
- The skill writes `.murmur/manifest.json`, a record of the Murmur version, run history, every file and line Murmur added, its environment variables, its test data and the settings it relies on outside the repo.
- The skill reads the project's manifest and migrates projects mapped by an older version, one release at a time, before running the mapping prompt. It stops when the project is newer than the skill.
- Migrations: `migrations/index.json` lists every release, and each release has a `migrations/vX.Y.Z.md` describing how to bring a project up to it.
- `scripts/release.sh` requires a filled in migration file for the new version and appends the version to `migrations/index.json`.

## [0.1.0] - 2026-09-29

- Initial project skeleton: skill template, install and release scripts, runner and schema placeholders.
- API mapping prompt: finds the API and existing test credential rules, writes `.murmur/loadgraph.json` and `.murmur/skips.md`, and implements key protected, dev only skip endpoints.
