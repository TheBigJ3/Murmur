# Murmur: API mapping

Analyze this codebase and map its API into a JSON graph for Murmur, a realistic load-testing tool. Then implement any private Murmur endpoints the graph still needs.

If a .murmur/ folder or a Murmur module already exists, update them instead of starting over. Keep working code, fix what's wrong, and list what you changed.

If .murmur/skips.md exists, it was written by Murmur 0.1.0, before .murmur/README.md and .murmur/manifest.json existed. Carry its content into .murmur/README.md, delete .murmur/skips.md, and use its removal instructions to build the manifest in section 6.

## 1. Find the API

Find the API definition. Check for an OpenAPI/Swagger spec first. If none exists, read the route/controller files directly.

## 2. Find existing test credential rules

Many dev builds already have conventions that bypass external services, for example:
- Emails on a test domain (e.g. @test.com) that are auto-verified
- Phone numbers in a test range that skip real SMS
- A fixed OTP code for test numbers (e.g. 000000)
- Test card numbers, sandbox API keys, or mock providers enabled in dev

Search validation logic, auth/OTP services, SMS/email providers, seed scripts, fixtures, env configs, and docs. For each rule, note the file and function that implements it, and whether it is restricted to dev builds.

## 3. Write .murmur/loadgraph.json

Create a .murmur/ folder in the root of the project if it doesn't exist. Write .murmur/loadgraph.json using exactly this structure. The comments explain the fields; the file itself must be plain JSON with no comments.

```
{
  "murmur_version": "<version given by the skill>",
  "start": "<entry node>",
  "session_flags": ["<flag>"],             // flags that belong to a logged-in session
  "test_rules": {
    "<rule_name>": {
      "description": "<what it bypasses>",
      "generate": "<pattern>",            // how to make a value that triggers it
      "value": "<fixed value>",           // for fixed values like an OTP code
      "source": "<file:function>",
      "dev_only": true
    }
  },
  "headers": {                            // sent on every request once their values exist
    "Authorization": "{accessToken}"
  },
  "nodes": {
    "<node_name>": {
      "request": "METHOD /path/{placeholder}",
      "body": { "field": "{var}", "code": "{board:<board>}" },  // {board:...} takes a value another session posted
      "extract": {
        "var": { "path": "$.jsonpath[*].field", "pick": "random", "required": true },
        "accessToken": { "header": "Authorization", "required": true },  // from a response header
        "itemId": { "path": "$.items[*].id", "pick": "random", "unlocks": ["has_item"] },  // a state check
        "doneJob": { "path": "$.jobs[?(@.status == 'done')].id", "pick": "first", "locks": ["job_pending"] }
      },
      "sets": ["flag"],
      "clears": ["flag"],                 // or ["@session"] to clear all session_flags
      "requires": ["flag"],               // "@user": has an account; "@in:<group>": its account is in that group
      "requires_not": ["flag"],
      "account": {                        // this step creates an account
        "group": "<pool group>",
        "fields": { "email": "{test:test_email}", "password": "{gen:password}" },
        "ready": "<flag set once the account can log in>"
      },
      "joins": ["<pool group>"],          // the session's account gains this role or group
      "post": { "<board>": "{var}" },     // hands a value to other sessions
      "skip": {                           // may have its own account, joins and post, which win
        "reason": "<service name>",
        "request": "METHOD /internal/murmur/<action>",
        "headers": { "X-Murmur-Key": "{env:MURMUR_KEY}" },
        "body": { ... },
        "extract": { "var": { "path": "$.jsonpath", "pick": "first", "required": true } },
        "sets": ["flag"],
        "clears": ["flag"]
      }
    }
  },
  "edges": {
    "<node_name>": [ { "to": "<node_name or exit>", "p": 0.0, "tag": "<behavior>" } ]
  },
  "personas": {
    "<name>": { "share": 0.0, "multipliers": { "<tag>": 1.0 } },
    "<role>": { "count": 1, "pool": "<pool group>", "flags": ["<flag>"], "multipliers": { "<tag>": 1.0 } }
  }
}
```

### Test rule pattern syntax for "generate"
- "#" = one random digit
- "{rand}" = a random unique lowercase string
- Everything else is literal

Example: { "test_email": { "generate": "{rand}@test.com" }, "test_phone": { "generate": "+1000#######" }, "test_otp": { "value": "000000" } }

Each test rule has either "generate" or "value", never both. Reference rules in nodes as {test:<rule_name>}, e.g. {test:test_email}, {test:test_otp}.

### Rules
- Every {placeholder} must come from an earlier "extract", a test rule ({test:...}), the test account pool ({pool:...}), a generated input ({gen:...}), the shared board ({board:...}), or, for the Murmur key only, the environment ({env:MURMUR_KEY}). Placeholders also work inside an extract's JSONPath, e.g. a filter on a value extracted earlier.
- {gen:...} must be one of these generators. When a field needs a format none of them gives, use the closest one and note the gap in .murmur/README.md.
  - now_iso: the current time, ISO 8601 UTC with milliseconds (2026-09-29T18:05:07.123Z)
  - today: the current UTC date (2026-09-29)
  - birth_date: a date of birth for someone 18 to 80 years old (YYYY-MM-DD)
  - first_name, last_name, full_name: a plain name
  - username: a lowercase name followed by 8 digits, unique in every run (maya04821337)
  - query, word: a single common word, for search terms
  - sentence: 5 to 10 words ending in a full stop
  - number: an integer from 1 to 100
  - password: 16 characters mixing upper and lower case, digits and a symbol, for new accounts
  - uuid: a random version 4 UUID, unique in every run
- Use "requires" for anything that needs login, an existing ID, or prior state (e.g. a non-empty cart, an open draft, membership of a group).
- Use "requires_not" where a step only makes sense without a flag (e.g. login and signup have "requires_not": ["authed"]).
- Use "clears" whenever a step ends a state (e.g. releasing a reservation clears its flag, publishing a draft clears the draft flag).
- "@session" may only appear in "clears". "@user" and "@in:<group>" may only appear in "requires" and "requires_not". Neither goes in "sets", "session_flags", a persona's "flags" or an extract's "unlocks" or "locks".
- List every flag that belongs to a logged-in session in "session_flags". Logout nodes use "clears": ["@session"] instead of listing flags individually.
- Every flag in "requires", "requires_not", "clears", "session_flags", an extract's "locks" and an account's "ready" must be set somewhere: by a node's or skip's "sets", an extract's "unlocks", or a persona's "flags".
- Node names, flags, tags, and persona and test rule names use only letters, digits, _, . and -. "exit" is reserved for leaving the graph and is never a node name.
- Each node's outgoing "p" values must sum to 1, and every node needs an edge to "exit". Split thirds as 0.33, 0.33 and 0.34, never 0.33 three times.
- Tags: use a small set that fits this app (e.g. browse, search, create, account, share). Exit edges use the tag "exit".
- Probabilities are your best guess at typical user behavior. Reads more common than writes; each funnel step loses some users.
- Every persona has exactly one of "share" or "count". Create 3-4 personas for the realistic user types of this specific app, each with a "share"; their shares must sum to 1, and at least one persona must have a share. Multipliers may include "exit" to make a persona leave sooner or later.
- Users with a special role in this app, such as staff, moderators, sellers or owners, are personas with a "count" (1 or more) instead of a share: that many users always run the role, however large the swarm. Give each role persona a short, likely route from "start" to its role work, using its multipliers, so it spends its sessions doing that work. Give each role a "pool" group whose accounts hold that role, and "flags" that every session of the role starts with. Nodes only that role may use require those flags.
- "@in:<group>" is set while the session's account is in that pool group: leased from it, created into it, or joined to it. A role persona's "flags" say what the persona is; "@in:<role group>" says its account actually holds the role, so gate the role's own work with "requires": ["@in:<group>"].
- Every flag a node requires must come with every value that node uses. If several steps set or unlock the same flag, each of them must also extract every variable the flag's nodes use, under the same name, or a later step will find the flag but not the value.
- "@user" is set while a session has an account, leased from the pool or created by a step. Use "requires": ["@user"] on steps that log in with {pool:user...}, and "requires_not": ["@user"] on a signup that only sessions without an account should take.
- Skip health checks, internal, and admin-only endpoints unless they're part of a real user journey or a role persona uses them.

### Extraction
- When extracting from a list, default to "pick": "random" so users spread across items. Use "pick": "first" only where users realistically converge (e.g. the newest post, a limited release) or the response has a single item.
- Mark an extract "required": true when the node's "sets" flags only make sense if a value was found (e.g. has_item requires an itemId). If a required extract finds nothing, the runner skips that node's "sets" and "clears", and its "account", "joins" and "post"; the extracts' "unlocks" and "locks" still apply. Extracts are optional by default.
- For endpoints that show the user's current state (their items, pending requests, job status), give extracts "unlocks" and "locks" instead of relying on "sets". Each time the node runs, an extract's "unlocks" flags are set if it found a value and cleared if it found none, and its "locks" flags are cleared if it found a value. The routes a user can take then follow what the server says they have right now: a user whose last item is gone loses the route that acts on it, and a finished job clears its pending flag.
- Actions on a specific item must use the ID from the step that created or selected it, not a fresh list lookup.
- When the API returns a token or session value in a response header, extract it with {"header": "<name>", "required": true} and send it with the top-level "headers". A top-level header is left out until its value exists, so logged-out requests don't send it. Header names match in any case.
- The runner keeps cookies for each session, including rotated ones, and starts every session with none. Don't model cookies in the graph.

### Test data
- New accounts (signup) use test rules, e.g. {test:test_email} and {test:test_phone}, so the real signup flow runs without sending real email or SMS. Passwords use {gen:password}.
- Give the step that creates an account an "account" block: its "fields" are the same placeholders the request sent (one value per step, so they match), plus any id the step extracted, and "ready" names the flag that shows the account can log in. "ready" does not set that flag: the signup's own "sets" or a later step's (such as an email or code verification step) must set it. For a two-step signup, the signup step extracts the new user's id and creates the account, and the verification step sets the ready flag. The account becomes the session's user at once and joins its pool group when ready; the runner saves it in .murmur/pool.grown.json so later runs lease and log in with it. Don't add a password field if the app has none.
- Logins use {pool:user.email}, {pool:user.password}, {pool:user.phone}. Accounts come from .murmur/pool.json, for accounts that must already have something (history, purchases, a role that can't be granted by a step), and from accounts the graph creates. Pool accounts must themselves use test-rule emails and phones. Each account is leased to one virtual user at a time.
- Roles: find how this app gives an account a role (staff, owner, moderator, seller, and so on). If a real step grants it (creating an organization, accepting an invite, an admin action another persona can take), model that step and give it "joins": ["<group>"], so the account joins that pool group and role personas can lease it. If only a database change grants it, implement a Murmur endpoint POST /internal/murmur/grant-role that gives the current user a role, called as the skip of a node that has "joins" (on the node or in the skip), with the same protection and dev-only registration as every Murmur endpoint, and list the roles it can grant in .murmur/README.md. The skip replaces the node's request, so the node's own "request" can be the same POST /internal/murmur/grant-role. Give the grant node "requires_not": ["@in:<group>"], so accounts that already hold the role never call it. A role persona whose group starts empty should then sign up, get the role and join, so its accounts exist from the first run on. For a role no step and no grant-role can give, the accounts must be created by hand: list them in .murmur/pool.json and say so in .murmur/README.md.
- A placeholder gets one value per step: {test:test_email} twice in the same body is the same address, and a fresh one in the next step. To reuse a value in a later step, extract it from the response.
- OTP codes use the test rule value, e.g. {test:test_otp}. Never use {gen:...} for OTP codes, existing credentials, phones that receive SMS, or email recipients. The one exception is {gen:password}, for the password of an account a step creates.
- For recipients of transfers, invites, or shares, use {pool:other_user.email} so another simulated user receives it. other_user comes from the same pool group as the session's own account.
- When one user needs a value another user produced, such as a code, an id sent to them out of band, or an item to act on, the producing step "post"s it to a named board and the consuming step reads {board:<name>}. Each value is taken once, and a node that reads a board waits, locked, until the board has a value. {board:...} may only appear in a node's or skip's request, headers or body, never in an extract, an account, a post or the top-level headers, and every board read needs some step that posts to that board. Make the posting route at least as likely as the reading route across the personas that run them, or the readers mostly wait.
- Holds, reservations, claimed tasks, or anything that locks a shared resource must have a path that releases or completes it.
- Write .murmur/pool.example.json in the pool format the runner reads, {"accounts": [{...}]} for the default group or {"groups": {"<group>": [{...}]}} for several, with one example account per group the personas use, holding every field the graph uses as {pool:user.<field>} or {pool:other_user.<field>}, and example values that follow the test rules. An account listed in several groups is one account with several roles. The real pool goes in .murmur/pool.json and grown accounts in .murmur/pool.grown.json, or pool.grown.<K>of<N>.json per shard; they hold credentials, so add .murmur/pool.json and .murmur/pool.grown*.json to the project's .gitignore.

### External services (priority order)
1. If an existing test rule bypasses the service, use it in the node. No skip needed.
2. If no test rule exists, add a "skip" block to that node. The skip calls a private Murmur endpoint that produces the same result the real flow would (e.g. marks the payment complete and queues the follow-up job, or marks an address as verified) without calling the outside service.

- Check every step that depends on a third-party service: payments like Stripe, SMS/OTP, email verification, captchas, OAuth providers, notification transports, etc.
- No step may send a real SMS, email, or payment request.
- Name skip endpoints /internal/murmur/<action>. Authenticate them with the header X-Murmur-Key: {env:MURMUR_KEY}.
- The runner sends a node's skip in place of its request, and applies only the skip's "extract", "sets" and "clears", which must match what the real step would produce so later nodes still work. "account", "joins" and "post" describe the step's outcome: they may sit on the node or in the skip, and the skip's own win.

## 4. Implement the Murmur endpoints

Implement the health endpoint, and the skips not covered by test rules.

Before writing any code, study how this codebase is built:
- Folder structure, routing, controllers/handlers, middleware, validation, error handling, response format, logging, and naming conventions.
- Any lint rules, style configs, or contributor docs (e.g. .eslintrc, CONTRIBUTING.md, CLAUDE.md).

The Murmur code must look like it was written by the same team and pass the existing lint and type checks.

### Implementation rules
- Reuse the existing code that runs after the external service succeeds (e.g. the Stripe webhook handler's order-completion logic). Call those functions rather than duplicating their logic, so Murmur tests the real pipeline. If some duplication is unavoidable, keep it minimal and list it in .murmur/README.md so it can be kept in sync.
- Do not modify the real payment, OTP, or auth flows, or the existing test rules. Only add new code.
- Put all Murmur code in one clearly named module or folder (e.g. murmur/ or internal/murmur/) so it's easy to find and remove.
- Mark every record Murmur creates as test data if the schema allows it. If there's no such field, log the created IDs so they can be cleaned up.
- Add tests for the key check, the health endpoint and each skip, following the project's existing test setup.

### Health endpoint (required)
- Implement GET /internal/murmur/health, returning 200 with {"murmur": "ok", "rate_limits": "relaxed"} when the rate limit switch below is on and {"murmur": "ok", "rate_limits": "on"} otherwise, even when no skips are needed. It has the same protection and dev-only registration as every other Murmur endpoint.
- murmur try and murmur swarm call it before sending any traffic and refuse to run unless it answers 200. That proves the target runs in dev mode with the same MURMUR_KEY, so its test rules are on and its skips work.

### Protection (required)
- Every Murmur endpoint must check the X-Murmur-Key header against the MURMUR_KEY environment variable using a constant-time comparison.
- If MURMUR_KEY is unset or empty, the endpoints must refuse all requests. Fail closed.
- Return 404 (not 401/403) on any failed check, so the endpoints don't reveal they exist.

### Dev-only builds (required)
- Find how this project currently separates dev and prod: environment variables (NODE_ENV, APP_ENV, etc.), build flags, per-environment config files, separate entry points, deployment configs, CI/CD pipelines, Dockerfiles.
- Use that existing mechanism so Murmur routes are only registered in dev builds. In prod, the routes must not exist at all. Do not rely on the key check alone.
- If a clean build-time exclusion is possible (so the Murmur code isn't even shipped in prod), prefer it.
- Check whether each deployed dev environment actually turns dev mode on (e.g. its startup command or deployment config sets the dev flag). If you can't confirm it from the repo, say so clearly, since test rules may be off there and real SMS/email could be sent. Give a quick request I can send to verify it.
- If you find NO existing dev/prod separation:
  - Still implement the endpoints, but gate registration behind MURMUR_ENABLED=true, defaulting to off.
  - Add a large warning comment at the top of every Murmur file:
```
    // ⚠️⚠️⚠️ WARNING: NO DEV/PROD SEPARATION FOUND IN THIS PROJECT ⚠️⚠️⚠️
    // These endpoints bypass payments and verification.
    // DELETE THIS MODULE before deploying to production,
    // or add a real dev/prod build separation first.
```
  - Put the same warning at the very top of .murmur/README.md.
  - Make this warning the FIRST thing in your final summary, in capitals.
- If any existing test rule is NOT restricted to dev builds (e.g. @test.com auto-verifies in prod too), flag it with a similar warning. That is a live security hole regardless of Murmur. Do not fix it yourself; report it.

### Rate limits
- Find every rate limiter that applies to the graph's endpoints, of every kind: per IP, per user, per account, per route, login and signup limits, and send throttles such as OTP or email limits per recipient. Record each one's size and refill rate, and estimate the sustained request rate a single load-generator IP can reach through it.
- A swarm measures site performance. One load generator sends from one IP with a few hundred accounts, so the rate limits stop it long before the site is under real load. Murmur can add a dev-only switch that turns them off.
- Before changing anything about rate limiting, stop and ask me. List every limiter you found, say that the switch would turn off all of them, that it only works in dev builds when MURMUR_RELAX_RATE_LIMITS=true, that production is unaffected, and that the limits are still worth testing on their own. Wait for my answer.
- If I say yes: implement MURMUR_RELAX_RATE_LIMITS, off by default, behind the same dev-only check as the Murmur endpoints. Where a limiter can be configured, keep its code running with limits so high they never trigger, so its own cost (such as a Redis call) is still measured; skip it only where it can't. Make the health endpoint report "relaxed" while the switch is on. Record the variable, the files and the exact lines in the manifest, and add MURMUR_RELAX_RATE_LIMITS= to the same env example file as MURMUR_KEY.
- If I say no: change nothing, and explain in .murmur/README.md what the limits allow from one IP.

## 5. Write .murmur/README.md

.murmur/README.md is the human-readable guide to Murmur in this project. Include:
- How dev/prod separation works in this project and exactly how Murmur uses it (or the warning above if none exists), including whether deployed dev environments are confirmed to run in dev mode.
- Every test rule found: what it bypasses, where it's implemented, and whether it's dev-only.
- For each skip: which real step it replaces and why, the endpoint path, the request body, the response, which existing functions it calls, any duplicated logic, and which files you added or changed.
- What the test account pool needs (how many accounts, which fields, which test rules they rely on, any required state such as existing orders), and how to create the accounts and fill in .murmur/pool.json from .murmur/pool.example.json.
- Rate limits that affect the graph, whether the MURMUR_RELAX_RATE_LIMITS switch was added and what it turns off, and what the limits mean for swarm size when it is off.
- The pool groups the personas use, which roles they stand for, how accounts get into each group (pool.json, created by a step, or joined through a step or grant-role), and which boards pass values between users.
- Anything the runner must handle that the JSON can't express (e.g. time windows such as how long a hold lasts, requests the real client sends in parallel, or jobs whose completion the graph can't see).
- Endpoints left out of the graph and why.
- How to delete Murmur completely: every file and registration line to remove.

## 6. Write .murmur/manifest.json

The manifest is the machine-readable record of everything Murmur put into this project. The skill reads it to decide which migrations to apply, and a cleanup tool uses it to remove Murmur precisely. Write it using exactly this structure:

```
{
  "murmur_version": "<version given by the skill>",
  "updated_at": "<ISO 8601 timestamp>",
  "history": [ { "version": "<version>", "at": "<ISO timestamp>", "action": "install | update | migrate" } ],
  "files_added": [ "<path from project root>" ],
  "files_changed": [
    {
      "path": "<path>",
      "insertions": [ "<exact line Murmur added>" ],
      "description": "<why>"
    }
  ],
  "env_vars": [ { "name": "MURMUR_KEY", "files": ["<env files where it was added>"] } ],
  "test_data": {
    "description": "<how Murmur-created records can be identified>",
    "queries": [ "<SQL or commands that find them>" ]
  },
  "external_config": [ "<settings outside the repo that Murmur relies on, e.g. env vars on the dev server>" ]
}
```

### Rules
- Record every file Murmur added and every exact line it inserted into an existing file, so a cleanup tool can remove them precisely.
- Include .murmur/ itself in "files_added".
- Never list lines that existed before Murmur.
- If no manifest existed before this run, start "history" with one entry for this run, with action "install".
- On an update, merge with the existing manifest: keep "history" (including any "migrate" entries the skill added), add a new entry with action "update", and make the file lists match the current state.
- Set "murmur_version" to the version given by the skill, the same value as in .murmur/loadgraph.json.

## 7. Final summary

After finishing, give me a short summary:
- murmur_version, and whether this run was an install or an update
- Dev/prod separation found (or the warning), and whether deployed dev environments are confirmed to run in dev mode
- Test rules found, and any that are not dev-only
- How many nodes and edges
- Files added and changed (and, on an update, what changed since the last version)
- Which external services are covered by test rules vs. skips
- Rate limits found, and whether the relax switch was added
- Role personas and pool groups, how each group gets its accounts, and whether grant-role was added
- The boards that pass values between users, and the nodes that use unlocks and locks
- Any endpoints you skipped and why
- Any dependencies you weren't sure about
- The assumptions behind your probabilities