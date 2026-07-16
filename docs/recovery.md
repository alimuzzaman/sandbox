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

Database capture rejects empty output and performs format-aware validation before an artifact
can enter the encrypted publication pipeline.

Filesystem capture preserves declared in-root symlinks as links while rejecting links whose
resolved targets escape the allowed root.

Archive validation also rejects duplicate members and special device/FIFO nodes before restore,
preventing ambiguous replacement or unsafe filesystem materialization.
Member names are canonicalized before duplicate/traversal checks, so dot-segment aliases cannot
silently produce ambiguous restore paths.

Schedule plans render the reviewed profile selection and remote target into the disabled
service command, including the explicit confirmation required when an operator later activates
the schedule.

The filesystem restore adapter is target-explicit and injectable: it decrypts into owner-only
staging, validates archive members, uses Python's `data` tar filter on supported runtimes,
checkpoints the target, swaps atomically, verifies expected members, and restores the checkpoint
on failure. It is not automatically wired to production targets.

When RECOVERY_RCLONE_DESTINATION and RECOVERY_PASSPHRASE are both present, the service composes
the GnuPG and immutable rclone capture coordinator. Artifact paths remain explicit inputs;
missing secret configuration leaves capture unavailable rather than guessing sources.

The GnuPG passphrase descriptor handoff handles partial pipe writes and fails closed on a
non-progressing descriptor, without placing the passphrase in argv or process output.

Retention planning now inventories the configured destination, verifies each complete manifest
and ciphertext binding, and tests decryption with the current inherited crypto channel before
classifying candidates. Sets with an unavailable current passphrase or invalid timestamps are
reported as unclassified instead of disappearing from the plan. It remains non-destructive;
`--keep-count` and `--minimum-age-days` control the plan, while deletion remains separately
protected.

Read-only remote inventory also validates the complete response schema before returning it;
malformed JSON shapes fail as `inventory_failed` rather than being partially consumed.
RecoveryService also converts malformed adapter responses into operation-specific result errors
(`inventory_failed`, `list_failed`, `capture_failed`, `verify_failed`, `retention_failed`, or
`restore_failed`) instead of leaking raw Python exceptions.

Plans with symbolic host-manifest roots or composite source declarations report explicit
materialization warnings. Those warnings must be resolved by a target-bound adapter before
capture is considered ready.

The service rechecks that catalog/materialization boundary immediately before capture and
rejects unknown profiles, missing profile selections, unresolved warnings, and empty artifact
sets before invoking an adapter.

The CLI accepts repeated explicit materialized inputs with the recovery create command:
--backup-id SET --profile PROFILE --artifact NAME=PATH. It does not discover paths from the
host; unresolved catalog roots remain blocked.
