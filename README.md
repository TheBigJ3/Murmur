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

The runner is a Python package in `runner/` that installs a `murmur` command.

```bash
murmur validate                      # checks .murmur/loadgraph.json
murmur simulate                      # walks 1000 sessions without sending a request
murmur try --host http://localhost:3000             # a few real sessions, every step printed
murmur swarm --host http://localhost:3000 --users 50 --run-time 10m
```

### Checking a graph

`murmur validate` reports every problem at once and exits with 1 if there are errors.
Warnings, such as a node that can't be reached from `start`, are printed but don't fail
the check. It checks two things:

- **Structure**, against `runner/murmur_runner/loadgraph.schema.json`: required fields,
  types, `METHOD /path` requests, JSONPath and header extracts, names, and probabilities
  between 0 and 1.
- **The mapping prompt's rules**:
  - `start` and every edge target exist, and every node has an edge to `exit`.
  - Each node's `p` values sum to 1, and so do the shares of the personas that have
    one; a persona has either a `share` or a fixed `count`, and at least one has a
    share. Sums may differ from 1 by at most 1e-6, which absorbs float rounding but not
    a real mistake such as 0.33 + 0.33 + 0.33. Probabilities are never rescaled to fix
    a sum.
  - Every flag that is required, cleared, locked or used as an account's `ready` flag
    is set somewhere: by a step's `sets`, an extract's `unlocks` or a persona's
    `flags`. Every `{test:...}` rule exists.
  - Every `{board:...}` is posted to by some step, and only read in a step's request,
    headers or body. Every `@in:<group>` names a group a persona, an account or a
    `joins` uses.
  - Every `{pool:...}` names `user` or `other_user` and a field, such as
    `{pool:user.email}`.
  - No node requires and forbids the same flag, which would make it impossible to enter.
  - Warnings for a board nobody reads, for `joins` into a group no persona leases from,
    and for a skip node's own `sets`, `clears`, `extract` or `body`, which the skip
    replaces and which are never used.
  - Every `{value}` is extracted somewhere, including values used inside an
    extract's JSONPath and in the top-level `headers`.
  - Every `{gen:...}` names a generator the runner implements (see
    `runner/murmur_runner/generators.py`).
  - Skips call `/internal/murmur/` with `X-Murmur-Key: {env:MURMUR_KEY}`.

### Sending real traffic

`murmur try` and `murmur swarm` need three things:

1. **A dev server** to send to, given with `--host`. A host that isn't on this machine
   also needs `--yes`.
2. **`MURMUR_KEY`** in the environment, set to the key the server's dev build uses.
   Before sending anything, both commands call `GET /internal/murmur/health` with it and
   refuse to run unless it answers 200. The endpoint is only registered in dev builds,
   so a 200 proves the test rules are on and the skips work, and no step can reach a real
   SMS, email or payment provider. `--no-preflight` skips this check; don't use it on a
   server you haven't checked yourself.
3. **Test accounts**, when the graph uses `{pool:...}`: see [Accounts, roles and
   growth](#accounts-roles-and-growth). A graph whose steps create accounts can start
   with none.

### Accounts, roles and growth

The pool is `.murmur/pool.json` (or `--pool PATH`) plus the accounts the graph created in
earlier runs, saved next to it in `.murmur/pool.grown.json`. An account is any set of
text fields, in one or more groups. A group is whatever the app needs, usually a role:

```json
{"groups": {
  "default": [{"email": "pool1@test.com", "password": "..."}],
  "owner":   [{"email": "owner1@test.com", "password": "..."}]
}}
```

`{"accounts": [...]}` is short for the `default` group. An account listed in two groups
is one account with both roles, so it is never leased twice at once. `murmur-start`
writes `.murmur/pool.example.json` and adds `.murmur/pool.json` and
`.murmur/pool.grown*.json` to `.gitignore`.

- **Leasing.** Each session leases one account from its persona's group (`"pool"`,
  `default` when left out) as `user`, and borrows another from the same group as
  `other_user`, such as a transfer's recipient. A session without a free account runs
  without one; the flag `@user` tells the graph whether it has one, and `@in:<group>`
  whether its account is in a group, such as holding a role.
- **Growing.** A step with an `account` block creates an account. It becomes the
  session's user at once, and joins its group, available to every session, when the
  session sets the block's `ready` flag. A step with `joins` adds the session's account
  to more groups, such as a role it just granted. Both are saved to `pool.grown.json`,
  so later runs lease the grown accounts and log in with them. With `--pool-shard K/N`,
  each shard saves to its own `pool.grown.KofN.json`. Every run reads all the grown
  files; a shard keeps the accounts of its own file, and deals out the rest (pool file
  accounts and other shards' grown ones) by a hash of their fields, so they split
  evenly and the same way on every machine. `--no-grow` leaves the pool as it is.
- **Keeping grown accounts valid.** A grown account that lacks a field the graph now
  uses, such as one grown before the graph asked for a phone number, is left out with a
  note. Grown accounts only exist on the server they were created on: after resetting
  its database, delete `.murmur/pool.grown*.json`, or sessions will try to log in as
  accounts that are gone.
- **Roles.** A persona with `"count": N` always has N users, however large the swarm,
  and personas with a `share` split the rest. With `"pool": "owner"` and
  `"flags": ["is_owner"]`, those users lease owner accounts and start with that flag.
  Owner-only work requires `@in:owner`, which holds only once the account really has
  the role, and a grant step has `requires_not: ["@in:owner"]`. When the app can't grant a role through its own API,
  `murmur-start` adds a dev-only `POST /internal/murmur/grant-role` for the graph to call.
- **The board.** A step's `post` hands a value to other sessions, and `{board:<name>}`
  takes one, such as a code one user produced and another uses. Each value is taken
  once, and a node that reads a board stays locked until it has a value. Each process
  has its own board, so on several machines values only pass between users on the same
  one.

### Rate limits

A swarm from one machine is one IP with a few hundred accounts, so rate limits stop it
long before the site is under real load. `murmur-start` lists every limiter it finds and
asks whether to add a dev-only switch, `MURMUR_RELAX_RATE_LIMITS=true`, that turns them
all off. The health check reports whether it's on, and `murmur try` and `murmur swarm`
print a note when it isn't. Test the limits themselves separately, with the switch off.

### Step outcomes

Every step has one of three outcomes:

- **Failed:** the status isn't 2xx, or no response came back.
- **Found nothing:** the status is 2xx, but a required extract matched nothing, such as
  a user with no orders yet. The request worked, so it doesn't count as a failure, but
  the step's own `sets` and `clears` are skipped, so the session carries on without what
  they would have unlocked. Its extracts' `unlocks` and `locks` still follow the response.
- **Succeeded** with every required value.

**`murmur try`** runs sessions one at a time (1 by default) and prints each step: the
request, its status and time, the values it extracted, what it found nothing for, or
why it failed. Use it to find a wrong JSONPath, a missing token or a skip that returns
404 before running a swarm. It exits with 1 if any step failed.

**`murmur swarm`** runs Locust with `--users`, `--spawn-rate`, `--run-time` and
`--think` (seconds between a user's steps, `1-5` by default). Locust groups its
statistics by node name. At the end, Murmur prints sessions by persona, how they ended,
how many started without an account (and whether that group fills by itself), new
accounts and role joins, which steps found nothing, notes, and why steps failed.
Options after `--` go to Locust unchanged:

```bash
murmur swarm --host http://localhost:3000 --users 50 -- --csv results
```

**Watching a run live.** `--web` starts the run at once and shows it in Locust's
dashboard, with charts of requests per second, response times and failures, and a table
per node. Murmur prints its address when the run starts:

```
$ murmur swarm --host http://localhost:3000 --users 25 --run-time 5m --web
murmur: live dashboard at http://localhost:8089 (the run starts now and the dashboard stays open afterwards; Ctrl+C to stop)
```

The dashboard only listens on this machine. `--web-port` changes its port. It stays
open after the run so you can read the charts, until Ctrl+C, which also prints the
Murmur summary.

**Cold starts.** A server or database that sleeps when idle, such as a Neon database,
makes the first requests of a run slow. `--warm-up` sends the start node 3 times before
the swarm (`--warm-up N` for N times) and prints how long each took, then leaves the
ramp-up out of the statistics, so the numbers describe a warm system.

Rate limits per IP often cap what one machine can send. To spread a swarm over several
machines, run one master and a worker on each machine, each with its own share of the
pool. Locust's own `--processes` isn't supported while a pool is in use, because every
process would lease the same accounts; run one worker per process with its own
`--pool-shard` instead:

```bash
murmur swarm --host https://dev.example.com --yes --users 200 -- --master --expect-workers 2
murmur swarm --host https://dev.example.com --yes --pool-shard 1/2 -- --worker --master-host 10.0.0.5
murmur swarm --host https://dev.example.com --yes --pool-shard 2/2 -- --worker --master-host 10.0.0.5
```

### How a session walks the graph

1. A session picks a persona at random, weighted by `share`, or runs the persona of a
   fixed-count user in a swarm. `murmur simulate` runs fixed-count personas as count out
   of `--users` (100 by default), and `murmur try` runs them with `--persona`. The
   session starts with the persona's `flags`, and runs `start` first.
2. For the next step, it keeps only the edges whose target it can enter: every
   `requires` flag set and every `requires_not` flag unset (`@user` counts as set while
   the session has an account), and every board it reads has a value. Edges to `exit`
   are always kept. Each edge is weighted by `p` times the persona's multiplier for its tag (1
   when the persona has none), and the next node is drawn from the rescaled weights.
   When every weight is 0, the session ends.
3. Placeholders are filled in the path, body (object keys too), headers and extract
   JSONPaths. A string that is only a placeholder keeps the value's JSON type, so a
   numeric id stays a number. Each placeholder gets one value per step. A missing
   value fails the step; it is never sent as an empty string.
4. A node with a skip sends the skip's request, and uses only the skip's extracts and
   flags. Its `account`, `joins` and `post` come from the skip when it has its own, and
   from the node otherwise, since they describe the step's outcome. The graph's top-level `headers` go with every request once their values exist,
   and a skip's own headers win on a clash. Each session starts with no cookies and
   keeps the ones the server sets.
5. When the request works, the values it found are stored and values it didn't find
   are forgotten. If every required extract found a value, the step's `clears` apply
   (`@session` clears every flag in `session_flags`), then its `sets`.
6. Then, whether or not the required values were found, each extract's `unlocks` flags
   are set if it found a value and cleared if it didn't, and its `locks` flags are
   cleared if it found one. They come last, so the routes follow what the server shows
   right now, even over the step's own `sets`.
7. If every required value was found, the step's `account`, `joins` and `post` apply
   last. A failed request changes no flags and no values, and gives back any board
   value it took. Either way the session moves to that node and picks its next step
   from there, like a user looking at the page.

`murmur simulate` walks sessions this way without sending requests. Every extract
finds a stand-in value, and so do pool accounts and environment variables, except that
an extract that only unlocks or locks finds one half the time, and when a step creates
accounts, half the sessions start without one. Sessions share one board. It reports
each persona's sessions, session lengths, how often each node is requested, nodes that
are never requested, notes (such as an account a step could not create), steps that
failed because a value had not been extracted yet, and example sessions. Because every extract succeeds, it shows the most traffic a graph can
produce. A real run, where lists come back empty and tickets sell out, sends less.

### Developing the runner

```bash
cd runner
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```
