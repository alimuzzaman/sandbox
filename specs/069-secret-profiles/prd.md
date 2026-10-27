# Product Requirements Draft: Secret Profiles and Create-Only Manager Migration

**Status**: Discovery

**Created**: 2026-10-26

**Last Refined**: 2026-10-26

**Input**: "Separate registered secret source entries into office and personal profiles with create-only migration into hosted Infisical and scoped profile-only command use" (feedback record `190edc27`, user request "now fix it")

**Drafting Configuration**: Claude Opus 5.5 (personal account) draft; GPT-6.1-Sol parent refinement and integration, including the operator-confirmed destination decision

**Final Validation**: `PASS` — independent Claude Opus 5.5 review (personal account), 2026-10-26

**Validated On**: 2026-10-26

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

The operator keeps office and personal credentials in one registered dotenv
source (the `~/.zshrc.secrets` family, registered as a Sandbox source). Sandbox
can already group that source into documented product and service sections
(`sb secrets organize`, report-only by default) and can read one exact item
from a hosted Infisical manager through a project-scoped, read-only identity
(spec 067). It cannot:

- record which entries belong to the office account and which to the personal
  account, so the two boundaries the operator's own rules require are not
  enforced anywhere;
- move entries into the hosted manager; every attempt so far has meant
  hand-building an importer, re-authenticating in a browser, losing progress to
  one-hour credential expiry, and working around macOS socket path setup;
- give a command only the credentials of one chosen profile; the legacy path
  sources everything into every shell.

The approved import has stalled on this repeated manual toil, and each manual
attempt risks values reaching stdout, logs, intermediate files or the wrong
account. The operator wants Sandbox to own this workflow with the same
no-disclosure guarantees the existing secret tooling has.

## Users and Desired Outcomes

- **Operator (human owner of both accounts)**: Classify every registered entry
  into exactly one profile by explicit decision, move each profile into its own
  destination without overwriting anything, and run a command with only that
  profile's credentials, without needing to reveal a value.
- **Agent acting for the operator**: Prepare and run the dry-run, the
  classification review and execute within an unexpired human approval, and
  report sanitized evidence a human can audit, never handling or emitting
  values.
- **Security reviewer**: Inspect the proposed write authority, credential
  lifetime, revocation behaviour and evidence before any real credential is
  migrated or the feature is released.
- **Existing consumers (shells, scripts, Sandbox commands)**: Keep working
  unchanged, under the same variable names, until the operator opts in.

## Terminology

An **ownership profile** is the new office/personal ownership and destination
map. Existing `useProfiles`, `use-profile`, validation profiles, and the reserved
`personal` source alias retain their existing meanings and behavior. Separation
is enforced on this feature's opt-in ownership-profile paths only; legacy paths
are unchanged.

## Goals

- An explicit, reviewable profile map assigns each registered entry to one
  named profile (initially `office` and `personal`), with an optional
  destination name and the consumer name it must keep.
- Classification fails closed: any entry that is unmapped, ambiguous, mapped to
  two profiles, or mapped to a destination name another entry already claims
  blocks migration and is reported by name only.
- Migration is create-only into the hosted manager, one profile at a time, from
  the registered source only, dry-run by default, and reports names and counts
  only.
- Every write is journaled durably before and after it is attempted; a write
  whose result is unknown is reconciled, never blindly retried.
- An interrupted or expired migration resumes after re-approval by the same
  authority, without repeating confirmed writes.
- Destination values are verified against the source in memory, without
  display, hashing or persisted comparison material.
- A bounded direct-argv command can receive only the selected profile's
  entries, under their preserved consumer names.
- Migration uses an operator-managed controller-local bootstrap credential
  bound to the reviewed authority, plus a short-lived access credential held
  only in memory. Issued access is revoked best-effort on completion, with
  unconfirmed revocation surfaced and native-store cleanup left to the operator.
- Source cleanup never happens automatically; it is a separate, explicitly
  approved step after verified migration.

## Non-Goals

- Changing the default legacy `secrets run` behaviour, shell startup files, or
  the existing `organize` and `migrate-zshrc` behaviour.
- Shell activation that exports a profile into an interactive shell.
- Automatic or inferred profile assignment, inferred shared ownership, or
  sharing one destination entry across the office and personal accounts.
- Overwriting, updating, renaming or deleting existing destination entries.
- Deleting or rewriting source entries as part of migration.
- Broad provider administration: creating provider projects, identities,
  organizations or accounts; granting list, search, export or admin scopes.
- Backup scheduling, host restarts, account provisioning, any new UI, and
  unrelated production changes.
- Upgrading Infisical or changing its database schema, including its historical
  duplicate-removal migration; those are separately reviewed deployment work.
- Reading unregistered files, raw source files, process environments or shell
  history.
- Performing the operator's real credential migration or releasing the feature
  before a concrete human security review.

## Product Scenarios

### Scenario 1 — Dry-run classification report

- **Starting state**: A registered source with entries and a profile map that
  covers some of them.
- **User action**: The operator asks for a migration dry-run for one profile.
- **Expected outcome**: A metadata-only report: counts per profile, the names
  that would be created, names that already exist at the destination, and
  names that are unmapped, ambiguous or conflicting. No value, length, hash,
  prefix or file content appears. Source and provider state stay unchanged;
  ordinary sanitized audit records may still be appended. Classification alone
  needs no provider credential. Without an approved migration authority,
  destination existence is reported as unchecked. An approved exact-item
  existence check must omit secret values; unsupported checks report unknown
  and refuse mutation.

### Scenario 2 — Ambiguous or conflicting names fail closed

- **Starting state**: An entry name could plausibly belong to either account,
  is assigned to both profiles, or two entries map to one destination name.
- **User action**: The operator asks to migrate that profile.
- **Expected outcome**: Migration refuses before any write and lists the
  blocking names and the reason class. Resolution requires an explicit map
  edit; Sandbox never guesses.

### Scenario 3 — Approved create-only migration

- **Starting state**: Every entry of the profile is mapped, preflight passes,
  and the operator has approved this profile's migration.
- **User action**: The human operator confirms the migration.
- **Expected outcome**: Each name is created at its fixed destination path in
  that profile's destination only; each result is journaled; each created value
  is verified in memory; a sanitized completion summary lists counts and names.
  The source is unchanged.

### Scenario 4 — Destination entry exists or appears during the run

- **Starting state**: A destination name exists at initial preflight, or another
  writer creates a name after preflight and before this run attempts it.
- **User action**: The operator starts or continues a migration.
- **Expected outcome**: An initial pre-existing name refuses the whole batch
  before writes without fetching its value. A conflict after preflight stops
  further writes at that name; prior confirmed writes remain recorded, the
  conflicting item stays unchanged, and its value is not fetched. A response
  whose acceptance cannot be established remains `acceptance_unknown` rather
  than being optimistically classified as a conflict.

### Scenario 5 — Write outcome unknown

- **Starting state**: A create request times out, the connection drops, or the
  controller stops after sending it.
- **User action**: The operator resumes.
- **Expected outcome**: The journal records that name as `acceptance_unknown`.
  Resume reconciles it by an exact-item check restricted to journaled attempts;
  a match is recorded as reconciled, without claiming who created it. A proven
  absent item is eligible for one create; a mismatch stays unresolved. A
  confirmed write is revalidated before continuation and is never recreated.

### Scenario 6 — Credential expiry mid-run

- **Starting state**: The bounded migration credential expires part-way.
- **User action**: The operator resumes.
- **Expected outcome**: Sandbox stops cleanly at the next boundary, keeps the
  journal, and requires re-approval by the same authority (same operator, same
  profile, same destination scope). Already confirmed names are skipped.
  Approval from a different authority or for a broader scope is refused.

### Scenario 7 — Scoped command use

- **Starting state**: An ownership profile is mapped. Every entry declares an
  explicit read origin, either its registered source or registered destination;
  migration never changes that origin automatically. Selected entries within
  one ownership profile may use different explicitly declared origins; this
  does not permit an origin from the other ownership domain.
- **User action**: The operator runs a bounded direct-argv command with that
  profile and an explicit subset of mapped keys selected.
- **Expected outcome**: The child receives only the selected entries under
  their preserved consumer names; no other profile's entries, no shell
  evaluation, and no values in Sandbox output. Without a profile selection,
  the existing behaviour is unchanged.

### Scenario 8 — Wrong-profile or cross-account attempt

- **Starting state**: A profile map, a destination for each profile.
- **User action**: A request selects the office profile but targets the
  personal destination, or asks to inject both profiles at once.
- **Expected outcome**: Refused before any read or write, with a reason class
  and no values.

### Scenario 9 — Revocation and cleanup

- **Starting state**: Migration completed, failed or was abandoned.
- **User action**: Completion, or an explicit cleanup request.
- **Expected outcome**: Sandbox attempts to revoke the derived migration access
  credential and preserves the sanitized journal. A revocation that cannot be confirmed is
  reported as `cleanup_unknown` with the next human action, never as success.
  Native credential-store cleanup remains an operator action; automation never
  lists or deletes OS credential-store entries.

### Scenario 10 — Source cleanup after verification

- **Starting state**: A profile's migration is verified complete.
- **User action**: The human operator separately approves removing migrated
  entries from the source; MCP and non-interactive approval attempts refuse.
- **Expected outcome**: Only verified, migrated names are eligible. Immediately
  before removal, every name is re-verified in memory against its destination
  under a fresh approved authority. A missing, changed, approval-pending or
  unverifiable destination refuses removal of that name. The separate source
  change guard follows the same non-value-derived binding rule as migration;
  cleanup is never implied by migration success. The final check is an
  observation, not an atomic transaction across the source and provider; the
  operator must keep other destination writers quiescent during cleanup.

### Scenario 11 — Unregistered or unsupported source

- **Starting state**: The requested source alias is not registered, or its
  format is unsupported.
- **User action**: Any dry-run, migration or scoped run.
- **Expected outcome**: Refused with guidance to register the source; Sandbox
  never falls back to reading the file directly.

### Scenario 12 — Existing users who never opt in

- **Starting state**: No profile map exists.
- **User action**: Existing `secrets` commands, shell startup, MCP tools.
- **Expected outcome**: Identical behaviour to today.

### Scenario 13 — Provider returns a change-approval request

- **Starting state**: The destination routes creates through an approval policy.
- **User action**: A migration attempts a mapped create.
- **Expected outcome**: Where detectable, the policy refuses admission before
  writes. A response containing an approval request instead of a directly
  created item never counts as created or verified; the name is unresolved and
  the run stops, retaining prior receipts. Sandbox never approves or merges the
  provider's pending change on the operator's behalf.

### Scenario 14 — Source or map changes after approval

- **Starting state**: The human approved a source revision and complete map.
- **User action**: The source or map changes before completion or resume.
- **Expected outcome**: Further writes refuse until a new human approval binds
  the current source and map. Previously recorded writes are retained and are
  not silently reclassified or recreated.

### Scenario 15 — Another controller or concurrent process resumes

- **Starting state**: A controller has a pending or unknown migration journal.
- **User action**: Another local process starts the same profile/destination, or
  another controller attempts to resume without that journal.
- **Expected outcome**: Concurrent local migration refuses before reading values
  or issuing writes. Resume occurs only on the journal-owning controller; a
  different controller refuses and directs the operator to that controller.
  Cross-controller concurrency is not claimed to be prevented by a local lock;
  the operator must designate one active migration owner per profile/destination.

## Proposed Product Behavior

- **Profile map**: an operator-owned, reviewable mapping from source entry name
  to exactly one profile, its destination name, and the consumer name it keeps.
  The map holds names only, never values. Unmapped entries remain in the source
  and are reported, not migrated.
- **Preflight**: before any write, every name in the selected profile is
  checked for mapping completeness, conflicts, destination existence and
  credential scope. Any blocking result stops the run with zero writes.
- **Dry-run default**: every migration and cleanup operation reports only
  unless explicitly confirmed; dry-run output is metadata only.
- **Create-only writes**: one destination, one profile, fixed paths from the
  map; no overwrite, no update, no delete at the destination.
- **Durable journal**: per-name states covering at least pending, attempted,
  created, pre-existing, conflict, reconciled, `acceptance_unknown`, verified,
  failed and unresolved. The journal holds non-secret selectors, approval and
  controller references, names, states and timestamps only. No value-derived
  comparison material is retained. Resume reconciles unknown states before
  any new write.
- **Approval and resume**: only the human operator may approve or reapprove.
  Approval binds the operator, controller, ownership profile, source revision,
  complete profile map, exact destination and reviewed authority. It is bounded
  to at most one hour and cannot outlive the credential. The source revision
  must not expose or persist a value-derived comparison digest. These paths
  neither persist nor emit the existing keyed opaque source revision. Their
  source binding must invalidate approval on every content change, including a
  value-only edit with unchanged names and restored modification time. If source
  continuity cannot be established, fresh human approval is required. Agents may
  execute only inside an unexpired human approval; they may not self-confirm,
  widen or extend it. Non-interactive and MCP approval attempts are refused.
  The operator-only interface follows the existing same-user trust boundary;
  this is not a claim to distinguish a malicious same-UID actor from a human.
- **Value handling**: values move from the registered source to the
  destination in memory only. No plaintext intermediate export, temporary file,
  hash, length, prefix or comparison digest is written, printed or logged.
- **Scoped run**: an opt-in profile selector on the bounded direct-argv run
  path injects only an explicitly selected subset of that profile's mapped
  entries under consumer names; selecting a profile alone grants no keys.
- **Credentials**: migration authority is narrow to one ownership profile and
  bound to its configured HTTPS endpoint and complete reviewed map. The
  operator-provisioned bootstrap credential may remain in the native OS store;
  automation never lists or deletes it. The derived, short-lived access
  credential remains memory-only and is revoked best-effort at completion.
  Neither credential enters a journal or ordinary file. An authority change
  invalidates approval and requires new credential placement after review.
- **Evidence**: a sanitized completion record (profile, counts per state,
  names, timestamps, revocation outcome) suitable for review.
- **Platforms**: works on Linux and macOS controllers without manual IPC or
  socket path setup.

## Constraints and Dependencies

- Builds on spec 067 (Infisical secret sources). That feature's product rules
  (CLAUDE.md gotcha 32) forbid provider mutation and limit provider operations
  to one login plus one exact item read. This feature needs create-only write
  authority, so it must introduce that authority as a separate, narrower
  capability without widening the existing read path. A migration adapter may
  apply only where its create-only conflict and response contracts are
  established; an unsupported provider contract refuses before writes.
- Registered sources only; the `secret-inspection` skill's lowest-disclosure
  rules apply to every operation (`docs/secret-inspection.md`).
- The existing organizer (`sandbox/secrets/organizer.py`) groups by documented
  product and service sections and preserves names; it has no notion of
  account profile. The existing provider adapter is read-only and supports one
  configured manager per source.
- Tailscale or private reachability is not secret authority; endpoints must be
  verified HTTPS origins (spec 067).
- Constitution IV: completion requires live evidence, but live verification
  must use newly generated synthetic entries on a separate disposable,
  non-production Infisical instance. No test project or write identity is
  created on secrets.asb.bd before human security review. Storage-contract
  evidence for the real destination is a real-migration admission requirement,
  not a reason to inspect or mutate production during feature acceptance.
  Naming this target does not grant new provisioning, credential or restricted-
  runtime authority; any required approval is obtained on a concrete test plan
  after implementation. A missing live test remains explicitly unverified.
- Constitution V and VI: re-runnable operations; no removal or in-place
  disabling of existing secret paths.
- Office and personal account separation is the operator's standing policy;
  this feature enforces it on new opt-in ownership-profile paths only. Existing
  paths keep their current behavior and are not claimed to enforce ownership.
- The user has authorized implementation, but not real credential migration or
  production release before a concrete human security review. No commit or
  push for this PRD phase.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Ownership classification | Explicit operator map; ambiguous, duplicate or conflicting names fail closed | No inferred ownership across accounts | User request |
| Destination ownership | Existing secrets.asb.bd; separate office and personal projects and identities | Explicit server-enforced account isolation without separate hosting | User explicit decision, 2026-10-26 |
| Approval issuer | Human-only approval and reapproval; agents execute only within a live grant | Existing explicit-user-authorization policy; no agent self-approval | Standing User Context authorization rule |
| Feature acceptance target | Separate disposable non-production instance with synthetic entries | Existing disposable-testing and no-production-change-before-review rules | Standing user safety rules and conservative test scope |
| Consumer names | Preserved through explicit mapping | Existing consumers keep working | User request, feedback 190edc27 |
| Destination writes | Create-only; no overwrite, update or delete | Prevents silent loss of existing manager data | User request |
| Source deletion | Never automatic; separately approved after verification | Source remains the fallback | User request, feedback 190edc27 |
| Default mode | Dry-run, metadata-only output | Matches existing `organize` default | User request, existing behaviour |
| Unknown write result | Journaled `acceptance_unknown`, reconciled before any retry | Blind retry can duplicate or mask state | User request |
| Expiry resume | Re-approval by the same authority and scope | Prevents scope drift across sessions | User request |
| Value exposure | No values, hashes, lengths or plaintext intermediates anywhere | Existing secret policy | User request, `secret-inspection` skill |
| Shell integration | Direct-argv bounded run with one selected profile; no shell sourcing; default unchanged | Avoids loading all secrets into every shell | User request |
| Revocation | Best effort; unconfirmed outcome surfaced as `cleanup_unknown` | Honest reporting over false success | User request |
| Provider admin scope | Not granted or chosen by this feature | Least privilege | User request |
| Release and real migration | Blocked until concrete human security review | Consequential credential change | User request |

## Open Questions

None for product scope. Real deployment still requires the concrete security
review and live storage-contract evidence described below; source inspection
alone is not deployment evidence.

## Conservative Scope Resolutions

- Mutation admission requires a supported, reviewed create-only storage
  contract. The v0.166.5 source contains a shared-key unique constraint for its
  V2 path; legacy storage is unsupported. Before real migration, the operator
  must supply live metadata evidence that the destination uses that path and
  its shared-key unique constraint is present, valid and ready. Missing,
  expired or mismatched evidence refuses mutation. No self-reported version
  string alone proves database readiness, and source-only evidence is never
  labeled live verification. Normal consumption requires no new write grant.
- A new, explicitly approved migration authority is distinct from spec 067's
  read-only credential. It authorizes only the reviewed profile, exact mapped
  names and fixed destination. Normal read-only operations and locators retain
  their current behavior. Project and identity provisioning remain external.
- The operator explicitly authorizes the existing `secrets.asb.bd` manager for
  both office and personal secrets, with separate projects and identities.
  A name prefix alone is insufficient isolation. Each migration approval binds
  the exact project, identity, environment, path and mapping of one profile;
  neither profile may target or consume the other profile's project or identity.
- A metadata-only exact-item existence check must be available within the
  migration authority. Without it, dry-run reports unknown and mutation refuses.
- One controller owns each migration and its journal. Local concurrent mutation
  of one profile/destination refuses; journals are not copied or moved as an
  implicit resume mechanism. Cross-controller ownership is a human operational
  condition, and provider uniqueness remains independently required.
- Verification uses the migration authority, in memory, only for newly created
  items or attempts recorded in the matching protected journal. Initial
  pre-existing items cause a metadata-only refusal and are never value-read.
- A source key has one owner. A consumer name may be reused across profiles only
  through distinct, independently mapped source entries and destinations.
- Additional named profiles may exist, but each must declare its office or
  personal ownership. No invocation combines ownership domains.
- Bounded commands require explicit keys within one selected profile. A profile
  selector never means all profile entries or an interactive shell export.
- Missing revocation authority is reported as `cleanup_unknown` with a concrete
  operator step, while credential expiry remains bounded. No success claim
  implies that an external gateway token or OS-store item was removed.
- Journals and sanitized completion evidence remain owner-only and controller-
  local until an operator explicitly requests retention cleanup. Automatic
  cleanup never removes the journal needed to reconcile an unknown write.

## Acceptance Outcomes

- A dry-run against a disposable test source reports per-profile counts and
  names only; a scan of all output, logs, journals and evidence finds no test
  value, value hash, length or prefix.
- With any unmapped, ambiguous, duplicate or conflicting name in the selected
  profile, a confirmed migration performs zero destination writes.
- Against a disposable test destination containing a pre-existing entry, a
  confirmed migration leaves that entry unchanged, reports it as pre-existing,
  and performs zero destination writes for the entire batch.
- After an injected interruption on a write, resume reports that name as
  `acceptance_unknown`, reconciles it, and the destination ends with exactly one
  entry for it.
- After an injected credential expiry, resume refuses without re-approval,
  refuses approval with a different authority or wider scope, and with matching
  re-approval completes without repeating confirmed writes.
- A scoped run with one profile selected gives the child only that profile's
  consumer names; a child probe that reports names only finds no other
  profile's names.
- With no profile map present, existing `secrets` commands, shell startup and
  MCP tools behave exactly as before (regression comparison on the same test
  source).
- After completion, the derived access credential is revoked, or the record
  states `cleanup_unknown` with the required human action; native bootstrap
  cleanup remains explicitly operator-managed. The source is byte-for-byte
  unchanged until a separately approved cleanup.
- The flow runs on both a Linux and a macOS controller without manual IPC path
  setup.
- No real operator credential is migrated before a human security review signs
  off on write authority, credential lifetime and revocation.
- Wrong ownership profile, wrong destination, multiple selected ownership
  domains, or a map targeting the other profile's project or identity refuses
  before any secret read or provider write.
- An unregistered or unsupported source refuses without a raw-file fallback.
- A source-cleanup request involving unverified names or a stale source revision
  refuses without source mutation.
- Missing, expired, mismatched or invalid storage-contract evidence, a version
  string offered as proof, and legacy V1 storage all refuse mutation.
- A selected ownership profile with no explicit keys grants no secret values;
  a child given a subset receives exactly that subset, not all profile entries.
- A destination item inserted after initial preflight is not overwritten; later
  writes stop and earlier confirmed receipts remain in the journal.
- An approval-request or malformed create response never increments created or
  verified counts and cannot make any source name cleanup-eligible.
- A value-only edit of a mapped entry, even with unchanged names and restored
  modification time, or any map change after approval refuses further writes
  under that approval. The existing keyed source revision never appears in
  these paths' output, journal or approval records.
- Approval/reapproval through MCP or non-interactive interfaces refuses; an
  expired approval cannot be extended or replaced by an agent.
- A second local migration for the same profile/destination refuses, and resume
  from a controller without the owning journal refuses with controller guidance.
- Dry-run without migration authority reports destination checks as unchecked;
  an unavailable value-free existence check reports unknown and prevents writes.
- Migrating a profile does not change its explicitly configured scoped-run read
  origins. Entries within one profile may use different declared origins in one
  run. An origin belonging to the other ownership domain or to an unregistered
  source/destination refuses before value access.
- Cleanup approval through MCP or non-interactive interfaces refuses. A name
  whose destination was deleted, changed, approval-pending or unverifiable at
  the final check remains in the source even when an older receipt said verified.

## Risks and Assumptions

- **Risk**: The hosted manager may lack a create-only permission, forcing a
  broader write identity than the policy allows; the feature may then be
  blocked at security review.
- **Risk**: Check-then-create can race with another writer. The provider must
  enforce uniqueness atomically; a transaction around an insert is insufficient.
  Infisical v0.166.5 migrations 20260623144550 and 20260623144551 remove duplicate
  V2 shared rows and then create a partial unique index on key and folder. The
  former is destructive and is explicitly outside this feature. The latter's
  deployed presence, validity and readiness must be evidenced before migration.
- **Risk**: In-memory verification still requires reading values back, which
  widens the read path during migration.
- **Risk**: Misclassification by the operator moves an office credential into
  a personal destination; fail-closed preflight reduces but cannot eliminate
  this.
- **Risk**: Best-effort revocation may leave a live credential until it
  expires.
- **Risk**: The source and remote provider cannot be changed in one atomic
  transaction. Fresh cleanup verification reduces stale-receipt risk, but an
  external writer racing after that check remains an operational hazard;
  source cleanup requires a quiescent destination and separate human approval.
- **Assumption**: The registered source can be read entry by entry through
  existing Sandbox source handling without exposing values.
- **Assumption**: The hosted manager remains reachable over a verified HTTPS
  origin from both controllers.
- **Assumption**: The operator will provision any destination projects and
  identities outside this feature.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred; remaining
  deployment grants and live evidence are explicit admission requirements.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
