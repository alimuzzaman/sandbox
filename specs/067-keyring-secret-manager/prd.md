# Product Requirements Draft: Cross-Platform Open-Source Secret Manager

**Status**: Refined

**Created**: 2026-10-13

**Last Refined**: 2026-10-13

**Input**: "Use an open-source project directly for a secret manager that works on Linux and macOS. Proceed with self-hosted Infisical over Tailscale, integrated behind `sb secrets`."

**Drafting Model**: Codex `gpt-5.6-sol`, medium reasoning, no drafting helpers

**Final Validation**: `PASS` — independent `gpt-6.1-sol` high readiness review

**Validated On**: 2026-10-13

**Artifact Owner**: `speckit.prd.refine`

**Next Stage**: `speckit.specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox provides a least-disclosure broker for inspecting and using secrets,
but its supported sources are local files. Long-lived credentials are therefore
duplicated across plaintext machine-local files, and a user moving between Linux
and macOS has no supported shared source of truth. Tailscale makes the machines
privately reachable but does not synchronize or authorize secrets.

Operating-system keyrings improve local storage but do not give Linux and macOS
a shared vault. Building a new synchronized vault would duplicate mature
open-source work and create avoidable cryptographic, identity, audit, recovery,
and lifecycle risk. Sandbox instead needs to consume one existing open-source
manager through its current safety boundary.

Infisical's open-source core supplies centralized projects, environments, paths,
machine identities, built-in project roles, and a self-hosted distribution. The
product need is to integrate a reviewed community deployment as a registered
source behind `sb secrets`, while preserving Sandbox's stricter least-disclosure
behavior and avoiding provider surfaces that can browse, print, or export secret
values.

## Users and Desired Outcomes

- **Developer using Linux and macOS**: Select the same authorized project secret
  from either machine without copying its value into local project files.
- **Developer working across projects**: Use a distinct provider project and
  read-only identity for each Sandbox project so one project's automation cannot
  read another project's secrets.
- **Developer operating through Tailscale**: Reach the self-hosted manager over a
  private network without making its control endpoint public.
- **Automation or MCP caller**: Use only an exact registered secret and fixed use
  profile, never general vault browsing, plaintext reveal, migration, or prompt
  authority.
- **Security reviewer**: Verify that provider values and bootstrap credentials
  remain outside project config, argv, parent environments, routine output,
  Sandbox audit records, and retained job evidence.
- **Operator**: See manager availability, identity scope, authentication expiry,
  and refusal reasons without exposing session credentials or secret contents.

## Goals

- Add Infisical as an explicit registered source behind the existing `sb secrets`
  interface rather than introducing a second secret command surface.
- Support one shared source of truth across authorized Linux and macOS machines.
- Use provider-enforced project-level scope and the built-in read-only role for
  automation identities. Treat environment, path, and key as exact selectors,
  not authorization boundaries in the community-only first release.
- Give each Sandbox project one read-only provider identity and each participating
  controller its own revocable bootstrap credential for that identity. Protect
  that credential in the controller's approved local OS credential store.
- Preserve existing source metadata, offline validation, bounded trusted-child
  use, exact-value redaction, secret-free audit, and MCP authorization semantics.
- Distinguish unavailable endpoint, TLS or private-network failure, missing local
  bootstrap credential, denied scope, expired authentication, missing item,
  ambiguous reference, and provider failure with stable secret-free outcomes.
- Keep existing registered file sources fully supported and make Infisical
  adoption opt-in.
- Make provider and feature/license prerequisites visible without overstating
  what the open-source distribution includes.

## Non-Goals

- Automatically deploying, upgrading, backing up, restoring, or exposing an
  Infisical service in the first integration release.
- Providing disaster recovery for Infisical, its data store, its encryption
  material, or its local bootstrap credentials; these remain operator duties.
- Automatically migrating, deleting, rotating, or revoking existing credentials.
- Reading current secret files to discover migration candidates.
- Synchronizing OS keyrings or copying bootstrap credentials between machines.
- Using the Infisical CLI as an agent-visible general secret browser, exporter,
  or process wrapper.
- Enabling the unproven managed-native Credential Vault or changing its
  `implemented_unproven` and `adoptable=false` status.
- Replacing provider-specific authentication, authorization, or rotation
  workflows.
- Providing plaintext reveal through MCP or an agent-operated long-lived secret
  session.
- Guaranteeing that a trusted child or its descendants cannot disclose a value
  intentionally delivered to that child.
- Claiming that a Sandbox project directory isolates a human from other projects
  when that human holds broader provider or OS-user authority.
- Supporting Windows in the first release.
- Falling back to plaintext files when Infisical, Tailscale, TLS, authentication,
  or the local OS credential store is unavailable.

## Product Scenarios

### Scenario 1 — Use one secret from Linux and macOS

- **Starting state**: Both machines can privately reach the same registered
  self-hosted Infisical endpoint and each has a distinct approved local bootstrap
  credential for the same project-scoped provider identity.
- **User action**: The user runs the project's fixed secret-use profile on each
  machine.
- **Expected outcome**: Both machines resolve the same provider item and deliver
  it only to the bounded trusted child. No project file contains the value, and
  no secret value crosses between the two machines directly.

### Scenario 2 — Enforce project scope and exact selection

- **Starting state**: Project A and Project B use separate provider projects,
  Project A's read-only machine identity has no permission for Project B, and
  Project A registers one exact environment/path/key selector.
- **User action**: Project A requests its own secret and then attempts to select
  Project B's secret or a different environment, path, or key.
- **Expected outcome**: Its own request succeeds; the cross-project request is
  refused by provider authorization. Selector changes outside the registered
  source are refused by Sandbox without search or substitution. Sandbox does not
  claim that environment or path selection is provider-enforced isolation.

### Scenario 3 — Use through MCP or unattended automation

- **Starting state**: A source and fixed use profile explicitly authorize one
  item for non-interactive use.
- **User action**: An MCP client requests the profile.
- **Expected outcome**: Sandbox fetches only the exact registered item, may
  return only its approved secret-free metadata, supplies its value only to the
  fixed bounded child, and returns secret-free evidence. Provider collection
  listing, wildcard lookup, search, unregistered-item access, reveal, write,
  delete, migration, and caller-selected commands remain refused even if the
  provider identity could perform them.

### Scenario 4 — Local bootstrap store is unavailable

- **Starting state**: macOS Keychain or Linux Secret Service is locked, absent,
  unsupported, or would require interaction unavailable to the caller.
- **User action**: Sandbox attempts an Infisical-backed operation.
- **Expected outcome**: The request fails closed within its bound with a stable
  remedy. Sandbox does not read a fallback file, start an unreviewed keyring
  daemon, request a password in argv, or use an ordinary environment variable.

### Scenario 5 — Private manager endpoint is unreachable

- **Starting state**: Tailscale, name resolution, TLS identity, or the Infisical
  service is unavailable.
- **User action**: Sandbox attempts metadata inspection or bounded use.
- **Expected outcome**: The request returns a distinct availability or trust
  failure without retrying an uncertain operation, switching to a public
  endpoint, or exposing endpoint credentials.

### Scenario 6 — Provider identity expires or loses access

- **Starting state**: A bounded provider session expires, a bootstrap credential
  is revoked, or project permission is removed.
- **User action**: A new operation begins.
- **Expected outcome**: Sandbox performs only the approved authentication flow,
  obtains access within the request bound when still authorized, or returns a
  stable denial. It does not reuse a session past its validated lifetime.

### Scenario 7 — Item reference is missing, moved, or ambiguous

- **Starting state**: The configured project/environment/path/key no longer
  identifies exactly one eligible item.
- **User action**: Sandbox resolves it for inspection or use.
- **Expected outcome**: Missing and ambiguous references are refused. No broader
  search result or neighbouring secret is substituted.

### Scenario 8 — Existing file source remains in use

- **Starting state**: A project continues using a registered dotenv, structured,
  PEM, opaque, or binary source.
- **User action**: It performs an existing supported operation.
- **Expected outcome**: Existing behavior and safety boundaries remain unchanged;
  Infisical adoption is explicit and reversible at the configuration level.

### Scenario 9 — A second controller lacks bootstrap authority

- **Starting state**: A Tailscale-connected machine can reach Infisical but has
  no locally registered bootstrap credential for the requested project identity.
- **User action**: It attempts to use the project source.
- **Expected outcome**: Reachability alone grants nothing. The request is refused
  without copying credential material from another controller.

### Scenario 10 — A same-user caller has broader provider authority

- **Starting state**: The OS user or operator can access Infisical separately
  with authority broader than the identity registered for the Sandbox project,
  or can edit the project's authoritative source registration.
- **User action**: The same user changes the registration or bypasses `sb secrets`
  and accesses the provider directly.
- **Expected outcome**: Sandbox makes no false isolation claim: an authorized
  configuration change becomes a new registration, and direct provider access
  is outside the broker boundary. Documentation distinguishes this from MCP and
  unattended callers, which cannot alter registrations or supply selectors.

### Scenario 11 — Provider identity capacity is exhausted

- **Starting state**: The selected community deployment cannot accept another
  distinct project identity because of its configured or licensed capacity.
- **User action**: The operator attempts to register another Sandbox project.
- **Expected outcome**: Registration fails with a stable, secret-free capacity
  outcome before any secret is fetched. Sandbox does not share an identity across
  otherwise separate projects, silently require a paid tier, or weaken scope.

### Scenario 12 — A controller credential is revoked

- **Starting state**: Linux and macOS use distinct bootstrap credentials for the
  same project identity, and the Linux credential is revoked.
- **User action**: Both controllers attempt the same registered use profile.
- **Expected outcome**: Linux receives a stable authorization denial while macOS
  remains authorized. No credential is copied between controllers, and revoking
  one controller does not require replacing the project identity.

## Proposed Product Behavior

- Infisical is an explicit registered broker source. Registration identifies one
  provider project and one exact item selector using non-secret configuration;
  credential material remains outside project configuration.
- The self-hosted endpoint is operator-provided in the first release and is
  required to use an authenticated private/TLS transport. Endpoint registration
  does not deploy or mutate the service.
- Sandbox uses a reviewed provider integration boundary that cannot expose
  general provider output, bulk export, shell interpolation, collection listing,
  wildcard search, or raw provider responses to agents.
- Each Sandbox project uses a distinct project identity with Infisical's built-in
  Viewer role. This provider-enforced boundary covers the whole Infisical
  project; Sandbox enforces the narrower environment/path/key selection.
- Each controller receives a distinct, independently revocable bootstrap
  credential for that project identity and protects it in its approved local OS
  credential store. Controllers never copy or synchronize that credential.
  Provider session credentials are bounded, are not treated as project
  configuration, and are not persisted in Sandbox policy state.
- Human CLI access and automation access remain distinct. MCP and unattended
  callers require existing explicit source-mode and fixed use-profile authority
  and cannot create or alter source registrations. A same-user human who can edit
  authoritative project configuration remains inside that configuration's trust
  boundary; Sandbox does not claim to resist that user.
- Routine status may report backend class, reachability state, authorization
  scope identifiers already approved as non-secret, session lifetime state, and
  supported operations. It never reports secret values, bootstrap values, access
  credentials, provider response bodies, or sensitive OS-store object identifiers.
- Provider write, delete, rotation, identity administration, and permission
  changes are separate consequential capabilities and are excluded from the
  first read/use integration.
- No migration runs when the source is registered. A future migration requires
  its own approval, revision-bound plan, resumable evidence, and explicit warning
  that deleting a plaintext source does not erase backups or prior captures.

## Constraints and Dependencies

- Existing secret-inspection policy remains authoritative: agents do not reveal
  values, read source files, enumerate parent environments, or run operator
  session mode.
- Sandbox audit intent is durable before secret processing and outcomes remain
  secret-free, even when provider authentication or transport fails.
- The direct-child boundary, minimal environment, bounded output, stable refusal
  codes, and exact-value redaction remain in force.
- The provider bootstrap credential remains sensitive and requires protection,
  least privilege, rotation, and revocation. The detailed authentication method,
  session protocol, client library, and persistence schema are specification and
  design decisions, not requirements fixed by this PRD.
- Infisical's built-in Viewer role is the provider-side project boundary in the
  community-only first release. Environment, path, and key restrictions are
  Sandbox selection policy; a broad provider identity cannot be made narrow by
  project configuration alone.
- macOS Keychain and Linux Secret Service have different prompting and session
  models. Sandbox must not promise identical prompt frequency or headless
  behavior.
- Linux support in the first release requires an already available approved user
  Secret Service. Sandbox does not own a D-Bus session, keyring-daemon lifecycle,
  or headless unlock password.
- Provider lookup and audit metadata are non-secret by design. Values must never
  be embedded in project names, environments, paths, labels, or query attributes.
- The first release is limited to Infisical's MIT-licensed core and built-in
  project roles documented as available in free self-hosted deployments. It must
  not depend on enterprise code, custom roles, additional privileges, or paid
  environment/folder access controls.
- Hosted-plan marketing limits are not assumed to describe the selected community
  deployment. Registration must rely on the deployment's proven capacity and
  fail closed when a distinct project identity or per-controller credential is
  unavailable. Identity capacity is counted per isolated Sandbox project;
  credential capacity is counted per participating controller.
- Tailscale supplies private reachability, not Infisical identity, Sandbox
  authorization, TLS identity, backup, or availability.
- Existing file sources remain backward compatible until separately proven
  parity and explicit removal approval exist.
- No service deployment, remote mutation, credential migration, rotation, or
  deletion is authorized by this PRD phase.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Shared manager | Community self-hosted Infisical, excluding enterprise-only code and features | The MIT-licensed core is cross-platform and provides projects, machine identities, and built-in roles without requiring Sandbox to build a vault | User, 2026-10-13; source/license review, 2026-10-13 |
| Network reachability | Private Tailscale-reached endpoint with authenticated transport | Uses the existing two-machine private network without creating public control exposure | User-approved direction, 2026-10-13 |
| Product interface | Integrate behind `sb secrets` | Preserves least disclosure, existing audit, MCP authorization, and bounded use | Existing Sandbox policy and user approval |
| Provider access surface | A reviewed exact-item integration boundary; no general Infisical CLI exposure | General browsing and export conflict with least-disclosure requirements; protocol and library selection belong to later design | Security constraint accepted with the selected direction |
| Bootstrap protection | Store each controller's distinct bootstrap credential in its approved OS credential store | Keeps bootstrap material out of plaintext, permits per-controller revocation, and supports both desktop platforms | User-approved architecture, 2026-10-13 |
| Headless Linux | Require an already available approved Secret Service; otherwise fail closed | Avoids inventing unsafe daemon/unlock lifecycle and ambient master-password handling | Recommended architecture approved by user, 2026-10-13 |
| Project isolation | One Infisical project identity with the built-in Viewer role per Sandbox project; environment/path/key limits remain exact Sandbox selectors | Built-in project roles are documented for free self-hosted deployments; finer provider RBAC is paid, and directory config cannot constrain a human with broader authority | User-approved open-source constraint; source review, 2026-10-13 |
| Identity capacity | Support only as many isolated Sandbox projects as the selected community deployment demonstrably permits; never share identities to evade a limit | Hosted Free pricing currently advertises five identities, while the reviewed community self-hosted default has no enforced identity limit; deployed capability, not marketing inference, is authoritative | Source and entitlement review, 2026-10-13 |
| Controller credentials | One independently revocable bootstrap credential per participating controller for the project's single provider identity | Preserves one project authorization boundary while allowing either controller to be removed without copying credentials or replacing the other controller's access | Cross-platform least-privilege requirement, clarified 2026-10-13 |
| Registration trust | Project registration is authoritative when changed by a same-user human; MCP and unattended callers cannot create, mutate, or override it | The broker can constrain its callers but cannot isolate an OS user from configuration and provider authority that user already controls | Existing Sandbox trust boundary, clarified 2026-10-13 |
| Service lifecycle | Operator-provided endpoint in the first integration release | Deployment, upgrades, backup, and recovery require a separate operational capability and explicit authorization | Handoff safety limit and current user approval scope |
| Automatic migration | Excluded | Migration, deletion, and rotation require separate reviewed authority | User-approved handoff |
| Existing file sources | Remain supported and opt-out is reversible at configuration level | Constitution requires proven parity before removal | Sandbox Constitution VI |

## Open Questions

- None blocking specification. Service provisioning, managed headless unlock,
  migration, provider writes, rotation, and disaster recovery are intentionally
  separate future product decisions. The detailed provider protocol, client
  library, authentication method, and config schema remain design choices that
  must satisfy these product outcomes.

## Acceptance Outcomes

- One synthetic Infisical item authorized to a test project can be selected from
  both supported macOS and Linux user sessions and delivered to one bounded
  trusted child without appearing in project files, argv, parent environment,
  routine stdout/stderr, Sandbox audit records, or retained job evidence.
- A provider identity scoped to Project A cannot resolve a Project B item even
  when Project A configuration is deliberately altered to name Project B.
- A caller-supplied request that overrides the authoritative registration's
  environment, path, or key is refused without wildcard lookup, search,
  adjacent-item substitution, or collection listing. Tests and documentation do
  not describe those selectors as provider-enforced authorization boundaries.
- MCP cannot enumerate or use an Infisical source unless the project explicitly
  authorizes the exact source, item, mode, and fixed use profile. Only approved
  secret-free metadata for that exact item may be returned; provider collection
  listing, wildcard lookup, search, unregistered-item access, reveal, bulk
  listing, write, delete, identity administration, migration, and caller-selected
  commands remain unavailable even when source credentials are broader.
- MCP and unattended operations cannot create, change, or override a source
  registration. A same-user human configuration edit is treated as an authorized
  registration change and is documented as outside the broker's isolation claim.
- The bootstrap credential and provider session credential never appear in
  project config, command arguments, ordinary environment variables, Sandbox
  policy state, routine output, logs, audit records, or retained job evidence.
- An expired provider session is not reused. Reauthentication occurs only within
  the bounded approved flow and only when the local bootstrap store and provider
  authorization remain valid.
- Missing or locked OS credential store, unavailable user session, revoked
  bootstrap credential, denied provider scope, unreachable Tailscale path, TLS
  failure, unavailable service, missing item, ambiguous reference, malformed
  provider response, timeout, and response overflow each produce stable,
  secret-free outcomes with no plaintext fallback.
- A Tailscale-connected machine without a local bootstrap credential cannot access
  a provider item merely because it can reach the endpoint.
- Linux and macOS can use distinct bootstrap credentials for the same project
  identity. Revoking either credential denies that controller without denying the
  other or requiring credential copying between machines.
- Capability/status inspection does not trigger a secret-value fetch and reports
  no provider response bodies or sensitive backend object identifiers.
- Existing file-source conformance scenarios remain unchanged and pass after the
  Infisical source is enabled.
- Enabling or registering the source performs no Infisical deployment, public
  exposure, migration, deletion, rotation, provider write, or daemon installation.
- Documentation distinguishes provider-enforced automation scope from same-user
  human authority, distinguishes project configuration from provider policy, and
  never claims that project configuration alone is isolation.
- A conformance check against the selected community self-hosted distribution
  proves that project identities and the built-in Viewer role work without
  enterprise code or a paid feature. If they do not, the source is reported as
  unsupported rather than silently requiring a paid tier.
- Registration of each additional Sandbox project proves that a distinct
  project identity is available. Capacity exhaustion produces a stable,
  secret-free refusal before value retrieval and never causes identity sharing
  across isolated projects.
- Adding a controller proves that another independently revocable bootstrap
  credential is available for the existing project identity. Credential-capacity
  exhaustion fails closed and never causes controllers to share a credential.

## Risks and Assumptions

- **Risk**: The Infisical bootstrap credential becomes a high-value credential;
  excessive provider scope would expose multiple projects.
- **Risk**: A compromised self-hosted service or administrator can access or
  alter centralized secrets despite client-side least-disclosure behavior.
- **Risk**: The open-source and premium feature boundary can change. The first
  release therefore depends only on built-in project-level Viewer access and must
  fail its provider conformance check if that boundary changes.
- **Risk**: macOS may authorize the Python executable rather than the Sandbox
  entrypoint, and Linux Secret Service authority is generally user-session-wide.
- **Risk**: A trusted child can print, transform, persist, or forward a delivered
  credential despite broker redaction.
- **Risk**: Private Tailscale reachability may be mistaken for authentication,
  service availability, backup, or disaster recovery.
- **Risk**: Centralization increases blast radius and creates an availability
  dependency compared with independent local files.
- **Assumption**: The operator supplies and maintains a supported self-hosted
  Infisical endpoint and independently protects its encryption, database,
  backups, and administrator access.
- **Assumption**: Each participating desktop has an approved local OS credential
  store available to the user session.
- **Assumption**: Project identities and the built-in project-level Viewer role
  remain available in the selected community self-hosted distribution.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or source-code changes are included.
- [x] The latest independent readiness validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
