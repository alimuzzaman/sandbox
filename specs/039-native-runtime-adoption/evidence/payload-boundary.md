# Payload execution boundary: what the kernel actually allows (Ubuntu 24.04)

**Scope**: the payload half of T047 — how an untrusted command inside a managed machine
is confined, measured against a live host rather than reasoned about. Every line below is
a kernel audit record or observed command output.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, AppArmor 4, `kernel.apparmor_restrict_unprivileged_userns=1`.
Machine `sb-d71dfb0667143794`. 2026-08-03.

## Instrument first: `machinectl shell` cannot be trusted here

`machinectl shell` returned output intermittently — `id -u` answered twice then returned
nothing, and `test -x /usr/sbin/aa-exec` reported success for a file that does not exist.
Several conclusions drawn through it were wrong, including an earlier claim that a stacked
transition worked.

`systemd-run --machine=<id> --pipe --wait --quiet` answered correctly 3/3 in the same
conditions and is the channel used for everything below. **The helper's `guest_run` still
uses `machinectl shell`**; that is a live-proof correctness issue in its own right and is
recorded as such.

## Exec transitions: three forms, three refusals

```text
/** cx -> payload             DENIED  info="profile transition not found"
                                      (`cx` names a child of the CURRENT profile)
/** px -> <full>//payload     DENIED  info="no new privs"
                                      bwrap sets NNP before exec; the kernel refuses
                                      an AppArmor domain transition under NNP
/** px -> <full>//&payload    DENIED  info="profile transition not found"
change_profile -> <target>            parses, but does NOT permit stack_onexec
change_profile -> &<target>           REJECTED by apparmor_parser (does not load)
```

With any `px` rule present, **every** exec inside bwrap is refused under NNP — including
`/bin/sh`, before the payload can do anything at all.

## What does work: inherit, then stack

Replacing the transition with `/** ix,` plus an unqualified `change_profile,` in the bwrap
profile, and having the final exec stack the payload profile onto itself:

```text
printf %s 'stack <profile>//payload' > /proc/self/attr/apparmor/exec && exec <command>
```

Effective confinement, read from inside the sandbox:

```text
sandbox-native-<id>//bwrap//&sandbox-native-<id>//payload (enforce)
```

That is the intersection of both profiles, and it holds:

| Probe | Result |
|---|---|
| mount tmpfs | denied — audit names `//payload` |
| read `/run/systemd/private` | Permission denied |
| unstack to `unconfined` | denied — audit `change_onexec target="unconfined"` |
| ordinary work (`echo`) | succeeds |

Stacking is irreversible: the payload profile grants no `change_profile`, so the stacked
process cannot drop back to the weaker profile. This satisfies FR-044's intent — the
payload can do no more than the payload profile alone allows — without a domain
transition that NNP forbids.

**Cost**: `change_profile,` in the bwrap profile is unqualified, because AppArmor 4
rejects every scoped form tried. bwrap is the trusted root-only setup step (mode 0750)
that already holds `sys_admin`, `mount`, and `userns`, so this does not widen a boundary
an attacker could otherwise reach; and under NNP a transition can only narrow. It should
still be recorded as a deliberate trade in the isolation contract rather than assumed.

## The remaining blocker is not AppArmor

With the real execution flags, bwrap stops before any of the above:

```text
--unshare-user                                    OK
--unshare-user --disable-userns                   cannot open /proc/sys/user/max_user_namespaces:
                                                  Permission denied   (a profile rule was added,
                                                  but /proc/sys is read-only in nspawn, so the
                                                  write cannot succeed from inside at all)
--unshare-pid                                     OK
--unshare-user --unshare-pid                      Can't mount proc on /newroot/proc:
                                                  Operation not permitted   — NO AppArmor denial
```

No audit record accompanies the last one, so it is a kernel refusal, not policy: mounting
a fresh procfs inside a non-initial user namespace requires an existing **fully visible**
procfs in the current mount namespace. systemd-nspawn deliberately masks paths under
`/proc` (`/proc/sys`, `/proc/sysrq-trigger`, and others), so the machine's `/proc` is not
fully visible and the nested mount is refused.

The execution boundary as specified — new user namespace *and* new PID namespace *and* a
fresh `/proc` — cannot hold inside an nspawn machine. Two independent walls, both measured:

```text
--unshare-pid + --proc          Can't mount proc: Operation not permitted   (no audit record)
                                a fresh procfs needs a fully visible /proc, and nspawn masks it.
                                Reproduced with and without --unshare-user, and as uid 33.

--disable-userns                cannot open /proc/sys/user/max_user_namespaces: Permission denied
                                nspawn mounts /proc/sys read-only, so the nested-userns ceiling
                                cannot be written from inside the machine at all.
```

## The configuration that does work, and what it still lacks

```text
--unshare-user --unshare-ipc --unshare-uts --unshare-cgroup --ro-bind / /
--proc /proc --dev /dev --tmpfs /tmp --cap-drop ALL --uid 33 --gid 33
+ ix/change_profile stack of the payload profile
```

Measured end to end on the live machine:

| Property | Result |
|---|---|
| payload runs | `payload-ran` |
| effective profile | `//bwrap//&//payload (enforce)` |
| uid | 33 |
| mount tmpfs | denied — audit names `//payload` |
| `/run/systemd/private` | Permission denied |
| own PID namespace | **no** — shares the machine's |
| nested user namespace | **CREATED — not blocked** |

Two guarantees are therefore still unmet, and neither is an AppArmor-policy question:

1. **PID isolation between payloads in one machine.** Blocked by the masked-`/proc` rule
   above. Either payloads share the machine's PID namespace, or they run without a fresh
   `/proc`.
2. **No nested user namespace.** `deny userns,` in the payload profile does NOT stop it:
   the payload already runs inside bwrap's user namespace, and the stack still permitted
   creation. A different mechanism is needed — writing the machine's userns ceiling from
   the HOST side at start (root can enter the machine's userns; the machine itself cannot,
   because `/proc/sys` is read-only), or a seccomp filter rejecting `clone`/`unshare` with
   `CLONE_NEWUSER`.

Both are isolation-contract decisions with real trade-offs, not implementation details, so
they belong with FR-001/FR-044 and the "Managed Isolation Policy" entry in `data-model.md`.

## Not covered

- The hostile probe matrix, sibling exhaustion, and cleanup (T047 remainder, T072).
- The two unmet guarantees above, each of which needs a mechanism decision first.
- Whether the host-side ceiling write or a seccomp filter is the better answer for nested
  user namespaces; both were identified, neither was tried.
