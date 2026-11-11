# Product Requirements Draft: Managed server installation

**Status**: Refined
**Created**: 2026-11-11
**Last Refined**: 2026-11-11
**Input**: Implement the accepted shared-server recommendation; root retains live cutover authority.
**Drafting Configuration**: Personal Codex gpt-6.1-sol high
**Final Validation**: PASS, independent personal Codex gpt-6.1-sol high
**Validated On**: 2026-11-11
**Artifact Owner**: speckit-refine
**Next Stage**: speckit-specify

## Problem and Motivation

Shared Sandbox state and executable authority currently belong to a person's home. Another admitted operator can join the service while SSH actions still resolve a different home and process identity. Moving directories alone cannot unify authority or preserve concurrent writers, credential use, binds and recovery records.

## Users and Desired Outcomes

- Administrators maintain a non-personal shared installation and can preview, resume or recover migration without losing workloads.
- Admitted operators discover genuine installation authority and use supported operations with intended full owner authority, without copied owner bearer credentials.
- Personal users retain existing installation selection without opting into managed mode.
- Controllers detect replacement installations and policy drift before side effects.

## Goals

- Explicit managed mode with stable service ownership and installation identity.
- Executable and administrator policy authority separate from mutable source/job state.
- Authenticated read-only installation discovery in existing owner and managed layouts.
- Supported lifecycle, migration planning, staging and recovery checks preserving data and identity.
- Concrete cutover package with fencing, backup proof and rollback boundaries.

## Non-Goals

- Changing admitted operators' current full owner and approved sudo access.
- Automatically migrating, deploying or activating services during source implementation.
- Replacing xCloud ingress or changing protected workloads, xspeedcache.com apex or /mcp.
- Implementing 086 credential consumption, actor authentication or admission ledger.
- Claiming backup/migration/capability proof when required adapters or live evidence are absent.

## Product Scenarios

### Scenario 1: Existing joined controller

Starting with missing service metadata, an admitted operator refreshes their registration. Authenticated service and machine evidence is recorded locally, preserving existing pins and other fields. Changed identity refuses. No bearer copying or service reinstall occurs.

### Scenario 2: Managed installation

An administrator previews and explicitly confirms installation on an eligible Linux host. One stable non-personal service owner owns private state; executable releases and administrator policy remain protected. Personal mode remains unchanged.

### Scenario 3: Discovery and drift

An admitted operator discovers an initialized owner or managed installation. They see stable installation, incarnation and machine identity, current service/runtime/policy evidence, and only capabilities implemented by installed handlers. A server consuming bound work checks current authority. Replacement or policy drift refuses. Uninitialized installations fail closed without fabricated identity.

### Scenario 4: Safe relocation

With approved migration and verified encrypted recovery coverage, an administrator fences/drains writers, stages and verifies data, then confirms cutover. Exactly one authority becomes active. Project/workspace IDs, jobs, deliveries, leases, volumes, domains and credential continuity survive. Workload downtime requires explicit approval. Migration never deletes the retained source.

### Scenario 5: Interrupted or unsafe migration

A crash, active writer, destination conflict or missing backup proof prevents unsafe transitions. Resume uses the same migration identity. Before new writes, rollback may return to retained old authority after fencing. After new writes, stale old state cannot become authoritative; reverse migration or forward repair requires reconciliation. Ambiguous operations are never blindly replayed.

## Proposed Product Behavior

Managed mode is opt-in. State belongs to an installation rather than an alias, SSH username or personal home. Relocation and upgrade preserve installation identity; replacement changes incarnation. Explicit supported lifecycle initialization establishes genuine identity in both owner and managed layouts. Readers never create identity.

Current authenticated admission and full owner policy remain unchanged. All lifecycle changes are previewable, attributable and explicitly authorized. Transfer is schema-aware and resumable, with durable request identities and proof requirements. Recovery uses supported encrypted procedures without exposing credential values. Missing required coverage refuses cutover.

Install refuses an existing unrelated service account or occupied destination;
an existing name is reusable only with proof it belongs to this installation.
Updates preserve the installation identity and select only a validated release.
An interrupted update keeps the previous validated release or remains fenced
until recovery proves which release is active. Managed removal requires explicit
scope and preserves state, volumes, credentials and backups. Generic personal
uninstall behavior must never delete managed data. Destructive data removal is
outside this feature.

## Constraints and Dependencies

- Accepted paths: root-controlled releases /usr/local/lib/sandbox, private state /var/lib/sandbox, administrator policy /etc/sandbox and broker-private child, ephemeral /run/sandbox. Fixed sandbox:sandbox non-login identity; numeric allocation checked during authorized install.
- Project ownership, authoritative registry, idempotency and docs-with-code remain binding.
- 086 owns real actor/grant/credential execution and consumes genuine installation discovery with current-policy checks. Advertise capabilities only for assembled installed handlers.
- Root freshly verifies target revision/compatibility before rollout; installed e46777b is historical supplied evidence. No whole outdated branch deployment or global controller migration.
- Preserve root Nginx/xCloud 80/443, shared dev/setup-plan/guided, volumes, domains and credential sources.
- Root serializes and authorizes all server mutations and credential operations. Source work cannot authorize its own live migration.
- This session permits short focused helper checks only. Full suites, soak tests and repeated builds excluded; unrun release gates explicit.

## Decisions

| Decision | Choice | Confirmed by |
| --- | --- | --- |
| Layout | Accepted managed paths, fixed identity | User resume brief |
| Personal mode | Existing defaults unchanged | User brief |
| Current access | Full owner and approved sudo for admitted operators | User coordination |
| Live authority | Root owns cutover and credentials | User resume brief |
| Discovery | Genuine identity in both layouts, authenticated read-only | Root and 086 coordination |
| Spec | Reserved 089; protect 085/086/088 | Root reservation |

## Open Questions

None blocking source implementation. Per-host UID allocation, backup destination, retention, migration window and privileged operation scope are concrete cutover inputs requiring root approval, never implicit defaults.

## Acceptance Outcomes

- Existing personal state/credential selection remains unchanged without managed opt-in.
- Two controllers discover the same installation; aliases and relocation do not change identity. Uninitialized/replaced authority refuses bound work.
- Normal source/job operations cannot replace executable releases or administrator policy.
- Both layouts support explicit idempotent identity initialization and read-only discovery without secret-value disclosure.
- Preview detects active writers, unsupported binds, conflicts, missing authority and unverified backup/restore coverage before cutover.
- Disposable migration preserves inventories, IDs, receipts and data, resumes interrupted staging and retains old data.
- Old authority cannot write after cutover; rollback to stale source after new writes refuses.
- Conflicting accounts or destinations refuse installation; failed updates never
  select an unvalidated release; managed removal never deletes stored data.
- Root receives candidate revisions, focused evidence, independent review, prerequisites and recovery procedures before host action.

## Risks and Assumptions

Docker/helper access is consequential authority requiring root review. Mixed ownership and bind mounts may require approved workload restarts; transparent relocation is not promised. Missing recovery coverage blocks affected cutover. Full-sudo users remain host administrators; directory separation does not isolate secrets from root. Policy change and installation replacement invalidate bound work even when aliases and paths stay equal.

## Readiness for Specification

- [x] Problem, users and outcomes explicit.
- [x] Goals and non-goals bound scope.
- [x] Primary and negative scenarios covered.
- [x] Constraints, dependencies and risks recorded.
- [x] Source choices confirmed; live inputs gated.
- [x] Measurable acceptance outcomes.
- [x] No blocking product question.
- [x] No implementation tasks, contracts or code included.
- [x] Latest independent review PASS.

**Readiness**: READY FOR SPECKIT
