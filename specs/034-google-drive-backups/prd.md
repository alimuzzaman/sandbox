# Product Requirements Draft: Google Drive Backups for Permanent Instances

**Status**: Refined

**Created**: 2026-07-22

**Last Refined**: 2026-07-29

**Input**: "Add built-in scheduled Google Drive backups for permanent sandbox instances. Scope: database, uploads, and sanitized recovery metadata; plugin source remains in Git. Default retention: 7 daily and 4 weekly backups. Include manual backup, scheduled execution, integrity verification, restore, failure reporting, and safe Google Drive authorization without creating accounts or exposing secrets."

**Drafting Model**: `gpt-5.6-sol` (active-session fallback; preferred `gpt-5.6-terra` Medium was unavailable)

**Final Validation**: `REOPEN` — independent `gpt-5.6-sol` High

**Validated On**: 2026-07-29

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Permanent WordPress instances can hold production database and media state that
cannot be reconstructed from Git. Existing local snapshots live beside the
instance they protect and may disappear with host or instance loss. Existing
scoped recovery provides fixture-verified encryption, publication, verification,
retention, and rollback safety primitives, but no real recovery set, production
restore, fresh-server drill, pruning, or schedule activation has been proven.
Permanent instances do not yet have a complete, opt-in product flow that captures
their recoverable state, runs automatically, verifies remote durability, applies a
bounded retention policy, and restores into a usable instance.

Operators need an off-host Google Drive recovery path that is automatic enough
to be dependable, explicit enough to avoid backing up disposable development
instances, and safe enough to avoid leaking credentials or silently replacing
live production data.

## Users and Desired Outcomes

- **Permanent-instance operator**: Keep recent, verified, off-host recovery
  points without manually exporting database and uploads every day.
- **Incident responder**: Identify newest recoverable backup, understand its
  source and integrity, and restore it into a fresh target after host loss.
- **Site administrator**: Perform an explicitly confirmed in-place restore when
  faster rollback is needed, while retaining a recovery point for overwritten
  state.
- **Security-conscious maintainer**: Use an existing Google Drive identity and
  external secret channels without placing OAuth tokens, encryption secrets, or
  provider credentials in project configuration, logs, manifests, or chat.

## Goals

- Allow operators to explicitly opt eligible production WordPress instances into
  Google Drive backup.
- Capture database, uploads, and sanitized recovery metadata sufficient to
  identify and restore one instance.
- Support both on-demand backup and dependable daily scheduled backup on the
  instance host.
- Encrypt backup content before it leaves the instance host and verify the
  uploaded recovery point before reporting success.
- Retain 7 daily and 4 weekly verified recovery points by default without
  deleting the only recoverable copy.
- Expose bounded, redacted status for success, failure, skipped runs, pending
  publication, verification, retention, and restore.
- Restore a verified backup into either a fresh target or an existing target
  through separate safety gates.
- Preserve compatibility with existing scoped-recovery safety rules rather than
  reviving superseded broad machine-backup behavior.

## Non-Goals

- Back up ordinary disposable development instances by default.
- Back up plugin source, repositories, worktrees, WordPress core, containers,
  images, caches, logs, sockets, or unrelated host files.
- Replace Git as source-of-truth for plugin and application code.
- Create Google accounts, Drive identities, OAuth applications, or encryption
  secrets for the operator.
- Store or display OAuth tokens, Drive credentials, SSH credentials, database
  passwords, or encryption passphrases.
- Synchronize or mirror an entire host to Google Drive.
- Treat a backup as successful before remote integrity verification completes.
- Support generic Compose workloads or Herd instances in first release.
- Automatically promote a fresh restored target into production traffic.

## Product Scenarios

### Scenario 1 — Opt in a protected-production instance

- **Starting state**: Operator has an eligible WordPress instance explicitly
  classified as protected production and an existing host-local Google Drive
  identity and encryption secret channel. Remote location or persistence alone
  does not confer eligibility.
- **User action**: Operator explicitly enables backups for that instance.
- **Expected outcome**: Product validates eligibility and backup prerequisites,
  identifies exact protected instance and destination without revealing secrets,
  and keeps backups disabled when any required prerequisite is unsafe or missing.

### Scenario 2 — Create and verify an on-demand backup

- **Starting state**: Eligible instance is opted in and healthy enough to capture.
- **User action**: Operator requests an on-demand backup.
- **Expected outcome**: Product blocks site writes for no more than the confirmed
  freeze/abort window, captures database, uploads, and sanitized recovery metadata
  on the instance host, then restores normal write access. It encrypts captured
  state before upload, verifies the complete remote recovery point, and reports
  stable recovery-point identity and verification result. Partial or unverifiable
  publication is never reported as successful.

### Scenario 3 — Run scheduled backup

- **Starting state**: Backup schedule is enabled after a verified on-demand backup
  and recovery drill satisfy activation gates.
- **User action**: Daily schedule becomes due, including after host downtime.
- **Expected outcome**: One non-overlapping backup attempt runs on instance host.
  Resource pressure or an existing run produces visible skipped state rather than
  false success. Missed schedules receive no more than the confirmed number of
  catch-up attempts.

### Scenario 4 — Handle failed or interrupted publication

- **Starting state**: Capture succeeds but encryption, upload, connectivity, or
  verification fails.
- **User action**: Operator checks backup status and retries when safe.
- **Expected outcome**: Product reports actionable redacted failure, preserves any
  verified encrypted pending material that can be resumed safely, and never
  publishes an incomplete recovery point as complete.

### Scenario 5 — Apply retention

- **Starting state**: Drive contains multiple complete, incomplete, legacy, or
  unverifiable objects for protected instance.
- **User action**: Verified scheduled backup completes and retention evaluation
  runs.
- **Expected outcome**: Product keeps 7 daily and 4 weekly verified recovery points
  by default according to the confirmed bucket, timezone, missed-period,
  minimum-age, and deletion-authorization policy. Newest verified point, only
  verified point, incomplete/unverifiable objects, and objects that cannot be
  confidently classified are protected. Retention reports what it kept and any
  reviewed candidates or removals without exposing secrets.

### Scenario 6 — Restore into fresh target

- **Starting state**: Original host or instance is unavailable, and operator has a
  fresh eligible WordPress target with no protected production state.
- **User action**: Operator selects verified recovery point and requests fresh-target
  restore.
- **Expected outcome**: Product verifies source, destination, decryptability,
  integrity, and target emptiness before mutation; restores database and uploads;
  validates restored site state; and leaves production traffic changes to separate
  explicit operation.

### Scenario 7 — Restore in place

- **Starting state**: Existing eligible instance contains live state that will be
  replaced.
- **User action**: Operator selects verified recovery point and explicitly confirms
  in-place restore.
- **Expected outcome**: Product creates and verifies pre-restore recovery point for
  current state, shows exact target and consequences, replaces database and uploads
  only after confirmation, validates result, and offers bounded rollback when
  restore fails. If target state cannot be checkpointed, normal restore is blocked;
  separate break-glass confirmation may authorize emergency overwrite while clearly
  recording absence of rollback point. Failure cannot be reported as success.

### Scenario 8 — Reject unsafe or ambiguous operations

- **Starting state**: Request targets disposable development instance, unsupported
  runtime, mismatched project/instance identity, unavailable credentials, wrong
  encryption secret, incomplete backup, or ambiguous destination.
- **User action**: Operator requests backup, retention, or restore.
- **Expected outcome**: Product fails before destructive or remote side effects,
  reports stable redacted reason, and does not guess target, credentials, or backup
  identity.

## Proposed Product Behavior

- Backup eligibility requires an explicitly declared protected-production
  WordPress instance opted in by the operator. A development label, remote
  location, or persistence alone never confers eligibility. A development
  instance must first undergo a separate explicit classification change before it
  can be considered.
- Backup work runs on protected instance host. That host owns access to WordPress
  state, configured Google Drive identity, and encryption secret channel.
- Each recovery point contains consistent recoverable database state, uploads, and sanitized
  metadata needed to identify source, scope, creation outcome, integrity, and
  restore compatibility. Plugin source is referenced through source identity but
  not copied into backup. Capture uses a brief write freeze so database and uploads
  represent one recoverable site state, and normal writes resume after every
  success or failure.
- All protected content is encrypted in owner-only host staging before leaving the
  host. Recovery points are immutable, and retry never overwrites an existing
  point. Remote completion becomes visible only after complete uploaded content
  is verified. Plaintext staging is removed after both success and failure; a
  cleanup failure is explicitly reported. Incomplete and unverifiable states
  remain distinguishable.
- Scheduled backup defaults to daily execution with overlap prevention, bounded
  runtime, visible skip/failure state, and missed-run catch-up. Schedule activation
  requires one verified real backup, one successful disposable fresh-target
  restore drill, review of the exact schedule/profile selection, separate
  scheduling authorization, and monitoring of the first activated run.
- Default retention aims to protect 7 daily and 4 weekly verified recovery points.
  Pruning is governed by the confirmed bucket/timezone, missed-period,
  minimum-age, and deletion-authorization policy. Safety floors may retain more
  than the nominal counts. Pruning occurs only after a new recovery point is
  verified and must never remove the only verified or newest verified point.
- Fresh-target restore and in-place restore are distinct operations. Fresh restore
  requires empty/new target. In-place restore normally requires exact target
  confirmation and verified pre-restore recovery point. When checkpoint creation is
  impossible because target is already unrecoverable, separate break-glass action
  may authorize overwrite without rollback point; normal confirmation is not enough.
- Backup, retention, and restore status remains bounded and redacted. Provider and
  encryption secrets never appear in command arguments, stored manifests, output,
  logs, or instance configuration.
- Authorization consumes operator-configured Google Drive identity; product does
  not create accounts or provider applications. Authorization uses least privilege
  limited to the configured backup destination. Missing, overbroad, or invalid
  authorization blocks operation with setup guidance.
- Existing local snapshots remain useful for fast local rollback but do not count
  as off-host backup evidence.

## Constraints and Dependencies

- Per-project instance identity remains authoritative; no implicit global or
  fallback instance may be backed up or restored.
- Protected instance and Drive destination must be explicit and stable across
  scheduled runs.
- Production credentials and development credentials must remain separate and
  must never be selected by inference.
- Encryption passphrase and OAuth/provider tokens must remain external secrets and
  must not cross CLI/MCP result boundaries.
- First release supports Docker-backed WordPress instances explicitly classified
  as protected production. Development-labelled instances, Herd, and generic
  Compose need separate product definitions or an explicit classification change
  outside this backup flow.
- Restore mutates database and uploads and therefore requires capability checks,
  exact target resolution, confirmation, integrity checks, and recoverable failure
  handling before side effects.
- Backup schedule cannot be activated solely because configuration exists; live
  backup and restore evidence is required first.
- Retention must fail closed when object age, identity, decryptability, or
  completeness cannot be established.
- Remote outage, Drive quota, credential expiry, host disk pressure, large uploads,
  and interrupted processes are expected operating conditions, not exceptional
  cases to hide.
- Existing scoped recovery safety and compatibility behavior remains rollback
  control until replacement parity is proven.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Product form | Built-in scheduled backup with manual execution | Dependable protection requires both automation and operator-triggered recovery points | User, 2026-07-22 |
| Backup scope | Database, uploads, sanitized recovery metadata | Protects non-Git production state while avoiding broad host backup | User, 2026-07-22 |
| Source code | Remains in Git; excluded from Drive backup | Avoids duplicate authority and oversized archives | User, 2026-07-22 |
| Default retention | 7 daily and 4 weekly verified points | Bounded storage with useful short- and medium-term rollback | User, 2026-07-22 |
| Eligibility | Explicitly opted-in, protected-production WordPress instances; persistence or remote location alone is insufficient | Prevents development data from entering production backup flow while retaining explicit reclassification | User decision, 2026-07-23, refined by existing production-classification policy |
| Execution location | Protected instance host | Avoids routing database/uploads through developer machine and keeps host-local capture coherent | User, 2026-07-23 |
| Restore modes | Fresh-target and explicitly confirmed in-place restore | Supports disaster recovery and fast rollback while preserving separate safety gates | User, 2026-07-23 |
| Encryption | Client-side before Drive upload using external secret channel | Existing security boundary prevents provider or project configuration from seeing plaintext secrets | Existing policy and requested security scope |
| Authorization | Consume existing operator-configured Google Drive identity; create no account or provider app | Complies with no-live-account-creation policy and keeps credential ownership explicit | Existing policy and requested authorization scope |
| Schedule activation | Require verified backup, disposable fresh-target restore drill, reviewed exact schedule/profile, separate activation authorization, and first-run monitoring | Automation must not run before recoverability is demonstrated and specifically approved | Existing recovery policy |
| Capture consistency | Briefly freeze site writes during database and uploads capture | One recovery point should represent one coherent production state | User, 2026-07-23 |
| In-place safety | Require verified pre-restore recovery point and exact confirmation under normal conditions | Live replacement should remain recoverable and unambiguous | User restore choice plus existing destructive-operation policy |
| Emergency in-place restore | Permit separate break-glass override when current target cannot be checkpointed | Already-unrecoverable target may still need direct disaster restoration | User, 2026-07-23 |

## Open Questions

- **Retention buckets and time**: Are the 7 daily and 4 weekly points distinct
  buckets or may a weekly point also satisfy a daily slot? Which timezone defines
  bucket boundaries, and does a period with no verified point remain empty or use
  the nearest earlier verified point?
- **Retention deletion authority**: What minimum-age floor protects recent
  recovery points, and does one explicit schedule/policy activation authorize
  routine pruning within that exact policy, or must every prune apply receive
  separate confirmation as existing recovery deletion does?
- **Fresh-target source responsibility**: Must the operator pre-provision the exact
  compatible Git-owned source/runtime before restore, or does Sandbox reacquire
  the recorded source identity? In either case, unavailable or incompatible source
  must block database/uploads mutation.
- **Availability limits**: What maximum write-freeze/abort window is acceptable for
  one production capture, and how many missed daily runs may one schedule firing
  attempt to catch up?

## Acceptance Outcomes

- Operator can identify eligible permanent instance, opt it in, and see backup
  prerequisites without any secret value appearing in output or persisted product
  configuration.
- On-demand run creates remotely verified encrypted recovery point containing
  database, uploads, and sanitized metadata, with no plugin source content.
- Interrupted or failed upload never appears as successful recovery point, and
  status distinguishes capture, pending, incomplete, unverifiable, skipped, failed,
  and verified outcomes.
- Scheduled backup can run daily on instance host, prevents overlap, performs no
  more than the confirmed number of catch-up attempts after downtime, and exposes
  last attempt and last verified point.
- With more than the nominal retention window, verified points satisfy the
  confirmed 7-daily/4-weekly bucket policy while minimum-age and safety floors may
  retain additional points; newest, only verified, and ambiguous objects remain
  protected.
- Fresh-target drill restores selected verified point into empty disposable target,
  validates database and uploads against a compatible Git-owned source/runtime,
  and demonstrates the confirmed minimum usable-site checks without changing
  production traffic. Unavailable or incompatible source blocks mutation.
- Backup capture blocks writes for no more than the confirmed freeze/abort window,
  produces database and uploads from one coherent state, and restores write access
  after both successful and failed runs.
- In-place restore refuses without explicit exact-target confirmation and verified
  pre-restore point under normal conditions; successful restore validates result,
  and induced failure leaves actionable rollback path.
- When current target cannot be checkpointed, in-place restore remains blocked until
  operator invokes distinct break-glass confirmation; resulting status records that
  no pre-restore rollback point exists.
- Wrong passphrase, expired Drive authorization, quota exhaustion, insufficient host
  space, identity mismatch, unsupported runtime, and remote outage fail with stable
  redacted status and no false success.
- Ordinary development, Herd, and generic Compose instances remain excluded in
  first release.
- Before schedule activation, live evidence demonstrates one verified backup and
  one successful disposable fresh-target restore drill. The operator reviews the
  exact schedule/profile selection, separately authorizes activation, and monitors
  the first run; in-place restore, rollback, and destructive pruning retain their
  separate protected gates.
- Drive access is limited to the configured backup destination, retries never
  overwrite an immutable recovery point, and owner-only plaintext staging is
  removed after success and failure or a cleanup failure is explicitly reported.

## Risks and Assumptions

- **Risk**: Brief write freeze may affect production availability when database or
  uploads capture takes longer than expected; backup must bound this impact and
  always restore write access.
- **Risk**: Large uploads or slow Drive links may exceed host disk, quota, or bounded
  execution time.
- **Risk**: Host-local OAuth or encryption credentials can expire or become
  unavailable while schedule continues firing.
- **Risk**: In-place restore can extend downtime or leave mixed state if checkpoint,
  replacement, validation, and rollback are not treated as one protected operation.
- **Risk**: Retention bugs can destroy recovery depth; ambiguous objects must remain
  protected even when that delays pruning.
- **Risk**: A backup can be internally valid yet operationally unusable because
  source code/version or runtime compatibility changed.
- **Assumption**: Operator already controls Google Drive identity, encryption secret,
  and enough Drive quota; product provides no account creation.
- **Assumption**: Eligible protected-production instance is Docker-backed WordPress
  and can produce consistent recoverable database state and a bounded uploads
  archive on its host.
- **Assumption**: Git source identity and sanitized recovery metadata can identify
  compatible code/runtime, but restore responsibility remains an open product
  decision.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [ ] Consequential choices are confirmed rather than inferred.
- [ ] Acceptance outcomes are measurable and implementation-independent.
- [ ] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
