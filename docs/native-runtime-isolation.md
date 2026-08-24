# Native runtime isolation and recovery

Sandbox keeps Compose as the default WordPress runtime. Native execution is opt-in from a
gitignored machine override and is never selected from detection alone. `./sb native
support --json` is the authoritative support advertisement.

## Runtime classes

| Mode | Initial adapters | Isolation claim | Adoption rule |
|---|---|---|---|
| `compose` | Compose | container runtime | default; existing compatibility path |
| `managed_native` | Ubuntu 24.04 systemd-nspawn | `managed_container` | only after every effective isolation gate and live hostile proof passes |
| `incumbent_native` | Herd, official Valet, declared POSIX | `trusted_shared_host` | explicit selection and user-supplied database; never safe for hostile project code |
| detect-only | Local, XAMPP, Laragon, WAMP | none | inspection only; cannot be adopted |

An adapter that is implemented but lacks current live evidence remains
`implemented_unproven`, returns a non-mutating blocked result, and is not adoptable.

## Managed Ubuntu boundary

Each managed instance owns a deterministic machine identity, a fixed-size/inode ext4 image,
a private UID range, a dedicated systemd unit/cgroup, and a unique point-to-point veth.
Project source is resolved against allowed roots and mounted read-only. Only individually
declared writable subpaths are rebound writable. Host home, Sandbox control state, sibling
projects/images, Docker/systemd/SSH-agent sockets, and host devices are not mounted.

The start sequence is fail closed:

1. Install and digest-bind the policy and AppArmor profile.
2. Create, bootstrap, configure, and unmount the owned image.
3. Start only container init with project services masked.
4. Install default-deny networking and observe the effective boundary.
5. Verify namespaces, mounts, capabilities, seccomp, AppArmor, resources, descriptors, and
   reachability.
6. Inject instance-only credentials out of band.
7. Start MariaDB on its private Unix socket and bootstrap only the owned databases.
8. Start PHP-FPM, the selected private web backend, and the inert cron service; an
   isolated WordPress runner is installed only when `wpCron.enabled` is true.
9. Persist ready ownership only after all previous gates pass.

Every web, optional WordPress cron, WP-CLI/eval, arbitrary exec, Composer, activation, PHPUnit, and durable-job
path goes through the same policy-digest gateway. If the gateway or an effective observation
is unavailable, Sandbox does not run the payload on the host or fall back to Compose.

## Network policy

The guest has no default route, forwarding, NAT, host resolver, or public DNS path. Host,
sibling, loopback, RFC1918/ULA, link-local, metadata, control, and undeclared public traffic
are denied. Host-to-guest traffic is limited to the exact private backend address and port.

An egress grant is immutable, instance-owned, expiring, revocable, and countered. Fixed
public CIDR grants include exact TCP ports. Hostname grants allow HTTPS only and require a
rebinding-safe broker with pinned public addresses and exact hostname validation. A grant is
kept fail closed unless the broker, peer binding, firewall rule, counters, and revocation
behavior are all observed. Broad routes such as `0.0.0.0/0`, private/special destinations,
and metadata addresses are invalid even when explicitly written.

## Package transaction

`./sb native install-plan --project-dir . --json` simulates exact candidates and dependency
closures from the host's configured, signed Ubuntu Noble archive sources. The plan lists
versions, service effects, owned roots, privileged actions, and a confirmation digest.

`./sb native install` requires a current interactive TTY and a fresh matching simulation.
MCP, CI, and redirected input remain `pending_confirmation` with zero mutation. The trusted
helper accepts only fixed verbs and fixed owned roots. Image package scripts run with service
start suppression. Foreign host service active/enabled state and configuration/data digests
must match before and after the transaction.

## Credentials and secrets

Project configuration stores references, never credential bytes. Machine-local values belong
in gitignored local configuration. A credential is staged in an owner-only fixed directory,
validated without following links, transferred through a fixed helper verb, installed under
the guest's private `/run/credentials/sandbox/`, and removed from staging in a `finally`
path. Secret bytes never appear in argv, environment, helper output, ownership state, status,
recovery records, or evidence.

## Incumbent limitations and route ownership

Herd, Valet, and declared POSIX profiles share the host with trusted project code. Their
status must say `trusted_shared_host`; they cannot be promoted to managed-container
isolation. Runtime C reports a document root/private backend and execution capabilities but
makes no hostname, DNS, TLS, link, or proxy mutation. Ingress A owns route/link/TLS lifecycle,
and resolver B owns local name resolution. Incumbent databases are explicit user-supplied
references.

## Cleanup and incident recovery

Destroy compares observed state with the last applied digest and removes only unchanged
C-owned objects, in dependency-safe order: services, databases, network/broker state,
machine, mounts, image, AppArmor/policy, then ownership state. Shared host packages are never
uninstalled. A route is cleaned independently by ingress A.

Foreign collisions, changed owned bytes, unavailable observers, or partial failures produce
a non-secret `cleanup_incomplete` recovery record before registry/local identity is removed.
`./sb native status --json` exposes residuals and `./sb native cleanup --json` retries them.
Repeated cleanup is idempotent. Never manually delete a residual until its owner/digest has
been reviewed.

## Evidence required before adoption

The Ubuntu evidence set must be collected on a normally booted Ubuntu 24.04 host with systemd
255+, cgroup v2 delegation, AppArmor enforcement, seccomp, nspawn, bubblewrap, and nftables.
It includes nginx and Apache lifecycle, foreign-package/service coexistence, every hostile
entry path, resource exhaustion, exact egress grant and live revocation, repeated/drifted
cleanup, timing bounds, Compose regression, and the end-to-end quickstart. Fixture-only or
nested-container output cannot make an adapter adoptable.

See `specs/039-native-runtime-adoption/evidence/README.md` for the current evidence index.
