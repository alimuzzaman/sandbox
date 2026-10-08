# Product Requirements Draft: Server-First Recovery Capture and Later Drive Promotion

**Status**: Ready

**Created**: 2026-10-08

**Last Refined**: 2026-10-08

**Input**: "Feedback 9e54f17b: manual recovery lacks server-first capture and script submission. Need a unique server-first capture job with retained status, a complete table inventory, archive hashes, and later encrypted Drive promotion without recapture."

**Drafting Configuration**: Claude Opus 5.5 root drafting; Opus 5.5 read-only repository research.

**Final Validation**: `PASS` — independent read-only review, Claude Fable 5.1 (default effort)

**Validated On**: 2026-10-08

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

An operator needed a fresh backup of a hosted production site (Amar Sonar)
kept on its server first, and copied to Google Drive afterwards. Today
`sb recovery create --remote` can only do both in one blocking run:

- Capture requires the Drive destination and the recovery passphrase up front,
  so there is no "capture now, publish later".
- The capture is one synchronous call of up to an hour that streams the archive
  back over the connection. It has no job identity, no status to ask for, and
  no way to inspect an interrupted run, although the docs describe that state.
- The server keeps the plaintext archive and a receipt, but nothing lists,
  promotes or retires them.
- A ciphertext left `locally_pending` after a failed upload has no command that
  finishes the upload.
- The MariaDB capture records no table inventory, and the two archive members
  carry no hashes, so the operator cannot show the capture is complete.
- Trying to work around this with `sb remote ssh` fails, because that escape
  hatch caps commands at 4096 characters by design and is not a recovery path.

Spec 023 (scoped recovery profiles) owns recovery sets. This feature amends its
staging and publication model (FR-012 staging removal, FR-014 archive-first,
FR-016 locally pending), keeping 023's encryption and manifest guarantees.
The PostgreSQL recovery path already has a replay-safe request identity, a
retained status and a capture receipt; the WordPress/MariaDB path should reach
the same standard.

## Users and Desired Outcomes

- **Recovery operator**: start a capture on the server, walk away, and later
  see exactly what state it is in; publish it to Drive when ready, without
  capturing again.
- **Auditor / restorer**: prove a capture is complete (every table present,
  every member hashed) and that the Drive copy is the same capture.

## Goals

- A confirmed command starts a capture that runs on the server as a durable
  job with a unique identity; nothing streams over the operator's connection
  during capture.
- A status command returns the job's retained state at any time, the same
  answer on repeat, including after an interruption.
- The capture records a complete table inventory and hashes for the archive and
  each member, and fails if the dump and the inventory disagree.
- A confirmed promote command publishes an existing completed capture to Drive,
  encrypted, verified against the capture receipt, without recapturing.
- Promote also finishes a `locally_pending` ciphertext.

## Non-Goals

- Raising the `remote ssh` cap, or running capture through `remote ssh` or
  `job-start`.
- Encrypting on the server or placing the recovery passphrase on the server.
- Restore apply, schedule activation, or legacy Drive deletion (023 T061, T069,
  T071, T072 stay as they are).
- New hosted profiles beyond those the controller supports today.
- PostgreSQL recovery changes.
- Automatic deletion of server archives.

## Product Scenarios

### Scenario 1 — Capture on the server

- **Starting state**: a supported hosted profile on a registered remote; no
  Drive destination or passphrase configured.
- **User action**: the operator starts a capture with a new backup id and
  confirmation.
- **Expected outcome**: the command returns promptly with the capture identity;
  the capture proceeds on the server.

### Scenario 2 — Check status

- **User action**: the operator asks for the status of that backup id, during
  and after the capture.
- **Expected outcome**: queued, running (with phase), complete, failed (with a
  typed reason) or incomplete (interrupted), with start and end times. A
  complete capture shows the archive and member hashes and the table inventory
  summary.

### Scenario 3 — Promote later

- **Starting state**: a complete server capture; Drive destination and
  passphrase now configured.
- **User action**: the operator promotes that backup id with confirmation.
- **Expected outcome**: the archive is transferred through the Sandbox remote
  transport in bounded chunks, resumable for the same backup id, and its hash
  is re-verified against the receipt after transfer. It is then encrypted on the
  operator side and uploaded archive-first and manifest-last. The published
  manifest records the capture's request id, backup operation id, archive hash
  and member hashes, so the Drive set is traceable to the server capture
  without decryption. `recovery list` shows it published.

### Negative scenarios

- Starting a capture for a backup id that already has one returns the existing
  identity and state without recapturing; if the source binding (profile
  declaration, remote identity, revision) differs, it refuses with a typed
  reason and never overwrites.
- Capture start on a remote holding an unpromoted archive past its retention
  bound refuses with `retention_exceeded`.
- Insufficient free space on the server for the estimated archive: capture
  refuses before dumping, naming the shortfall.
- Capture start on a remote whose installed runtime revision mismatches refuses
  with `remote_runtime_stale`; status still answers.
- Promote of a capture that is not complete, or whose transferred bytes do not
  match the receipt, refuses and publishes nothing.
- An interrupted capture, or an archive whose receipt is missing or malformed,
  reports `incomplete`; it is never reported complete and never promoted.
- Dump table set and the inventory taken at dump start disagree: the capture
  fails with `inventory_mismatch` and the differing names.
- Promote without the passphrase or destination refuses before transferring.
- Two promotes of the same backup id: the second finds the published set and
  reports it, without uploading again.
- Status for an unknown backup id returns a distinct "no such capture" result.

## Proposed Product Behavior

- Capture, status and promote are recovery commands on the Sandbox CLI with MCP
  parity. Capture and promote are protected and require explicit confirmation
  on both CLI and MCP; status needs none.
- Status and the server-side listing work with neither the passphrase nor the
  Drive destination configured.
- The capture identity derives from the backup id and target, so a repeated
  request is recognized rather than duplicated.
- Server archives are owner-only and visible to the operator. They carry a
  retention bound (default 7 days, per-remote override); past it, status and
  list report `retention_exceeded` and new captures on that remote refuse until
  the archive is promoted or retired. Retirement goes only through the existing
  reviewed-and-confirmed retention path; nothing is deleted automatically.

## Constraints and Dependencies

- Amends spec 023 FR-012, FR-014 and FR-016; 023's encryption, ciphertext-only
  Drive and manifest-last rules hold.
- No raw SSH for recovery operations (023 FR-026).
- Live production use needs explicit approval and the brokered database
  credential through `sb secrets`; never in argv, logs or feedback.
- Promote takes the non-secret Drive destination as a reviewed argument or
  `RECOVERY_RCLONE_DESTINATION`, and the passphrase only through the inherited
  environment channel (023 FR-013).
- Supported profiles are those the controller supports today.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Owner | New feature amending 023 | 023 owns recovery sets; 048/051 are unrelated | Repository research |
| Encryption location | Operator side at promote | Passphrase never reaches the server | Spec 023 policy |
| Locally pending ciphertext | Finished by promote | Gives the existing state an exit | Repository research |
| Plaintext at rest on server | Allowed, owner-only, 7-day default bound; refuse new captures past it; no auto-delete | Code already keeps it today; bound prevents accumulation; amends 023 FR-012 | Fable decision (delegated by user), 2026-10-08 |
| Status prerequisites | None (no passphrase, no destination) | Status is read-only server inspection | Fable decision (delegated by user), 2026-10-08 |

## Open Questions

- None.

## Acceptance Outcomes

- Capture start returns within 30 seconds, with the capture running on the
  server; no archive bytes cross the connection during capture.
- For a capture in a terminal state (complete, failed, incomplete), 3 repeated
  status calls return identical data; for a running capture, repeat calls never
  regress to an earlier phase. A capture killed mid-run reports `incomplete`.
- Status and server-side list succeed with `RECOVERY_PASSPHRASE` and
  `RECOVERY_RCLONE_DESTINATION` both unset.
- The receipt lists every base table and view reported by the source database
  at dump start, with a row-count estimate per table; the dump's table set
  equals that list, and hashes are recorded for the archive and each member.
- A pre-capture free-space check compares the estimated size (database plus
  files) with free space in the controller location.
- Promote of a complete capture publishes a set whose plaintext hash equals the
  receipt's archive hash, with zero recapture.
- For a 2 GB archive, peak memory of the capture job on the server and of the
  promote process on the operator side each stays under 256 MB.

## Risks and Assumptions

- **Risk**: plaintext production data at rest on the server between capture and
  promote widens exposure if the server is compromised.
- **Risk**: live proof needs production access and approval.
- **Confirmed**: the current capture reads the full archive into memory on the
  server (hash and stream) and on the operator side; whether this caused the
  reported failure is not yet reproduced.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
