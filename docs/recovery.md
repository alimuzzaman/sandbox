# Scoped recovery

`sb recovery plan` is safe and does not capture data. `sb recovery create`, restore apply,
retention deletion, and schedule activation are protected operations. A recovery passphrase is
accepted only through the inherited `RECOVERY_PASSPHRASE` environment channel, never an argument.

Use `sb recovery profiles --json` and `sb recovery plan --json` to review scope. A restore starts
with a plan and requires a known set ID; apply requires an explicit confirmation and a disposable,
configured target adapter. The generated systemd units remain disabled until a verified real set
and fresh-server drill exist. Do not delete legacy Drive objects or activate a timer until the
recorded protected checkpoints are approved.

Retention planning is policy-driven: callers may retain a configured number of newest
qualifying sets and protect every set newer than a minimum age. Inventory entries with
invalid or missing timezone-aware timestamps are protected rather than guessed. Plans remain
side-effect-free, and deletion still requires confirmation plus a fresh candidate list.

When a staging coordinator is configured with an owner-only pending directory, a verified
encrypted artifact is retained there if remote publication or verification fails. The pending
artifact is immutable by set ID and can be retried without recapturing the source.

Restore rollback includes the profile currently being applied when checkpointed work fails,
not only profiles that completed earlier in the restore order.

Recovery listing reports complete manifests, incomplete remote sets, malformed or unverifiable
sets, structurally legacy objects, and locally pending encrypted artifacts separately. The
legacy pending key remains as a compatibility alias for incomplete remote objects.
The read-only listing includes the configured destination root so legacy objects outside the
new sets prefix are visible for review.
Human-readable `sb recovery list` output prints each category and its paths; `--json` remains the
stable machine-readable envelope.
Complete-set listing also performs the same manifest/ciphertext hash and object-binding checks as
verification, so a same-size tampered archive is classified as unverifiable rather than complete.
Malformed manifest JSON values are rejected as stable invalid-manifest errors rather than escaping
through the CLI or MCP result envelope.
Human-readable verification output includes only the set ID and ciphertext identity/digest/size;
manifest provenance is not printed outside JSON mode.
Result envelopes also redact credential-bearing remote URLs and bearer values at the top-level
boundary, not only inside operation data.

Git provenance strips URL userinfo, query strings, and fragments before it can enter a recovery
manifest, so embedded credentials and token-bearing remote URLs are not retained as metadata.
Git bundles and working-tree patches are written to owner-only temporary files and atomically
published only after bundle verification; failed generation cannot leave a partial final artifact.

Database capture rejects empty output and performs statement-aware format validation before an
artifact can enter the encrypted publication pipeline; comment-only SQL is not treated as a dump.
Manifest artifact hashes are bound to the same verified source snapshots used during archive
capture, avoiding a second unprotected source read.

Filesystem capture preserves declared in-root symlinks as links while rejecting links whose
resolved targets escape the allowed root.
Filesystem capture snapshots regular-file digests recursively before and after archiving,
so same-size or same-mtime source rewrites are rejected as `source_changed`.
It also rejects source trees and link targets that cross the declared root filesystem's device
boundary; cross-filesystem capture requires a separately reviewed adapter.
For hosts with GNU tar, `GnuTarFilesystemCapture` is the explicit injected adapter for ACL/xattr
preservation and numeric ownership; it also enables `--one-file-system` and validates/atomically
publishes the resulting archive.

Archive validation also rejects duplicate members and special device/FIFO nodes before restore,
preventing ambiguous replacement or unsafe filesystem materialization.
Member names are canonicalized before duplicate/traversal checks, so dot-segment aliases cannot
silently produce ambiguous restore paths.
Restore verification then compares extracted member types, symlink targets, regular-file digests,
and the complete materialized path set before reporting success.

Schedule plans render the reviewed profile selection and remote target into the disabled
service command, including the explicit confirmation required when an operator later activates
the schedule.
Human-readable schedule output includes the disabled flag and generated service/timer units;
activation remains a separate protected operation.
The scheduler also propagates failed or skipped action envelopes, so a run cannot be reported as
complete—and become eligible for downstream pruning—when capture did not complete.

The filesystem restore adapter is target-explicit and injectable: it rejects symlink/non-directory
targets before checkpointing, decrypts into owner-only staging, validates archive members, uses
Python's `data` tar filter on supported runtimes, checkpoints the target, swaps atomically,
verifies expected members, and restores the checkpoint on failure. It is not automatically wired
to production targets. The restore coordinator preflights every required adapter operation before
checkpointing any profile, so malformed adapters fail without partial restore work.

Database, control-plane, and Git restore adapters use the same explicit callback lifecycle. They
validate and stage a caller-supplied artifact, then delegate checkpoint, import, verification,
resume, and rollback to injected services; no database credentials, Git commands, or production
targets are inferred by the recovery module. Artifact inode/size/mtime/digest changes during
staging are rejected before import.

When RECOVERY_RCLONE_DESTINATION and RECOVERY_PASSPHRASE are both present, the service composes
the GnuPG and immutable rclone capture coordinator. Artifact paths remain explicit inputs;
missing secret configuration leaves capture unavailable rather than guessing sources.

The GnuPG passphrase descriptor handoff handles partial pipe writes and fails closed on a
non-progressing descriptor, without placing the passphrase in argv or process output.
Ciphertext verification also requires the plaintext digest to remain stable before and after
decryption, preventing a source rewrite from being reported as a valid verification.
GnuPG outputs are created with exclusive owner-only pending files; stale pending paths are
rejected rather than overwritten, and successful outputs remain mode `0600`.

Retention planning now inventories the configured destination, verifies each complete manifest
and ciphertext binding, and tests decryption with the current inherited crypto channel before
classifying candidates. Sets with an unavailable current passphrase or invalid timestamps are
reported as unclassified instead of disappearing from the plan. It remains non-destructive;
`--keep-count` and `--minimum-age-days` control the plan, while deletion remains separately
protected.
Large ciphertext verification and retention decryption use streamed file downloads when the
Drive adapter supports them, avoiding whole-archive memory materialization.

Read-only remote inventory also validates the complete response schema before returning it;
malformed JSON shapes fail as `inventory_failed` rather than being partially consumed.
Drive object listings likewise require relative string paths and non-negative integer sizes;
malformed remote metadata fails closed before set classification or retention planning.
RecoveryService also converts malformed adapter responses into operation-specific result errors
(`inventory_failed`, `list_failed`, `capture_failed`, `verify_failed`, `retention_failed`, or
`restore_failed`) instead of leaking raw Python exceptions.

Plans with symbolic host-manifest roots or composite source declarations report explicit
materialization warnings. Those warnings must be resolved by a target-bound adapter before
capture is considered ready.

Human-readable restore planning prints the set, selected profiles, ordered actions, checkpoints,
and rollback steps; it never applies the plan. JSON remains the structured contract.
In-process callers can use `RecoveryService.restore_apply` with an explicit `RestorePlan` and
adapter mapping; the service re-verifies the manifest first, requires confirmation, and never
discovers a target. CLI/MCP apply remains separately protected until disposable adapters are wired.

The service rechecks that catalog/materialization boundary immediately before capture and
rejects unknown profiles, missing profile selections, unresolved warnings, and empty artifact
sets before invoking an adapter.

The CLI accepts repeated explicit materialized inputs with the recovery create command:
--backup-id SET --profile PROFILE --artifact NAME=PATH. It does not discover paths from the
host; unresolved catalog roots remain blocked.
