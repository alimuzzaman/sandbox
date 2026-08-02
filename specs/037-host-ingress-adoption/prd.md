# Product Requirements Draft: Host Ingress Adoption

**Status**: Discovery

**Created**: 2026-08-01

**Last Refined**: 2026-08-01

**Input**: "Host ingress adoption: when another tool already owns :80/:443 (Herd, Valet, system nginx, Apache, system Caddy, Traefik, Nginx Proxy Manager, DDEV router, Local, Laragon, XAMPP/WAMP), Sandbox must detect the incumbent and register its instance routes THROUGH that tool instead of binding the ports itself or failing. Own Caddy container remains the fallback when no incumbent exists. Must be safely reversible and never steal a port it does not own."

**Drafting Model**: `claude-opus-5[1m]` (fallback; `gpt-5.6-terra` Medium not available in this session)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-01

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox's clean-URL feature assumes it may own the machine's HTTP ports. Its proxy
publishes `127.0.0.77:80` and `127.0.0.77:443` (`sandbox/core/_paths.py:207`,
`sandbox/core/_domains.py:393-394`), and `proxy_available()` reports the feature
usable whenever the `docker` binary exists (`sandbox/core/_domains.py:196-200`) —
it never asks whether another process already holds those ports.

Developer machines and shared dev servers usually already run something on :80/:443:
a system nginx or Apache, Laravel Herd or Valet, a Traefik or Nginx Proxy Manager
container, a DDEV router, or another local-WordPress app. On those machines Sandbox
does not adopt the incumbent and does not report the real cause. The proxy container
fails to start and the user is told `proxy container did not start (is Docker
running?)` (`sandbox/core/_domains.py:788`) — a misleading message when the true
cause is a port already owned by Apache. The instance still works, but only at
`http://localhost:<port>`, so the clean-URL feature silently disappears on exactly
the machines that are most likely to be a developer's primary box.

One adoption path already exists and proves the shape works: on macOS, Sandbox
registers routes through Valet with `valet proxy <domain> http://127.0.0.1:<port>`
(`sandbox/core/_domains.py:149`). Nothing equivalent exists for any other tool —
not even for Herd, which Sandbox already drives as a server runtime
(`sandbox/core/_herd.py`) and which is Valet-compatible.

The repository has no uniform product model for these incumbents. Some expose a
supported command or configuration interface, some require credentials, and some
can only be identified safely. The missing capability is therefore broader than a
single adapter: Sandbox must distinguish what it can adopt from what it can only
detect, and report that distinction before changing the host.

**Why now**: Sandbox is being positioned for use on shared dev servers and on
developer machines that already have a full stack, not only on clean Docker hosts.
Many such machines currently lose clean URLs or receive a misleading failure.
Valet is an existing exception, and a listener that does not overlap
`127.0.0.77:80/443` may coexist without conflict.

## Users and Desired Outcomes

- **Developer on a machine with an existing web server** (system nginx/Apache,
  Herd, Valet, XAMPP): creates a Sandbox instance and reaches it at its clean
  hostname, without stopping or manually reconfiguring the server they already
  depend on.
- **Developer on a machine with an adoptable container ingress** (a compatible
  Traefik file provider or existing Caddy): same outcome, with routes appearing in
  the ingress they already operate and observe. Detect-only products such as Nginx
  Proxy Manager or a DDEV router are named accurately without private-state mutation.
- **Developer whose machine has nothing on :80/:443**: sees no change — Sandbox's
  own proxy continues to serve clean URLs exactly as it does today.
- **Developer whose incumbent cannot be driven automatically**: is told precisely
  which process owns the port and what single action would finish the job, instead
  of receiving a misleading Docker error.
- **Owner of the host machine**: can remove Sandbox and be certain no route,
  virtual host, or config fragment it added is left behind, and that nothing they
  authored themselves was modified or deleted.

## Goals

- Detect the owner of each conflicting bind endpoint on :80 and :443, and identify
  it as a known ingress product where the operating system exposes enough evidence.
- Register and unregister Sandbox instance hostnames through a detected incumbent,
  so instances are reachable at clean URLs without Sandbox binding :80/:443.
- Keep the existing Sandbox Caddy proxy as the DEFAULT provider on every platform
  and for every runtime, with no change to that path, and make incumbent adoption an
  opt-in alternative selectable at setup and switchable on demand.
- Make every route Sandbox adds attributable to Sandbox and individually removable,
  so adoption is fully reversible.
- Replace the misleading port-conflict failure with an accurate report naming the
  owning process and the available options.
- Let a project or machine pin which ingress to use, overriding detection.
- Classify every named incumbent as adoptable, credential-pending, detect-only, or
  outside the supported platform, so coverage is not confused with adoption.

## Non-Goals

- Name resolution. Mapping a hostname to the loopback address — wildcard DNS,
  `/etc/hosts` entries, systemd-resolved, Pi-hole — is a separate concern with
  separate failure modes and is owned by the TLD/DNS adoption feature (spec B).
  This feature assumes the hostname already resolves and covers only what happens
  once the request reaches the machine's HTTP port.
- Non-Docker runtimes for the instance itself (native PHP/MySQL/nginx, completing
  Herd as a runtime). Owned by the native-runtime feature (spec C).
- Installing, configuring, starting, upgrading, or repairing an incumbent. Sandbox
  adopts what is already running; it never provisions an ingress on the user's behalf.
- Taking over a port an incumbent holds, stopping an incumbent, or restarting it
  beyond the reload its own documented interface performs when a route changes.
- Managing certificates for an incumbent. Where an incumbent issues its own
  certificates, Sandbox uses that mechanism; it does not install its own CA into a
  third-party ingress.
- Exposing instances beyond the local machine. Adoption is about local ports, not
  public reachability, which remains the remote-hosting feature's concern.
- Native Windows support. Sandbox has no native Windows entry point
  (`docs/cross-platform-support.md` §5); WSL2 is the supported path.

## Product Scenarios

### Scenario 1 — System nginx already owns :80

- **Starting state**: A Linux dev box runs a system nginx serving other projects on
  :80. Sandbox is installed; the machine has never run the Sandbox proxy.
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: Sandbox reports that nginx owns :80, registers a route for
  the instance hostname through nginx, and the instance is reachable at its clean
  URL. The Sandbox proxy container is not started. Every site nginx served before
  still serves.

### Scenario 2 — Herd owns the ports on macOS

- **Starting state**: macOS with Laravel Herd running and serving other sites.
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: Sandbox registers the instance through Herd and the
  instance opens at a Herd-served hostname. This holds whether the instance itself
  runs in Docker or under Herd as a runtime.

### Scenario 3 — Traefik owns the ports

- **Starting state**: A machine running a Traefik container that routes other
  services.
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: The instance appears as a route in Traefik and is reachable
  at its clean URL. Traefik's existing routes are unaffected.

### Scenario 4 — Nothing owns the ports

- **Starting state**: A machine with Docker and nothing bound to :80/:443.
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: The Sandbox Caddy proxy remains the selected ingress and serves
  the hostname supplied by spec B. Existing persisted hostnames remain unchanged; new
  unpinned hostname policy belongs to spec B.

### Scenario 5 — Incumbent detected but not adoptable

- **Starting state**: A process owns :80 that Sandbox cannot identify, or can
  identify but cannot drive (no scriptable interface, or credentials it has not
  been given).
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: The instance is created and works at `http://localhost:<port>`.
  Sandbox reports which process owns the port, that the clean URL is unavailable
  for that reason, and what would resolve it. It does not attempt to bind the port,
  and it does not report a Docker fault.

### Scenario 6 — Removing an instance

- **Starting state**: An instance is registered through an adopted incumbent.
- **User action**: The developer destroys the instance.
- **Expected outcome**: The route Sandbox added is removed from the incumbent, the
  incumbent reloads, and no other route in that incumbent changes.

### Scenario 7 — Hostname already claimed in the incumbent

- **Starting state**: The incumbent already serves the hostname Sandbox is about to
  register, from a route Sandbox did not create.
- **User action**: The developer creates an instance that would use that hostname.
- **Expected outcome**: Sandbox refuses to register, leaves the existing route
  untouched, reports the collision and the owning route, and falls back to the
  per-port URL. It never overwrites a route it does not own.

### Scenario 8 — Incumbent disappears

- **Starting state**: Sandbox registered routes through an incumbent; the user has
  since stopped or uninstalled that incumbent.
- **User action**: The developer runs a status or repair command, or re-ensures the
  instance.
- **Expected outcome**: Sandbox reports that the previously adopted ingress is gone
  and that the clean URL no longer serves, and offers the currently available option
  (adopt a different incumbent, or start the Sandbox proxy). It does not report the
  instance as healthy at a URL that does not answer.

### Scenario 9 — Removing Sandbox

- **Starting state**: Sandbox has registered routes through one or more incumbents
  over time.
- **User action**: The developer uninstalls Sandbox.
- **Expected outcome**: Every unchanged Sandbox-owned route in an available incumbent is
  removed and the incumbent reloads without changing user-authored configuration. Any
  unavailable, drifted, or rejected cleanup is reported as incomplete and retains the
  minimum recovery record needed to retry; uninstall never claims it was removed.

### Scenario 10 — Incumbent requires credentials, first detection, interactive

- **Starting state**: A machine running an ingress that manages routes only through
  a documented authenticated interface supported by its adapter. Sandbox has no
  credentials for it. The developer is at an interactive terminal.
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: Sandbox reports that this ingress owns the ports and can be
  adopted with credentials, and offers to take them now. Supplying them completes
  adoption; declining is remembered, is not re-asked, and leaves the instance on its
  per-port URL. Credentials are stored with Sandbox's other per-machine secrets and
  are never echoed to output, a commit, or a log.

### Scenario 11 — Incumbent requires credentials, non-interactive caller

- **Starting state**: As Scenario 10, but the operation runs from the MCP server, a
  CI job, or any context with no terminal, and no credentials have been stored.
- **User action**: An instance is created and a clean URL is requested.
- **Expected outcome**: Sandbox does not prompt and does not block. It reports the
  ingress as detected-but-not-adoptable pending credentials, states how to supply
  them, and the instance is created on its per-port URL.

### Scenario 12 — WSL2 with a Windows-side ingress

- **Starting state**: Sandbox runs inside a WSL2 distribution; the process holding
  :80 is a Windows-side application (for example Laragon), not reachable from the
  distribution.
- **User action**: The developer creates an instance and asks for a clean URL.
- **Expected outcome**: Sandbox reports that the port is held from the Windows side
  and cannot be adopted, rather than reporting an unexplained bind failure. The
  instance is created on its per-port URL.

### Scenario 13 — Explicitly pinned ingress

- **Starting state**: A machine where two candidate ingresses are present and
  detection would pick the wrong one, or where the user wants the Sandbox proxy
  regardless.
- **User action**: The developer pins the ingress in configuration.
- **Expected outcome**: Sandbox uses the pinned ingress and does not re-run detection.
  If the pinned ingress is absent or unusable, Sandbox reports that plainly rather
  than silently choosing another.

### Scenario 14 — First adoption from a non-interactive caller

- **Starting state**: An adoptable incumbent is detected, but the machine has no
  recorded adoption consent. The request comes from MCP or CI.
- **User action**: The caller requests a clean URL.
- **Expected outcome**: Sandbox does not prompt, block, or mutate the incumbent. It
  reports adoption as pending consent, states how to grant or decline it from an
  interactive terminal, and leaves the instance on its per-port URL. A previously
  declined decision can be reconsidered only through an explicit user action.

### Scenario 15 — Route changed outside Sandbox

- **Starting state**: Sandbox created a marked route, after which an operator changed
  its target or other material properties directly in the incumbent.
- **User action**: Sandbox re-ensures or removes the instance.
- **Expected outcome**: Sandbox detects that the marked route no longer matches its
  last known state and does not overwrite or remove it automatically. It reports the
  drift and the explicit reconciliation or cleanup action available to the operator.

### Scenario 16 — Partial or split port ownership

- **Starting state**: Only :443 conflicts, or :80 and :443 are held by different
  products or bind addresses.
- **User action**: The developer requests a clean URL.
- **Expected outcome**: Sandbox reports the owner and bind address for each endpoint
  separately and applies the confirmed split-ownership policy. It never treats a
  listener on `127.0.0.1` as conflicting with `127.0.0.77` unless the actual bind
  scopes overlap.

### Scenario 17 — Sandbox already owns the endpoints

- **Starting state**: Sandbox's proxy is already serving other instances on its
  dedicated loopback endpoints.
- **User action**: Another instance is ensured or status is requested.
- **Expected outcome**: Sandbox identifies its own proxy as the current owner, keeps
  using it without asking to adopt itself, and preserves all existing routes.

### Scenario 18 — Incumbent configuration or reload is unhealthy

- **Starting state**: A selected nginx, Apache, or Caddy has pre-existing invalid
  configuration, or rejects/reports unhealthy after a candidate Sandbox route is applied.
- **User action**: The developer requests or updates a clean URL.
- **Expected outcome**: Sandbox validates the complete incumbent configuration before
  mutation. On validation or reload failure it restores the prior state, verifies the
  incumbent's pre-existing routes remain healthy where they were healthy before, reports
  the actual failure, and leaves the Sandbox instance on its per-port URL.

## Proposed Product Behavior

- **Detection before action.** Before any attempt to serve clean URLs, Sandbox
  determines the owner and bind scope of each relevant :80 and :443 endpoint.
  Detection distinguishes free, Sandbox-owned, recognized adoptable,
  credential-pending, detect-only, outside-platform, and unidentified states.
  Detection is read-only and never changes machine state.

- **Default provider, adoption on request.** Sandbox's own Caddy proxy is the default:
  when the required endpoints are free or already Sandbox-owned it serves the clean URL,
  regardless of whether an adoptable incumbent is also present. When the user selects an
  incumbent, or when a foreign listener holds an endpoint Sandbox needs, Sandbox registers
  through the selected incumbent instead. Sandbox never binds a port it found held, and
  never stops an incumbent to take a port.

- **Uniform route lifecycle.** Whatever the incumbent, the product-visible lifecycle
  is the same: a route is added when an instance gains a clean URL, updated when the
  instance's port changes, and removed when the instance is destroyed or the clean
  URL is turned off. Re-running any of these is safe and produces the same end state.

- **Ownership and attribution.** Every route Sandbox creates is marked as Sandbox's
  and associated with the instance that owns it. Sandbox only ever modifies or
  removes routes bearing its own mark. A hostname collision with an unmarked route
  is a refusal, not an overwrite.

- **Consent for changing a user-owned service.** Registering into a service the user
  installed and operates — writing a virtual host, calling an ingress API, reloading
  a system daemon — is a change to their machine outside Sandbox's own directories.
  Sandbox asks for confirmation the first time it adopts a given incumbent on a given
  machine and remembers the answer, matching the existing one-time-consent pattern
  used for privileged host actions (`sandbox/core/_domains.py:714-751`). A declined
  offer is remembered and not re-asked. Without recorded consent, a non-interactive
  caller never prompts or mutates; it receives a pending-consent result and the
  instance remains on its per-port URL. Reconsidering a decline requires an explicit
  user action.

- **Credentials are offered, never demanded.** Where an incumbent can only be driven
  with credentials, Sandbox offers to collect them the first time it detects that
  incumbent at an interactive terminal. It never prompts a non-interactive caller,
  never blocks waiting for input, and remembers a declined offer. Until credentials
  exist, that incumbent is reported as detected-but-not-adoptable with the exact
  action that would enable it.

- **Degrade, never block.** Failure to adopt never blocks instance creation or any
  other Sandbox operation. The instance is created and reachable at its per-port URL,
  and the clean URL is reported as unavailable with the reason.

- **Truthful reporting.** Status and health output names the ingress currently in
  use, whether it is adopted or Sandbox's own, and the reason when clean URLs are
  unavailable. The generic "is Docker running?" message is not used for a port
  conflict.

- **Explicit override.** Configuration can pin the ingress for a machine or a
  project, including pinning Sandbox's own proxy or disabling clean URLs entirely.
  An explicit pin is never silently overridden by detection. A machine-local project
  override wins over the committed project pin, matching the repository's existing
  global → project → machine-override merge order.

- **Transactional validation and reload.** For configuration-file incumbents, Sandbox
  validates the incumbent's complete current configuration before mutation, validates the
  candidate state before activation, and rolls back its owned fragment if activation or
  post-apply health fails. It never uses a failed reload as proof that the route exists.

- **Extensibility as a product property.** The set of supported incumbents grows
  over time. Support for a given incumbent is declared, discoverable, and reportable
  — a user can ask which ingresses this Sandbox build can adopt, and what each
  requires (for example, credentials, or a writable configuration directory).

- **Capability honesty per incumbent.** Incumbents differ in what they can express.
  Where an incumbent cannot provide something Sandbox's own proxy provides — for
  example TLS, or wildcard hostnames for subdomain multisite — Sandbox reports that
  limitation for that instance rather than presenting a URL that will not work.

- **Drift-aware ownership.** A Sandbox mark is necessary but not sufficient to edit
  or remove a route. The observed route must also match the state Sandbox last wrote.
  A changed or ambiguous route is reported for reconciliation and is left untouched
  by ordinary ensure, destroy, and uninstall operations.

- **Recoverable cleanup.** If an incumbent is absent or rejects cleanup, Sandbox
  reports the residual route and retains the minimum non-secret ownership and recovery
  record needed to retry. It never claims complete cleanup while a known route remains.

## Constraints and Dependencies

- **Depends on name resolution (spec B).** A registered route only produces a working
  clean URL if spec B supplies the hostname and resolves it to an address on which the
  selected ingress accepts requests. Resolution to `127.0.0.77` is not sufficient for
  an incumbent bound only to `127.0.0.1`, and the inverse must not be assumed either.
  This feature consumes that hostname/address decision; it does not silently replace
  the hostname or TLD.
- **Privileged operations.** Writing into `/etc/nginx`, `/etc/apache2`, or an
  equivalent, and reloading a system service, requires elevated privileges. Sandbox's
  established policy is a single explicit consent step, after which host actions are
  password-free and non-interactive (`sandbox/core/_domains.py:670-690`); privileged
  calls must never block on an interactive password prompt, because they run from
  non-interactive contexts including the MCP server and CI.
- **Credentialed incumbents.** A future or installed supported adapter may expose route
  management only through a documented authenticated interface. Sandbox cannot adopt
  it without credentials the user supplies. Nginx Proxy Manager is not such an adapter
  in the initial matrix because its public documentation does not establish an external
  route-management API. Credentials are per-machine secrets and are subject to the
  existing secrets policy: they live in
  `sandbox.local.yml` / `.env.local` and are never echoed to output, a commit, a
  comment, or a memory file (constitution, Additional Constraints).
- **Non-scriptable incumbents.** Some products (for example Local) own the ports but
  publish no supported route-registration interface. These are detected and reported;
  Sandbox does not write into their private configuration.
- **Platform reach.** Windows-only products cannot be adopted natively because
  Sandbox has no native Windows entry point (`docs/cross-platform-support.md` §5).
  Under WSL2, the incumbent that matters is the one inside the WSL2 distribution;
  a Windows-side ingress is out of reach.
- **Bind-address subtlety.** Port ownership is per bind address. Sandbox's proxy uses
  a dedicated loopback address (`127.0.0.77`), which does not collide with a process
  bound only to `127.0.0.1`, but does collide with one bound to all addresses.
  Detection must reflect actual conflict rather than assuming any listener on :80
  conflicts. Sandbox's existing per-instance probe tests only `127.0.0.1`
  (`sandbox/core/_instances.py:180-198`) and is not sufficient on its own.
- **Constitution — idempotency (V).** Every adoption action must be safe to re-run.
- **Constitution — live-stack proof (IV).** Each supported incumbent is "done" only
  when a request to the clean URL is served by that incumbent on a running stack.
- **Constitution — docs with code (V).** Supported-incumbent documentation lands with
  the capability.
- **Module boundaries (CLAUDE.md).** Ingress support registers through an explicit
  manifest/contract; capability checks precede side effects.
- **Backward compatibility.** Every machine keeps Sandbox's own Caddy ingress as the
  default — including machines that also run an adoptable incumbent — and existing
  persisted hostnames remain unchanged. Disabling that path in place counts as removal
  under constitution VI. Spec B may choose a standards-safe
  default for new unpinned projects. The existing Valet integration must keep working or
  be superseded by an equivalent that is verified before the old path is removed
  (constitution VI).

## Incumbent Coverage Policy

| Product or family | Initial tier | Boundary |
|-------------------|--------------|----------|
| Laravel Herd / Valet | Adoptable | Use the documented site/proxy lifecycle exposed by the incumbent CLI. |
| System nginx | Adoptable | Only when Sandbox can add an isolated owned route, validate the complete configuration, and perform a graceful reload. |
| System Apache HTTP Server | Adoptable | Only when an isolated owned virtual host can be validated and gracefully reloaded. |
| System Caddy | Adoptable | Only through its documented administration/configuration surface with collision protection; an unavailable or unprotected control surface is detect-only. |
| Traefik | Conditionally adoptable | Adoptable only when an existing file-provider directory is enabled and available for an isolated owned route; Sandbox does not reconfigure Traefik's install/static configuration. |
| Nginx Proxy Manager | Detect-only | Its public documentation does not establish a supported external route-management API; Sandbox does not automate a private UI API or internal database. |
| DDEV router | Detect-only | Sandbox identifies the router but does not inject configuration into a DDEV-owned project or container network without a documented external-route contract. |
| Local | Detect-only | Local exposes an in-process add-on API, not a supported external route-registration interface; installing a Local add-on is outside this feature. |
| Laragon / WAMP | Outside native platform; detect-only from WSL2 | Sandbox has no native Windows entry point and does not reach across WSL2 to mutate Windows-side services. |
| XAMPP | Detect-only initially | Product-specific adoption requires a supported POSIX control/configuration contract and live proof; a Windows-side install remains outside the native platform. |
| Unidentified listener | Detect-only | Report bind endpoint and available owner evidence; never mutate. |

These tiers describe the initial product contract, not an implementation sequence.
A detect-only product can become adoptable later only when it has a documented control
surface and passes the same live-stack ownership, collision, update, and cleanup proof.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Breadth of incumbent coverage | Every named ingress is classified and reported; only products with a supported, live-proven control path are called adoptable | Detect-only and outside-platform products must not be advertised as adopted, while still eliminating misleading bind failures | User scope; repository safety policy |
| Feature split | Ingress adoption is its own feature, separate from TLD/DNS adoption and native runtimes | Port ownership and name resolution fail independently; a machine can have Apache on :80 and systemd-resolved on DNS | User |
| Default provider | Sandbox's own Caddy remains the ingress on every platform and runtime, incumbent present or not; hostname selection comes from spec B | Preserves the working default without duplicating or contradicting DNS/TLD policy | User (2026-08-02 decision); A/B boundary |
| Adoption trigger | Explicit selection at setup or `./sb domains use <provider>` on demand, or a foreign listener holding a required endpoint | Presence of an incumbent is not consent to route through it | User (2026-08-02 decision) |
| Proof-tier scope | Live-proof gating applies to incumbent adoption only, never to the default provider | An unproven adapter must not be able to downgrade every instance to a per-port URL | User (2026-08-02 decision) |
| Port stealing | Never; Sandbox does not bind a port it found held, and never stops an incumbent | Taking a port would break services the user depends on | User (input) |
| Reversibility | Every Sandbox-created route is attributable and individually removable | Adoption writes into services the user owns; it must be undoable | User (input) |
| Failure posture | Degrade to per-port URL, never block instance creation | Matches the existing clean-URL posture, which is an opt-in upgrade over a working per-port URL | Existing policy (`sandbox/core/_domains.py:770-773`) |
| Collision with a foreign route | Refuse and report; never overwrite | Overwriting a route Sandbox did not create could take down a user's service | Existing policy (proxy-helper declines rather than stealing :53) |
| Consent model | One-time explicit confirmation per incumbent per machine, remembered | Mirrors the established one-time-sudo/declined-offer pattern | Existing policy (`sandbox/core/_domains.py:714-751`, `:203-213`) |
| Detection side effects | Read-only | Detection runs on ordinary status commands; it must be safe on any machine | Existing policy (`sb doctor` health checks are read-only) |
| Windows-only incumbents | Out of scope natively; WSL2-side incumbents are in scope, and a Windows-side holder of the port is detected and reported | Sandbox has no native Windows entry point; porting it is a larger change than this feature and a prerequisite to it | User; repository evidence (`docs/cross-platform-support.md` §5) |
| Non-scriptable incumbents | Detect, report precisely, fall back to the per-port URL. Sandbox does not write into a product's private configuration | Reverse-engineered writes break on the product's own updates and risk corrupting the user's other sites — a worse outcome than an honest, actionable message | User |
| Credentialed incumbents | Supported only when an adapter uses a documented authenticated control surface. On first detection at an interactive terminal Sandbox may offer to take credentials; non-interactive callers are never prompted. Nginx Proxy Manager remains detect-only in the initial matrix | Improves discovery without treating a private UI API as a stable product contract | User intent refined by primary-source research; existing policy (`sandbox/core/_domains.py:717-721`) |
| Declining a credential offer | Remembered per incumbent per machine; not re-asked | Matches the existing declined-offer marker for the HTTPS upgrade | Existing policy (`sandbox/core/_domains.py:203-213`) |
| Unrecorded consent in non-interactive contexts | Do not prompt or mutate; report pending consent and use the per-port URL | MCP and CI must never hang, and changing a user-owned service requires prior consent | Existing non-interactive policy (`sandbox/core/_domains.py:717-721`) |
| Route drift | Leave a changed marked route untouched until explicit reconciliation | A marker alone cannot prove the user did not subsequently take ownership of the route | Ownership and reversibility policy |
| Cleanup while incumbent is absent | Retain recovery state and report residual configuration; retry when the incumbent is available | Removal cannot be truthfully guaranteed against an unavailable tool | Truthful-reporting policy |
| Split ownership | One hostname has one authoritative ingress for all protocols it advertises; Sandbox never divides HTTP and HTTPS for that hostname across products | Split ownership makes redirects, certificates, health, attribution, and rollback ambiguous | Industry ingress practice; conservative ownership policy |
| Partial conflict | Select an ingress capable of every protocol requested for the hostname; otherwise use the per-port URL. A listener on an unrequested protocol is reported but does not justify taking or rewriting it | Capability follows the URL actually promised while preserving every foreign listener | Least-mutation and truthful-reporting policy |
| Incompatible explicit hostname/TLD | Preserve it and fall back; never silently rewrite project identity. An interactive command may offer an explicit migration owned by spec B | Silent hostname changes alter WordPress URLs, callbacks, and stored content | Backward compatibility and explicit-mutation policy |

## Open Questions

- None.

## Acceptance Outcomes

- On a machine where a supported incumbent holds :80, creating an instance and
  requesting a clean URL results in an HTTP response served for that hostname by
  that incumbent, and the Sandbox proxy is not running.
- On that same machine, every service the incumbent served before the operation is
  still served after it.
- On a machine with nothing on the required :80/:443 endpoints, Sandbox's own Caddy is
  selected, existing persisted hostnames keep serving, and a new unpinned instance serves
  the standards-safe hostname supplied by spec B.
- Destroying an instance removes its unchanged Sandbox-owned route from every available
  incumbent and leaves every other route unchanged; unavailable or drifted routes produce
  an explicit incomplete-cleanup result and retained recovery record.
- Uninstalling Sandbox removes every unchanged owned route it can verify, modifies no
  user-authored configuration, and explicitly lists any residual route it could not safely
  remove.
- Running any adoption action twice in a row produces the same end state as running
  it once, with no error on the second run.
- When adoption is impossible, the reported reason names the conflicting endpoint
  and, where the operating system permits identification, its process or product; no
  port-conflict situation produces a message attributing the failure to Docker being
  unavailable.
- The instance is created and reachable at its per-port URL after every
  ingress-adoption failure above, assuming instance creation itself succeeds.
- A user can list which ingresses this build can adopt and what each requires.
- Sandbox reports which ingress currently serves each instance's clean URL.
- An explicitly pinned ingress is always the one used, or the reason it cannot be
  is reported; detection never silently overrides a pin.
- Where an adopted incumbent cannot provide TLS or wildcard hostnames for an
  instance that needs them, that limitation is reported for that instance rather
  than surfacing a URL that does not work.
- No operation prompts for credentials when there is no terminal; the same
  operation from a non-interactive caller completes and reports the ingress as
  pending credentials.
- Credentials supplied for an incumbent never appear in command output, logs, or
  any file tracked by git.
- Detecting a product whose configuration Sandbox does not write into leaves that
  product's files unmodified, verifiable by comparing them before and after.
- A change to an instance's per-port target updates its unchanged Sandbox-owned route;
  a foreign hostname collision or externally changed route is refused and reported.
- If an incumbent disappears, status reports the clean URL unhealthy; uninstall or
  destroy reports any route it could not remove and preserves a retryable recovery
  record rather than claiming complete cleanup.
- A non-interactive first adoption with no recorded consent performs no incumbent
  mutation, and an explicit user action can later grant consent or reconsider a
  remembered decline.
- HTTP-only and HTTPS-capable incumbents are reported and verified according to their
  declared capabilities; a :443-only conflict is not collapsed into a generic port
  error.
- Each named product has a visible support tier, and live acceptance evidence covers
  every product advertised as adoptable or credential-pending.
- A pre-existing invalid configuration, candidate validation failure, or reload/health
  failure leaves the incumbent's prior configuration restored, its previously healthy
  routes healthy, and the Sandbox instance available at its per-port URL.
- When project and machine-local ingress pins differ, the machine-local override is used
  and status identifies the effective pinned source.

## Risks and Assumptions

- **Risk**: Writing into a user's system web server configuration can break sites
  they depend on. Mitigated by attribution, refusal on collision, consent, and
  verified reversibility — but the blast radius is the user's machine, not
  Sandbox's own directories, so this is the feature's dominant risk.
- **Risk**: Reloading a system service to apply a route is disruptive if the
  service's configuration is already invalid for unrelated reasons; Sandbox could be
  blamed for a pre-existing fault.
- **Risk**: Breadth without depth. Ten incumbents each verified only by reading
  documentation would produce a feature that appears to support everything and
  works nowhere. Constitution IV (live-stack proof per incumbent) is the control,
  and it makes breadth expensive — incumbent support should be staged and each
  stage proven.
- **Risk**: Incumbents change their configuration interfaces between versions,
  so adoption can break on upgrade of a tool Sandbox does not control.
- **Risk**: Detection misidentifies an incumbent and Sandbox writes a route into
  the wrong tool. Mitigated by requiring positive identification before adoption
  and treating uncertainty as not-adoptable.
- **Risk**: Holding credentials for a user's ingress widens what a compromised or
  careless Sandbox run can reach — those credentials often administer routing for
  every service on the machine, not just Sandbox's. Mitigated by the existing
  secrets policy, but the exposure is larger than Sandbox's own state.
- **Risk**: A product that is detect-only today may be read as unsupported. The
  distinction between "detected and reported" and "adopted" must be visible in the
  product's own listing of supported ingresses, or the scope decision will read as
  a bug.
- **Assumption**: Users want their existing ingress to keep serving its existing
  sites, and would rather Sandbox join it than replace it.
- **Assumption**: The hostname already resolves to the local machine, or spec B
  arranges it. This feature's outcomes are not observable without that.
- **Assumption**: The one-time consent pattern already used for privileged host
  actions is acceptable to users for this class of change as well.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed or explicitly accepted as grounded product
      policy.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
