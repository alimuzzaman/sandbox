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
guest init exec            SUCCEEDS                 enters the //guest AppArmor profile
guest API filesystems      SUCCEEDS                 typed mounts + logind session namespaces
guest session observation  SUCCEEDS                 3/3 with zero denials after the logind rules
bwrap sandbox setup        SUCCEEDS                 binds /, mounts proc, drops its bounding set
payload exec               BLOCKED                  AppArmor refuses the transition under NNP
isolation verification     not reached
hostile probe suite        not reached
```

**The open blocker (2026-08-03)**: the payload cannot enter its own profile.
Every transition form was tried on the live host and the kernel refused each one,
for a different reason:

```text
/** cx -> payload                 apparmor="DENIED" info="profile transition not found"
                                  `cx` names a child of the CURRENT profile, so the
                                  kernel looked for `bwrap//payload`

/** px -> <full>//payload         apparmor="DENIED" info="no new privs"
                                  bwrap sets no_new_privs before exec, and the kernel
                                  refuses an AppArmor domain transition under NNP

/** px -> <full>//&payload        apparmor="DENIED" info="profile transition not found"
```

The committed rule keeps the correctly-named `px -> <full>//payload`: the name is
right, and NNP is what blocks it. That is the more honest failure to leave in the
tree than a wrongly-named rule that fails for a second reason.

An intermediate reading suggested the stacked form worked. It did not: it was measured
through `machinectl shell` sessions that were failing silently at the time, and it does
not survive a clean run. Any future attempt here needs a trustworthy channel first —
see the reliability note below.

**Likely resolution, deliberately not taken in passing**: enter the payload profile
with `aa_change_onexec` BEFORE exec'ing bwrap, while NNP is not yet set, instead of
transitioning after it. That changes WHICH layer applies the payload confinement, so it
belongs with FR-001/FR-044 and the "Managed Isolation Policy" entry in `data-model.md`.

**Observation reliability**: `machinectl shell` goes through systemd-logind, and the
guest profile denied logind's per-session namespace mounts. Sessions then failed
intermittently and returned empty output, which reads as a broken probe rather than a
denied mount. Three separate debugging cycles chased that phantom. With the logind
rules in place, three consecutive observations returned the guest profile with zero
denials. Anything measured through a guest session before those rules should be
treated as unreliable.

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
10. **NoNewPrivileges on the machine** blocked the AppArmor transition into `//guest`
    (FR-043).
11. **The guest profile had no mount rule at all**, so its init could not mount its API
    filesystems; typed, target-scoped rules were added without granting a general mount
    primitive (FR-044).
12. **CAP_SYS_ADMIN was dropped at the machine level**, which PID 1 needs inside its own
    user namespace (FR-044).
13. **`@system-service` does not include the mount syscalls**, so the seccomp filter
    refused them even once the capability and AppArmor rules allowed them.

Two diagnosability fixes were needed to see any of this: the helper's fixed-command runner
discarded the failing command's output, and `./sb ensure` crashed with `KeyError: 'instance'`
whenever a runtime returned a typed refusal instead of an instance record.

## Defects found and fixed after the first write-up

Each was found only by running against a real host, and each surfaced as something
misleading two or three layers away:

14. **`ptrace (read) peer=@{profile}`** emitted `@sandbox-native-<id>` — an AppArmor
    VARIABLE reference to a variable that does not exist — named the parent instead of
    the child profile, and granted only `read` where the kernel checks `read` on the
    reader and `readby` on the target. systemd's session bookkeeping broke and
    `machinectl shell` exited 0 with no output.
15. **`/usr/bin/bwrap cx -> bwrap`**: `cx` names a child of the CURRENT profile, so the
    kernel looked for `guest//bwrap` and refused every exec of bwrap.
16. **`/run/mysqld` did not exist** before mariadb started, so bwrap could not bind a
    declared writable target. A tmpfiles.d entry now creates the declared `/run` targets
    at boot.
17. **bwrap was denied `setpcap` and `sys_ptrace`**, which it uses to drop its own
    bounding set and read its child's /proc entry. It failed part-way through and
    reported an unrelated "Can't mount proc: Operation not permitted".
18. **`/ r,` was missing from the bwrap and payload profiles** — `/**` matches paths
    below the root, never the root directory entry. The same defect had already been
    fixed once in the supervisor profile and missed in the two inner ones.
19. **`/** cx -> payload`** looked for `bwrap//payload`, refusing every payload exec.
20. **logind's per-session namespace mounts were denied**, so `machinectl shell`
    sessions failed intermittently and observations returned empty output.

Six of these are one root cause: a rule naming a profile the kernel cannot resolve.
`tests/test_isolation_profile_structure.py` now reads the generated policy the way the
kernel does, so an unresolvable transition or peer fails in a unit test instead.

## Not covered

- The payload NNP / stacked-transition decision above (the current blocker).
- The hostile probe suite across every untrusted execution path, sibling resource
  exhaustion, and the cleanup matrix (T047 remainder, T072).
- The quickstart end-to-end run (T077).

## Host state after this session

No machines, no leftover images or mounts, and the host's own services — Caddy, the Hermes
dashboard, the MCP control endpoint, and 42 containers — were healthy before and after.
