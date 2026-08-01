# Product Requirements Draft: Host Ingress Adoption

**Status**: Discovery

**Created**: 2026-08-01

**Last Refined**: 2026-08-01

**Input**: "Host ingress adoption: when another tool already owns :80/:443 (Herd, Valet, system nginx, Apache, system Caddy, Traefik, Nginx Proxy Manager, DDEV router, Local, Laragon, XAMPP/WAMP), Sandbox must detect the incumbent and register its instance routes THROUGH that tool instead of binding the ports itself or failing. Own Caddy container remains the fallback when no incumbent exists. Must be safely reversible and never steal a port it does not own."

**Drafting Model**: `claude-opus-5[1m]` (fallback; `gpt-5.6-terra` Medium not available in this session)

**Final Validation**: `PENDING` — `gpt-5.6-sol` High

**Validated On**: N/A

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

The competing tools have the same gap. DDEV's router owns :80/:443 and its
documented remedy for a conflict is to stop the competing application; its
port-override setting has been reported broken across releases. Local and Laragon
also expect to own the ports and register hostnames by writing the system hosts
file. Adopting an incumbent rather than fighting it is therefore both the missing
capability and a genuine differentiator.

**Why now**: Sandbox is being positioned for use on shared dev servers and on
developer machines that already have a full stack, not only on clean Docker hosts.
Every such machine currently loses clean URLs.

## Users and Desired Outcomes

- **Developer on a machine with an existing web server** (system nginx/Apache,
  Herd, Valet, XAMPP): creates a Sandbox instance and reaches it at its clean
  hostname, without stopping or reconfiguring the server they already depend on.
- **Developer on a machine with an existing container ingress** (Traefik, Nginx
  Proxy Manager, an existing Caddy, a DDEV router): same outcome, with routes
  appearing in the ingress they already operate and observe.
- **Developer whose machine has nothing on :80/:443**: sees no change — Sandbox's
  own proxy continues to serve clean URLs exactly as it does today.
- **Developer whose incumbent cannot be driven automatically**: is told precisely
  which process owns the port and what single action would finish the job, instead
  of receiving a misleading Docker error.
- **Owner of the host machine**: can remove Sandbox and be certain no route,
  virtual host, or config fragment it added is left behind, and that nothing they
  authored themselves was modified or deleted.

## Goals

- Detect which process, if any, currently owns :80 and :443, and identify it as a
  known ingress product where possible.
- Register and unregister Sandbox instance hostnames through a detected incumbent,
  so instances are reachable at clean URLs without Sandbox binding :80/:443.
- Keep the existing Sandbox Caddy proxy as the behavior when no incumbent is
  present, with no change to that path.
- Make every route Sandbox adds attributable to Sandbox and individually removable,
  so adoption is fully reversible.
- Replace the misleading port-conflict failure with an accurate report naming the
  owning process and the available options.
- Let a project or machine pin which ingress to use, overriding detection.

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
- **Expected outcome**: Today's behavior, unchanged: the Sandbox Caddy proxy starts
  and serves the clean URL.

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
- **Expected outcome**: Every route Sandbox added is removed from every incumbent it
  touched, each incumbent reloads, and configuration the user authored is unchanged.

### Scenario 10 — Incumbent requires credentials, first detection, interactive

- **Starting state**: A machine running an ingress that manages routes only through
  an authenticated interface (Nginx Proxy Manager). Sandbox has no credentials for
  it. The developer is at an interactive terminal.
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

## Proposed Product Behavior

- **Detection before action.** Before any attempt to serve clean URLs, Sandbox
  determines whether :80 and :443 are held, and by what. Detection reports one of:
  free, held by a recognized adoptable ingress, held by a recognized non-adoptable
  ingress, or held by an unidentified process. Detection is read-only and never
  changes machine state.

- **Adoption in preference to binding.** When a recognized adoptable incumbent holds
  the ports, Sandbox registers through it and does not start its own proxy. When the
  ports are free, Sandbox uses its own proxy. Sandbox never binds a port it found
  held, and never stops an incumbent to take a port.

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
  offer is remembered and not re-asked.

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
  An explicit pin is never silently overridden by detection.

- **Extensibility as a product property.** The set of supported incumbents grows
  over time. Support for a given incumbent is declared, discoverable, and reportable
  — a user can ask which ingresses this Sandbox build can adopt, and what each
  requires (for example, credentials, or a writable configuration directory).

- **Capability honesty per incumbent.** Incumbents differ in what they can express.
  Where an incumbent cannot provide something Sandbox's own proxy provides — for
  example TLS, or wildcard hostnames for subdomain multisite — Sandbox reports that
  limitation for that instance rather than presenting a URL that will not work.

## Constraints and Dependencies

- **Depends on name resolution (spec B).** A registered route only produces a working
  clean URL if the hostname resolves to the machine. This feature's acceptance is
  therefore stated in terms of the route being served for a given hostname, with
  resolution assumed or arranged out of band.
- **Privileged operations.** Writing into `/etc/nginx`, `/etc/apache2`, or an
  equivalent, and reloading a system service, requires elevated privileges. Sandbox's
  established policy is a single explicit consent step, after which host actions are
  password-free and non-interactive (`sandbox/core/_domains.py:670-690`); privileged
  calls must never block on an interactive password prompt, because they run from
  non-interactive contexts including the MCP server and CI.
- **Credentialed incumbents.** Some ingresses (for example Nginx Proxy Manager)
  expose route management only through an authenticated interface. Sandbox cannot
  adopt these without credentials the user supplies. Credentials are per-machine
  secrets and are subject to the existing secrets policy: they live in
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
- **Backward compatibility.** Machines with no incumbent must see no behavioral
  change. The existing Valet integration must keep working or be superseded by an
  equivalent that is verified before the old path is removed (constitution VI).

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Breadth of incumbent support | All named ingresses, adopting whichever is active at the time | The user's environment determines the incumbent; a partial list leaves the same silent-failure gap on the machines not covered | User |
| Feature split | Ingress adoption is its own feature, separate from TLD/DNS adoption and native runtimes | Port ownership and name resolution fail independently; a machine can have Apache on :80 and systemd-resolved on DNS | User |
| Behavior with no incumbent | Unchanged — Sandbox's own Caddy proxy | Avoids regressing the common clean-Docker-host case | User (input) |
| Port stealing | Never; Sandbox does not bind a port it found held, and never stops an incumbent | Taking a port would break services the user depends on | User (input) |
| Reversibility | Every Sandbox-created route is attributable and individually removable | Adoption writes into services the user owns; it must be undoable | User (input) |
| Failure posture | Degrade to per-port URL, never block instance creation | Matches the existing clean-URL posture, which is an opt-in upgrade over a working per-port URL | Existing policy (`sandbox/core/_domains.py:770-773`) |
| Collision with a foreign route | Refuse and report; never overwrite | Overwriting a route Sandbox did not create could take down a user's service | Existing policy (proxy-helper declines rather than stealing :53) |
| Consent model | One-time explicit confirmation per incumbent per machine, remembered | Mirrors the established one-time-sudo/declined-offer pattern | Existing policy (`sandbox/core/_domains.py:714-751`, `:203-213`) |
| Detection side effects | Read-only | Detection runs on ordinary status commands; it must be safe on any machine | Existing policy (`sb doctor` health checks are read-only) |
| Windows-only incumbents | Out of scope natively; WSL2-side incumbents are in scope, and a Windows-side holder of the port is detected and reported | Sandbox has no native Windows entry point; porting it is a larger change than this feature and a prerequisite to it | User; repository evidence (`docs/cross-platform-support.md` §5) |
| Non-scriptable incumbents | Detect, report precisely, fall back to the per-port URL. Sandbox does not write into a product's private configuration | Reverse-engineered writes break on the product's own updates and risk corrupting the user's other sites — a worse outcome than an honest, actionable message | User |
| Credentialed incumbents | Supported. On first detection at an interactive terminal Sandbox offers to take credentials; non-interactive callers are never prompted and the ingress is reported as pending credentials | Improves discovery where a human is present, while honoring the existing rule that privileged/interactive paths must never block the MCP server or CI | User; existing policy (`sandbox/core/_domains.py:717-721`) |
| Declining a credential offer | Remembered per incumbent per machine; not re-asked | Matches the existing declined-offer marker for the HTTPS upgrade | Existing policy (`sandbox/core/_domains.py:203-213`) |

## Open Questions

- None.

## Acceptance Outcomes

- On a machine where a supported incumbent holds :80, creating an instance and
  requesting a clean URL results in an HTTP response served for that hostname by
  that incumbent, and the Sandbox proxy is not running.
- On that same machine, every service the incumbent served before the operation is
  still served after it.
- On a machine with nothing on :80/:443, the observable result of creating an
  instance and requesting a clean URL is identical to the current release.
- Destroying an instance leaves the incumbent with no route for that instance's
  hostname, and with every other route unchanged.
- Uninstalling Sandbox leaves no Sandbox-created route in any incumbent, and no
  user-authored configuration modified.
- Running any adoption action twice in a row produces the same end state as running
  it once, with no error on the second run.
- When adoption is impossible, the reported reason names the process holding the
  port; no port-conflict situation produces a message attributing the failure to
  Docker being unavailable.
- The instance is created and reachable at its per-port URL in every failure case
  above.
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
- **Assumption**: A single machine has at most one ingress that meaningfully owns
  :80/:443 at a time; where several are installed, only the running one matters.
- **Assumption**: The one-time consent pattern already used for privileged host
  actions is acceptable to users for this class of change as well.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent Sol High validation verdict is `PASS`. — not run;
      no `gpt-5.6-sol` High reviewer is exposed in this session.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
