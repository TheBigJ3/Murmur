# Murmur

Murmur is a load-testing tool that behaves like real users. It maps an API into a
Markov graph (endpoints are states, weighted transitions are the likely next calls)
and runs swarms of simulated users through that graph with [Locust](https://locust.io).

It has two parts:

- **`murmur-map` Claude Code skill.** Run inside a project, it reads the codebase and
  writes `.murmur/loadgraph.json` (the Markov graph) and `.murmur/skips.md`. It also
  implements dev-only Murmur skip endpoints in that project.
- **Runner** (`runner/`). A Python package that loads the graph and drives Locust.

## Layout

```
VERSION                    single source of truth for the version
prompts/map.md             the API-mapping prompt the skill fetches
skill/SKILL.md.template    the skill, with {{VERSION}} and {{REPO}} placeholders
scripts/install-skill.sh   installs the skill with a version baked in
scripts/release.sh         bumps VERSION, updates CHANGELOG, commits, tags
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
records the version it used as `murmur_version` in `.murmur/loadgraph.json`.

`prompts/map.md` must start with the line `<!-- murmur:map-prompt -->`. The skill uses
this marker to confirm it received a real prompt and not an error page or placeholder.
`release.sh` refuses to release without it.

> [!IMPORTANT]
> **Never move or delete a tag once it has been pushed.** Installed skills fetch their
> prompt by tag, so rewriting `vX.Y.Z` silently changes the behavior of every install
> pinned to it, and deleting it breaks them. To fix a release, cut a new one.

The repository must stay public so `raw.githubusercontent.com` can serve the prompt
without authentication.

## Releasing

1. Add notes under `## [Unreleased]` in `CHANGELOG.md` and commit everything.
2. Run the release script:

   ```bash
   ./scripts/release.sh patch   # or minor / major
   ```

   It refuses to run if the working tree is dirty or `prompts/map.md` is empty (or still
   the placeholder). It bumps `VERSION` (and syncs `runner/pyproject.toml`), moves the
   unreleased notes into a dated section, commits, and creates an annotated tag `vX.Y.Z`.
3. Push with the command it prints, for example `git push origin main v0.1.0`.
4. Optionally publish a GitHub release so the skill's update check sees it:
   `gh release create v0.1.0 --notes-from-tag` (without releases, it falls back to tags).
5. Reinstall locally: `./scripts/install-skill.sh`.

## Runner

Not implemented yet. For development:

```bash
cd runner
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
```
