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

Database capture rejects empty output and performs format-aware validation before an artifact
can enter the encrypted publication pipeline.

Filesystem capture preserves declared in-root symlinks as links while rejecting links whose
resolved targets escape the allowed root.

Schedule plans render the reviewed profile selection and remote target into the disabled
service command, including the explicit confirmation required when an operator later activates
the schedule.
