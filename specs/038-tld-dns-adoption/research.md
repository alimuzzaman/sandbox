# Research: TLD and DNS Adoption

## Decision 1: Route scoped queries; never replace a managed resolver

**Decision**: Detect the effective owner first. For systemd-resolved, configure a
route-only domain on the loopback link to a Sandbox authority. For NetworkManager using
resolved, use the same resolved path. Never replace its `/etc/resolv.conf` symlink or
change NetworkManager's global DNS mode.

**Rationale**: `resolvectl` exposes per-link DNS servers, route-only domains, and revert;
the host's current Ubuntu 24.04 installation uses the resolved stub symlink. Routing is a
scoped extension point and survives alongside unrelated upstream/search-domain state.

**Alternatives considered**: Rewriting `/etc/resolv.conf` was rejected as takeover;
installing an NSS plugin was rejected as broader and harder to reverse; `/etc/hosts` is
kept only as an exact-name fallback.

## Decision 2: Use one non-forwarding dnsmasq authority on an unprivileged endpoint

**Decision**: Run a user-owned dnsmasq process from generated configuration below
`$SANDBOX_HOME/runtime/network/`. Select a free loopback address/port from a bounded
range, bind both UDP and TCP, disable upstream forwarding and hosts-file loading, and
serve only owned exact names/zones. Reference-count zones and stop the process after the
last binding is removed.

**Rationale**: Routed resolvers select an answerer but do not synthesize records. dnsmasq
is already a Sandbox dependency, supports exact and suffix answers, and avoids writing a
custom DNS protocol implementation. A high port lets the authority run unprivileged;
resolved and macOS scoped resolver configuration can target a non-default port.

**Alternatives considered**: Binding a second service to port 53 risks incumbent
collision and privilege; forwarding unrelated queries would turn Sandbox into a general
resolver; a handwritten DNS server adds protocol and security risk; one process per
instance wastes resources and complicates wildcard ownership.

## Decision 3: Separate observation, selection, planning, and mutation

**Decision**: Adapters implement read-only `observe`, pure `plan`, guarded `apply`,
`status`, and guarded `cleanup`. The application service re-observes immediately before
mutation and compares the plan precondition fingerprint.

**Rationale**: Resolver/network ownership can change between commands. A single boolean
such as `_resolver_present()` cannot express owner, bind collision, consent, drift, or
actual answer. Transaction objects make non-interactive dry runs and recovery explicit.

**Alternatives considered**: Branching directly in CLI handlers repeats policy across
CLI/MCP; marker-file-only ownership cannot distinguish drift; optimistic overwrite is
unsafe for shared networking.

## Decision 4: Exact records first; shared zones only for declared wildcard needs

**Decision**: Create exact names unless subdomain multisite or another declared capability
requires a local wildcard. Zone ownership is a set of project-root/label owners. Publicly
delegated names are verify-only and never enter authority configuration.

**Rationale**: This minimizes namespace shadowing. A shared `.test` zone is justified only
when the selected adapter itself is suffix-oriented or a wildcard capability is required;
cleanup removes it only after its final unchanged owner leaves.

**Alternatives considered**: A permanent `*.test` wildcard is simple but unnecessarily
broad; per-subdomain hosts entries cannot support arbitrary multisite names.

## Decision 5: Preserve persisted identity and change only the new default

**Decision**: Treat an existing registry/local-state hostname as persisted identity.
Otherwise use explicit project hostname/TLD, then `.test` as the default. Reject new
`.local`; preserve existing `.local` as a legacy incompatible identity with per-port
fallback. Never rewrite a persisted `.tst` hostname during ordinary lifecycle calls.

**Rationale**: `.test` is reserved for testing by RFC 6761; `.local` belongs to mDNS under
RFC 6762. WordPress stores absolute URLs, so silent migration is data mutation, not a DNS
preference.

**Alternatives considered**: Migrating all `.tst` names would break stored URLs;
continuing `.tst` for new projects misses the standards-safe default; accepting `.local`
creates platform conflicts.

The legacy loader currently normalizes an omitted `tld` to `tst`, erasing provenance.
Implementation therefore introduces a common naming config provider that records whether
hostname/TLD was omitted, project-pinned, or machine-overridden before applying defaults.
It must be consumed by both the WordPress and generic Compose descriptor paths. Merely
changing `sandbox_core.DEFAULTS["tld"]` is explicitly rejected.

## Decision 6: Proof-gate adapter advertising

**Decision**: The adapter manifest separates implementation state from advertised support
tier and records the required evidence profile. An adapter without matching live evidence
reports detect-only/implemented-unproven and cannot be selected automatically.

**Rationale**: Constitution IV requires live host proof. Resolver mocks cannot establish
that unrelated internet/VPN/search resolution or incumbent reload behavior remains intact.

**Alternatives considered**: Advertising from unit tests alone violates governance;
hard-coding all implementations as adoptable creates false safety claims.

## Decision 7: Keep privilege narrow and non-interactive-safe

**Decision**: A fixed helper validates suffixes, loopback endpoints, owned fragment paths,
and exact actions for resolved/NetworkManager/macOS/hosts integration. The CLI gathers
interactive consent before invoking it. MCP/CI may observe and plan but returns pending
consent/privilege without prompting or mutation.

**Rationale**: The existing helper/sudoers pattern avoids hanging automation. Resolver
mutation affects the whole host and must not accept arbitrary commands or paths.

**Alternatives considered**: General passwordless `resolvectl`, `nmcli`, or file-write
rights are too broad; piping passwords is prohibited; silently falling back after a pin
would violate explicit intent.

## Decision 8: Cleanup identity survives instance deletion

**Decision**: The domain service captures and executes cleanup before registry/local
instance removal when possible. Any incomplete result is committed to its dedicated
versioned recovery repository before the project registry entry is removed. Recovery keys
contain canonical owner identity and last-applied evidence independent of the deleted
instance block.

**Rationale**: The legacy delete path removes local and registry identity before DNS
cleanup. Resolver disappearance or drift would then leave no trustworthy retry context.

**Alternatives considered**: Keeping arbitrary DNS fields in the project registry mixes
shared zone lifecycle with instance identity; best-effort cleanup after deletion cannot
satisfy truthful residual reporting.

## Decision 9: Reuse bounded mechanisms, not their insufficient policy

**Decision**: Inject the existing `BoundedProcessRunner` and `HttpProbe`. Add a DNS
endpoint reservation mechanism that covers UDP and TCP on the selected loopback endpoint;
do not reuse the TCP-only `127.0.0.1` allocator unchanged.

**Rationale**: Shared services should own safe execution and HTTP probing. DNS has distinct
transport and address requirements that must be represented explicitly.

**Alternatives considered**: Adapters invoking `subprocess` directly duplicates timeout
and output policy; pretending a TCP reservation proves UDP availability creates a bind
race.

## Primary references

- systemd `resolvectl`: <https://www.freedesktop.org/software/systemd/man/resolvectl.html>
- NetworkManager configuration: <https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/NetworkManager.conf.html>
- Laravel Valet DNS behavior: <https://laravel.com/docs/valet>
- RFC 6761 special-use names: <https://www.rfc-editor.org/rfc/rfc6761>
- RFC 6762 multicast DNS: <https://www.rfc-editor.org/rfc/rfc6762>
- Existing live findings: `docs/cross-platform-support.md`
