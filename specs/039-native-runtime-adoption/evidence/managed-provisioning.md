# Managed-native provisioning progress (Ubuntu 24.04)

**Scope**: the provisioning half of T047 — how far a managed-native instance gets on a real
Ubuntu 24.04 host, and exactly where it stops.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, cgroup v2, x86_64, unprivileged caller with
scoped sudo. Projects `~/native-proof/primary` (nginx) and `~/native-proof/sibling`
(apache), both `wordpressRuntime.mode=managed-native`, `adapter=ubuntu-nspawn`. 2026-08-03.

## Where it stands

```text
isolation preflight        19/19 gates ready        (see isolation-prerequisites.md)
package transaction        planned and applied      host packages: keep (already present)
image bootstrap            SUCCEEDS                 debootstrap noble into the owned image
image configure            SUCCEEDS                 PHP-FPM, nginx and Apache config tests pass
machine start              FAILS                    systemd-nspawn: "Failed to pin outer mount
                                                    namespace: Permission denied"
isolation verification     not reached
hostile probe suite        not reached
```

The remaining failure is inside `systemd-nspawn`'s mount setup for the machine unit. It is
a unit/namespace configuration problem, not a missing prerequisite: every gate the runtime
declares passes, the rootfs is built, and both web servers validate their configuration.

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

Two diagnosability fixes were needed to see any of this: the helper's fixed-command runner
discarded the failing command's output, and `./sb ensure` crashed with `KeyError: 'instance'`
whenever a runtime returned a typed refusal instead of an instance record.

## Not covered

- The machine unit's mount namespace setup (the current blocker).
- The hostile probe suite across every untrusted execution path, sibling resource
  exhaustion, and the cleanup matrix (T047 remainder, T072).
- The quickstart end-to-end run (T077).

## Host state after this session

No machines, no leftover images or mounts, and the host's own services — Caddy, the Hermes
dashboard, the MCP control endpoint, and 42 containers — were healthy before and after.
