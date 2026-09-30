# Murmur

Murmur is a load-testing tool that behaves like real users. It maps an API into a
Markov graph (endpoints are states, weighted transitions are the likely next calls)
and runs swarms of simulated users through that graph with [Locust](https://locust.io).

It has two parts:

- **Agent skills.** Murmur ships as [Agent Skills](https://agentskills.io), the open
  `SKILL.md` format that Claude Code, Codex, Gemini CLI, Cursor, GitHub Copilot and
  many other coding agents read. It isn't tied to any one of them.
  - `murmur-start` reads a project's codebase and writes three files into `.murmur/`:
    `loadgraph.json` (the Markov graph), `README.md` (the human-readable guide to
    Murmur in that project) and `manifest.json` (a record of everything Murmur
    added). It also implements dev-only Murmur skip endpoints in that project.
  - `murmur-update` updates the installed skills to the latest release.
- **Runner** (`runner/`). A Python package that loads the graph and drives Locust.

## Layout

```
VERSION                                  single source of truth for the version
install.sh                               installs, updates and removes the skills
skills/murmur-start/SKILL.md             maps a project
skills/murmur-start/references/map.md    the mapping prompt
skills/murmur-start/migrations/          one file per release, plus index.json and TEMPLATE.md
skills/murmur-update/SKILL.md            updates the installed skills
scripts/release.sh                       bumps and stamps the version, commits, tags
runner/                                  Python runner (Python 3.11+): the murmur command
runner/murmur_runner/loadgraph.schema.json   JSON Schema for .murmur/loadgraph.json
```

## Installing

```bash
curl -fsSL https://raw.githubusercontent.com/TheBigJ3/murmur/main/install.sh | bash
```

This downloads the latest release and installs every Murmur skill, plus the `murmur`
command (the runner) at the same version. The command is installed with
[uv](https://docs.astral.sh/uv/) (`uv tool install`), or with pipx when uv is missing.
With neither, only the skills are installed and the installer prints how to run the
command through `uvx` instead. Where the skills go depends on your agent:

| Agent | Skills directory | Flag |
| ----- | ---------------- | ---- |
| Codex, Gemini CLI, Cursor, GitHub Copilot and others | `~/.agents/skills` | `--agent agents` |
| Claude Code | `~/.claude/skills` | `--agent claude` |
| Both | both of the above | `--agent all` |
| Any other agent | its skills directory | `--dir PATH` |

On a first install without flags, it uses `~/.agents/skills`, plus `~/.claude/skills`
when `~/.claude` exists. Later installs reuse the same directories. Pass flags after
`bash -s --`, for example:

```bash
curl -fsSL https://raw.githubusercontent.com/TheBigJ3/murmur/main/install.sh | bash -s -- --agent claude --version 0.3.0
```

Other options: `--version X.Y.Z` installs a specific release, `--from PATH` installs
from a local checkout, `--no-runner` installs only the skills, and `--uninstall`
removes the skills and the `murmur` command. The installer records what it installed
in `~/.murmur/install`. Set `MURMUR_REPO=owner/name` to install from a fork.

Cursor and VS Code read both `~/.agents/skills` and `~/.claude/skills`. If you install
into both, they list each Murmur skill twice. The copies are identical.

Then, in a project, run the `murmur-start` skill: `/murmur-start` in Claude Code,
Cursor and VS Code, `$murmur-start` in Codex. Gemini CLI activates it when you ask for
Murmur load testing.

### Upgrading from v0.2.0 or earlier

Those versions installed a single `murmur-map` skill with `scripts/install-skill.sh`.
Run the install command above once. It removes `murmur-map` and installs
`murmur-start` and `murmur-update`. Projects mapped by those versions keep working:
`murmur-start` migrates them.

## How versioning works

Each installed skill is a complete copy of one release. `murmur-start` carries the
mapping prompt and every migration for its version in its own directory, and records
that version as `murmur_version` in `.murmur/loadgraph.json` and
`.murmur/manifest.json`. It needs the network only for an optional check for a newer
release, which it reports without installing anything.

Updating is always a separate step. `murmur-update` shows the changelog and the
project migrations between your version and the latest release, flags breaking
changes and manual steps, and asks before it runs the installer for the new release.
It never changes a project. Each project migrates the next time `murmur-start` runs in
it.

> [!IMPORTANT]
> **Never move or delete a tag once it has been pushed.** The installer downloads a
> release by its tag, and `murmur-update` reads the changelog and migrations from it.
> Rewriting `vX.Y.Z` changes what everyone pinned to it gets, and deleting it breaks
> installs and updates. To fix a release, cut a new one.

The repository must stay public so the installer and `murmur-update` can download
releases without authentication.

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

`murmur-start` reads the manifest to decide what to do, and a future cleanup skill will
use it to remove Murmur from a project line by line. On every run the mapping prompt
merges the manifest with the current state rather than rewriting its history.

Projects mapped by v0.1.0 have `.murmur/skips.md` and no manifest. `murmur-start`
treats them as a fresh install, and the prompt moves `skips.md` into `README.md` and
builds the manifest from it.

## Migrations

When `murmur-start` (version S) runs in a project whose manifest says P:

| Case   | What `murmur-start` does |
| ------ | ------------------------ |
| No manifest | Fresh install. |
| P = S  | Normal run in update mode. |
| P > S  | Stops and tells you to run `murmur-update`. |
| P < S  | Migrates, then runs in update mode. |

To migrate, it reads `migrations/index.json` and `migrations/vV.md` for every version V
with P < V ≤ S, all from its own directory. It lists them and flags breaking changes
and manual steps, and it stops to ask before continuing if any migration needs manual
steps. Then it applies each migration in order, runs its verification, and records it
in the manifest history as `migrate`. If one fails, it stops and leaves the manifest
at the last version that migrated cleanly.

A migration file has these sections: Summary, Breaking changes (yes or no), Manual
steps required (yes or no), Steps to migrate a project from the previous version, and
How to verify. Start from `skills/murmur-start/migrations/TEMPLATE.md`.

## Releasing

1. Add notes under `## [Unreleased]` in `CHANGELOG.md`.
2. **Every release needs a migration file**, even one that only says no migration is
   needed. Copy `skills/murmur-start/migrations/TEMPLATE.md` to `vX.Y.Z.md` in the same
   folder for the version you are about to release, fill in every section, and commit
   everything.
3. Run the release script:

   ```bash
   ./scripts/release.sh patch   # or minor / major
   ```

   It refuses to run if the working tree is dirty, if the mapping prompt is empty (or
   still the placeholder), if a skill breaks the Agent Skills naming rules, or if the
   migration file is missing, still has a TODO, lacks a section, or does not answer
   yes or no. It bumps `VERSION`, writes the new version into every skill's
   `murmur-version` and into `runner/pyproject.toml`, moves the unreleased notes into
   a dated section, appends the version to `migrations/index.json`, commits, and
   creates an annotated tag `vX.Y.Z`.
4. Push with the command it prints, for example `git push origin main v0.3.0`.
5. Optionally publish a GitHub release so update checks see it:
   `gh release create v0.3.0 --notes-from-tag` (without releases, they fall back to tags).
6. Update your own install: run `murmur-update`, or the install command above.

## Runner

The runner is a Python package in `runner/` that installs a `murmur` command. It
checks load graphs and simulates them. Sending real traffic with Locust comes next.

```bash
murmur validate                      # checks .murmur/loadgraph.json
murmur validate path/to/loadgraph.json
murmur simulate                      # walks 1000 sessions without sending a request
murmur simulate --sessions 5000 --seed 7 --persona buyer --show 10
```

It reports every problem at once and exits with 1 if there are errors. Warnings, such
as a node that can't be reached from `start`, are printed but don't fail the check.
It checks two things:

- **Structure**, against `runner/murmur_runner/loadgraph.schema.json`: required fields,
  types, `METHOD /path` requests, JSONPath extracts, names, and probabilities between 0
  and 1.
- **The mapping prompt's rules**:
  - `start` and every edge target exist, and every node has an edge to `exit`.
  - Each node's `p` values sum to 1, and so do persona shares. Sums may differ from 1
    by at most 1e-6, which absorbs float rounding but not a real mistake such as
    0.33 + 0.33 + 0.33. Probabilities are never rescaled to fix a sum.
  - Every flag that is required or cleared is set somewhere, and every
    `{test:...}` rule exists.
  - Every `{value}` is extracted somewhere, including values used inside an
    extract's JSONPath.
  - Every `{gen:...}` names a generator the runner implements (see
    `runner/murmur_runner/generators.py`).
  - Skips call `/internal/murmur/` with `X-Murmur-Key: {env:MURMUR_KEY}`.

### How a session walks the graph

1. A session picks a persona at random, weighted by `share`, and runs `start` first.
2. For the next step, it keeps only the edges whose target it can enter: every
   `requires` flag set and every `requires_not` flag unset. Edges to `exit` are always
   kept. Each edge is weighted by `p` times the persona's multiplier for its tag (1
   when the persona has none), and the next node is drawn from the rescaled weights.
   When every weight is 0, the session ends.
3. Placeholders are filled in the path, body (object keys too), headers and extract
   JSONPaths. A string that is only a placeholder keeps the value's JSON type, so a
   numeric id stays a number. Each placeholder gets one value per step. A missing
   value fails the step; it is never sent as an empty string.
4. A node with a skip sends the skip's request, and uses only the skip's extracts and
   flags.
5. A step succeeds when its request succeeds and every required extract finds a value.
   Then its values are stored, its `clears` are applied (`@session` clears every flag
   in `session_flags`), and then its `sets`. A failed step changes no flags and no
   values, but the session still moves to that node and picks its next step from there,
   like a user looking at an error page.

`murmur simulate` walks sessions this way without sending requests. Every extract
finds a stand-in value, and so do pool accounts and environment variables. It reports
each persona's sessions, session lengths, how often each node is requested, nodes that
are never requested, steps that failed because a value had not been extracted yet, and
example sessions. Because every extract succeeds, it shows the most traffic a graph can
produce. A real run, where lists come back empty and tickets sell out, sends less.

### Developing the runner

```bash
cd runner
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```
