# Research: Native Runtime Adoption

## Decision 1: Managed-native is package-native inside an OS-container boundary

**Decision**: Install and run Ubuntu nginx/Apache, PHP, MariaDB, cron, and tooling inside a
per-instance systemd-nspawn image. Do not run untrusted managed-native PHP as ordinary host
processes, even with separate ports/users.

**Rationale**: The user made hostile-code isolation the priority. Paths, Unix users, and
ports do not hide the host process table, home directories, control sockets, IPC, or
loopback services. systemd-nspawn is Ubuntu's native OS-container mechanism and integrates
with user namespaces, cgroup v2, disk images, private networking, service supervision, and
machine-scoped execution without requiring Docker.

**Alternatives considered**: Bare systemd units cannot by themselves provide one complete
mount/network/PID boundary across durable services and later CLI/test execution; chroot is
not a security boundary; Podman is a valid Docker alternative but duplicates the OCI path
rather than providing the requested first-party Ubuntu package stack; VMs provide stronger
kernel isolation but exceed the Docker-like requirement and cost target.

## Decision 2: Bubblewrap is defense in depth, not the security product

**Decision**: Require bubblewrap 0.9 and use it for CLI, WP-CLI, Composer scripts, tests,
cron jobs, and other one-shot commands inside the nspawn instance. The durable nspawn
boundary remains mandatory for all web/database/service processes.

**Rationale**: Bubblewrap's maintainers describe it as a sandbox construction tool, not a
complete policy. Alone it lacks durable multi-service supervision, cgroup policy, safe
later re-entry, persistent private networking, and disk/inode quota management. Within the
container it provides a clean environment, read-only source mapping, private temp, no-new-
privileges, capability drop, nested-user-namespace disablement, and another PID/mount/IPC/
UTS layer for one-shot commands.

**Alternatives considered**: Treating `bwrap --unshare-all` as sufficient would leave
service lifecycle and later execution inconsistent; using it only for CLI but leaving web
PHP on the host would violate the same-policy requirement.

## Decision 3: Use a private point-to-point veth with explicit firewall policy

**Decision**: Give each instance a unique private network namespace and extra veth pair,
not nspawn's auto-masqueraded default. Configure only a point-to-point subnet. Host ingress
may initiate to the declared web port; new instance-to-host, sibling, forwarding, and
internet connections are dropped. No default route or DNS exists until an egress grant is
applied.

**Rationale**: nspawn's convenient default veth/networkd path may enable forwarding and
masquerading. That conflicts with deny-by-default. A direct veth lets A reach the backend
while nftables state permits replies and denies instance-initiated access.

**Alternatives considered**: Host networking exposes loopback and siblings; Unix sockets
cannot serve the Apache variant through its documented `Listen` directive; a bridge
increases sibling visibility; default nspawn masquerading grants unintended egress.

## Decision 4: Egress is an explicit network capability

**Decision**: Support no-egress, fixed public CIDR+TCP-port grants, and hostname+HTTPS
grants through a Sandbox broker. The host firewall always rejects loopback, link-local,
RFC1918/ULA, metadata, host-veth, sibling, and Sandbox control destinations. Grants have
per-instance nftables sets/counters, expiry/revocation, and status output.

**Rationale**: WordPress may need downloads/API calls, but opening NAT broadly defeats the
boundary. A hostname broker can resolve and authorize public destinations without giving
the instance a general host resolver or private-network path.

**Alternatives considered**: An unrestricted default route is not scoped; injecting host
DNS exposes host policy/services; static IP grants alone do not safely represent changing
HTTPS destinations.

## Decision 5: Fixed-size filesystem images enforce bytes and inodes

**Decision**: Create one fixed-size ext4 image per instance with an explicit inode count,
mounted nodev/nosuid and used for the container root/writable WordPress/database state.
Bind project source read-only by default after resolving every component without symlink
escape; separately declared writable subpaths are individual mounts. Host and sibling
roots are never mounted.

**Rationale**: Directory permissions do not impose byte/inode quotas and filesystem project
quotas are not portable across the initial host matrix. A fixed image gives deterministic
capacity and cleanup ownership.

**Alternatives considered**: Sparse ordinary directories can exhaust the host; tmpfs loses
persistent data; host filesystem quotas are an acceptable future backend only after
explicit proof.

## Decision 6: Layer cgroup, service, syscall, and connection ceilings

**Decision**: Put the entire machine in a dedicated cgroup subtree with CPUQuota,
MemoryHigh/Max, MemorySwapMax, TasksMax, I/O weight/bandwidth, and OOM policy. Apply
RuntimeMaxSec to one-shot units; PHP request/worker timeouts to web/cron; LimitNOFILE to all
payload services; server worker/connection caps and nft connection limits to network
sockets. Apply payload seccomp deny groups, no-new-privileges, minimal address families,
private devices, and zero ambient capabilities. The durable container init retains only
the small bounding set needed to switch service users and bind its web port; high-risk
kernel/host powers such as `SYS_ADMIN`, `NET_ADMIN`, `NET_RAW`, `MKNOD`, tracing, and
reboot are explicitly dropped. One-shot hostile payloads drop the remaining set through
bubblewrap.

**Rationale**: No single mechanism covers every resource. Fixed disk/inodes, cgroup totals,
per-process FD ceilings, service connection ceilings, and execution deadlines together
bound the requested failure modes and make effective maxima observable.

**Alternatives considered**: `ulimit` alone is per-process and easy to omit; Docker-style
CPU/memory/PID limits alone do not protect disk/inodes/FDs; a bespoke eBPF socket accountant
would add privileged kernel code and is unnecessary for the initial bounded connection
model.

This implements the 2026-08-01 specification clarification: Linux cgroup v2 has no
standard independent socket-count controller. Open sockets are included in descriptor
ceilings, while web/database worker caps and nft connection limits independently bound
network connections. No stronger claim is advertised.

## Decision 7: Close inherited descriptors and isolate credentials

**Decision**: The installed launcher starts from systemd with null/explicit standard I/O,
uses `close_range`/`close_fds`, rejects any unexpected descriptor before payload exec, and
passes only per-instance credentials through private container files. No host control,
Docker, systemd, SSH-agent, desktop, database, or sibling socket is mounted.

**Rationale**: A mount namespace does not revoke an already-open file/socket. Descriptor
sanitation is a start gate, not best effort.

**Alternatives considered**: Relying on Python subprocess defaults misses durable service
launchers and future callers; environment variables can leak through logs/process state.

## Decision 8: Preview two trusted package transactions

**Decision**: Preview exact host prerequisites (`systemd-container`, bubblewrap, uidmap,
debootstrap and already-present nftables/image tools) and exact Noble packages installed
inside the image (PHP 8.3 modules/FPM/CLI, MariaDB 10.11, selected nginx 1.24 or Apache
2.4). Resolve versions from currently configured signed APT sources, simulate dependency
actions, list image/host paths and service effects, then require current TTY confirmation.
Use an image-local `policy-rc.d` so package scripts cannot start services while building.

**Rationale**: Installing PHP/database/web packages into the image avoids enabling or
rewriting host service instances. Exact simulation reveals version unavailability and
maintainer effects before privilege.

**Alternatives considered**: Installing host daemons then disabling them still exposes
global package service side effects; remote install scripts and unapproved PPAs violate
acquisition policy; source builds undermine reproducibility.

## Decision 9: Runtime mode is explicit, provenance-aware, and immutable with data

**Decision**: Add a common `wordpressRuntime` config provider with mode, adapter, versions,
web server, isolation/egress/resource policy, and source provenance. Non-Compose activation
must come from the machine-local override; committed requirements may constrain but not
silently opt a machine into native execution. Registry state records the selected mode;
ensure refuses a different mode when populated data exists.

**Rationale**: The existing top-level `runtime` config already governs local/remote job
placement and must not be overloaded. Presence of Herd/PHP is not authorization.

**Alternatives considered**: Continuing `server: herd` branches conflates web server and
runtime; auto-detection creates nonportable and surprising instances; reusing `runtime`
would collide with existing schema semantics.

## Decision 10: A and B exclusively own route and hostname state

**Decision**: C returns a veth backend endpoint or incumbent document-root requirements,
plus runtime health/capabilities. It never invokes Herd/Valet link/secure, Caddy, DNS, or
hosts mutations. The clean-URL service composes C → A offer → B resolution → A route.

**Rationale**: Current Herd provisioning mixes runtime and route state. Exclusive ownership
makes destroy/recovery attributable and lets the same runtime serve with different ingress
strategies.

**Alternatives considered**: Product-specific route calls in runtime adapters recreate the
special-case architecture this feature is removing.

## Decision 11: One isolation gateway owns every hostile execution path

**Decision**: All managed-native web PHP, cron, WP-CLI/eval, arbitrary exec, Composer
scripts, plugin/theme activation hooks, PHPUnit, and local durable jobs resolve the same
runtime selection and policy digest through `IsolationLauncher`. Callers cannot invoke a
host-PHP or raw-subprocess fallback.

**Rationale**: Current Herd paths bypass the typed runtime service in several places,
including MCP exec and dependency/test setup. Securing only the new adapter would leave
equivalent attacker-controlled PHP executing directly as the developer account.

**Alternatives considered**: Auditing call sites without a mandatory gateway is fragile;
separate launchers can drift in mounts, secrets, network, and resource limits.

## Decision 12: Incumbent native remains explicitly lower isolation

**Decision**: Herd, official macOS Valet, and declared POSIX profiles are trusted-project
adapters. They must prove version, database, lifecycle, execution, and ownership but are
never labeled hostile-code-contained. Local/XAMPP/Laragon/WAMP remain detect-only/outside
platform initially.

**Rationale**: Shared host PHP and user-account execution can read developer files and host
services. Honest capability reporting is safer than implying nspawn/Docker equivalence.

**Alternatives considered**: Calling any native profile “isolated” based on separate site
directories is misleading; refusing incumbents entirely would discard useful trusted
workflows.

## Primary references

- systemd nspawn configuration: <https://www.freedesktop.org/software/systemd/man/systemd.nspawn.html>
- systemd resource control: <https://www.freedesktop.org/software/systemd/man/systemd.resource-control.html>
- bubblewrap security model: <https://github.com/containers/bubblewrap/blob/main/README.md>
- Apache `Listen` address/port contract: <https://httpd.apache.org/docs/2.4/mod/mpm_common.html#listen>
- Linux Landlock documentation: <https://docs.kernel.org/userspace-api/landlock.html>
