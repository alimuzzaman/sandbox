# Product Requirements Draft: TLD and DNS Adoption

**Status**: Discovery

**Created**: 2026-08-01

**Last Refined**: 2026-08-01

**Input**: "TLD and DNS adoption: detect the host name-resolution owner and register Sandbox wildcard hostnames through it instead of replacing or fighting it; preserve explicit project identity, use standards-safe defaults, keep changes attributable and reversible, and degrade to per-port URLs when safe automatic registration is unavailable."

**Drafting Model**: `gpt-5.6-sol` High (fallback; active root model cannot be switched to preferred `gpt-5.6-terra` Medium)

**Final Validation**: PASS — gpt-5.6-sol High

**Validated On**: 2026-08-01

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Sandbox clean URLs currently couple name resolution to Sandbox's own proxy setup.
On macOS it writes a per-suffix resolver and uses dnsmasq; on Linux it runs its own
dnsmasq and replaces a plain `/etc/resolv.conf`. The Linux helper deliberately declines
when `/etc/resolv.conf` is managed by systemd-resolved or NetworkManager, or when another
process owns `127.0.0.1:53` (`docs/cross-platform-support.md` §4;
`tools/proxy-helper.sh:82-116`). Those are safe refusals, but they exclude common desktop
Linux configurations and produce no adoption path through the resolver already in charge.

The current product also treats the suffix and its destination as mostly independent.
Each project can persist a TLD, with `.tst` as the historical default, and current DNS
always points it at `127.0.0.77` (`sandbox/core/_paths.py:207-210`). That is correct for
Sandbox's own Caddy proxy but wrong for an adopted ingress that listens only on another
loopback address. Spec A cannot promise a clean URL until spec B supplies a hostname and
an address actually served by the selected ingress.

The naming policy needs a standards baseline. `.test` is reserved for testing by RFC
6761, while `.local` is reserved for multicast DNS by RFC 6762. Existing `.tst` projects
cannot be renamed silently because WordPress stores absolute URLs and integrations may
depend on them, but new unpinned projects should not create another private pseudo-TLD by
default.

The product need is therefore adoption AS AN ADDITION, not a replacement of the working
path and not a DNS takeover: keep Sandbox's own scoped bootstrap as the default provider,
and additionally identify the active resolver owner, use its supported extension point when
the user opts in, preserve foreign configuration, and report exact limitations when safe
registration is unavailable. (2026-08-02 decision: the Docker/Caddy stack and its DNS remain
the default on every platform and runtime; adoption is selectable at setup and switchable on
demand.)

## Users and Desired Outcomes

- **Linux desktop developer using systemd-resolved or NetworkManager**: gets a clean
  hostname without replacing the resolver-managed `/etc/resolv.conf`.
- **macOS developer using Herd or Valet**: uses the incumbent's `.test` resolution and
  does not start or reload a competing dnsmasq.
- **Developer with an existing dnsmasq**: adds a Sandbox-owned suffix rule through a
  supported include mechanism without changing unrelated DNS behavior.
- **Developer with an explicit project hostname or suffix**: keeps that identity unless
  they explicitly request a migration.
- **Developer on a clean supported host**: receives standards-safe `.test` names for new
  unpinned projects and the existing Sandbox-managed resolver fallback.
- **Developer whose resolver cannot be adopted**: keeps a working per-port URL and sees
  which resolver owns name resolution and why a clean hostname was not installed.
- **Host owner**: can destroy an instance or uninstall Sandbox without leaving resolver
  rules, exact host entries, or altered foreign configuration behind.

## Goals

- Keep Sandbox's own scoped resolution bootstrap as the default provider, ungated by
  adapter proof tiers, so clean URLs work with zero adoptable adapters.
- Let a user select the resolution provider at setup and switch it on demand, per project
  or per machine, without reprovisioning or renaming.
- Detect the active local name-resolution mode and its owner before mutation.
- Resolve every Sandbox hostname to an address accepted by the ingress selected under
  spec A.
- Register and remove Sandbox-owned wildcard or exact-name rules through documented
  resolver extension points.
- Preserve explicit existing hostnames/TLDs and migrate them only through a separately
  confirmed operation.
- Use `.test` for new unpinned local projects while keeping existing `.tst` projects
  compatible.
- Make name-resolution capability, ownership, health, and remediation visible.
- Keep every change idempotent, attributable, drift-aware, and recoverable.

## Non-Goals

- HTTP/TLS route registration or port ownership; spec A owns ingress.
- Running WordPress through native PHP/database/web-server stacks; spec C owns runtimes.
- Public or remote-host DNS. Existing remote hosting and Cloudflare workflows own public
  FQDNs and DNS records.
- Installing, replacing, stopping, or globally reconfiguring a user-owned resolver.
- Managing VPN, enterprise search domains, DHCP, DNSSEC policy, encrypted DNS, or upstream
  resolver selection.
- Native Windows resolver mutation. WSL2 is supported as Linux; Windows-side DNS remains
  outside the current platform.
- Silently renaming existing WordPress sites or rewriting their stored URLs.

## Product Scenarios

### Scenario 1 — systemd-resolved owns local DNS

- **Starting state**: `/etc/resolv.conf` points to systemd-resolved and no Sandbox rule
  exists for the selected suffix.
- **User action**: The developer enables a clean URL for an instance.
- **Expected outcome**: Sandbox identifies systemd-resolved, registers only the selected
  local namespace as a route to a scoped Sandbox-owned answering authority, verifies that
  the authority supplies the selected ingress address, and leaves unrelated DNS unchanged.

### Scenario 2 — NetworkManager with dnsmasq

- **Starting state**: NetworkManager owns DNS and runs its dnsmasq plugin.
- **User action**: The developer requests a clean hostname.
- **Expected outcome**: Sandbox uses NetworkManager's supported DNS extension/reload path,
  does not create a competing listener on port 53, and the name resolves after bounded
  cache refresh.

### Scenario 2a — NetworkManager delegates to systemd-resolved

- **Starting state**: NetworkManager owns network configuration but delegates DNS to
  systemd-resolved.
- **User action**: The developer requests a clean hostname.
- **Expected outcome**: Sandbox follows the systemd-resolved authority path rather than
  writing a dnsmasq rule or switching NetworkManager's global DNS mode.

### Scenario 3 — Herd or Valet supplies `.test`

- **Starting state**: Herd or Valet is the selected ingress/runtime and already provides
  `.test` resolution.
- **User action**: A new unpinned project is ensured.
- **Expected outcome**: Sandbox uses the incumbent-provided `.test` hostname, makes no
  competing resolver change, and verifies the resolved address is served by that
  incumbent.

### Scenario 4 — Existing `.tst` project

- **Starting state**: A project already has a persisted `example.tst` identity.
- **User action**: The project is re-ensured after this feature ships.
- **Expected outcome**: The hostname remains `example.tst`. Sandbox either registers it
  safely with the selected resolver or uses the per-port URL and reports incompatibility;
  it never silently changes the site to `.test`.

### Scenario 5 — New clean host

- **Starting state**: No incumbent resolver extension is in use and Sandbox can safely
  provide its managed fallback.
- **User action**: A new unpinned project requests a clean URL.
- **Expected outcome**: The project receives a `.test` hostname, Sandbox's resolver
  fallback answers it at the selected ingress address, and ordinary internet resolution
  remains unchanged.

### Scenario 6 — Explicit custom suffix conflicts with incumbent

- **Starting state**: The project explicitly pins a suffix that the selected incumbent
  cannot serve.
- **User action**: The developer ensures the instance.
- **Expected outcome**: Sandbox preserves the explicit identity, performs no partial DNS
  mutation, reports the incompatibility, and uses the per-port URL. At an interactive
  terminal it may offer a separately confirmed migration to a compatible hostname.

### Scenario 7 — `.local` requested

- **Starting state**: A user tries to configure a new `example.local` hostname.
- **User action**: The configuration is validated or applied.
- **Expected outcome**: Sandbox refuses the new local-DNS mapping because `.local` is an
  mDNS-reserved namespace and recommends `.test`. Existing persisted `.local` sites are
  reported as legacy conflicts and are not silently renamed.

### Scenario 8 — Foreign rule collision

- **Starting state**: The active resolver already has a rule for the requested exact
  hostname or suffix that Sandbox did not create.
- **User action**: Sandbox tries to enable clean URLs.
- **Expected outcome**: Sandbox leaves the foreign rule unchanged, reports its observed
  destination, and falls back rather than shadowing or overwriting it.

### Scenario 9 — Resolver or network changes

- **Starting state**: Sandbox registered through one resolver, then the user changes
  networks, VPN state, or resolver owner.
- **User action**: Status or ensure runs.
- **Expected outcome**: Sandbox detects the current owner and actual lookup result,
  reports stale or missing prior integration state, and requires consent before adopting
  a different user-owned resolver.

### Scenario 10 — Non-interactive first use

- **Starting state**: Registration requires first-use consent or privilege and the caller
  has no terminal.
- **User action**: MCP or CI requests a clean hostname.
- **Expected outcome**: Sandbox never prompts or blocks, makes no resolver mutation, and
  returns pending-consent guidance with the working per-port URL.

### Scenario 11 — Subdomain multisite

- **Starting state**: A WordPress network needs arbitrary subdomains beneath its primary
  local hostname.
- **User action**: The network is enabled for clean URLs.
- **Expected outcome**: Sandbox uses a wildcard-capable resolver path or reports that the
  active path supports exact names only. It never advertises subdomain multisite as ready
  when arbitrary subdomains do not resolve.

### Scenario 12 — Cleanup after drift or disappearance

- **Starting state**: A Sandbox-owned resolver rule was edited externally or its resolver
  is no longer running.
- **User action**: The instance is destroyed or Sandbox is uninstalled.
- **Expected outcome**: Changed rules are not removed automatically; unavailable cleanup
  leaves a visible residual record and retry guidance. Unchanged owned rules are removed
  without touching other namespaces.

## Proposed Product Behavior

- **Observe first.** Name-resolution detection is read-only and distinguishes the active
  owner, resolution source, supported extension point, current answer, and whether a rule
  is Sandbox-owned, foreign, or unknown.
- **Ordered A/B handoff.** Spec A first detects/selects an ingress without activating a
  hostname route and supplies its acceptable local listener addresses and naming/TLS
  capabilities. Spec B then chooses or preserves the hostname and installs/verifies
  resolution to one accepted address. Spec A finally activates the hostname route and
  performs end-to-end health verification. Runtime spec C supplies only the backend
  endpoint consumed by A.
- **Standards-safe default.** New projects without a persisted or explicit hostname use
  `.test`. Existing persisted suffixes remain unchanged. New `.local` names are refused;
  publicly delegated suffixes and HTTPS-mandatory names require explicit, compatible
  ownership and TLS rather than being treated as ordinary local pseudo-TLDs.
- **Explicit identity wins.** A project-pinned hostname/TLD is never rewritten by
  detection. Incompatibility degrades to the per-port URL. Migration is a distinct,
  reviewed mutation that updates both resolution and WordPress identity or rolls back.
- **Incumbent before fallback.** Sandbox adopts the active resolver only through a
  documented extension point. It uses its own DNS fallback only when no incumbent owns
  the relevant path and doing so does not replace resolver-managed state or steal port 53.
- **Scoped answering authority for routed DNS.** A resolver that only routes queries,
  including systemd-resolved, is paired with a Sandbox-owned local authority that answers
  only declared Sandbox exact names/zones and does not become the machine's upstream
  resolver. Its listener is collision-checked, attributable, health-checked, and stopped
  when no owned zone uses it. If no safe endpoint can be registered, Sandbox falls back to
  exact hosts mapping or the per-port URL.
- **Least-wide rule.** Exact-name registration is preferred when it satisfies the
  instance. A wildcard rule is used only for a declared suffix that needs it, especially
  subdomain multisite, and never for a public suffix or broader namespace.
- **One-time consent.** First mutation of each user-owned resolver on a machine requires
  interactive confirmation. A decline is remembered; non-interactive callers return
  pending consent. Reconsideration is explicit.
- **Owned fragments, not shared-file rewrites.** Every rule is attributable to Sandbox and
  an instance or suffix. Sandbox modifies/removes only unchanged state it previously
  wrote; drift triggers reconciliation instead of overwrite.
- **Truthful fallback.** DNS adoption failure never blocks instance provisioning. Status
  reports the resolver owner, requested hostname, actual answer, expected ingress address,
  capability gap, and per-port URL.
- **Pinned strategy.** Machine or project configuration may pin an allowed resolver
  strategy or disable clean hostnames. A missing/unusable pin is reported and never
  silently replaced by detection. A machine-local project override wins over a committed
  project pin, matching the repository's global → project → machine-override merge order.
- **Public names are consumed, not overridden.** For an explicit publicly delegated FQDN,
  spec B creates no local wildcard or shadowing rule. It only verifies externally managed
  resolution already reaches an accepted ingress address; otherwise it preserves the
  identity and uses the per-port URL.

## Constraints and Dependencies

- systemd-resolved supports per-interface DNS servers and route-only domains and can
  revert per-interface state; transient state may disappear with the interface
  ([official `resolvectl` manual](https://www.freedesktop.org/software/systemd/man/resolvectl.html)).
- A systemd-resolved route-only domain selects an answering DNS server; it does not itself
  synthesize wildcard records. The Sandbox-owned authority lifecycle is therefore part of
  this product path, not an optional implementation detail.
- NetworkManager supports resolver modes including dnsmasq and systemd-resolved,
  configuration snippets, and a DNS-only reload; changing DNS can briefly interrupt
  resolution ([official NetworkManager reference](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager.conf.html)).
- Valet provides `.test` resolution and wildcard subdomains for linked/parked sites
  ([official Valet documentation](https://laravel.com/docs/valet)).
- DDEV's `.ddev.site` is public wildcard DNS to `127.0.0.1` and may fall back to exact
  hosts entries; it is not treated as ownership of the host resolver
  ([official DDEV troubleshooting](https://ddev.readthedocs.io/en/stable/users/usage/troubleshooting/)).
- `.test` and `.local` policy follows RFC 6761 and RFC 6762 respectively.
- Privileged actions follow existing passwordless, non-interactive-safe Sandbox policy;
  no operation may wait for a password from MCP or CI.
- The resolver destination is not a universal constant; it depends on the selected
  ingress and its bind scope.
- Public DNS remains governed by existing managed-hosting features and their explicit
  plan/apply/rollback policy.
- Constitution IV requires live lookup and HTTP verification on every resolver path
  advertised as adoptable; mocks alone are insufficient.
- Constitution V requires idempotency and docs-with-code.

## Resolver Coverage Policy

| Resolver environment | Initial tier | Boundary |
|----------------------|--------------|----------|
| Sandbox-managed macOS resolver + dnsmasq | DEFAULT provider | Preserve existing behavior unchanged, with `.test` for new unpinned projects. Not gated by adapter proof tiers. |
| Sandbox-managed Linux dnsmasq with plain resolv.conf | DEFAULT provider | Preserve existing behavior unchanged. Declines only when an incumbent manages resolv.conf or port 53, and then reports the selectable adoption options. |
| systemd-resolved | Adoptable with Sandbox authority | Route only the owned namespace to a scoped Sandbox answerer; do not replace its resolv.conf symlink. |
| NetworkManager with systemd-resolved | Adoptable with Sandbox authority | Preserve NetworkManager's mode and use the resolved route/authority path. |
| NetworkManager with dnsmasq | Adoptable through incumbent | Add only an owned rule through the active dnsmasq extension and scoped DNS reload; do not start another port-53 listener or switch global mode. |
| Existing standalone dnsmasq | Conditionally adoptable | Only when an enabled include directory and safe scoped reload are positively identified. |
| Herd / Valet DNS | Adoptable as incumbent | Use the incumbent's provided `.test` namespace without a competing resolver. |
| Exact hosts-file mapping | Supported exact-name fallback | Requires existing one-time privilege consent; insufficient for wildcard multisite. |
| DDEV public wildcard DNS | Externally resolved | May be selected explicitly when its fixed destination matches the selected ingress; Sandbox does not own or remove the public record. |
| Pi-hole, AdGuard Home, enterprise DNS, VPN-owned DNS | Detect-only initially | Report ownership; no private API, appliance, or enterprise-policy mutation in this feature. |
| Windows-side resolver from WSL2 | Outside platform | Detect/report where possible; do not mutate across the boundary. |

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Default suffix for new unpinned projects | `.test` | Standards-reserved for testing and already supported by major local-dev incumbents | RFC 6761; best practice selected by user delegation |
| Existing `.tst` projects | Preserve | Silent renames break stored WordPress URLs and integrations | Backward compatibility |
| New `.local` projects | Refuse | `.local` is reserved for multicast DNS and collides with platform behavior | RFC 6762 |
| Explicit incompatible identity | Preserve and fall back; migration only by separate confirmation | Project identity is more consequential than clean-URL convenience | User delegation; safety policy |
| Resolution destination | Selected ingress address, not a global constant | The hostname must reach the actual listener | Spec A/B contract |
| Resolver selection | Adopt documented active owner; otherwise safe Sandbox fallback or per-port URL | Avoids resolver replacement and private interfaces | Best practice |
| Rule breadth | Exact by default, wildcard only when declared capability requires it | Least-wide namespace mutation | Least privilege |
| Foreign collision | Refuse and report | Never shadow configuration Sandbox does not own | Existing ownership policy |
| Drift | Do not overwrite/remove until explicit reconciliation | A marker alone does not prove continued ownership | Existing ownership policy |
| Non-interactive first use | No prompt or mutation; pending-consent result | Prevents MCP/CI hangs | Existing policy |
| Public/remote DNS | Out of scope | Existing hosting features already own it with stronger approval gates | Repository architecture |
| Support breadth | Core OS resolvers and incumbent developer resolvers first; appliances and enterprise DNS detect-only | Stable documented local control surfaces are required before mutation | Best practice selected by user delegation |
| Routed-resolver answer source | Sandbox-owned authority serving only declared local names/zones, with collision-safe endpoint and reference-counted cleanup | Routing configuration cannot create wildcard records by itself | Primary-source correction |
| A/B ordering | A supplies acceptable listener addresses/capabilities, B selects and resolves the hostname, then A activates the route | Removes circular ownership and makes each verification boundary observable | Cross-feature review |
| Pin precedence | Machine-local project override wins over committed project pin; explicit pins beat detection | Matches existing configuration merge order | Repository policy |
| Publicly delegated FQDNs | Never create a local wildcard/shadow override; consume and verify external resolution only | Public DNS is owned by existing hosting features | Scope boundary |

## Open Questions

- None.

## Acceptance Outcomes

- On systemd-resolved and NetworkManager-with-resolved hosts, enabling a clean hostname
  leaves the managed `/etc/resolv.conf` relationship intact, routes only the owned local
  namespace to the Sandbox authority, and resolves it to the selected ingress address.
- The routed-DNS authority answers no undeclared namespace, forwards no unrelated query,
  starts only on a collision-free local endpoint, survives re-ensure idempotently, and
  stops after its last owned zone is removed.
- On NetworkManager-with-dnsmasq, the owned rule loads through the incumbent plugin with no
  competing listener and unrelated resolution unchanged.
- On Herd/Valet, the same action creates no competing port-53 listener or resolver rule and
  the `.test` hostname resolves and serves.
- A new unpinned project receives a `.test` hostname; an existing `.tst` project retains
  its exact hostname across ensure/apply.
- New `.local` configuration is rejected before mutation with an actionable `.test`
  alternative.
- Existing internet, VPN, search-domain, and unrelated local-name resolution produce the
  same answers before and after each adoption action.
- A foreign exact or wildcard collision is not modified or shadowed and the instance
  remains reachable at its per-port URL.
- Exact-only paths are never reported as sufficient for subdomain multisite; a wildcard-
  capable path resolves an arbitrary new subdomain without adding another rule.
- Non-interactive first use completes without prompting or resolver mutation and reports
  pending consent.
- Repeating add, update, remove, and status actions produces the same end state.
- Destroy removes every unchanged Sandbox-owned exact rule for the instance; removing the
  last user of a Sandbox-owned wildcard removes that wildcard and nothing else.
- Externally changed or unavailable resolver state produces a residual/reconciliation
  report and is not silently overwritten or declared cleaned.
- Status reports requested hostname, resolver owner, actual address, expected ingress
  address, health, ownership, and fallback URL without exposing secrets.
- Every environment advertised as adoptable passes a live DNS lookup followed by a live
  HTTP request through the selected ingress.
- A publicly delegated FQDN produces no local override; it is used only when its external
  answer already targets an accepted ingress address, otherwise the per-port URL remains.
- When project and machine-local resolver pins differ, the machine-local override is
  effective and status reports its source.

## Risks and Assumptions

- **Risk**: Resolver changes can disrupt all network access on the machine. Scoping changes
  through incumbent extension points and refusing global-mode replacement is the dominant
  safety control.
- **Risk**: NetworkManager, systemd-resolved, VPNs, and interface churn can replace
  transient state after successful setup; status must measure current answers, not markers.
- **Risk**: DNS caches can preserve stale answers beyond a configuration change and create
  false verification results.
- **Risk**: Changing the new-project default from `.tst` to `.test` expands interaction
  with Herd/Valet; ingress ownership must be selected before resolver mutation.
- **Risk**: A broad wildcard can shadow names owned by another tool. Least-wide rules and
  collision checks mitigate this.
- **Risk**: Live proof across resolver managers requires distinct host environments and is
  materially more expensive than unit testing.
- **Assumption**: Local clean hostnames are for the current machine; remote/public access
  continues through existing hosting workflows.
- **Assumption**: A supported resolver exposes a stable scoped extension point or can be
  left untouched in favor of exact hosts/per-port fallback.
- **Assumption**: The selected ingress supplies at least one local listener address that
  the resolver may safely return.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed or explicitly accepted as grounded product policy.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: READY FOR SPECKIT

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
