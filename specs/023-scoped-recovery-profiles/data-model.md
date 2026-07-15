# Data Model: Scoped Recovery Profiles

## RecoveryProfile

Fields: `id`, `version`, `enabled`, `scope`, `source_type`, `allowed_roots`, `sources`,
`capture_mode`, `consistency`, `excludes`, `sensitivity`, `restore_target`, `verification`,
`retention_class`, `dependencies`, `schedule_class`, and non-secret `metadata`.

Rules: IDs are stable DNS-like slugs; roots are absolute after server-side resolution; partial
sources are relative to one allowed root; no command text or credential values are permitted;
dependency graph must be acyclic; only known adapter/capture modes are accepted.

## ArtifactPlan

Fields: `profile_id`, `artifact_id`, `source_type`, resolved roots/sources, exclusions,
consistency operations, expected output type, staging path, sensitivity, dependencies,
restore target, verification steps, warnings, and estimated bytes when discoverable.

State: `planned` only. Planning has no mutating transition.

## ArtifactRecord

Fields: plan identity, capture start/end, adapter/version, archive member name, plaintext hash,
size, source provenance, consistency evidence, validation result, and restore metadata.

States: `capturing -> validated -> packaged`; any state may transition to `failed`, never back.

## RecoverySet

Fields: schema version, immutable set ID, timestamps, host identity, catalog digest, selected
profiles, artifact records, exclusions, ciphertext object/hash/size, manifest object, and status.

States: `staging -> captured -> encrypted -> remotely_verified -> complete`; failures become
`incomplete`. Only `complete` is restorable or protects legacy-prune safety floors.

## RestorePlan

Fields: set ID, selected profiles, ordered actions, prerequisites, free-space requirement,
quiesce/resume units, checkpoints, staging targets, swaps/imports, verification, rollback,
warnings, and `requires_confirmation=true`.

States: `planned -> confirmed -> checkpointed -> applying -> verifying -> complete`; failures
transition to `rolling_back -> rolled_back` or `manual_intervention` with retained evidence.

## SchedulePolicy

Fields: ID, selected profiles, calendar expression, randomized delay, lock path, timeout,
resource floors, retry policy, retention policy ID, enabled state, and last-run summary.

Rules: schedule creation defaults disabled; activation requires confirmation; one lock owner;
retention only follows a complete verified run.

## RetentionPlan

Fields: destination prefix, all observed objects, classifications, protected sets, candidates,
reason per candidate, safety-floor checks, and confirmation requirement.

Policy inputs: keep_count (at least one), minimum_age, and an injected timezone-aware reference
time for deterministic planning. The planner retains the newest keep_count qualifying sets and
every qualifying set newer than the age floor. Invalid or missing timestamps are unclassified
and therefore protected. Newest/only complete set, objects outside prefix, incomplete current
run, unverified sets, and non-current-passphrase sets are never automatic candidates.
