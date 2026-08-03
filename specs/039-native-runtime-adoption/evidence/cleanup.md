# Managed-native cleanup: live proof (039 T072)

**Scope**: whether the product removes the host resources it owns, refuses the ones it
does not, and converges when run again. Every action below went through the runtime
service's `destroy` operation. No `machinectl`, `nft`, `apparmor_parser` or `rm` was used
to remove a managed resource — that is the property under test.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, AppArmor 4. Machine `sb-d71dfb0667143794`,
left behind by the provisioning attempts in `managed-provisioning.md`. 2026-08-03.

**Status**: partial. The drifted, repeated and unavailable cases are proven on a real
half-provisioned machine. The normal case — cleanup of a fully provisioned, running
machine — cannot be run until the payload boundary in `payload-boundary.md` is resolved,
because provisioning does not currently reach a running machine.

## Starting host state

Four provisioning attempts had left a partially removed instance:

```text
/var/lib/sandbox/native/instances/sb-d71dfb0667143794/root.img   8589934592 bytes
/etc/sandbox/native/networks/sb-d71dfb0667143794.json            present
/etc/sandbox/native/policies/sb-d71dfb0667143794.json            present
/etc/apparmor.d/sandbox-native-*                                 absent
machinectl list                                                  empty
nft list tables | grep sb_                                       empty
```

So: an image and two ownership records present, their AppArmor profile and nft table
already gone. `policy-remove` refused, correctly — `native policy still owns runtime
resources` — because the image and network record were still there.

## Three defects the live run found

**1. Absence was indistinguishable from drift.** `cleanup-observe` answered either "ours
and unchanged" or "ownership changed", so a resource that was never created, or whose
table had already been removed, answered like drift. Cleanup stopped at it, retained it
as a residual forever, and the surviving policy record then failed every later
provisioning at its first step. Fixed by reporting `state` (`present`/`absent`) with the
resource identity, where absence is always a successful read that found nothing.

**2. A cleanup progress record could strand resources permanently.** Progress recorded
which resources had been removed, and cleanup skipped those without asking the host. A
released bug (`2c19a97`, since reverted) wrote that record from provisioning's own
bookkeeping, marking every step an attempt never reached as removed. The record found on
this host:

```json
{"object_type": "cleanup_progress",
 "removed": ["services","database","machine","network","mount","image"]}
```

Six resources claimed removed, while an 8.5 GB image and a network record were still on
the host — exactly the resources cleanup was then told to skip. Progress is now consulted
only when an observation cannot be made, so a record that turns out to be wrong self-heals
instead of stranding what it names.

**3. The harness ran the product as root.** The helper requires an authenticated non-root
caller so a privileged change stays attributable to a real person; a nested `sudo` hands
it `SUDO_UID=0` and it refuses. Because a refused observation is indistinguishable from an
unavailable one, the run reported:

```json
{"ok": false, "state": "cleanup_incomplete",
 "cleanup": {"removed": ["services","database","machine","network","mount","image"],
             "residual": ["policy"]}}
```

Six resources reported removed; none of them were. The harness now refuses to start under
sudo and takes sudo only for its read-only host-state probes.

Running as root also left `~/sandbox/runtime/native/state.json` root-owned, so the next
ordinary run raised an unhandled `PermissionError`. That now reports which file and why,
and never falls back to empty state — doing so would forget which host resources are ours
and orphan every one of them.

## Result

With all three fixed, one `destroy` as the ordinary user, against the poisoned progress
record still in place:

```json
{"ok": true, "state": "ready", "reason": "cleanup_complete",
 "cleanup": {"complete": true, "residual": [],
             "removed": ["services","database","machine","network","mount","image",
                         "policy","state"]}}
```

Host state afterwards:

```text
/var/lib/sandbox/native/instances/     empty
/etc/sandbox/native/networks/          empty
/etc/sandbox/native/policies/          empty
/etc/apparmor.d/sandbox-native-*       absent
machinectl list                        empty
nft list tables | grep sb_             empty
native state.json                      every section empty, including recovery
```

The 8.5 GB image, both ownership records and the local state were removed by the product,
through observation, past a progress record that claimed they were already gone.

| Case | Result |
|---|---|
| drifted (half-removed network and policy) | removed; each remaining piece verified, then removed |
| wrong progress record | self-healed; resources observed present and removed |
| repeated | converges — an owner with no records returns `cleanup_complete`, unmutated |
| unavailable (observer refuses) | residual retained, nothing removed, no false claim |
| foreign identity | refused before any removal (contract suite; not re-run live here) |
| normal (fully provisioned machine) | **not run** — blocked by the payload boundary |

## Not covered

- The normal case above. It needs a machine that starts, which `payload-boundary.md`
  shows requires an isolation-contract decision first.
- Timing bounds for cleanup of a full instance, for the same reason.
