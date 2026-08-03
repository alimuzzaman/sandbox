# Managed-native provisioning progress (Ubuntu 24.04)

**Scope**: the provisioning half of T047 — how far a managed-native instance gets on a real
Ubuntu 24.04 host, and exactly where it stops.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, cgroup v2, x86_64, unprivileged caller with
scoped sudo. Projects `~/native-proof/primary` (nginx) and `~/native-proof/sibling`
(apache), both `wordpressRuntime.mode=managed-native`, `adapter=ubuntu-nspawn`. 2026-08-03.

## Where it stands

```text
isolation preflight        19/19 gates ready        (see isolation-prerequisites.md)
package transaction        planned and applied      host packages already present
image bootstrap            SUCCEEDS                 debootstrap noble into the owned image
image configure            SUCCEEDS                 PHP-FPM, nginx and Apache config tests pass
machine unit start         SUCCEEDS                 nspawn sets up the namespaces
guest init exec            FAILS                    AppArmor denies the profile transition
isolation verification     not reached
hostile probe suite        not reached
```

## The remaining blocker is a policy decision, not a bug

```text
apparmor="DENIED" operation="exec" info="no new privs" error=-1
  profile="sandbox-native-<machine>" name="/usr/lib/systemd/systemd"
  comm="systemd-nspawn" target="sandbox-native-<machine>//guest"
```

The machine passes `--no-new-privileges=yes` for the payload, and the AppArmor policy moves
the guest into a tighter `//guest` subprofile with a `cx ->` transition. The kernel refuses
that combination: under NoNewPrivileges an AppArmor domain transition counts as gaining
privileges, so the guest init can never exec.

Two controls the spec asks for are in direct conflict, and the resolution is a deliberate
choice about which boundary carries the isolation guarantee:

1. keep `--no-new-privileges=yes` and confine the guest with the OUTER profile (`ix` instead
   of `cx -> guest`), losing the tighter guest subprofile; or
2. keep the `//guest` transition and drop the payload's NoNewPrivileges, relying on the
   guest profile plus dropped capabilities and the seccomp filter; or
3. restructure the policy as an AppArmor stack (`//&`), which is NNP-safe, if the guest
   profile can be expressed as a stacked subset.

This needs the spec's isolation contract to say which one it wants (`data-model.md`
"Managed Isolation Policy" and FR-001/FR-003 all bear on it), so it is recorded here rather
than decided in passing.

## Defects found and fixed to get this far

Each of these blocked provisioning on ANY host, and none were visible from unit fixtures:

1. **Private-network gate** counted a fresh namespace's kernel unreachable `::/0` routes as
   IPv6 connectivity, so the gate failed everywhere.
2. **cgroup-delegation gate** ran `systemd-run --wait --scope`, a combination systemd
   refuses outright.
3. **Allowed project roots** for the managed runtime were only the checkout and the state
   base, so ensure rejected every project in a developer's own directory.
4. **The composed runtime service** passed the same narrow roots separately, so the fix
   above did not reach it.
5. **The owned image was mounted `nodev`**, so `debootstrap`'s first act — a `/dev/null`
   probe — failed with "Permission denied".
6. **Web-server config tests bound their listen sockets** in the host namespace, where the
   machine's veth address does not exist yet ("Cannot assign requested address"). They now
   run in a throwaway namespace with nonlocal bind.
7. **The machine unit passed `--link-journal=no-host`**, which systemd-nspawn does not
   accept, so every machine failed to start.
8. **The AppArmor profile denied `open /`** to the supervising nspawn process — `/**`
   matches paths below the root, never the root directory entry — so nspawn could not pin
   the outer mount namespace. Fixed in both the helper and control-plane copies.
9. **The image shipped no init.** It is booted with `--boot`, but debootstrap's minbase
   variant contains no systemd, so the machine died instantly on exec. systemd,
   systemd-sysv, and dbus are now part of the image package set.

Two diagnosability fixes were needed to see any of this: the helper's fixed-command runner
discarded the failing command's output, and `./sb ensure` crashed with `KeyError: 'instance'`
whenever a runtime returned a typed refusal instead of an instance record.

## Not covered

- The NoNewPrivileges / AppArmor-transition decision above (the current blocker).
- The hostile probe suite across every untrusted execution path, sibling resource
  exhaustion, and the cleanup matrix (T047 remainder, T072).
- The quickstart end-to-end run (T077).

## Host state after this session

No machines, no leftover images or mounts, and the host's own services — Caddy, the Hermes
dashboard, the MCP control endpoint, and 42 containers — were healthy before and after.
