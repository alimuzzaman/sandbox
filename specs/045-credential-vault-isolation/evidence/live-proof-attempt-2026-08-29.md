# Credential Vault live-proof attempt — 2026-08-29

## Decision

T003, T022, T029, and T031 remain open. This run proved the selected remote's
revision parity and fail-closed behavior, but the host was not a qualified
managed-native proof host and the accepted source still has no installed
Credential Vault v2 helper lifecycle.

Support remains `implemented_unproven`, `adoptable=false`, with a null evidence
ID. No credential value, source reference, request body, authorization header,
lease, descriptor, or production workload was used.

## Exact source and remote runtime

- Source commit: `7d8c15a4159d3cffea7988811002be4a691d548d`
- Accepted Sandbox runtime revision: `483914586a6e3d5ce3d9a278`
- Remote: `scaleway-sandbox`
- The supported migration plan first observed installed revision
  `740cb664eb535d5cff742abb` and a mismatch.
- `./sb remote service migrate scaleway-sandbox --confirm --json` installed the
  accepted runtime. A fresh plan independently observed matching local and
  installed revision `483914586a6e3d5ce3d9a278`, active service, proven
  ownership, expected listener, and successful authentication.

The apply response retained its pre-migration observation, so it was not used
as parity evidence. Only the independent post-apply observation was accepted.

## Ubuntu preflight

Durable job `da65a9aa7328720a329c593a9431f32c` ran:

```sh
./sb native preflight --project-dir . --json
```

The job succeeded as a command and retained complete output. It observed Ubuntu
24.04, systemd 255, PID 1 systemd, cgroup v2, enforcing AppArmor, and user
namespaces. The proof gate correctly returned `ok=false`, `mutated=false`, and
`isolation_prerequisite_missing` for:

- `systemd-nspawn`
- `machinectl`
- `debootstrap`
- cgroup delegation
- private networking
- nftables
- seccomp

No package installation was attempted. This host carries other active work and
was not treated as a disposable proof machine. A container would not substitute
for the missing booted-host kernel and systemd evidence.

## Public Credential Vault refusal

Durable job `9bb3a2d06debc1e0f6ce3b62700448fe` submitted one fake-metadata revoke through
the public `./sb native credential-acceptance` command. It used no source
reference or credential-bearing material. The command returned a bounded,
secret-free refusal:

```text
ok=false, state=blocked, mutated=false, reason=unsupported_capability
```

This is fail-closed evidence only. It is not a T022 or T029 pass.

## Source blocker for T022 and T029

At the exact source commit above, all eight fixed Credential Vault v2 helper
verbs call `credential_v2_lifecycle_action(...)`, which deliberately exits 69
with `native credential v2 lifecycle is not installed`. The public acceptance
controller therefore cannot be composed into a real broker/controller service
lifecycle on this revision. The current live native harness proves the base
managed-native boundary; its Credential Vault coverage remains injected/offline
and does not run bind, request, revoke, restart, exhaustion, or cleanup against
the real v2 services.

Because the Ubuntu preflight failed before provisioning and the v2 service
lifecycle is absent, the destructive hostile/exhaustion matrix was not launched.
No managed-native instance or Credential Vault unit was created, so no cleanup
claim is needed.

## Linux regression result

The first clean-source Linux discovery job,
`92a34400c2340aba7eef1a729be46274`, ran 626 `test_credential*.py` tests and
exposed one platform-dependent test defect: a missing-`SO_PASSCRED` negative
case assumed the constant was absent, which is true on the local macOS test
host but false on Linux. The test now explicitly patches `SO_PASSCRED` to an
unavailable value for that one negative case. This changes no runtime behavior.

Durable Linux rerun `c67e9b4c9ac05876f6d5a07610c0a578` then passed all 626
credential tests in 84.602 seconds with complete retained output. This is Linux
unit/contract evidence only. It does not close T003, T022, or T029.

## Reviewer packet for T031

An independent human reviewer should not start the final T031 decision yet.
The next candidate must provide all of the following from one clean exact source
revision on one authorized disposable Ubuntu 24.04 host:

1. Passing managed-native preflight and full T003 hostile, grant/revoke,
   exhaustion, warm-start, timing, and cleanup evidence.
2. Installed root-owned controller/broker unit and config lifecycle using only
   the eight fixed v2 helper verbs, including start, status, drain, stop, exact
   ownership, and post-stop absence evidence for T022.
3. A live T029 public bind/request/revoke/restart/exhaustion/no-leak matrix over
   the exact T040-T043 v2 authorities, with a replay-safe durable request ID and
   a fully validated proof bundle.
4. The exact contracts, manifest, bundle, ledger, typed checks, cleanup record,
   source identity, installed runtime identity, and retained job identities.
5. Independent source, security, privacy, provenance, and evidence review. Only
   that reviewer may decide whether support metadata can change.
