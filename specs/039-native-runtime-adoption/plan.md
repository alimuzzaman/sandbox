# Implementation Plan: Native Runtime Adoption

**Branch**: `latest` | **Date**: 2026-08-01 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/039-native-runtime-adoption/spec.md`

## Summary

Move WordPress lifecycle behind the existing runtime adapter contract and expose three
explicit local modes: unchanged Compose default, trusted/lower-isolation incumbent native,
and Linux-only managed-native. Herd, official Valet, and declared POSIX profiles become
capability adapters without owning hostname routes.

Because isolation is the primary requirement, managed-native is not implemented as bare
host daemons separated only by paths and ports. It installs Ubuntu packages into a
Sandbox-owned per-instance systemd-nspawn OS-container image and runs nginx or Apache,
PHP-FPM/CLI, MariaDB, cron, tests, and dependency scripts inside that one boundary. A
private user mapping, filesystem/PID/IPC/UTS/network namespaces, cgroup v2 controls,
seccomp, no-new-privileges, a fixed-size/inode filesystem image, instance-only secrets,
and default-deny veth firewall provide Docker-class Linux isolation. Bubblewrap 0.9 is a
required defense-in-depth launcher for one-shot untrusted commands, not the sole boundary.
Any missing or unverifiable layer fails closed before project code executes.

## Technical Context

**Language/Version**: Python 3.10+ control plane; Ubuntu Noble userspace; generated
systemd/nspawn/network/service configuration; no project PHP executes in the host control
plane

**Primary Dependencies**: Existing runtime/config/command/MCP manifests and bounded
services; systemd 255 + `systemd-container`; systemd-nspawn/machined; cgroup v2; bubblewrap
0.9; nftables 1.0; debootstrap/apt from configured Ubuntu sources; ext4 image tooling;
PHP 8.3, MariaDB 10.11, nginx 1.24 or Apache 2.4 inside the instance image

**Storage**: Versioned native repository under `$SANDBOX_HOME/runtime/native/state.json`;
per-instance fixed-size ext4 images/config/log/recovery artifacts under
`$SANDBOX_HOME/runtime/native/instances/<id>/`; root-owned applied policy and nspawn
descriptors under `/etc/sandbox/native/` and `/run/systemd/nspawn/`; secrets remain
machine-local and are injected as per-instance credentials

**Testing**: `unittest` contract/unit/integration suites; package-plan fixtures; namespace,
mount, network, FD, secret, syscall, and resource hostile probes through web/cron/CLI/exec/
dependency/test paths; live nginx and Apache lifecycle on Ubuntu 24.04

**Target Platform**: Managed-native only on the advertised Ubuntu 24.04/systemd 255/cgroup
v2 matrix; incumbent adapters on their declared POSIX/macOS platforms; Compose everywhere
currently supported and for CI/remote

**Project Type**: Single Python CLI/MCP application with runtime and isolation adapters

**Performance Goals**: Preflight/status within 3 seconds; warm instance start within 20
seconds; bounded CLI/test execution per supplied timeout; isolation revalidation before
every start and untrusted operation

**Constraints**: No silent runtime switching/downgrade; no host/sibling filesystem,
process, IPC, device, socket, secret, or network reach; source read-only by default;
deny-by-default egress; exact package preview/current TTY confirmation; host services never
enabled/stopped/rewritten; A/B own route/naming; conservative cleanup

**Scale/Scope**: A small number of simultaneous local instances; one OS-container image,
network namespace/veth pair, cgroup subtree, database, and web/PHP service set per instance;
shared host control binaries only

## Constitution Check

*GATE: Passed before research and re-checked after design.*

- **I — Per-project model**: Every native machine/image/policy is keyed by a canonical
  registry project root plus label. Detection never creates a fallback instance.
- **II — Registry source of truth**: The project registry resolves instance identity and
  selected immutable mode; the native repository contains attributable runtime/isolation
  state only and is accessed through a repository contract.
- **III — Modular package**: Runtime selections, adapters, config providers, commands, and
  MCP tools register through explicit manifests. Shared isolation services own mechanisms;
  adapters own runtime policy. No new direct `sandbox_core.py` or state JSON consumer.
- **IV — Live proof**: Managed-native cannot be advertised until nginx and Apache variants
  pass the full live hostile/lifecycle matrix. Incumbents require live evidence at their
  truthful isolation tier.
- **V — Idempotency/docs**: Plan/apply/status/destroy compare desired, applied, and observed
  state. Capability/docs/config/isolation guidance land with code.
- **VI — Parity before removal**: Existing WordPress Compose and Herd branches stay behind
  compatibility facades until adapter parity is live-proven. No facade removal is planned.

Post-design re-check: **PASS**. Using an OS-container boundary adds mechanism but avoids the
unacceptable policy violation of presenting bare host processes as hostile-code isolation.
The Linux-container threat model matches Docker class: the host kernel remains trusted and
kernel exploits are not claimed to be contained like a VM.

## Project Structure

### Documentation (this feature)

```text
specs/039-native-runtime-adoption/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── runtime-service.md
│   ├── managed-isolation.md
│   └── package-transaction.md
└── tasks.md
```

### Source Code (repository root)

```text
sandbox/
├── application/
│   ├── runtime_service.py          # expanded operation/capability orchestration
│   └── context.py                  # composition only, compatibility facade retained
├── config/
│   ├── wordpress_runtime.py        # explicit mode/profile/isolation schema + provenance
│   └── manifest.py
├── runtimes/
│   ├── registry.py
│   ├── manifest.py                 # deterministic runtime/profile registration
│   ├── wordpress.py                # adapter-neutral WordPress lifecycle
│   ├── incumbent/
│   │   ├── herd.py
│   │   ├── valet.py
│   │   └── posix.py
│   └── managed/
│       ├── adapter.py
│       ├── packages.py
│       ├── image.py
│       ├── services.py
│       ├── database.py
│       └── repository.py
├── isolation/
│   ├── models.py
│   ├── manifest.py                 # advertised host/version matrix
│   ├── preflight.py
│   ├── policy.py
│   ├── nspawn.py
│   ├── bubblewrap.py
│   ├── network.py
│   ├── resources.py
│   ├── credentials.py
│   └── verification.py
├── commands/
│   └── native.py                   # feature-owned CommandSpec/support/install/preflight
└── core/
    └── _herd.py                    # retained compatibility facade

tools/
└── native-helper/                  # root-owned installed helper + policy schema
    ├── native-helper.py
    └── VERSION

mcp/wp-server/tools/
└── runtime.py                      # explicit capability/preflight/result operations

tests/
├── test_wordpress_runtime_config.py
├── test_native_runtime_service.py
├── test_incumbent_adapters.py
├── test_managed_package_plan.py
├── test_managed_native_adapter.py
├── test_isolation_policy.py
├── test_isolation_preflight.py
├── test_native_ownership.py
├── test_native_cli_mcp.py
└── hostile/
    ├── probe.php
    ├── probe.sh
    └── probe_plugin/
```

**Structure Decision**: Extend the existing adapter/service framework rather than add more
`server == herd` branches. The installed root helper accepts only schema-validated,
root-owned applied policies and fixed lifecycle verbs; project code never sees its host
path or control socket. Managed-native uses nspawn for the durable instance boundary and
bubblewrap inside it for consistent one-shot mount/environment/syscall policy. Runtime
resolution becomes two-dimensional—project kind plus an explicit machine-local backend
selection—because the existing one-adapter-per-project-kind registry cannot represent
three WordPress backends honestly.

## Complexity Tracking

No constitution violations require justification.

## Plan amendment — 2026-08-13 (PHP extension provisioning)

Extend the existing WordPress runtime selection and managed package transaction rather
than adding a second provisioning path. The implementation order is: parse and
normalize the additive field; validate the immutable profile/catalog and reject all
unknowns before side effects; probe the four execution planes; then choose the
allowlisted official-image child build or the signed-APT managed-native plan. Custom,
LiteSpeed, Herd, and Valet remain validation-only, and generic Compose is an explicit
v1 refusal. Every package/image result is digest-bound and TTY-approved where the
managed-native plan mutates state. Reconcile only web/runtime artifacts and retain
database/uploads/snapshots/project files.
