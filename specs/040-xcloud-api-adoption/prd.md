# Product Requirements Draft: xCloud API Adoption

**Status**: Discovery

**Created**: 2026-08-05

**Last Refined**: 2026-08-05

**Input**: "we also need xcloud api suppots / check requirement" — expanded by the
user to all four candidate scopes: deploy to an xCloud site, pull an xCloud site
down locally, use xCloud as a hosting/ingress provider, and run xCloud as a
runtime adapter.

**Drafting Model**: `claude-opus-5[1m]` — the preferred `gpt-5.6-terra` Medium
configuration was not the active root model and this skill cannot switch it

**Final Validation**: `NOT RUN` — `gpt-5.6-sol` High was not available in this
session and no fallback reviewer was substituted for it

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox operates local WordPress stacks and generic remotes it provisions over
SSH. xCloud is the hosting platform this team's sites actually run on, and it now
publishes a self-service REST API. Today there is no path between the two: a
developer who fixes something in a Sandbox instance has no supported way to put
it on the xCloud site it belongs to, and no supported way to reproduce an xCloud
site locally. That work is done by hand through the xCloud dashboard, which
leaves no evidence, is not repeatable, and cannot be driven from an agent session.

Why now: the xCloud Public API reached a documented, versioned state
(OpenAPI 3.0.3, committed at `docs/external/xcloud-public-api.v1.yaml`), so the
integration can be built against a stable contract rather than a moving target.

## Users and Desired Outcomes

- **Plugin developer**: Reproduce a bug that only appears on a hosted site by
  pulling that site into a local Sandbox instance, without hand-copying files or
  databases.
- **Plugin developer**: Publish verified local work to the hosted site it belongs
  to, through one command that reports what it changed.
- **Release manager**: See the real state of a hosted site — version, health,
  SSL, backups, vulnerabilities — without opening a dashboard.
- **Agent session**: Operate a hosted site through the same runtime vocabulary as
  every other Sandbox instance, and receive a truthful refusal when an operation
  is unavailable on that runtime rather than a silent no-op.

## Goals

- A developer registers an xCloud account with Sandbox once, and afterwards
  addresses their xCloud sites by project rather than by UUID.
- A developer can push a local project's application code to its xCloud site and
  observe the deployment result.
- A developer can create a local Sandbox instance from an existing xCloud site
  that is faithful enough to reproduce a bug.
- A developer can read hosted-site state — health, domains, SSL, backups,
  vulnerabilities, WordPress inventory — through Sandbox.
- Every operation Sandbox cannot perform on an xCloud site reports a typed,
  actionable limitation instead of appearing to succeed.
- No xCloud credential is ever written to a committed file or printed to output.

## Non-Goals

- Provisioning or destroying xCloud **servers**. Site lifecycle only.
- Becoming a general xCloud administration console. Endpoints that serve no
  Sandbox workflow — fail2ban, sudo users, supervisor processes, firewall rules,
  PHP-version management — are out of scope.
- Migrating an existing instance between runtimes. The repository already refuses
  mode/adapter changes once an instance contains data, and export/recreate/import
  remains a separate workflow.
- Replacing the xCloud dashboard for billing, teams, or server purchasing.
- Two-way continuous synchronisation. Each direction is an explicit, operator-run
  action.

## Product Scenarios

### Scenario 1 — Connect an xCloud account

- **Starting state**: A developer has an xCloud account and a Sandbox project.
- **User action**: Registers an xCloud API token with Sandbox and links the
  project to one of their xCloud sites.
- **Expected outcome**: Sandbox confirms the token's identity and the scopes it
  actually carries, records the link, and stores the token where secrets already
  live. The token value is never echoed back.

### Scenario 2 — Read hosted-site state

- **Starting state**: A project linked to an xCloud site.
- **User action**: Asks Sandbox for the site's status.
- **Expected outcome**: Sandbox reports health, primary domain, SSL state, PHP
  version, backup recency, outstanding WordPress updates and vulnerability counts,
  and states plainly that this is a remote hosted site, not a local instance.

### Scenario 3 — Publish local work to the hosted site

- **Starting state**: A linked project with verified local changes.
- **User action**: Asks Sandbox to deploy the project to its xCloud site.
- **Expected outcome**: Sandbox transfers the application code, reports what it
  transferred, and reports the deployment outcome including failure detail. It
  does not touch the site's database or uploads unless explicitly asked.

### Scenario 4 — Reproduce a hosted bug locally

- **Starting state**: A linked project and a hosted site exhibiting a bug.
- **User action**: Asks Sandbox to create a local instance from the xCloud site.
- **Expected outcome**: Sandbox produces a local instance containing the site's
  code, database and uploads, reports exactly what it copied and what it did not,
  and rewrites the local site URL so the copy is usable offline.

### Scenario 5 — An operation the runtime cannot perform

- **Starting state**: A project whose runtime is an xCloud site.
- **User action**: Runs a command requiring command execution inside the site.
- **Expected outcome**: Sandbox refuses with a typed limitation naming the
  capability and a safe alternative. It never silently runs the command against a
  different instance, and never partially applies it.

### Scenario 6 — Insufficient token scope

- **Starting state**: A registered token carrying only read scopes.
- **User action**: Attempts a mutating operation.
- **Expected outcome**: Sandbox refuses before any mutation, naming the missing
  scope. A scope failure discovered mid-operation leaves the site unchanged and is
  reported as such.

### Scenario 7 — Rate limit reached

- **Starting state**: A session issuing many hosted-site reads.
- **User action**: Continues operating.
- **Expected outcome**: Sandbox stays within the published limit, reports when it
  is waiting rather than appearing to hang, and never converts a rate-limit
  response into a false "site unavailable" or a destructive retry.

### Scenario 8 — The link is stale

- **Starting state**: A project linked to a site since deleted, or moved to a team
  the token cannot see.
- **User action**: Runs any hosted operation.
- **Expected outcome**: Sandbox distinguishes "not found" from "not permitted"
  from "unreachable", retains the local link for inspection, and does not delete
  local state on the strength of a remote absence.

## Proposed Product Behavior

- **Two transports, stated openly.** The xCloud API is a control plane: it can
  create a site, read its state, trigger backups and deployments, and manage SSL.
  It cannot execute commands, move files, or touch a database. Any workflow
  needing those rides SSH, whose connection details the API itself supplies. The
  product presents this as one coherent feature while reporting truthfully, per
  operation, which transport was used and what was therefore possible.
- **Read-only by default.** Registering an account and reading site state require
  only read scopes. Every mutating operation is explicit.
- **Refusal over inference.** When a required scope, SSH route, or capability is
  absent, Sandbox refuses with a typed reason. It never falls back to a different
  site, a different transport, or a partial application.
- **Local state is never destroyed on remote evidence alone.** A missing or
  inaccessible remote site retains the local link and instance.
- **Secrets stay out of the repository.** The token lives where Sandbox already
  keeps credentials, never in `sandbox.config.json`, committed overrides, or
  runtime ownership state, and is never printed.
- **Adoption is gated by evidence.** A new runtime adapter enters the runtime
  manifest as unproven and non-adoptable until live evidence exists, exactly as
  the managed-native adapter does today.

## Constraints and Dependencies

- **Committed contract**: `docs/external/xcloud-public-api.v1.yaml` — OpenAPI
  3.0.3, base `https://app.xcloud.host/api/v1`, 98 endpoints, Sanctum bearer
  token. Work reads this file rather than probing the live API for schema.
- **Capability gaps, verified against that schema**:
  - No command-execution, shell or WP-CLI endpoint.
  - No file upload, download, import, export or restore endpoint.
  - No database endpoints of any kind.
  - Domains are readable but not writable (`GET` only).
  - Backups can be triggered and listed, but not downloaded or restored.
  - Deployment is Git-based only (`POST /servers/{uuid}/sites/git`,
    `POST /sites/{uuid}/git/deploy`).
  - `GET /sites/{uuid}/ssh` supplies SSH/SFTP connection configuration.
- **Scopes**: `read:servers`, `write:servers`, `read:sites`, `write:sites`, `*`.
  Site creation requires `write:servers`, not `write:sites`.
- **Rate limit**: 60 authenticated requests per minute, with limit headers on
  every response. Status polling must live within this.
- **Response envelope**: `{success, message, data}`. `202 Accepted` indicates an
  asynchronous operation, so completion must be observed rather than assumed.
- **The committed schema does not match the deployed API.** Verified against the
  live API with the user's token on 2026-08-05:
  - Lists: the schema documents `data.data[]` with `data.meta`
    (`current_page`, `last_page`, `per_page`, `total`); the API actually returns
    `data.items[]` with `data.pagination` (`total`, `per_page`, `current_page`,
    `last_page`). Code generated from the committed schema cannot parse a single
    list response.
  - Rate-limit headers are documented as present on every response; no
    `X-RateLimit-*` header was returned on any observed call.
  Treat the committed schema as indicative, and confirm each response shape
  against the live API before relying on it.
- **The server's stack is chosen outside this feature and limits it.** Verified
  live on 2026-08-05 against a provisioned server with `stack: docker_nginx`:
  `POST /servers/{uuid}/sites/wordpress` returns
  `422 {"message": "WordPress is not supported on Docker servers"}`. The schema
  documents no stack constraint on that endpoint at all. Because server
  provisioning is dashboard-only and out of this feature's scope, the feature
  inherits whatever stack the operator picked, and on a Docker stack the primary
  WordPress path is simply unavailable. `POST /servers/{uuid}/sites/git` does
  accept `site_type: wordpress`, but requires a repository and a real domain, so
  it cannot serve a disposable proving site.
- **Blueprints cannot be referenced.** `GET /blueprints` returns three entries
  (Elementor, Gutenberg, WooCommerce) whose `uuid` is `null`, while
  `CreateWordPressSiteRequest.blueprint_uuid` expects a UUID.
- **Integrations are read-only.** `GET /integrations/cloudflare` returns an empty
  list and there is no endpoint to create an integration, so the `cloudflare`
  flag on site creation depends on dashboard-side setup.
- **Repository policy**: credentials must not appear in `sandbox.config.json`,
  runtime ownership state, or committed overrides.
- **Repository policy**: the runtime manifest is the promotion authority; a new
  adapter is `implemented_unproven` / `adoptable: false` until proven.
- **Constitution IV**: live-stack verification is the only proof of done, which
  collides with the standing rule against leaving state on live services (see
  Open Questions).
- **Existing building blocks**: Sandbox already performs SSH-based remote deploy
  and remote job execution (specs 029, 032), and already models ingress providers
  and per-project instance ownership.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Scope breadth | All four candidate scopes are in | User: "we will take all of them" | User, 2026-08-05 |
| API contract source | Published OpenAPI, committed to the repo | User chose public docs; standing rule forbids probing a live API for schema | User, 2026-08-05 |
| Credential availability | The user's own xCloud API token is supplied for this work | User provided a token for Sandbox to use against the API; verified working against `GET /user` | User, 2026-08-05 |
| Schema authority | The committed schema is indicative; live response shapes win | The schema is already provably wrong about list pagination and rate-limit headers | Evidence, 2026-08-05 |
| Server lifecycle | Out of scope; sites only | Server provisioning is billing-bearing and has no Sandbox workflow behind it | Existing policy |
| Credential storage | Existing secret locations only | Repository policy already forbids credentials in project config | Existing policy |
| Adapter promotion | Enters unproven and non-adoptable | The runtime manifest is the promotion authority | Existing policy |

## Open Questions

1. **Which server stack does this feature target?** (BLOCKING) A server now exists
   and is provisioned, so there is something to act on, but it was built with the
   `docker_nginx` stack and that stack refuses WordPress site creation outright.
   Sandbox is a WordPress tool, so on this server the feature's primary path
   cannot run at all. Needed: a decision to rebuild or add a server on a
   WordPress-capable stack, or an explicit narrowing of this feature to the
   Git-deploy path with a real repository and domain. Until one of those, the
   WordPress scopes cannot be proven and Constitution IV keeps the adapter at
   `implemented_unproven`.

2. **Does the xCloud runtime adapter publish a reduced required-capability set,
   or does SSH fill the gap?** (BLOCKING) The API alone cannot serve `exec`,
   `wordpress_cli` or `test`. Either the adapter declares a smaller required set —
   honest, but `sb wp`, `install` and `doctor` then refuse on xCloud projects — or
   SSH backs those capabilities so the full contract is met, at the cost of a
   second transport with its own failure modes and credential handling.

3. **What does "deploy" transfer?** (BLOCKING) The API deploys from Git only.
   Either Sandbox pushes a Git ref and triggers xCloud's own deployment — matching
   the platform and producing its deployment logs, but requiring the project to be
   a Git repository with a remote xCloud can reach — or Sandbox transfers the
   working tree over SFTP, matching how its existing remote deploy behaves but
   bypassing the platform's deployment pipeline.

4. **Does "pull down" include the database and uploads?** A code-only copy is
   cheap and needs no destructive local action; a full copy reproduces far more
   bugs but requires a database dump over SSH and can be large. Which is the
   default, and is the other opt-in?

## Acceptance Outcomes

- A developer with a valid token can link a project to an xCloud site and read
  that site's state, without any credential appearing in a committed file, in
  command output, or in runtime state.
- A token carrying insufficient scope produces a refusal naming the missing scope,
  before any remote mutation occurs.
- A deploy reports what it transferred and the platform's own outcome for that
  deployment, including failure detail when it fails.
- A local instance created from a hosted site serves that site's content locally
  and reports exactly which components were and were not copied.
- Every runtime operation unsupported on an xCloud site returns a typed limitation
  naming the capability, and no such operation partially applies.
- Sustained operation stays within the published rate limit, and a rate-limit
  response is reported as throttling rather than as site unavailability.
- Removing the link leaves the hosted site untouched.
- The xCloud adapter's manifest entry truthfully reports its support tier, and
  claims adoptability only when live evidence exists.

## Risks and Assumptions

- **Risk**: The API prose advertises database management, but no database endpoint
  exists in the path set. Any requirement resting on the prose rather than the
  paths will not survive implementation.
- **Risk**: The published schema is already wrong or silent on four counts, all
  confirmed live — the list envelope, the rate-limit headers, the stack
  restriction on WordPress site creation, and blueprint UUIDs. Its other
  unverified shapes are therefore not trustworthy either, and every response this
  feature depends on needs live confirmation before it is relied upon.
- **Risk**: Capabilities depend on decisions made outside the feature. The server
  stack is chosen in the dashboard at provisioning time and cannot be changed
  through the API, so an operator can put a server into a state where most of
  this feature does not apply, with no API signal until a call is refused.
- **Risk**: The API is labelled Beta. Endpoints may change; the committed schema
  is a snapshot, and drift will be discovered at runtime.
- **Risk**: Two transports double the failure surface. An operation that
  half-succeeds — API accepted, SSH failed — must not leave the hosted site in a
  state the developer cannot see or undo.
- **Risk**: Pulling a production site down brings production data, including
  personal data, onto a developer machine. This needs an explicit position before
  the capability ships.
- **Risk**: 60 requests per minute is low for a chatty status view; a naive
  implementation will throttle itself during ordinary use.
- **Risk**: A live token plus mutating operations means a defect in this feature
  damages a production site, not a disposable sandbox. Blast radius is materially
  larger than for any existing runtime.
- **Risk**: Without sanctioned mutation on a proving account, the feature stays
  permanently unproven and non-adoptable — the position spec 039 spent
  considerable effort escaping.
- **Assumption**: The user's xCloud sites are WordPress sites on xCloud-managed
  servers, so site-scoped endpoints apply.
- **Assumption**: SSH access to those sites is available to the same person who
  holds the API token.
- **Assumption**: The committed schema matches the deployed API at specification
  time.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [ ] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [ ] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
