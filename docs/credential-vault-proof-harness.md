# Credential Vault proof harness

The harness in `tests/credential_vault_proof/` prepares the authorized Ubuntu
24.04 run that T022 and T029 need. It plans the run, records what happened,
validates the resulting evidence, and reports the outcome. It does not execute
a live check, and it is not evidence.

## What it proves

Only things about itself, offline:

- an acceptance manifest is exact, canonical, bounded, and secret-free, and its
  digest binds the whole execution plan;
- a revision mismatch refuses before any test action;
- one request identity owns one run, a retry consults the ledger before it
  launches, and an empty or malformed job acceptance is `acceptance_unknown`
  rather than success;
- partial evidence never classifies as `passed_live`, and cleanup trouble
  outranks a clean result;
- every planned probe is an argv array of allowlisted, manifest-derived tokens
  with a finite timeout and bounded, redacted output, and each declares how it
  proves itself: `exit_zero`, `exit_nonzero`, or `empty_output`. Absence checks
  use the latter two, because a removed cgroup makes `test -d` exit non-zero
  and reading that as a failure would report a clean host as bad and a host
  with leftover state as good;
- a completed bundle is refused when it is stale, copied, mixed-revision,
  contradictory, incomplete, or carries fake markers;
- a resource that cannot be proven ours is retained, never removed;
- the report keeps local harness behaviour and live evidence visibly apart.

## What it does not prove

Nothing about Linux. The harness has never opened a socket, transferred a
descriptor, started a unit, read a cgroup, or resolved a route. Specifically it
does **not** establish:

- process, namespace, or filesystem isolation on Ubuntu 24.04;
- systemd unit ownership, start-time identity, or drain timing;
- `SO_PEERCRED` / `SCM_CREDENTIALS` / `SCM_RIGHTS` kernel behaviour;
- `memfd` sealing, descriptor closure, or descriptor absence after cleanup;
- guest unreachability of the controller, lease socket, host, loopback,
  metadata, or a sibling interface;
- `SO_BINDTODEVICE`, DNS pinning, TLS verification, or redirect refusal;
- nftables default-drop, AppArmor enforcement, or seccomp mode;
- that any credential was ever applied, bounded, or confined.

Every local test uses injected fakes. A run whose provenance is
`local_injected_fake` is refused by the bundle validator by design.

## Prerequisites for the authorized host

- Disposable Ubuntu 24.04 host you are authorized to use for proof work.
- The documented managed-native prerequisites already installed and passing
  `sb native preflight`.
- The exact Git SHA and installed Sandbox revision named in the manifest.
- A non-root operator account, a separate broker service UID, and a separate
  control-plane UID.
- No production data, no real credential, and nothing you would mind losing.

## The future lifecycle

1. **validate** — `validate-manifest`. Refuses an inexact, non-canonical, or
   secret-bearing plan, and refuses a revision that is not the planned one.
2. **plan** — `plan`. Emits one bounded argv entry per check. Review it before
   anything runs; this is the last point where the plan is cheap to change.
3. **accept a durable job** — submit the run as a durable job with a
   replay-safe request ID, then `record-acceptance`. Empty or malformed output
   is `acceptance_unknown`: do not launch again, and do not mint a second
   request identity.
4. **poll the ledger** — a retry calls `should_launch` first. The ledger, not
   the operator's memory, decides whether anything may start.
5. **collect** — `record-artifact` for each planned artifact, then `finalize`.
6. **validate** — `validate-bundle` against the same manifest. This is where
   copied, stale, mixed-revision, or contradictory evidence dies.
7. **clean up** — verify absence with the cleanup verifier. A foreign or
   unreadable resource is a retained item for a human, never a deletion.
8. **review independently** — a reviewer who did not run the harness reads the
   bundle and the report and decides. Only that decision closes T031.

## Evidence retention and redaction

- Artifacts are named in the manifest and hashed in the ledger. Anything not
  planned is refused as an unplanned artifact.
- Raw stdout and stderr are never persisted. A parsed check keeps its state, a
  stable code, and the small set of expected observations that matched.
- The no-leak scanner runs over the evidence directory before a bundle is
  accepted. A finding names a code and an offset, never the matched text.
- Ledger records are owner-only, canonical, and bounded. Symlinked,
  foreign-owned, oversized, corrupt, or non-canonical records are refused
  rather than repaired.
- No credential, source reference, header, body, operation ID, lease ID, or
  request digest may appear anywhere in a manifest, record, artifact, or
  report.

## Why local tests do not close T022 or T029

T022 needs an authorized host to show that the root helper and the broker
service actually start, supervise, drain, stop, and disappear. T029 needs the
full live matrix — hostile no-leak probes, grant and revoke, exhaustion, warm
start, cleanup, and timing — against a real kernel. Neither can be satisfied by
a process that has never made a syscall against the thing being tested. The
harness makes those runs deterministic and reviewable; it does not stand in for
them.

## Why T031 still needs an independent review

T031 is a judgement, not a computation. Someone who did not build the harness
has to read the exact source revision, the contracts, the live evidence, and
the cleanup evidence, and decide whether the capability should move. The
harness deliberately refuses to promote anything itself: support stays
`implemented_unproven`, `adoptable` stays false, and the evidence identity
stays null in every code path, including a fully passing live bundle.
