# Quickstart: Native Runtime Adoption Validation

## Compose regression and capability discovery

```bash
./sb ensure --project-dir /path/to/project --json
./sb native support --json
./sb native preflight --project-dir /path/to/project --json
```

An unpinned project remains Compose. Detection of Herd/PHP/system packages cannot change
mode. Capability output distinguishes Compose, trusted/lower-isolation incumbents, managed-
container isolation, implemented-unproven, detect-only, and unsupported matrices.

## Managed package preview

On a disposable Ubuntu 24.04 host with systemd 255/cgroup v2:

```bash
./sb native install-plan --project-dir /path/to/project --json
./sb native install --project-dir /path/to/project
```

The plan must show exact configured-source packages/versions and image/privilege/service
effects. Non-TTY execution must return pending with zero mutations. After interactive
install, unrelated host web/database/PHP service state and configuration must match the
baseline.

## Live nginx and Apache lifecycle

Set `wordpressRuntime.mode=managed-native` only in the gitignored machine override and run:

```bash
./sb ensure --project-dir /path/to/project --json
./sb status
./sb wp core version
./sb exec -- php -r 'echo PHP_MAJOR_VERSION,".",PHP_MINOR_VERSION;'
./sb test
./sb apply
./sb ensure --project-dir /path/to/project --json
```

Repeat with nginx and Apache on fresh instances. C must return a private-veth backend and
make no hostname route. After A/B integration, perform a live request and verify web, CLI,
exec, and tests all report PHP 8.3. Re-ensure/apply must converge.

## Hostile boundary probes

Run the same probe through web plugin, WP cron, WP-CLI, arbitrary exec, Composer script,
and tests. It must fail to:

- read/write host home, Sandbox control state, sibling image/source/secret, or escaping
  symlink targets;
- enumerate/signal host or sibling processes/IPC;
- open host devices, Docker/systemd/DBus/SSH/database/control sockets, or inherit a seeded
  host descriptor;
- connect to host loopback/veth, sibling address, private/link-local/metadata/internet
  destinations without an exact grant;
- gain capabilities, new privileges, raw sockets, nested user namespaces, or disallowed
  syscalls.

Then add/revoke one public scoped egress grant and verify only that destination/port works.

## Resource exhaustion

Independently exhaust CPU, memory, PIDs, execution/request time, disk bytes, inodes, file
descriptors, connections/sockets, and I/O. After each probe, verify the sibling instance and
host baseline remain healthy and the effective limit is reported.

## Collision, drift, and cleanup

Exercise foreign machine/image/database/path identities, changed owned state, missing
runtime, and repeated destroy. Foreign/drifted bytes remain unchanged and produce a
recovery record. Normal destroy removes only unchanged C-owned state; A cleans hostname
routes separately. Shared host prerequisites remain installed.

## Incumbent truthfulness

On supported Herd and official macOS Valet hosts, run preflight/ensure/web/CLI/test/destroy
and verify every status calls the mode `trusted_shared_host` or lower isolation. No
incumbent may be promoted to managed-container isolation, and C performs zero route/TLS/DNS
mutations.

