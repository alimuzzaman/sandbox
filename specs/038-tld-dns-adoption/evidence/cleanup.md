# Resolver cleanup and recovery (Ubuntu 24.04 + macOS)

**Scope**: T050 — owner-change / drift / unreachable / normal / repeated cleanup. The
owned-binding cases ran live against systemd-resolved on Ubuntu; the nothing-owned cases
were captured on macOS.

**Host**: Ubuntu 24.04.4 LTS, systemd 255, systemd-resolved owning the stub symlink.
Harness: `python3 tests/live_resolver_recovery.py --project-dir ~/git/templately
--label tmp-logo --evidence-id 038-t050-ubuntu-2404`. 2026-08-02.

## External drift is preserved, not removed

```text
apply                  ready
fragment edited outside Sandbox: "# edited outside sandbox" appended

cleanup_with_drift     cleanup_incomplete   ownership=residual
fragment_preserved     "# edited outside sandbox"     (byte-for-byte intact)
recovery_records       [{"reason_code": "observed_state_changed", "status": "drifted"}]
```

Sandbox refused to remove a binding whose observed state no longer matched its receipt,
left the operator's edit alone, and retained a retryable non-secret record (FR-019, FR-020).

## Authority stopped mid-cleanup

```text
pkill dnsmasq (the scoped authority)
authority_running_during   False
cleanup_with_authority_down  ready   cleanup_complete
```

Cleanup converges rather than reporting an unclearable residual: the goal state is exactly
what a dead authority already provides, and the generated artifacts are dropped.

## Normal and repeated cleanup

```text
cleanup_normal   ready  already_absent
cleanup_repeat   ready  already_absent
fragment_gone    True
resolvectl domain -> Global: (none)
```

## Unrelated resolution unchanged

```text
example.com before and after: identical answers
/etc/resolv.conf: still the systemd-resolved stub symlink
```

## Defect this run found and fixed

Removing the LAST owned zone required terminating the recorded authority process, so a
process that had already died left `remove()` returning False and cleanup reporting
incomplete forever with nothing left to clean. A pid that no longer exists is now treated
as goal-reached; a live process under that pid is still preserved.

## Not covered

- **Owner change**: switching the machine's active resolver between apply and cleanup, then
  showing status report `resolver_owner_changed` without mutating either resolver. Unit
  coverage exists in `tests/test_domain_cleanup.py`.
- Shared-owner release across two projects holding one zone (see `wildcards.md`, T055).
