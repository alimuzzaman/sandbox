# Feature Specification: Secret Profiles and Create-Only Manager Migration

**Feature Branch**: `069-secret-profiles` (feature directory; work proceeds on the active `latest` branch)

**Created**: 2026-10-26

**Status**: Draft

**Input**: Ready PRD `specs/069-secret-profiles/prd.md` (`READY FOR SPECKIT`, independent `PASS`, 2026-10-26): "Separate registered secret source entries into office and personal profiles with create-only migration into hosted Infisical and scoped profile-only command use" (feedback record `190edc27`, user request "now fix it")

## Clarifications

### Session 2026-10-26

All items below were resolved from the authoritative PRD; no new operator decision was required.

- Q: What makes an entry "ambiguous" when ownership is never inferred? → A: Ambiguity is detected from the map only: an entry with no single explicit ownership-profile decision (unassigned, marked undecided, or assigned to more than one profile) is ambiguous and blocks; Sandbox never judges plausibility from names or values (PRD Goals, Scenario 2, Non-Goals).
- Q: Which destination is in scope, and is it built into the product? → A: Destinations are operator-configured metadata (verified HTTPS origin, project, identity, environment, path) per ownership profile. The operator's chosen deployment is the existing `secrets.asb.bd` manager with separate office and personal projects and identities; no project, identity or environment identifier is hard-coded in the product (PRD Decisions, Conservative Scope Resolutions).
- Q: What storage contract admits mutation? → A: Only the provider's V2 shared-secret storage with its key-and-folder unique index present, valid and ready, proven by operator-supplied live metadata evidence for the exact destination; V1/legacy storage, a version string, or source-only evidence refuses. Upgrading the provider or running its duplicate-removal migration is out of scope (PRD Conservative Scope Resolutions, Risks).
- Q: What does preflight read at the destination? → A: A value-free, exact-item existence check for every mapped name in the profile; no existing destination value is ever fetched. Without migration authority the result is "unchecked"; if the value-free check is unavailable the result is "unknown" and mutation refuses (PRD Scenario 1, Conservative Scope Resolutions).
- Q: How long may an approval live? → A: At most one hour and never longer than the access credential it governs; only the human operator grants or regrants it, and agents execute only inside an unexpired grant (PRD Proposed Product Behavior).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Metadata-only dry-run and fail-closed classification (Priority: P1)

The operator maintains an explicit ownership-profile map for a registered secret source and asks for a dry-run of one profile. Sandbox reports counts and names only: what would be created, what already exists at the destination, and which names are unmapped, ambiguous, duplicated or conflicting. Any blocking name stops a later migration before a single write.

**Why this priority**: Classification is the foundation of account separation; nothing else is safe without it, and it delivers value (a reviewed office/personal split) without any provider credential.

**Independent Test**: Against a disposable synthetic source and map, run the dry-run and confirm the report contents, that source and provider state are unchanged, and that no value, hash, length or prefix appears in any output, log, journal or evidence.

**Acceptance Scenarios**:

1. **Given** a registered source and a map covering some entries, **When** the operator requests a dry-run for one profile, **Then** a metadata-only report lists per-profile counts, would-create names, pre-existing names and unmapped/ambiguous/conflicting names, and source and provider state are unchanged.
2. **Given** no approved migration authority, **When** a dry-run runs, **Then** destination existence is reported as unchecked and no provider credential is required.
3. **Given** an entry assigned to two profiles, an undecided entry, or two entries mapped to one destination name, **When** the operator asks to migrate that profile, **Then** migration refuses with zero writes and lists each blocking name with its reason class.
4. **Given** an unregistered alias or unsupported source format, **When** any dry-run, migration or scoped run is requested, **Then** it refuses with registration guidance and never reads the file directly.
5. **Given** no profile map exists, **When** existing `secrets` commands, shell startup or MCP tools are used, **Then** behaviour is identical to today.

---

### User Story 2 - Approved create-only migration with a durable journal (Priority: P1)

Under a live human approval, the operator (or an agent inside that approval) migrates one profile into that profile's configured destination. Every name is created at its fixed mapped path, each attempt is journaled before and after, each created value is verified in memory, and a sanitized completion summary is produced. Nothing at the destination is ever overwritten, updated or deleted, and the source is unchanged.

**Why this priority**: This is the stalled manual toil the feature exists to remove, and it carries the highest risk of data loss or cross-account leakage.

**Independent Test**: On a separate disposable non-production manager with synthetic entries, run approved migrations covering a clean run, a pre-existing item, a mid-run conflicting insert, an injected interruption, and an approval-request response; inspect the destination, journal and evidence.

**Acceptance Scenarios**:

1. **Given** every entry of the profile is mapped, preflight passes and a live approval exists, **When** the operator confirms, **Then** each name is created once at its mapped path in that profile's destination only, journaled, verified in memory, and summarized by counts and names, and the source is byte-for-byte unchanged.
2. **Given** a destination name already exists at initial preflight, **When** migration is confirmed, **Then** the whole batch refuses with zero writes, the item is unchanged, reported as pre-existing, and its value is never fetched.
3. **Given** another writer creates a mapped name after preflight, **When** this run reaches that name, **Then** writes stop at that name, earlier confirmed receipts remain journaled, the conflicting item is unchanged and its value is not fetched.
4. **Given** a create times out, the connection drops or the controller stops after sending, **When** the operator resumes, **Then** the name is `acceptance_unknown` and is reconciled by an exact-item check before any new write: a match is recorded as reconciled (without claiming authorship), a proven-absent item is eligible for exactly one create, and a mismatch stays unresolved; the destination ends with exactly one entry for that name.
5. **Given** the destination routes creates through a change-approval policy, **When** a create returns an approval request or a malformed response, **Then** the name is unresolved, the run stops, prior receipts are retained, created/verified counts do not increase, and Sandbox never approves or merges the pending change.
6. **Given** the destination's storage-contract evidence is missing, expired, mismatched, invalid, only a version string, or shows legacy V1 storage, **When** migration is confirmed, **Then** mutation refuses before any write.

---

### User Story 3 - Human-only approval, expiry and same-authority resume (Priority: P2)

Approval is granted only by the human operator through an interactive operator interface and binds the operator, controller, ownership profile, current source state, the complete map, the exact destination and the reviewed authority, for at most one hour. Expiry, a source or map change, or a different authority stops further writes until the human re-approves the same scope.

**Why this priority**: It keeps agents inside explicit human authority and prevents scope drift across sessions; it is required before any real migration but builds on User Story 2.

**Independent Test**: With synthetic data, inject credential expiry, a value-only source edit with restored modification time, a map edit, an MCP approval attempt, a second local process and a resume from a second controller; confirm each refuses as specified and matching re-approval completes without repeating confirmed writes.

**Acceptance Scenarios**:

1. **Given** the migration credential expires mid-run, **When** the operator resumes, **Then** Sandbox stops at the next boundary, keeps the journal, and requires re-approval by the same operator for the same profile and destination; confirmed names are skipped.
2. **Given** a re-approval request from a different authority or for a broader scope, **When** it is presented, **Then** it is refused.
3. **Given** a value-only edit of a mapped entry (names unchanged, modification time restored) or any map change after approval, **When** further writes are attempted, **Then** they refuse until a new human approval binds the current state; recorded writes are neither reclassified nor recreated.
4. **Given** an approval or reapproval attempt through MCP or a non-interactive interface, or an agent trying to extend an expired approval, **When** it is made, **Then** it is refused.
5. **Given** a pending or unknown journal on one controller, **When** another local process starts the same profile/destination, **Then** it refuses before reading values; **When** a different controller tries to resume without that journal, **Then** it refuses and names the owning controller.

---

### User Story 4 - Scoped profile command run (Priority: P2)

The operator runs a bounded direct-argv command with one ownership profile and an explicit subset of that profile's mapped keys. The child receives exactly those entries under their preserved consumer names, read from each entry's declared origin (registered source or that profile's registered destination).

**Why this priority**: It replaces loading every credential into every shell with least-privilege injection, but is independent of migration and can ship after it.

**Independent Test**: With a synthetic map for two profiles, run a child probe that reports environment names only, selecting one profile and a key subset; confirm only that subset appears and no other profile's names are present.

**Acceptance Scenarios**:

1. **Given** a mapped profile with declared origins, **When** the operator selects that profile and an explicit key subset, **Then** the child receives exactly that subset under consumer names, with no shell evaluation and no values in Sandbox output.
2. **Given** selected keys within one profile with different declared origins, **When** the run starts, **Then** each is read from its own declared origin in the same run.
3. **Given** a profile selected with no explicit keys, **When** the run starts, **Then** no secret value is granted.
4. **Given** a request that selects one profile but targets the other profile's destination, selects both ownership domains, or uses an origin from the other domain or an unregistered origin, **When** it is made, **Then** it refuses before any secret read or provider write, with a reason class and no values.
5. **Given** a profile was migrated, **When** a scoped run executes, **Then** each entry's declared origin is unchanged by the migration.
6. **Given** no profile selection, **When** the existing run path is used, **Then** behaviour is unchanged.

---

### User Story 5 - Access revocation and sanitized evidence (Priority: P3)

On completion, failure or abandonment, Sandbox attempts to revoke the derived short-lived access credential, preserves the sanitized journal, and records an evidence summary a security reviewer can audit.

**Why this priority**: Honest cleanup reporting is required for security review but does not block delivering classification or migration in a test environment.

**Independent Test**: In the disposable environment, complete one run with revocation available and one with revocation authority missing; inspect the evidence record.

**Acceptance Scenarios**:

1. **Given** a completed, failed or abandoned migration, **When** completion or an explicit cleanup request occurs, **Then** revocation of the derived access credential is attempted and the outcome is recorded.
2. **Given** revocation cannot be confirmed, **When** the record is written, **Then** it states `cleanup_unknown` with the concrete next human action and never claims success.
3. **Given** any outcome, **When** cleanup runs, **Then** automation never lists or deletes OS credential-store entries; bootstrap credential cleanup remains an operator action.

---

### User Story 6 - Separately approved source cleanup (Priority: P3)

After a profile's migration is verified, the human operator may separately approve removing migrated entries from the source. Each name is re-verified in memory against its destination under a fresh approved authority immediately before removal.

**Why this priority**: The source remains the fallback; cleanup is optional and last.

**Independent Test**: With synthetic data, request cleanup with a mix of verified, unverified, deleted, changed and approval-pending destination items, plus an MCP approval attempt; confirm only freshly verified names are removed.

**Acceptance Scenarios**:

1. **Given** a verified profile migration and a separate human cleanup approval, **When** cleanup is confirmed, **Then** only names freshly re-verified against their destination are removed from the source.
2. **Given** a name whose destination is missing, changed, approval-pending or unverifiable at the final check, **When** cleanup runs, **Then** that name remains in the source even if an older receipt said verified.
3. **Given** unverified names or a stale source state, **When** cleanup is requested, **Then** it refuses without source mutation.
4. **Given** a cleanup approval via MCP or a non-interactive interface, **When** it is made, **Then** it is refused.

---

### Edge Cases

- A mapped source key is absent from the source at preflight: reported by name as a blocking mapping error; zero writes.
- The same consumer name is used in both profiles: allowed only through distinct, independently mapped source entries and destinations; one source key never has two owners.
- An additional named profile lacks an office or personal ownership declaration: the map is invalid and every migration and scoped run using it refuses.
- Map points a profile at the other profile's project or identity: refused before any secret read or provider write.
- Source continuity cannot be established (for example, the source was replaced): fresh human approval is required.
- Controller stops between journaling "attempted" and sending the request: resume reconciles by exact-item check; it never assumes not-sent.
- Approval expires between two names: the run stops at that boundary with all prior receipts intact.
- Cleanup races with an external destination writer after the final check: not prevented atomically; the operator must keep the destination quiescent, and the evidence states that the check was an observation.
- Two controllers both hold authority for one profile/destination: not prevented by a local lock; the operator designates one owner, and provider uniqueness remains required.
- Retention cleanup is requested while a journal holds an unresolved or `acceptance_unknown` name: that journal is retained.

## Requirements *(mandatory)*

### Functional Requirements

**Ownership profiles and classification**

- **FR-001**: The system MUST let the operator keep an explicit, reviewable ownership-profile map that assigns each registered source entry name to exactly one ownership profile, a destination name and a preserved consumer name; the map MUST contain names and selectors only, never values.
- **FR-002**: Every ownership profile MUST declare whether it belongs to the office or personal ownership domain; `office` and `personal` are the initial profiles and additional named profiles are permitted.
- **FR-003**: The system MUST NOT infer, suggest-apply or default any ownership assignment.
- **FR-004**: Classification MUST fail closed: an unmapped, undecided, multiply assigned, duplicate-destination or other-domain-targeting name in the selected profile MUST block migration, reported by name and reason class only.
- **FR-005**: Each ownership profile's destination MUST be operator-configured metadata (verified HTTPS origin, project, identity, environment, path); the product MUST NOT hard-code any operator project, identity or environment identifier, and a name prefix alone MUST NOT count as isolation.
- **FR-006**: The office and personal domains MUST use separate destination projects and identities; a map or request that targets or consumes the other domain's project or identity MUST be refused before any secret read or provider write.
- **FR-007**: Ownership profiles MUST be a new concept; existing `useProfiles`, `use-profile`, validation profiles and the reserved `personal` source alias MUST keep their current meaning and behaviour.

**Dry-run and preflight**

- **FR-008**: Every migration and cleanup operation MUST default to dry-run and change no source or provider state unless explicitly confirmed; sanitized audit records may still be appended.
- **FR-009**: Dry-run and all other output of this feature MUST be metadata only: counts, names, states, reason classes and timestamps; no value, hash, length, prefix, comparison digest or file content.
- **FR-010**: Classification MUST work without any provider credential; without approved migration authority, destination existence MUST be reported as `unchecked`.
- **FR-011**: Preflight MUST check every name in the selected profile for mapping completeness, conflicts, destination existence and credential scope before any write; any blocking result MUST stop the run with zero writes.
- **FR-012**: Destination existence MUST be checked with a value-free exact-item check for every mapped name; the system MUST never fetch an existing destination value during preflight. If no value-free check is available, the result MUST be `unknown` and mutation MUST refuse.
- **FR-013**: A name pre-existing at the destination at initial preflight MUST refuse the whole batch with zero writes.

**Create-only migration**

- **FR-014**: Migration MUST write one ownership profile into that profile's single configured destination per run, at fixed paths from the map, reading only the registered source.
- **FR-015**: Destination writes MUST be create-only; the system MUST NOT overwrite, update, rename or delete any destination entry.
- **FR-016**: Migration MUST NOT delete or rewrite any source entry; the source MUST remain byte-for-byte unchanged by migration.
- **FR-017**: A conflict detected after preflight MUST stop further writes at that name, leave the conflicting item unchanged and unread, and retain all prior confirmed receipts.
- **FR-018**: A response containing a provider change-approval request, or a malformed or unclassifiable response, MUST NOT count as created or verified; the name MUST be marked unresolved and the run MUST stop. The system MUST NOT approve or merge provider changes.
- **FR-019**: Where a provider change-approval policy is detectable before writes, admission MUST refuse.
- **FR-020**: Each newly created value MUST be verified against the source in memory only; verification MUST read back only items created by this run or attempts recorded in the matching journal, never items that pre-existed at initial preflight.
- **FR-021**: Values MUST move from source to destination in memory only, with no plaintext export, temporary file, hash, length, prefix or comparison digest written, printed or logged.

**Storage-contract admission**

- **FR-022**: Mutation MUST be admitted only for a provider whose create-only uniqueness and response contracts are established; an unsupported provider contract MUST refuse before writes.
- **FR-023**: Before mutation, the operator MUST supply live metadata evidence, bound to the exact destination, that it uses V2 shared-secret storage and that its key-and-folder unique index is present, valid and ready.
- **FR-024**: Missing, expired, mismatched or invalid evidence, a self-reported version string offered as proof, source-only evidence, or legacy V1 storage MUST refuse mutation. Source inspection MUST never be labeled live verification.
- **FR-025**: The feature MUST NOT upgrade the provider, change its schema, or run its duplicate-removal migration.

**Journal, reconciliation and concurrency**

- **FR-026**: The system MUST durably journal each write before and after it is attempted, with per-name states covering at least `pending`, `attempted`, `created`, `pre_existing`, `conflict`, `reconciled`, `acceptance_unknown`, `verified`, `failed` and `unresolved`.
- **FR-027**: The journal MUST hold only non-secret selectors, approval and controller references, names, states and timestamps; no credential and no value-derived material.
- **FR-028**: A write whose acceptance cannot be established MUST be recorded `acceptance_unknown` and MUST NOT be blindly retried or optimistically classified as a conflict.
- **FR-029**: On resume, the system MUST reconcile every unknown state by an exact-item check restricted to journaled attempts before any new write: match → `reconciled` (without authorship claims), proven absent → eligible for one create, mismatch → `unresolved`.
- **FR-030**: On resume, confirmed writes MUST be revalidated and MUST never be recreated.
- **FR-031**: Exactly one controller owns each migration and its journal; a concurrent local migration of the same profile/destination MUST refuse before reading values or writing, and a resume from a controller without the owning journal MUST refuse and name the owning controller. Journals MUST NOT be copied or moved as an implicit resume mechanism.

**Approval**

- **FR-032**: Only the human operator MAY grant or regrant migration and cleanup approval, through an interactive operator interface; approval attempts via MCP or non-interactive interfaces MUST be refused. This follows the existing same-user trust boundary and does not claim to distinguish a malicious same-UID actor.
- **FR-033**: An approval MUST bind the operator, controller, ownership profile, current source state, complete profile map, exact destination and reviewed authority, MUST last at most one hour, and MUST NOT outlive the access credential.
- **FR-034**: Agents MAY execute only inside an unexpired approval and MUST NOT self-confirm, widen, extend or replace it.
- **FR-035**: Any source content change (including a value-only edit with unchanged names and restored modification time), any map change, or any authority change after approval MUST refuse further writes until a new human approval binds the current state; if source continuity cannot be established, fresh approval MUST be required.
- **FR-036**: The source binding MUST NOT expose or persist a value-derived comparison digest, and these paths MUST neither emit nor persist the existing keyed opaque source revision.
- **FR-037**: Credential expiry MUST stop the run at the next boundary, keep the journal and require re-approval by the same authority and scope; re-approval by a different authority or for a broader scope MUST be refused, and confirmed names MUST be skipped on resume.

**Credentials and revocation**

- **FR-038**: Migration authority MUST be a new capability distinct from the existing read-only provider credential, narrowed to one ownership profile, its configured HTTPS endpoint, the exact mapped names and fixed destination; the existing read-only path and its rules MUST NOT be widened.
- **FR-039**: The operator-provisioned bootstrap credential MAY remain in the native OS credential store; automation MUST NOT list or delete OS credential-store entries.
- **FR-040**: The derived short-lived access credential MUST be held in memory only, and neither credential MAY enter a journal or ordinary file.
- **FR-041**: On completion, failure or abandonment, the system MUST attempt best-effort revocation of the derived access credential; an unconfirmed or impossible revocation MUST be reported as `cleanup_unknown` with the concrete next human action, never as success.
- **FR-042**: The feature MUST NOT create provider projects, identities, organizations or accounts, or grant list, search, export or admin scopes.

**Scoped run**

- **FR-043**: The bounded direct-argv run path MUST accept an opt-in ownership-profile selector plus an explicit subset of that profile's mapped keys, and MUST inject exactly that subset under consumer names; a profile selector alone MUST grant no values.
- **FR-044**: Each mapped entry MUST declare an explicit read origin (its registered source or its profile's registered destination); migration MUST NOT change origins, and selected entries within one profile MAY use different declared origins in one run.
- **FR-045**: A scoped run MUST refuse, before any value access, when it selects more than one ownership domain, targets the other domain's destination, or uses an origin from the other domain or an unregistered source/destination.
- **FR-046**: A scoped run MUST NOT evaluate a shell, export into an interactive shell, or print values; reading a destination for normal consumption MUST NOT require a write grant.

**Source cleanup**

- **FR-047**: Source cleanup MUST never follow automatically from migration; it MUST be a separate, dry-run-default operation requiring its own human approval bound by the same non-value-derived rules as migration.
- **FR-048**: Only verified, migrated names MAY be eligible; immediately before removal, each MUST be re-verified in memory against its destination under a fresh approved authority, and any missing, changed, approval-pending or unverifiable destination MUST keep that name in the source.
- **FR-049**: Cleanup involving unverified names or a stale source state MUST refuse without source mutation. Evidence MUST state that the final check is an observation and that the destination must be kept quiescent.

**Compatibility, evidence and platforms**

- **FR-050**: Only registered sources MAY be read; an unregistered or unsupported source MUST refuse with registration guidance and no raw-file fallback. Unregistered files, process environments and shell history MUST NOT be read.
- **FR-051**: With no profile map present or no profile selected, existing `secrets` commands (including `run`, `organize` and `migrate-zshrc`), shell startup files and MCP tools MUST behave exactly as before.
- **FR-052**: Each run MUST produce a sanitized, owner-only, controller-local completion record (profile, counts per state, names, timestamps, revocation outcome); journals and evidence MUST persist until the operator explicitly requests retention cleanup, and retention cleanup MUST NOT remove a journal needed to reconcile an unknown or unresolved write.
- **FR-053**: All operations MUST be safely re-runnable and MUST work on Linux and macOS controllers without manual IPC or socket-path setup.

### Key Entities

- **Ownership Profile**: A named profile (initially `office`, `personal`) with a declared ownership domain (office or personal) and one configured destination.
- **Profile Map Entry**: Source entry name → one ownership profile, destination name, consumer name and declared read origin. Names only.
- **Destination**: Operator-configured metadata for one profile: verified HTTPS origin, project, identity, environment and path. Never shared across domains.
- **Storage-Contract Evidence**: Operator-supplied live metadata proving the exact destination uses supported storage with its uniqueness guarantee present, valid and ready; has an expiry and a destination binding.
- **Migration Authority**: Narrow create-only capability for one profile and destination, distinct from the read-only credential; composed of an operator-managed bootstrap credential and a memory-only derived access credential.
- **Approval**: Human-only grant binding operator, controller, profile, source state, complete map, destination and authority; at most one hour and never past the access credential.
- **Migration Journal**: Durable, owner-only, controller-local per-name state log for one profile/destination run; non-secret contents only.
- **Completion Record**: Sanitized evidence summary of a run, including revocation outcome.
- **Scoped Run Request**: One ownership profile, an explicit key subset and a direct-argv command.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In acceptance runs on synthetic data, a scan of all output, logs, journals, approvals and evidence finds 0 test values, value hashes, lengths, prefixes or keyed source revisions.
- **SC-002**: 100% of migrations with any unmapped, ambiguous, duplicate, conflicting or other-domain name, any initial pre-existing destination name, or missing/expired/mismatched/V1/version-only storage evidence perform 0 destination writes.
- **SC-003**: After an injected write interruption and resume, the destination holds exactly 1 entry for the affected name and the journal records its reconciliation.
- **SC-004**: After an injected credential expiry, resume refuses without re-approval and refuses a different or wider authority; matching re-approval completes with 0 repeated confirmed writes.
- **SC-005**: A destination item inserted after preflight is unchanged after the run, and 100% of earlier confirmed receipts remain in the journal.
- **SC-006**: Approval-request and malformed create responses add 0 to created or verified counts and make 0 names cleanup-eligible.
- **SC-007**: A name-only child probe in a scoped run reports exactly the selected subset and 0 names from another profile; a profile selection with no keys yields 0 secret values.
- **SC-008**: With no profile map present, a regression comparison on the same test source shows 0 behavioural differences in existing `secrets` commands, shell startup and MCP tools.
- **SC-009**: Every completion record shows either confirmed revocation or `cleanup_unknown` with a human action; 0 records claim success for unconfirmed revocation.
- **SC-010**: The source is byte-for-byte unchanged after migration, and cleanup removes 0 names that were unverified, missing, changed, approval-pending or unverifiable at the final check.
- **SC-011**: 100% of approval and cleanup-approval attempts via MCP or non-interactive interfaces, second-local-process starts and wrong-controller resumes are refused.
- **SC-012**: The full acceptance flow completes on one Linux and one macOS controller with 0 manual IPC or socket-path steps.
- **SC-013**: 0 real operator credentials are migrated and 0 test projects or write identities are created on the production manager before a human security review signs off.

## Assumptions

- The registered source can be read entry by entry through existing Sandbox source handling without exposing values; dotenv-family sources (the `~/.zshrc.secrets` family) are the initial supported format.
- The destination is a hosted Infisical manager (the existing read-only source integration is spec 067); the operator's chosen deployment is `secrets.asb.bd` with separate office and personal projects and identities, supplied as configuration, not product constants.
- Destination projects and identities are provisioned by the operator outside this feature.
- The hosted manager is reachable over a verified HTTPS origin from both controllers; private reachability is not secret authority.
- Feature acceptance uses newly generated synthetic entries on a separate disposable, non-production manager; any provisioning or restricted-runtime approval is obtained on a concrete test plan after implementation, and a missing live test is reported as unverified.
- The evidence-freshness window for storage-contract evidence is a planning and security-review parameter; until set, evidence without a valid expiry or destination binding is treated as invalid.
- The operator designates one active migration owner per profile/destination across controllers and keeps the destination quiescent during source cleanup.
- Known risks accepted for security review: the provider may lack a create-only permission; in-memory verification widens the read path during migration; operator misclassification cannot be fully prevented; best-effort revocation may leave a credential live until expiry.
- Implementation is authorized; real credential migration and release are not, until a concrete human security review signs off on write authority, credential lifetime and revocation.
