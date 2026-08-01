# Contract: Managed-Native Isolation

## Threat model

Plugin/theme PHP, web requests, cron, WP-CLI, arbitrary exec, Composer/npm dependency
scripts, and tests are hostile. The Sandbox host control plane, approved Ubuntu packages,
systemd/nspawn/bubblewrap, and the host kernel are trusted. The boundary targets the same
host-kernel class as Docker; it does not claim VM-grade containment of kernel exploits.

## Mandatory start gates

Before every machine start and untrusted operation, verify:

1. exact supported OS/kernel/systemd/cgroup/nspawn/bubblewrap/nft/image matrix;
2. root-owned helper and policy schema/version/digest;
3. unique private UID mapping, machine identity, image and owner record;
4. mount list contains only approved immutable runtime data, instance image, read-only
   source, and exact declared writable subpaths; no escaping symlink;
5. private namespaces and minimal devices are effective;
6. veth/firewall policy denies new host/sibling/forwarding traffic and matches grants;
7. cgroup/resource/service ceilings match desired values;
8. no ambient capabilities, new privileges, host/control sockets, unexpected environment,
   secrets, or inherited file descriptors;
9. payload seccomp/bubblewrap policy is effective;
10. sibling canary paths/processes/sockets are absent.

Failure returns `blocked` before project PHP/argv runs. A prior successful result is not
cached as permanent proof.

## Filesystem contract

- Container root and writable WordPress/database state live in the owned ext4 image with
  fixed byte and inode capacity.
- Project source is `ro` by default. A writable subpath is separately resolved/mounted and
  grants no access to its parent or symlink target.
- Host home, `$SANDBOX_HOME` outside the instance, repository control plane, sibling
  images, `/run/docker.sock`, host systemd/DBus/SSH/desktop/database sockets, block devices,
  and host `/proc`/`sys` are absent.
- The instance may see its own namespaced `/proc`, minimal `/dev`, and instance runtime
  sockets only.

## Network contract

- No host network namespace, bridge, default route, resolver, forwarding, or masquerading.
- Host ingress may initiate only to the declared instance web endpoint; established replies
  are allowed.
- New instance-to-host and instance-to-sibling connections are always denied.
- No-egress is default. Each grant is explicit, public-only, port-scoped, observable,
  revocable, and countered. Hostname HTTPS uses the broker; fixed network grants use exact
  nftables sets. Raw/packet/netlink sockets and private destinations remain unavailable.

## Resource contract

The effective result reports exact CPU quota, memory high/max/swap, PIDs, one-shot/request
time, filesystem bytes/inodes, per-process FDs, service/instance connection ceiling, and
I/O limits. Exhaustion must terminate/refuse only within the instance; sibling/host health
probes remain successful.

## Execution contract

Every untrusted entry uses the same machine/policy digest. One-shot commands additionally
use bubblewrap with cleared environment, private temp, read-only source, capability drop,
nested-user-namespace disablement, and bounded output/time. The launcher rejects unexpected
open descriptors before `exec` and never executes fallback argv on the host.

