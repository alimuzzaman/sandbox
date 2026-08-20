# PRD 02 — Herd-equivalent polyglot development stacks

Date: 2026-08-11 · Status: Product brief for later Spec-Kit conversion; **NOT READY** pending §12 decisions · Owner surfaces: generic project initialization, parity inspection, related-project lifecycle, registered application environments, generic Compose health/diagnostics

Sources: `.ai/research/2026-08-11-herd-sandbox-parity/` · official Laravel Herd, Laravel Sail, Docker Compose, Next.js, Node Docker, pnpm, PHP image, and MySQL image documentation accessed 2026-08-11 · local read-only probes of Sandbox and the representative Laravel/Next checkouts · generic project spec 021 · native runtime spec 039 · safe secret inspection spec 041

> Standalone brief. “Herd-equivalent” means an observable, evidence-backed
> development contract. It does not mean that a Linux container becomes Herd,
> that shared-host native execution is isolated, or that a compatible version
> may be reported as exact.

---

## 1. Problem

Sandbox already supports explicitly configured PHP, Node, Laravel, Astro, and
similar repositories through one generic Compose adapter. The entry point looks
more complete than the product behavior: `sb init --type laravel` and
`--type node` are accepted labels, yet both require the repository to already
own a Compose file. Only Astro receives generated project configuration.

This gap surfaced while trying to reproduce a real two-repository development
setup:

- Laravel 12 on Herd PHP 8.4.23, served on port 8000;
- MySQL 8.0.27 managed independently by DBngin;
- Next.js 16.2.12 on NVM Node 22.18.0, pnpm-pinned to 11.5.2, served on 3000.

The setup is called “Herd” conversationally but is actually three independently
managed host components. A developer asking Sandbox to reproduce it cannot tell:

- which facts match exactly, merely satisfy a constraint, differ, or were not
  checked;
- which configuration Sandbox inferred and whether running it will execute
  untrusted repository code;
- how separately owned frontend and backend instances find each other without
  exposing the database;
- how the applications receive their registered `.env` configuration without
  an agent opening the files or every service receiving every secret;
- whether requested ports are safe to claim;
- whether a healthy public service also means its database and server-side
  dependencies are ready;
- whether Docker Desktop edit/reload performance is acceptable compared with
  the prior host-native workflow.

Without an explicit product contract, “same as Herd” becomes an untestable claim
and convenience changes risk weakening current Compose-default, registry,
secret, and native-adoption boundaries.

## 2. Research conclusions

### 2.1 Herd and Compose are different environments

Herd is a native macOS stack with its own PHP binaries, nginx, dnsmasq, Node
tooling, `.test` routing, curated extensions, and optional versioned services.
Sandbox Compose runs Linux images with Sandbox-owned loopback/Caddy routing.
PHP and Node versions may match while OS, libc, extensions, INI state, routing,
filesystem watching, signal behavior, and service ownership remain different.

The product must report these differences rather than erase them under a single
boolean.

### 2.2 Compose is still the correct supported default

Laravel documents Sail/Compose as its supported container development model,
including PHP 8.4, Node 22, persistent MySQL, service-name discovery, and test
execution. Sandbox already has the required generic lifecycle and is marked
adoptable. Its Herd adapter is WordPress-only, trusted-shared-host,
`implemented_unproven`, and `adoptable=false`.

This phase therefore builds on the generic Compose runtime. It does not use a
framework-specific runtime adapter and does not silently select Herd.

### 2.3 Exact database version and native performance conflict

The exact PHP 8.4.23 and Node 22.18.0 images support ARM64. The exact MySQL
8.0.27 image is amd64-only. On the representative Apple Silicon host, selecting
MySQL 8.0.27 requires emulation; selecting a supported native-ARM image changes
the database version. Both are valid experiments, but neither may be chosen or
called equivalent without an owner decision and measured evidence.

### 2.4 Docker-compatible Next.js is not automatically native-fast

Next.js supports Docker but recommends native local development on macOS and
Windows for better development performance. The frontend outcome must therefore
include measured edit/reload behavior. “It boots” is insufficient proof for a
workflow intended to replace NVM + host-native Turbopack.

## 3. Desired outcomes

1. A developer can explicitly ask Sandbox to prepare a Laravel or Node project,
   review every proposed runtime fact, and start it without hand-authoring a
   Compose model for conventional cases.
2. Sandbox produces an honest parity report that separates exact matches,
   compatible values, mismatches, unavailable facts, and unverified facts.
3. Related frontend and backend projects keep independent project/instance
   identities while gaining stable, owned connectivity and ordered readiness.
4. Each application receives only its declared configuration and secrets from
   registered sources without values entering chat, argv, logs, the registry,
   committed configuration, or research artifacts.
5. A local MySQL dependency is persistent, health-gated, and close enough to
   the selected reference contract to eliminate avoidable remote-database
   latency without overstating version equivalence.
6. A developer can compare cold start, warm edit/reload, backend latency, and
   representative tests against a captured host-native baseline before adopting
   the Sandbox environment.

## 4. Users and jobs

| User | Job |
| --- | --- |
| Laravel developer | “Give this checkout the right PHP/extensions and a local database, then tell me exactly where it differs from my current environment.” |
| Next.js developer | “Use this repository's Node and package-manager pins, preserve fast editing, and make server-side requests reach the backend.” |
| Full-stack developer | “Start, inspect, stop, and recover both repositories as one declared development relation without losing either project's ownership.” |
| Coding agent | “Inspect and operate the environment through bounded CLI/MCP capabilities without opening `.env` or guessing executable commands.” |
| Sandbox maintainer | “Add presets and relations without a Laravel runtime silo, implicit global instance, or adoption shortcut around native proof gates.” |

## 5. Product scope

### 5.1 Parity baseline and report

The user can capture a non-secret reference baseline from an explicitly selected
environment and compare it with a proposed or running Sandbox environment.

The report treats each fact independently and uses these states:

| State | Meaning |
| --- | --- |
| `matched` | Exact observed value or content identity is the same. |
| `compatible` | A declared constraint is satisfied, but the observed value differs. |
| `mismatched` | A declared or captured requirement is violated. |
| `unavailable` | The target cannot provide the requested capability on this platform. |
| `unverified` | Safe evidence was not available or collection was not authorized. |

Minimum parity dimensions are runtime and patch version, OS/architecture/libc,
PHP extensions and relevant INI facts, Node and package-manager version, database
engine/version and selected compatibility facts, application command, internal
and requested host ports, source/edit behavior, environment-source eligibility,
dependency and public health, service discovery, and URL/routing provider.

The overall summary may say “adoptable,” “compatible with differences,” or
“not ready.” It may say “exact” only when every required dimension is `matched`
and no required fact is unverified.

### 5.2 Guided Laravel and Node proposals

An explicit initializer inspects only inert, project-local metadata such as
Composer/package manifests, lockfiles, runtime-version files, declared scripts,
and existing configuration. It does not import application code, execute a
package script, contact an application service, open `.env`, or boot anything
while preparing the proposal.

The proposal shows, with provenance and confidence:

- selected runtime and exact/range source;
- package manager and install policy;
- application command and bind requirement;
- public service, internal port, requested host port, and health path;
- required PHP extensions or Node native-build concerns;
- dependency services and persistence expectation;
- source mount/write behavior and ignored dependency volumes;
- environment sources and the keys/consumers policy without values;
- known platform or architecture differences.

Unknown or conflicting values remain explicit questions. Sandbox writes
reviewable project configuration only after acceptance, and repository code runs
only in a separate explicit start/ensure action. Existing project-owned Compose
remains authoritative and is never overwritten by a preset.

Both presets use the existing generic Compose adapter. “Laravel” and “Node” are
proposal types, not new runtime kinds.

### 5.3 Related-project lifecycle

A developer can declare that independently rooted Sandbox projects participate
in one named relation, with explicit roles such as backend and frontend.

Required behavior:

- every member retains its canonical project-root registry identity;
- membership is explicit, bounded, and visible from every member;
- the relation provides stable service discovery only between declared members;
- dependency-only services such as MySQL remain private to their owning project;
- start waits for declared dependency health in order and reports which member
  or dependency failed;
- status gives one aggregate view plus per-project evidence;
- stop/destroy operations name their scope and never remove a sibling project,
  persistent data, or shared relation resource still owned by another member;
- partial failure retains enough non-secret ownership state for retry/recovery;
- no implicit global “stack instance” is created.

Browser-visible frontend configuration and server-side frontend discovery are
separate facts. A URL that works in the browser must not be assumed to resolve
inside a frontend container, and an internal service alias must not leak into a
browser bundle.

### 5.4 Registered application environments

Sandbox extends registered secret-source policy with an application-consumer
contract. The contract identifies a registered source alias, selected keys or
reviewed key groups, consuming service and phase, destination name/file, and
whether the value is needed at runtime, build time, or initialization time.

Product invariants:

- no arbitrary caller path and no source auto-discovery;
- source ownership, permissions, syntax, size, link, and change checks remain
  fail-closed;
- values never enter argv, logs, status, parity reports, registry records,
  committed descriptors, or generated research evidence;
- a service sees only granted values;
- build-time and browser-exposed values require separate, prominent treatment;
- machine-local connectivity overrides can replace values such as database host
  without rewriting the user's source file;
- ordinary agents cannot request a whole-source reveal;
- application delivery is auditable by aliases, key names, consumers, and
  result state only;
- revocation/reconciliation removes stale grants without deleting the source.

This contract does not weaken the existing single-secret bounded-use workflow.
It is a separate consumer class with its own least-privilege and output policy.

### 5.5 Dependency-aware readiness and diagnosis

Ready means more than a container process exists. The selected contract may
require:

- dependency service health, including completion of first database
  initialization;
- backend HTTP health;
- frontend HTTP health;
- successful internal frontend-to-backend resolution;
- expected public URLs;
- selected runtime/package-manager/version probes;
- absence of blocking parity mismatches.

A bounded failure result identifies the failing member/dependency, readiness
stage, elapsed budget, relevant non-secret version/health facts, and bounded log
tail. It does not dump container environments, Compose interpolation values, or
secret-bearing application configuration.

### 5.6 Port ownership and host coexistence

Requested ports such as 3000 and 8000 are preferences, not authorization to
stop another process. Before claiming one, Sandbox re-probes occupancy and
returns only safe process identity needed for a decision. It either allocates a
different port or waits for a fresh explicit operator decision. It never kills,
reconfigures, or masks a host process implicitly.

Port evidence is transient. A prior research result or parity baseline cannot
authorize a later takeover.

## 6. Non-goals

- Claiming byte-for-byte, native-process, or performance equivalence between
  Herd/macOS and Compose/Linux when evidence shows differences.
- Enabling the current Herd, Valet, declared-POSIX, or managed-native adapters;
  changing their support tiers; or expanding their WordPress scope.
- Replacing Docker Compose, Laravel Sail, Dev Containers, NVM, Herd, or DBngin
  as general-purpose products.
- Automatically executing discovered Dockerfiles, Compose models, Artisan,
  Composer scripts, package scripts, installers, or migrations.
- Reading, copying, loosening permissions on, or rewriting `.env` automatically.
- Importing the existing DBngin database, cloning production data, or defining a
  general database backup/restore feature.
- Publishing, deploying, exposing, or changing production/staging systems.
- Guaranteeing native Next.js edit latency before measurement.
- Creating a hidden global instance or making one repository own another
  repository's source or persistent data.
- Supporting arbitrary framework-specific presets in this phase beyond Laravel
  and Node-compatible web projects.

If the owner later requires genuine generic Herd execution, it receives a
separate PRD and must remain labeled trusted-shared-host/lower-isolation until
independent live proof makes it adoptable.

## 7. Core scenarios

### Scenario A — Conventional Laravel proposal

- **Starting state:** Laravel repository, no Sandbox descriptor or Compose file,
  inert Composer metadata available, registered but undisclosed `.env` source.
- **Action:** Developer explicitly requests a Laravel proposal.
- **Outcome:** Sandbox reports PHP constraint, proposed concrete runtime,
  extensions, server command, database dependency, ports, persistence, health,
  environment grants, and differences. Nothing runs. After acceptance and a
  separate ensure, the backend becomes reachable only after database and HTTP
  health pass.

### Scenario B — Node/Next proposal with a package-manager pin

- **Starting state:** `.nvmrc`, `packageManager`, lockfile, and `scripts.dev`
  disagree with host-global tooling in at least one version.
- **Action:** Developer requests a Node proposal.
- **Outcome:** Sandbox proposes the project pins rather than the host global,
  reports exact/compatible differences, avoids Alpine when compatibility is
  unknown, and measures edit/reload behavior after explicit start.

### Scenario C — Related frontend and backend

- **Starting state:** Two independently registered projects with their own
  instances; backend owns a private database.
- **Action:** Developer declares a relation and starts it.
- **Outcome:** Database becomes healthy, backend becomes healthy, frontend
  becomes healthy, a server-side frontend probe reaches the backend through a
  stable internal name, and the browser receives only public URLs.

### Scenario D — Unsafe secret source

- **Starting state:** A registered `.env` source is group/world-readable,
  changed during inspection, contains unsupported syntax, or grants no required
  consumer.
- **Action:** Developer proposes or starts the environment.
- **Outcome:** Sandbox refuses before value delivery, names only the source alias
  and safe remediation class, and does not fall back to raw reads or whole-file
  injection.

### Scenario E — Port race

- **Starting state:** Port 3000 appears free during proposal but is occupied
  before ensure.
- **Action:** Developer starts the relation.
- **Outcome:** Sandbox re-probes, refuses implicit takeover, offers a safe
  alternate or requests fresh direction, and leaves the host process unchanged.

### Scenario F — Exact MySQL pin unavailable natively

- **Starting state:** ARM64 host, reference MySQL 8.0.27, only amd64 image
  available.
- **Action:** Developer asks for exact database parity.
- **Outcome:** Sandbox reports emulation and expected performance implications,
  refuses to call a native-version alternative exact, and waits for the selected
  policy.

### Scenario G — Partial relation failure

- **Starting state:** Database and backend are healthy; frontend install or
  health fails.
- **Action:** Developer inspects status and retries.
- **Outcome:** Backend/database ownership and persistent data remain intact;
  status identifies the frontend stage and bounded diagnostics; retry does not
  create duplicate instances, networks, or volumes.

## 8. Acceptance outcomes

The later specification must make these observable and independently testable:

1. Representative Laravel and Node repositories with no prior Compose file can
   reach a reviewable, non-executing proposal through one explicit action; no
   package/application command runs until a second explicit action.
2. Every proposed fact shows provenance and one of `matched`, `compatible`,
   `mismatched`, `unavailable`, or `unverified`; sampled compatible and
   unverified fixtures are never labeled exact.
3. PHP, Node, package-manager, database, architecture, routing, environment,
   dependency-health, and edit/reload dimensions all appear in the parity
   result for the representative stack.
4. Three repeated ensure → status → stop → start → apply cycles preserve one
   registry identity per project, one relation identity, stable discovery,
   persistent database data, and no orphaned Sandbox-owned resources.
5. A fresh database never allows the backend to be reported ready before its
   declared health passes; a failed initialization produces bounded,
   secret-safe diagnostics.
6. Browser-side and server-side frontend requests both reach the intended
   backend without exposing the private database network or an internal service
   hostname to browser configuration.
7. One hundred percent of sampled ungranted, unsafe-permission, duplicate-key,
   changed-during-use, and unsupported-syntax secret sources fail before value
   delivery; logs, status, registry, generated descriptors, and captured output
   contain no secret values.
8. Requested-port collisions never stop or reconfigure a host process without a
   fresh explicit decision; a collision after proposal is detected again at
   ensure time.
9. On Apple Silicon, the result records whether each selected image is native or
   emulated. MySQL 8.0.27 is never reported native in the current manifest
   evidence.
10. The representative frontend records cold start and median warm
    edit-to-visible time across at least five edits for both prior host-native
    and selected Sandbox modes. The owner-selected adoption threshold in §12 is
    met or the result remains not adoptable.
11. Backend application latency is measured against the selected local database
    for a representative request/query path and compared with the prior setup;
    the result is evidence, not a guaranteed percentage chosen in advance.
12. Existing WordPress and generic Compose live behavior remains unchanged for
    projects that do not opt into a new preset or relation.

## 9. Dependencies and constraints

- Compose remains the default runtime and clean URLs remain Sandbox
  Caddy-owned; Herd/Valet routing is opt-in only and outside this phase.
- One project root remains one registry-owned instance identity. Relation state
  cannot become a second instance authority.
- Project manifests, Compose models, and scripts are untrusted executable input.
- Existing generic cleanup preserves project-owned volumes; relation cleanup
  must retain that guarantee across members.
- Secret policy from spec 041 remains authoritative. No convenience path may
  make same-identity filesystem access an excuse for raw reads.
- Remote deployment transfers and production environment delivery are separate
  products. Local success does not authorize deploy/expose.
- Docker Desktop filesystem and architecture behavior are part of acceptance,
  not assumed away.
- Both representative repositories contain user-owned commits/changes and may
  receive concurrent edits. Preset validation must be reproducible in clean
  fixtures before any repository-specific acceptance.

## 10. Risks

- **False equivalence:** matching version strings can conceal OS, libc,
  extension, INI, routing, and performance differences. Mitigation: dimensional
  results and an extremely strict definition of exact.
- **Secret expansion:** a convenient application-env feature could become a
  whole-file export. Mitigation: registered source, declared key/consumer/phase,
  service-granular access, and value-free evidence.
- **Cross-project ownership:** an external network or group command could create
  hidden global state or destructive cleanup. Mitigation: explicit membership,
  reference-counted ownership semantics, and per-project identity preservation.
- **Supply-chain drift:** floating images and install commands can change after a
  proposal. Mitigation decision is deferred to specification after the owner
  chooses tag/digest posture.
- **Apple Silicon emulation:** exact MySQL may remove the latency benefit that
  motivated local execution. Mitigation: make architecture visible and benchmark
  before adoption.
- **Frontend regression:** Docker bind mounts may make Turbopack editing slower.
  Mitigation: measurable edit latency and a product decision on hybrid/native
  fallback rather than a boot-only acceptance gate.
- **Browser/server URL confusion:** one API URL rarely works both inside a
  container and in the browser. Mitigation: separate public and internal
  endpoints with disclosure-aware phases.
- **Port races:** planning-time availability can change. Mitigation: re-probe at
  apply and never infer stop authority.

## 11. Confirmed policy decisions

| Decision | Choice | Source |
| --- | --- | --- |
| Default runtime | Existing generic Compose adapter | Current adoptable manifest; project policy |
| Native fallback | Never silent; current Herd adapter remains non-adoptable | Native runtime spec 039 and runtime manifest |
| Project identity | Each root retains its own registry instance | Sandbox constitution |
| Code execution | Explicit proposal/acceptance before project execution | Generic project spec 021 |
| Secret handling | Registered sources, least privilege, no raw reads or value-bearing output | Safe secret inspection spec 041 |
| Destructive behavior | No implicit process stop, data deletion, deployment, or exposure | Repository policy and current user scope |

## 12. Unresolved decisions (owner input)

1. **Equivalence promise:** adopt the recommended “observable parity with
   explicit differences,” or require genuine host-native Herd execution? The
   latter moves generic Herd support to a separate prerequisite PRD and keeps
   this Compose phase from claiming the requested outcome.
2. **Frontend execution strategy and adoption threshold:** Compose-only for
   reproducibility, or permit a future Sandbox-managed host-native frontend for
   edit speed? Proposal: ship Compose first only if median warm edit-to-visible
   time is no worse than 1.5× the measured host-native baseline and no individual
   measured edit exceeds 3×; otherwise return the PRD to discovery for a native
   process contract.
3. **MySQL on Apple Silicon:** exact 8.0.27 under amd64 emulation, or a current
   native-ARM MySQL version with an explicit compatibility difference? Proposal:
   benchmark exact 8.0.27 first because database behavior matters more than image
   purity; accept a native-version alternative only after schema/query tests show
   no consequential difference and it materially improves latency.
4. **Application environment scope:** selected-key allowlists only, reviewed
   groups, or whole registered source per service? Proposal: selected keys plus
   named reviewed groups; whole-source delivery remains refused because it
   defeats least privilege and makes frontend build/browser exposure too easy.
5. **Related-project ownership:** one relation that references independent
   instances, or a new composite root that owns both repositories? Proposal:
   relation metadata over independent instances, preserving the constitution's
   project-root identity and avoiding a wrapper checkout that owns foreign
   source.

## 13. Readiness and next stage

This brief has an explicit problem, users, outcomes, scope, negative scenarios,
security/compatibility constraints, measurable acceptance, and repository plus
official-source evidence. It is intentionally **NOT READY** for
`speckit-specify` until the five §12 choices are confirmed. The choices affect
runtime scope, performance acceptance, database compatibility, secret exposure,
and registry ownership; choosing them silently would materially change the
product.

After confirmation, reconcile this PRD in place, then run the normal Spec Kit
sequence. Do not create `specs/042-*` or change `.specify/feature.json` merely
because this backlog brief exists.
