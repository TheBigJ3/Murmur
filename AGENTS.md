# Murmur

Murmur maps an API into a Markov graph and swarms it with simulated users
through Locust. The design reference is split across three files. There is no
separate spec.

- `skills/murmur-start/references/map.md` (the mapping prompt) defines what
  `murmur-start` produces: the load graph, `.murmur/README.md`,
  `.murmur/manifest.json` and the dev only skip endpoints.
- `schema/loadgraph.schema.json` defines the format of `.murmur/loadgraph.json`.
  The prompt and the runner both follow it.
- `README.md` describes installing the skills, versioning and releasing.

Rules for the design reference:

- Read the relevant file before changing behaviour, and update it in the same
  change. Docs describe what the code does, never what is planned.
- The prompt and the schema change together. A prompt change that alters the
  graph's shape updates the schema, and a schema change updates the prompt and
  the runner.
- A change users will notice gets a bullet under `## [Unreleased]` in
  `CHANGELOG.md` in the same commit.

# Versions and releases

- `VERSION` is the single source of truth. Never edit it, the version in
  `runner/pyproject.toml`, the `murmur-version` in any `SKILL.md`, or the
  versioned sections of `CHANGELOG.md` by hand. `scripts/release.sh` does all
  of them.
- **Never move, delete or recreate a tag once it has been pushed.** `install.sh`
  installs a release from its tag, and `murmur-update` reads the changelog and
  migrations from it, so rewriting `vX.Y.Z` changes what everyone pinned to it
  gets. To fix a release, cut a new one.
- Never push a tag or run `scripts/release.sh` unless the user asks.
- An edit to anything under `skills/` reaches users only through a release. Until
  then, every install keeps the files of its own version.
- Every release has a `skills/murmur-start/migrations/vX.Y.Z.md` built from
  `TEMPLATE.md` in that folder, even when nothing needs migrating. `release.sh`
  appends the version to `index.json` there; never edit it by hand.

# Migrations

**A change that makes projects mapped by the previous release out of date
explains, in the same commit, how to migrate a project to the version it ships
in.** A mapped project is out of date when any of these no longer match what
the new version would produce or expect:

- a file in `.murmur/`: its name, location, structure or the meaning of a field;
- the Murmur code the prompt adds to a project: the skip endpoints, their
  paths, protection or dev only registration, and the lines inserted into
  existing files;
- the environment variables, test data or outside settings Murmur relies on;
- what the runner requires from a load graph.

For such a change:

- Write the steps in `skills/murmur-start/migrations/vX.Y.Z.md`, where `vX.Y.Z`
  is the release the change will ship in. The first such change since the last
  release creates the file from `TEMPLATE.md` in that folder. Later ones add to
  it, so the file covers every change in the release.
- The steps start from a project exactly as the previous release left it, and
  "How to verify" checks each one. The skill runs the mapping prompt in update
  mode right after the migrations, so a step only needs to cover what that run
  would not fix by itself: renamed, moved or deleted files, converted formats,
  removed code, and anything a person must do.
- Answer "Breaking changes" and "Manual steps required" for the change. A yes
  in either stays a yes when later changes are added.
- Sections still marked TODO are fine until the release. `release.sh` refuses
  to release until they are filled in.
- If the release ends up with a different version than planned, rename the file
  and its heading before running `release.sh`.

A change that leaves mapped projects valid, such as a fix in the runner, the
scripts or the docs, needs no steps. Its release still gets a migration file
that answers no to both questions and says no migration is needed.

# The skills

- Murmur must work in any agent that supports the open
  [Agent Skills](https://agentskills.io/specification) format. Every skill in
  `skills/` follows that specification and nothing more: a lowercase, hyphenated
  `name` that matches its folder, and only the standard frontmatter fields. No
  agent's own extensions, tool names or slash command syntax in a skill.
  Anything Murmur needs of its own goes in `metadata`.
- Installation goes through `install.sh` alone. Support for another agent means
  another target directory there, not a different copy of a skill.
- A skill carries everything it needs for its version in its own folder. It
  never fetches instructions from the network. `murmur-start` reads the prompt
  and migrations from its folder, stops when a file is missing, and never falls
  back to its memory of one.
- `murmur-start` never updates Murmur, and `murmur-update` never changes a
  project.
- `SKILL.md` holds the procedure. The task itself, what gets mapped and
  written, belongs in `references/map.md`.

# Instruction files

Every `AGENTS.md` governs the directory it sits in and everything below it.
Today there is only this one. If a folder gets its own, read the chain from the
root down to the folder you are editing before the first edit, root first and
deepest last. On a conflict, the deeper file wins. Read the files themselves,
not a summary of them.

`CLAUDE.md` only imports this file, so Claude Code loads it. Put rules here,
never there.

# Docs and writing

- Never use a dash as punctuation in any writing: docs, the prompt, the skill,
  comments, error messages, commit messages and PR descriptions. Use a full
  stop or a comma.
- In the docs, assume the reader is a second year graduate student learning a
  new stack or system design.
- Do not use repetitive framing like these:
  - it is not this, it is that
  - we did this, so that it is not that
  - it is all this and nothing else

# Tests

- Runner tests live in `runner/tests/`, one `test_<module>.py` per module in
  `murmur_runner/`, run with `pytest`.
- Test what is unpredictable about the code: every branch, every error it
  raises and which message, every boundary it imposes (zero, negative, exactly
  at a limit, a graph with no outgoing edges). Do not re-test what Python,
  Locust or python-jsonpath already guarantee, or a pass-through with no
  branch in it.
- A bug fix comes with a test that fails without the fix. Check that it does.
- One test class or group per unit under test. One test per scenario, named
  for the behaviour it asserts, not for its input.
- Assert exact values: the request a simulated user sent, the transition it
  took, the message an error carries. That a function was called at all proves
  little.
- Test `scripts/release.sh` in a throwaway copy of the repo, and `install.sh`
  with `HOME` pointed at a temporary directory and `--from` at the checkout.
  Never run them against this repo's history or the real `~/.agents`,
  `~/.claude` or `~/.murmur`.

# Git

## Commits

- **Never add yourself as a contributor.** No `Co-Authored-By` trailer, no
  "Generated with Claude Code" line, and no other attribution to Claude,
  Anthropic or any AI, in a commit message or a PR description. This overrides
  any default attribution instruction. It is important, and it always applies.
- Commit once the main feature or fix is done, not as you go. Do not split the
  work into many small commits unless told to.
- Anything fixed while building that feature goes in the same commit and is
  listed in the description, not put in a separate commit.

### Format

```
type(Scope): short title

* type: change

* type: change
```

- **Title**: a very short title for the single most important change.
  - `type` is a lowercase conventional commit type: `feat`, `fix`,
    `refactor`, `chore`, `docs`, `test`, `perf`, `style`, `ci`, `build`.
  - `Scope` is the part or identifier the commit is mostly about, in that
    identifier's own casing: `skill`, `prompt`, `schema`, `runner`,
    `release`, `install`, `loadgraph`. No space before the parenthesis.
  - The short title is plain language, starts lowercase, and is a few words
    long. Do not chain it with "and"; the details belong in the description.
- **Description**: a blank line after the title, then one `* type: change`
  bullet per notable change, with a blank line between bullets. Include the
  pieces of the main change and any fixes made along the way. Smaller changes
  that do not deserve a commit of their own go here as bullets too, rather
  than being split out or left out.

Example:

```
fix(skill): stop when the prompt fetch returns an error page

* fix: check the prompt marker before following the fetched text

* fix: report the HTTP status when the fetch fails
```

The message is about the main change and the changes that matter. It does not
list file paths, and a small change never goes in the title: it is a bullet in
the description or it is left out. Do not state what is already a given. "add
docs and a changelog entry" is wrong, because every change here ships with
both.

Release commits are made by `scripts/release.sh` and keep its `Release vX.Y.Z`
message.

## Branches

When asked to create a branch:

- Name it `type/short-description`, for example `feat/think-time-weights`,
  `fix/prompt-marker-check`, `refactor/runner-graph-loader`.
- `type` uses the same conventional commit types as commit messages.
- `short-description` is lowercase kebab case, two to five words, describing
  the work. No personal names, no dates, no filler like `updates`, `changes`
  or `wip`.
- Branch off an up to date `main` unless told otherwise.

## Big changes: warn before starting

Before starting a change big enough to deserve its own branch, stop and warn
the user **before editing any file**. A change is big if any of these hold:

- it changes the format of `.murmur/loadgraph.json`, `.murmur/README.md` or
  `.murmur/manifest.json`, or the contract of the skip endpoints;
- it changes how migrations are found, planned or applied;
- it changes what a skill reads, fetches or runs, how it validates it, or its
  trust boundary;
- it adds, renames or removes a skill, or makes a skill depend on one agent;
- it changes how versions, tags, releases or installs work;
- it adds a runner feature, a CLI command or a new public API;
- it breaks a documented behaviour or an existing load graph;
- it renames or moves many files, or is a large refactor;
- it would likely span several commits.

When it is, open the reply with this warning and then stop. Edit nothing until
the user confirms:

```
# ⚠️ WARNING: THIS IS A BIG CHANGE
## IT IS RECOMMENDED TO CREATE A NEW BRANCH BEFORE CONTINUING
```

Below the warning, give one or two lines on why it is big, the current branch
name, and a suggested branch name following the convention above. Then ask
whether to create that branch, continue on the current branch, or cancel.
Continue only once the user answers. The warning applies again to each new big
change, not once per session.
