# Managed-native isolation prerequisites (Ubuntu 24.04)

**Scope**: the prerequisite half of T047 — every isolation gate the managed runtime
requires, measured on a real Ubuntu 24.04 host through `./sb native preflight`.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, cgroup v2, x86_64, pid 1 systemd. 2026-08-02.

## Result

```text
./sb native preflight --project-dir <project> --json
  gates ok: 19 / 19
  reason:   {"code": "ready", "missing": []}
  adoptable: False        <- correct: the manifest still has no live evidence
```

Individual privileged probes:

```text
cgroup-delegation  {"ok": true, "state": "ready"}
private-network    {"ok": true, "state": "ready"}
nftables           {"ok": true, "state": "ready"}
seccomp            {"ok": true, "state": "ready"}
```

`adoptable` stays False because promotion needs the captured live evidence for the whole
required operation set (T047), not a passing preflight. That gate is working as designed.

## Defects this measurement found and fixed

Before these fixes, four gates failed on a host that fully supports them, so the managed
runtime was unreachable on ANY machine:

1. **`private_network`** — the probe read `/proc/net/ipv6_route` and treated any `::/0` row
   as a default route. A fresh network namespace always carries kernel-installed
   UNREACHABLE `::/0` entries on `lo`; those are the ABSENCE of IPv6 connectivity. The
   probe now requires the route to be up and not a reject, matching the IPv4 check beside
   it.
2. **`cgroup_delegation`** — the probe invoked `systemd-run --wait ... --scope`, and systemd
   refuses that combination outright ("--wait may not be combined with --scope"). A scope
   already runs synchronously, so `--wait` is both invalid and unnecessary.

`nftables` and `seccomp` were failing against a stale helper binary on the host; both pass
with the current one.

## Not covered

The rest of T047 — provisioning a managed-native instance, the hostile probe suite across
every untrusted execution path, and sibling resource exhaustion — is not captured here. The
acceptance harness (`tests/live_native_acceptance.py`) requires
`--confirm-disposable-host` because its exhaustion probes are destructive, and the host
used for this measurement runs live services. That run needs a throwaway Ubuntu 24.04 box.
