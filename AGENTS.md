# Murmur

Murmur maps an API into a Markov graph and swarms it with simulated users
through Locust. The design reference is split across three files. There is no
separate spec.

- `prompts/map.md` defines what the `murmur-map` skill produces: the load
  graph, `.murmur/skips.md` and the dev only skip endpoints.
- `schema/loadgraph.schema.json` defines the format of `.murmur/loadgraph.json`.
  The prompt and the runner both follow it.
- `README.md` describes installing the skill, versioning and releasing.

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
  `runner/pyproject.toml`, or the versioned sections of `CHANGELOG.md` by hand.
  `scripts/release.sh` does all three.
- **Never move, delete or recreate a tag once it has been pushed.** Installed
  skills fetch `prompts/map.md` by tag, so rewriting `vX.Y.Z` changes every
  install pinned to it. To fix a release, cut a new one.
- Never push a tag or run `scripts/release.sh` unless the user asks.
- `prompts/map.md` must keep `<!-- murmur:map-prompt -->` as its first line.
  The skill uses it to recognise a real prompt, and `release.sh` refuses to
  run without it.
- An edit to `prompts/map.md` reaches users only through a release. Until then,
  every installed skill keeps reading the prompt at its own tag.

# The skill

- `skill/SKILL.md.template` is a thin loader. Task instructions belong in
  `prompts/map.md`, never in the template.
- The template's `{{VERSION}}` and `{{REPO}}` placeholders are filled only by
  `scripts/install-skill.sh`.
- Keep its trust boundary intact: the skill takes instructions only from the
  prompt at its own tag in this repo, stops when the fetch fails or returns
  anything other than the prompt, and never falls back to another version.

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
- Test `scripts/release.sh` and `scripts/install-skill.sh` in a throwaway copy
  of the repo with `SKILLS_DIR` pointed at a temporary directory. Never run
  them against this repo's history or the real `~/.claude/skills`.

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

- it changes the format of `.murmur/loadgraph.json` or `.murmur/skips.md`, or
  the contract of the skip endpoints;
- it changes what the skill fetches, where from, how it validates it, or its
  trust boundary;
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
