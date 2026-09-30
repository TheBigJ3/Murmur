<!-- murmur:map-prompt -->
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

Create a .murmur/ folder in the root of the project if it doesn't exist. Write .murmur/loadgraph.json using exactly this structure:

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
  "nodes": {
    "<node_name>": {
      "request": "METHOD /path/{placeholder}",
      "body": { ... },
      "extract": {
        "var": { "path": "$.jsonpath[*].field", "pick": "random", "required": true }
      },
      "sets": ["flag"],
      "clears": ["flag"],                 // or ["@session"] to clear all session_flags
      "requires": ["flag"],
      "requires_not": ["flag"],
      "skip": {
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
    "<name>": { "share": 0.0, "multipliers": { "<tag>": 1.0 } }
  }
}
```

### Test rule pattern syntax for "generate"
- "#" = one random digit
- "{rand}" = a random unique lowercase string
- Everything else is literal

Example: { "test_email": { "generate": "{rand}@test.com" }, "test_phone": { "generate": "+1000#######" }, "test_otp": { "value": "000000" } }

Reference rules in nodes as {test:<rule_name>}, e.g. {test:test_email}, {test:test_otp}.

### Rules
- Every {placeholder} must come from an earlier "extract", a test rule ({test:...}), the test account pool ({pool:...}), or a generated input ({gen:...}, e.g. {gen:query}, {gen:first_name}). Document the expected format of any {gen:...} that has one (e.g. dates).
- Use "requires" for anything that needs login, an existing ID, or prior state (e.g. non-empty cart).
- Use "requires_not" where a step only makes sense without a flag (e.g. login and signup require_not authed).
- Use "clears" whenever a step ends a state (e.g. releasing a hold clears the hold flag, a finished job clears its pending flag where the graph can tell).
- List every flag that belongs to a logged-in session in "session_flags". Logout nodes use "clears": ["@session"] instead of listing flags individually.
- Each node's outgoing "p" values must sum to 1, and every node needs an edge to "exit".
- Tags: use a small set that fits this app (e.g. browse, search, purchase, account, transfer). Exit edges use the tag "exit".
- Probabilities are your best guess at typical user behavior. Reads more common than writes; each funnel step loses some users.
- Create 3-4 personas that represent realistic user types for this specific app. Persona shares must sum to 1. Multipliers may include "exit" to make a persona leave sooner or later.
- Skip health checks, internal, and admin-only endpoints unless they're part of a real user journey.

### Extraction
- When extracting from a list, default to "pick": "random" so users spread across items. Use "pick": "first" only where users realistically converge (e.g. a hot event drop) or the response has a single item.
- Mark an extract "required": true when the node's "sets" flags only make sense if a value was found (e.g. has_ticket requires an entryId). If a required extract finds nothing, the runner sets no flags from that node.
- Actions on a specific item must use the ID from the step that created or selected it, not a fresh list lookup.

### Test data
- New accounts (signup) use test rules, e.g. {test:test_email} and {test:test_phone}, so the real signup flow runs without sending real email or SMS.
- Logins use {pool:user.email}, {pool:user.password}, {pool:user.phone} from a pre-seeded test account pool. Pool accounts must themselves use test-rule emails and phones. Each pool account is leased to one virtual user at a time.
- OTP codes use the test rule value, e.g. {test:test_otp}. Never use {gen:...} for OTP codes, credentials, phones that receive SMS, or email recipients.
- For recipients of transfers, invites, or shares, use {pool:other_user.email} so another simulated user receives it.
- Holds, reservations, or anything that locks inventory must have a path that releases or completes it.

### External services (priority order)
1. If an existing test rule bypasses the service, use it in the node. No skip needed.
2. If no test rule exists, add a "skip" block to that node. The skip calls a private Murmur endpoint that produces the same result the real flow would (e.g. creates the order and queues the order job) without calling the outside service.

- Check every step that depends on a third-party service: payments like Stripe, SMS/OTP, email verification, captchas, OAuth providers, notification transports, etc.
- No step may send a real SMS, email, or payment request.
- Name skip endpoints /internal/murmur/<action>. Authenticate them with the header X-Murmur-Key: {env:MURMUR_KEY}.
- The skip's "extract", "sets", and "clears" must match what the real step would produce, so later nodes still work.

## 4. Implement the Murmur skip endpoints

Implement only the skips not covered by test rules.

Before writing any code, study how this codebase is built:
- Folder structure, routing, controllers/handlers, middleware, validation, error handling, response format, logging, and naming conventions.
- Any lint rules, style configs, or contributor docs (e.g. .eslintrc, CONTRIBUTING.md, CLAUDE.md).

The Murmur code must look like it was written by the same team and pass the existing lint and type checks.

### Implementation rules
- Reuse the existing code that runs after the external service succeeds (e.g. the Stripe webhook handler's order-completion logic). Call those functions rather than duplicating their logic, so Murmur tests the real pipeline. If some duplication is unavoidable, keep it minimal and list it in .murmur/README.md so it can be kept in sync.
- Do not modify the real payment, OTP, or auth flows, or the existing test rules. Only add new code.
- Put all Murmur code in one clearly named module or folder (e.g. murmur/ or internal/murmur/) so it's easy to find and remove.
- Mark every record Murmur creates as test data if the schema allows it. If there's no such field, log the created IDs so they can be cleaned up.
- Add tests for the key check and each skip, following the project's existing test setup.

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
- Find every rate limiter that applies to the graph's endpoints (per-IP, per-user, per-route). Record the bucket sizes and refill rates, and estimate the sustained request rate a single load-generator IP can reach through each. Do not bypass or change them; report them.

## 5. Write .murmur/README.md

.murmur/README.md is the human-readable guide to Murmur in this project. Include:
- How dev/prod separation works in this project and exactly how Murmur uses it (or the warning above if none exists), including whether deployed dev environments are confirmed to run in dev mode.
- Every test rule found: what it bypasses, where it's implemented, and whether it's dev-only.
- For each skip: which real step it replaces and why, the endpoint path, the request body, the response, which existing functions it calls, any duplicated logic, and which files you added or changed.
- What the test account pool needs (how many accounts, which fields, which test rules they rely on, any required state such as existing orders).
- Rate limits that affect the graph and what they mean for swarm size.
- Anything the runner must handle that the JSON can't express (e.g. tokens returned in headers, rotating cookies, placeholders used as object keys or inside JSONPath, time windows).
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
- Rate limits that will throttle a swarm from a single IP
- Any endpoints you skipped and why
- Any dependencies you weren't sure about
- The assumptions behind your probabilities