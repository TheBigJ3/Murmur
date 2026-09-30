# Murmur

Murmur is a load-testing tool that behaves like real users. It maps an API into a
Markov graph (endpoints are states, weighted transitions are the likely next calls)
and runs swarms of simulated users through that graph with [Locust](https://locust.io).

It has two parts:

- **`murmur-map` Claude Code skill.** Run inside a project, it reads the codebase and
  writes three files into `.murmur/`: `loadgraph.json` (the Markov graph), `README.md`
  (the human-readable guide to Murmur in that project) and `manifest.json` (a record
  of everything Murmur added). It also implements dev-only Murmur skip endpoints in
  that project.
- **Runner** (`runner/`). A Python package that loads the graph and drives Locust.

## Layout

```
VERSION                    single source of truth for the version
prompts/map.md             the API-mapping prompt the skill fetches
skill/SKILL.md.template    the skill, with {{VERSION}} and {{REPO}} placeholders
scripts/install-skill.sh   installs the skill with a version baked in
scripts/release.sh         bumps VERSION, updates CHANGELOG, commits, tags
migrations/index.json      every released version, in order
migrations/vX.Y.Z.md       how to bring a project up to vX.Y.Z from the version before
migrations/TEMPLATE.md     the starting point for a new migration file
runner/                    Python Locust runner (Python 3.11+)
schema/                    JSON Schema for .murmur/loadgraph.json
```

## Installing the skill

```bash
git clone https://github.com/TheBigJ3/murmur.git
cd murmur
./scripts/install-skill.sh           # installs the version in VERSION
./scripts/install-skill.sh 0.1.0     # or a specific released version
```

This writes `~/.claude/skills/murmur-map/SKILL.md`. Then, in any project, ask Claude
Code to run `murmur-map`.

Set `MURMUR_REPO=owner/name` to install against a fork.

## How versioning works

The installed skill is small. It contains a fixed version and, on every run, fetches
the mapping prompt for exactly that version:

```
https://raw.githubusercontent.com/TheBigJ3/murmur/v<VERSION>/prompts/map.md
```

If that fetch fails, the skill stops. It never falls back to another version. It also
checks the latest GitHub release and tells you if an update is available, and it
records the version it used as `murmur_version` in `.murmur/loadgraph.json` and
`.murmur/manifest.json`.

`prompts/map.md` must start with the line `<!-- murmur:map-prompt -->`. The skill uses
this marker to confirm it received a real prompt and not an error page or placeholder.
`release.sh` refuses to release without it.

> [!IMPORTANT]
> **Never move or delete a tag once it has been pushed.** Installed skills fetch their
> prompt by tag, so rewriting `vX.Y.Z` silently changes the behavior of every install
> pinned to it, and deleting it breaks them. To fix a release, cut a new one.

## Project manifests

`.murmur/manifest.json` is the machine-readable record of what Murmur did to a project.
It holds:

- `murmur_version` and `updated_at`: the version that last touched the project, and when.
- `history`: every install, update and migration, oldest first.
- `files_added` and `files_changed`: every file Murmur created (including `.murmur/`
  itself) and every exact line it inserted into an existing file. Lines that existed
  before Murmur are never listed.
- `env_vars`, `test_data` and `external_config`: environment variables Murmur added,
  how to find the records it created, and settings outside the repo it relies on.

The skill reads the manifest to decide what to do, and a future cleanup tool will use
it to remove Murmur from a project line by line. On every run the mapping prompt
merges the manifest with the current state rather than rewriting its history.

Projects mapped by v0.1.0 have `.murmur/skips.md` and no manifest. The skill treats them
as a fresh install, and the prompt moves `skips.md` into `README.md` and builds the
manifest from it.

## Migrations

When the skill (version S) runs in a project whose manifest says P:

| Case   | What the skill does |
| ------ | ------------------- |
| No manifest | Fresh install. |
| P = S  | Normal run in update mode. |
| P > S  | Stops and tells you to update the skill. |
| P < S  | Migrates, then runs in update mode. |

To migrate, it fetches `migrations/index.json` from its own tag, then fetches
`migrations/vV.md` for every version V with P < V ≤ S. It lists them and flags breaking
changes and manual steps, and it stops to ask before continuing if any migration needs
manual steps. It fetches everything, including the mapping prompt, before it changes
anything. Then it applies each migration in order, runs its verification, and records
it in the manifest history as `migrate`. If one fails, it stops and leaves the manifest
at the last version that migrated cleanly.

A migration file has these sections: Summary, Breaking changes (yes or no), Manual
steps required (yes or no), Steps to migrate a project from the previous version, and
How to verify. Start from `migrations/TEMPLATE.md`.

The repository must stay public so `raw.githubusercontent.com` can serve the prompt
without authentication.

## Releasing

1. Add notes under `## [Unreleased]` in `CHANGELOG.md`.
2. **Every release needs a migration file**, even one that only says no migration is
   needed. Copy `migrations/TEMPLATE.md` to `migrations/vX.Y.Z.md` for the version you
   are about to release, fill in every section, and commit everything.
3. Run the release script:

   ```bash
   ./scripts/release.sh patch   # or minor / major
   ```

   It refuses to run if the working tree is dirty, if `prompts/map.md` is empty (or
   still the placeholder), or if `migrations/vX.Y.Z.md` is missing, still has a TODO,
   lacks a section, or does not answer yes or no. It bumps `VERSION` (and syncs
   `runner/pyproject.toml`), moves the unreleased notes into a dated section, appends
   the version to `migrations/index.json`, commits, and creates an annotated tag
   `vX.Y.Z`.
4. Push with the command it prints, for example `git push origin main v0.2.0`.
5. Optionally publish a GitHub release so the skill's update check sees it:
   `gh release create v0.2.0 --notes-from-tag` (without releases, it falls back to tags).
6. Reinstall locally: `./scripts/install-skill.sh`.

## Runner

Not implemented yet. For development:

```bash
cd runner
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
```
