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
                                                  Permission denied   (fixed: profile rule added)
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
fresh `/proc` — cannot be satisfied inside an nspawn machine that masks `/proc`. One of
those three has to give:

1. **Drop `--unshare-user`** for the payload. It already runs as an unprivileged uid with
   all capabilities dropped, a seccomp filter, and the stacked payload profile; the userns
   was defence in depth, not the primary boundary. Nested userns stays blocked by
   `--disable-userns`.
2. **Drop `--unshare-pid`**, keeping the userns. The payload then sees the machine's PIDs,
   which weakens process isolation between payloads in the same machine.
3. **Unmask the machine's `/proc`**, which weakens the machine boundary itself to
   strengthen the payload one. This is the worst trade of the three.

Option 1 looks right and is the smallest loss, but it changes what the isolation contract
promises about the payload's namespaces, so it belongs in FR-001/FR-044 and the
"Managed Isolation Policy" entry of `data-model.md` — not in a passing edit.

## Not covered

- The hostile probe matrix, sibling exhaustion, and cleanup (T047 remainder, T072).
- Whether option 1 changes any result in the table above (it should not: the stack is
  applied at exec, independent of the namespace flags).
