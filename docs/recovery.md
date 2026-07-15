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
