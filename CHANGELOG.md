# Changelog

All notable changes to Murmur are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). Add notes under `[Unreleased]`;
`scripts/release.sh` turns them into a versioned section.

## [Unreleased]

## [0.5.1] - 2026-09-29

- A step whose required extract finds nothing in a successful response, such as a user with no tickets yet, no longer counts as a failure. It still sets no flags. `murmur try` notes it on the step and counts it separately, and `murmur swarm` lists it apart from failed steps, so it no longer fails the run.
- `murmur swarm --web` starts the run at once and prints the address of Locust's live dashboard, which listens on this machine only and stays open after the run until Ctrl+C. `--web-port` changes its port. Headless runs mention `--web`.
- New `murmur swarm --warm-up [N]`: sends the start node N times (3 by default) before the swarm and prints how long each took, then leaves the ramp-up out of the statistics, for servers and databases that sleep when idle.
- Fixed: Ctrl+C during `murmur swarm` killed Locust before it printed its statistics and the Murmur summary.

## [0.5.0] - 2026-09-29

- New `murmur try`: runs a few real sessions one at a time against a dev server and prints every request, its status and time, the values it extracted, or why it failed.
- New `murmur swarm`: runs many simulated users at once with Locust, grouped by node in Locust's statistics, with think time, users, spawn rate and run time, and prints a Murmur summary of sessions by persona, how they ended, pool shortages and failed steps. Options after `--` go to Locust, including `--master` and `--worker` for running workers on several machines.
- Load graphs can read a response header, such as a token, with `{"header": "Authorization", "required": true}`, and send values on every request with a top-level `headers` block. The runner keeps cookies per session.
- Test accounts come from `.murmur/pool.json`. Each session leases its own `user` account and borrows another as `other_user`. `--pool-shard K/N` splits the pool between machines.
- Safety before any traffic: `--host` is required, a host that is not on this machine needs `--yes`, and the target must answer the new `GET /internal/murmur/health` endpoint with `MURMUR_KEY`, which proves it runs in dev mode.
- The mapping prompt implements the health endpoint, writes `.murmur/pool.example.json` and adds `.murmur/pool.json` to `.gitignore`.

## [0.4.1] - 2026-09-29

- `install.sh` also installs the `murmur` command at the same version as the skills, with uv or pipx, so `murmur validate` and `murmur simulate` work after installing. `murmur-update` updates both, `--uninstall` removes both, and `--no-runner` installs only the skills.
- `murmur-start` checks the load graph with `murmur validate` at its own version, using the installed command or `uvx`, fixes every error it reports, and includes the result in its final summary.
- New `murmur --version`.

## [0.4.0] - 2026-09-29

- `{gen:...}` placeholders are limited to the generators the runner implements: `now_iso`, `today`, `birth_date`, `first_name`, `last_name`, `full_name`, `username`, `query`, `word`, `sentence`, `number` and `uuid`. The mapping prompt lists them with their formats, and `murmur validate` rejects any other name.
- `murmur validate` checks placeholders inside extract JSONPaths, such as a filter on a value extracted earlier.
- The runner walks simulated users through a load graph: it picks a persona by share, keeps only edges whose flags allow them, weights them by the persona's multipliers, fills in every placeholder, sends a node's skip in its place, and applies extracts and flags only when a step succeeds.
- New `murmur simulate` command: walks thousands of sessions without sending a request and reports the traffic mix per node and persona, session lengths, nodes never requested, and steps that fail because a value was not extracted yet. The same `--seed` gives the same report.
- The mapping prompt states that a skip replaces its node's request, and that a placeholder keeps one value within a step.

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
