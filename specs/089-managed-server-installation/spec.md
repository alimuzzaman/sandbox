# Feature Specification: Managed server installation

**Feature Branch**: plan/shared-server-installation
**Created**: 2026-11-11
**Status**: Draft, source implementation authorized; live operations separately gated
**Input**: Reviewed READY FOR SPECKIT PRD in this directory

## User Scenarios & Testing

### User Story 1: Discover genuine installation authority (Priority: P1)

Admitted operators discover one installation independent of their SSH home or alias.

**Why this priority**: Shared credential and worker consumers need real authority before execution.
**Independent Test**: Initialize an isolated owner fixture, read its binding twice and reject replacement or policy drift.

1. Given initialized owner or managed state, when admitted discovery runs, then installation/incarnation/machine identities and installed policy/runtime evidence are returned without credential values.
2. Given missing or malformed binding, when discovery runs, then it refuses and creates nothing.
3. Given changed binding/policy/runtime, when a consumer validates a prior binding, then it refuses before side effects.
4. Given a joined registration, when refresh or ordinary target lookup observes a changed machine, then it refuses without replacing the saved pin.

### User Story 2: Install and update managed authority (Priority: P2)

Administrators explicitly install a fixed service identity and protected executable/configuration layout.

**Why this priority**: Shared paths must have coherent ownership and lifecycle.
**Independent Test**: Prepare an isolated install plan, validate ownership/release prerequisites and check generated unit and collision refusals.

1. Given personal mode, when no managed opt-in exists, then state selection is unchanged.
2. Given unrelated accounts or occupied paths, when install is requested, then it refuses before altering those resources.
3. Given a verified release and approved identity, when explicit install/update is applied, then only validated release selection is committed and stable installation identity survives.
4. Given an interrupted update, when resumed, then previous validated selection is retained or admission remains fenced until recovery proves the active version.
5. Given managed removal, when explicitly requested, then service removal preserves state, volumes, credentials and backups.

### User Story 3: Stage and recover migration (Priority: P3)

Administrators use a plan-bound migration with encrypted recovery proof, fenced writers and retained source.

**Why this priority**: Directory relocation can otherwise lose data or split authority.
**Independent Test**: Copy a private fixture under a durable migration identity, interrupt/resume, verify inventory/digests and exercise rollback boundaries.

1. Given active writers, unresolved bind mounts, missing backup/restore proof or conflicting target data, when cutover is requested, then it refuses.
2. Given consistent staged data and proven old-owner fencing, when cutover is confirmed, then exactly one authority is selected and data/identity/receipts survive.
3. Given no new writes, when rollback is approved, then fenced new authority can return to retained source.
4. Given new writes or uncertain effects, when stale-source rollback is requested, then it refuses and requires reconciliation/reverse migration or forward repair.

### Edge Cases

Symlinked or foreign-owned state; malformed policy; reused installation identity on cloned hosts; changed machine identity; stale discovery; source changes during copy; SQLite WAL; live binds retaining old paths; mixed workload ownership; failed fsync/promotion; unknown request acceptance; simultaneous controllers; service reboot resurrecting old authority; insufficient space; untrusted source archives.

## Requirements

### Functional Requirements

- **FR-001**: Managed mode MUST be explicit and preserve personal default path/credential selection.
- **FR-002**: Managed state MUST use the accepted /var/lib/sandbox layout, root-controlled /usr/local/lib/sandbox releases and /etc/sandbox policy; /run/sandbox MUST contain only ephemeral authority.
- **FR-003**: Fixed sandbox:sandbox identity MUST be allocated only after collision/ownership checks. Unknown ownership MUST refuse; no recursive chown of workload data.
- **FR-004**: Supported lifecycle MUST explicitly initialize genuine random installation/incarnation identity in both layouts. Read APIs MUST never initialize or fabricate identity.
- **FR-005**: Binding MUST include canonical machine identity, current service marker, runtime revision, policy revision/digest, layout and assembled versioned capabilities. Identity MUST survive relocation/upgrades and differ for a replacement installation.
- **FR-006**: Authenticated discovery MUST use existing ControlGate and retain admitted operators' full owner/approved-sudo policy. No bearer distribution or speculative admission change.
- **FR-007**: Server consumers MUST revalidate current binding including policy before use. Unavailable/uninitialized/drifted authority MUST fail closed.
- **FR-008**: Executable release, administrator policy and mutable source/job authority MUST remain separate. Untrusted workloads MUST NOT select broker code, arbitrary privileged paths or images.
- **FR-009**: Capabilities MUST come from assembled installed handlers, never configuration claims, context frames or release descriptors alone. 085/086 own their execution mechanisms.
- **FR-010**: Installation/update MUST be previewable, explicit, idempotent and revision-bound. Invalid release or interruption MUST NOT select unvalidated code.
- **FR-011**: Managed removal MUST preserve state, volumes, secrets, backups and retained migration sources; destructive deletion is excluded.
- **FR-012**: Migration MUST use explicit source/destination/installation/request identity and versioned schema-aware locator updates. Original receipts/digests MUST remain intact.
- **FR-013**: Admission MUST fence every known writer before cutover, including legacy SSH clients, jobs, timers and old services. Unknown writer state MUST block cutover.
- **FR-014**: Migration MUST require verified encrypted backup and disposable restore proof through supported adapters, plus storage/bind/ownership inventory. Missing coverage MUST refuse affected cutover.
- **FR-015**: Stage/copy verification MUST reject escapes, source changes and destination conflicts and retain original data; resume MUST use the same journal identity.
- **FR-016**: Cutover MUST select exactly one active authority and reconnect aliases to that identity. Old lock inodes or stale services MUST NOT retain authority.
- **FR-017**: Rollback after new writes MUST refuse stale-source selection. Ambiguous job/delivery/external effects MUST be reconciled without blind replay or new request identity.
- **FR-018**: Existing project/workspace/job/delivery/lease/volume/network/domain/credential identities MUST survive migration. Runtime paths MUST be regenerated or schema-migrated, never global text replacement.
- **FR-019**: Existing xCloud/root ingress 80/443, apex /mcp and protected shared workloads MUST remain unchanged without separately scoped approval.
- **FR-020**: Public contracts MUST carry docs, tests and release/revision evidence. Live acceptance and cutover require root's explicit serialized authority.
- **FR-021**: Administrator journals MUST use root-owned /var/lib/sandbox-installation outside service-writable ancestors; operation lease anchors MUST use root-owned /run/lock/sandbox-installation. Parent and opened-FD authority, same-anchor lookup and atomic publication MUST be validated.
- **FR-022**: Supported CLI, HTTP, MCP and detached workers MUST hold shared installation leases; lifecycle exclusivity MUST refuse busy writers without closing underneath them. Legacy source sealing requires actual handle/mapping/bind evidence plus explicit administrator quiescence. Full sudo policy is retained and intentionally bypassing root administrators are outside the proof.

### Key Entities

- Installation binding: durable installation and incarnation plus live machine/service/runtime/policy evidence.
- Managed layout: approved immutable release, configuration, durable state and ephemeral paths.
- Lifecycle plan: expected identities/revisions, collision observations and approved actions.
- Migration journal: stable request, source/destination inventory, phase and recovery boundaries.
- Recovery proof: verified encrypted set and restore-drill coverage bound to the migration inventory.
- Capability registration: assembled trusted implementation and version, not user configuration.

## Success Criteria

- **SC-001**: Two aliases discover the same initialized installation; repeated reads create no files or identity changes.
- **SC-002**: All wrong-machine, malformed binding and changed-policy fixtures refuse before effects.
- **SC-003**: Existing personal selection fixtures produce unchanged locations without opt-in.
- **SC-004**: Account/path collision, unverified release and unsupported recovery fixtures refuse without changing existing data.
- **SC-005**: Interrupted disposable staging resumes under one request with identical verified data and retained source.
- **SC-006**: Cutover cannot pass with any unresolved writer/bind/backup/restore prerequisite; stale rollback after new writes refuses.
- **SC-007**: Operator package identifies exact candidate revision, proofs, service changes, protected resources and before/after-write rollback procedures.

## Clarifications

### Session 2026-11-11

- User accepted fixed managed paths and sandbox service identity; current full owner/sudo policy stays unchanged.
- Root alone authorizes live mutation/credentials/cutover. Per-host UID, backup destination, retention and maintenance window are cutover inputs, not source defaults.
- 085 owns worker/listener execution and 086 owns actor/grant/credential consumption. This feature supplies genuine lifecycle binding and verified extension registration.
- No full suites or repeated builds in this session; focused helper evidence does not replace later authorized live acceptance.
