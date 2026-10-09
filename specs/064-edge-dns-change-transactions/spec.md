# Feature Specification: Transactional Edge and DNS Changes

**Feature Branch**: `064-edge-dns-change-transactions`

**Created**: 2026-10-09

**Status**: Draft

**Input**: `specs/064-edge-dns-change-transactions/prd.md` (READY FOR SPECKIT; independent GPT-6.1-Sol PASS, round 2, 2026-10-09; feedback 6bd6bd1d, 50735fc8, 83cca354, 34af9b95, 075c6caf)

## Context

A hosted apply ends with its edge step: it upserts the declared hostnames'
DNS records at the provider, may switch the zone SSL mode to strict,
converges the front door (Caddy or nginx), and verifies the edge. This is
the only part of an apply that touches state outside the remote, and it
failed most often during the 2026-10-06/08 move to `xcloud-london`. Today's
rollback lives only in memory, cannot tell which target owns a record, and
reports failures as one string. Verification checks only the status class,
uses the controller's own resolver, and runs the authenticated check once.
This feature makes the edge step an ownership-checked, durably journaled
transaction with propagation-aware verification. Cloudflare remains the only
provider; the rules are provider-neutral.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Plan shows every change and refusal before anything changes (Priority: P1)

An operator runs `host plan` and sees, per declared hostname, every record
that will be created, updated, removed, adopted, left alone or refused, with
the reason, plus any zone SSL mode change. Records Sandbox does not own are
refused in the plan, and the apply then makes zero provider changes.

**Why this priority**: A foreign read-only record on an apex was knowable
before the first change, but it surfaced as a provider error mid-apply and
left stray records (feedback 83cca354). Ownership and preflight are also the
foundation for every other story.

**Independent Test**: Against a fixture zone with a Worker-managed read-only
AAAA on the apex, a Sandbox AAAA from an old origin, an IPv4-only new origin
and a non-strict SSL mode, run `host plan` and verify the per-record listing
and the `would_refuse` verdict; then run apply and verify zero provider
changes.

**Acceptance Scenarios**:

1. **Given** the fixture zone above, **When** `host plan` runs, **Then** it
   lists per hostname A (create or update to the new origin), AAAA (remove the
   stale Sandbox one; mark the apex `refused: provider_managed`), proxy status,
   TTL and the zone SSL mode change with its prior value, with verdict
   `would_refuse` naming the apex, and nothing has changed.
2. **Given** a declared hostname whose record carries another target's
   marker, **When** plan or apply runs, **Then** it is `refused:
   foreign_record` naming the owning target, and nothing changes.
3. **Given** a declared hostname with two records of one covered type, one of
   them not owned, **When** plan runs, **Then** it is `refused:
   ambiguous_records` naming both, and Sandbox never picks one to update.
4. **Given** an apex with MX and TXT records and an owned A record, **When**
   plan and apply run, **Then** MX and TXT are listed `left_alone`, the apply
   succeeds, and they are unchanged in every field.
5. **Given** a record this target owned whose type was changed at the
   provider to an uncovered type, **When** plan runs, **Then** it is a
   `foreign_record` refusal, not `left_alone`.
6. **Given** a legacy-comment record on a recorded preview hostname, **When**
   plan runs, **Then** it is `preview_owned`, refused, and never adoptable,
   changed or removed.
7. **Given** an owned record edited by hand at the provider, **When** plan
   runs, **Then** it is `drifted` naming each differing field; apply journals
   the live state as prior, so a forced rollback restores the hand edit field
   for field.

---

### User Story 2 - A failed edge step leaves the zone as it was (Priority: P1)

Every record and zone-setting change is journaled durably before it is made.
On any failure, the changes are reversed in reverse order, each reported
`restored`, `unchanged`, `restore_failed` or `rollback_conflict`. A rollback
never overwrites a later change. Leftovers block the next apply until they
are resolved or adopted.

**Why this priority**: Half-rolled-back edge steps took unrelated hostnames
off the air and left records nobody owned. Feature 062 needs these exact
rollback facts to release fences safely.

**Independent Test**: In a controlled run that fails the third of four
hostname changes, verify the first two records equal their journaled prior
state field for field (or are absent), each is reported `restored`, and the
outcome is `rollback_complete`.

**Acceptance Scenarios**:

1. **Given** four hostnames where the provider fails the third change,
   **When** the apply runs, **Then** the first two are restored to their
   journaled prior state (type, name, content, proxied status, TTL, comment,
   or absence), each listed `restored`, the failing record carries the
   provider's reason, and the result is `rollback_complete`; the retained
   outcome states the remote runtime as the delivery phase left it.
2. **Given** the same run where restoring a record also fails, or the provider
   becomes unreachable, **When** rollback runs, **Then** each remaining
   restore is attempted within a bounded budget and reported, the result is
   `rollback_incomplete` naming the exact record, its prior state and the
   supported manual step, and the next plan reports every leftover.
3. **Given** a controller crash after two of four journaled changes, **When**
   the next plan or apply runs for the target, **Then** the plan reports the
   interrupted transaction with each journaled change and prior state, and
   apply refuses until each is resolved or adopted.
4. **Given** an apply that changes the zone SSL mode and then fails, **When**
   rollback completes, **Then** the zone SSL mode equals its journaled prior
   value. Without `--allow-zone-ssl-change` the plan shows the change and the
   apply refuses before any change.
5. **Given** verification fails after the mutation step released feature
   060's remote-wide lease, **When** the apply finishes, **Then** it
   re-acquires the lease within a bounded wait and restores every journaled
   change, or ends `rollback_incomplete` naming each change still in place;
   zero changes remain unreported.
6. **Given** a later apply on the same zone relied on a changed zone SSL mode,
   or a record has since been moved by another remote, **When** the earlier
   transaction rolls back, **Then** that item is left as it is and reported
   `rollback_conflict` naming the later operation or live owner, and the
   result is `rollback_incomplete`; the later state is unchanged.
7. **Given** a previous `rollback_incomplete`, **When** plan runs, **Then** the
   leftover is reported with its journal reference; apply refuses until the
   leftover is resolved manually or adopted with `--adopt-records`, which
   re-reads the live record and never trusts the old journal's view.
8. **Given** the lease expires during the mutation step, **When** the holder
   notices, **Then** no further forward change is made and journaled changes
   are rolled back as in scenario 5.

---

### User Story 3 - Moving to a new origin leaves no stale family record (Priority: P1)

Moving a target to an origin without IPv6 updates A and removes the
Sandbox-managed AAAA in the same transaction. Records written by the same
project and environment on another remote count as owned.

**Why this priority**: A stale AAAA at a stopped server let the provider
route traffic to it (feedback 50735fc8).

**Independent Test**: Move a fixture target with owned A and AAAA to an
IPv4-only origin; verify a read-only zone listing shows zero Sandbox-managed
AAAA for its hostnames.

**Acceptance Scenarios**:

1. **Given** a hostname with owned A and AAAA at the old origin, **When** the
   target is applied to an IPv4-only remote, **Then** A is updated, the AAAA
   is removed, both are journaled, and no record at the old origin remains
   under Sandbox's marker.
2. **Given** the same move fails later, **When** rollback runs, **Then** both
   A and AAAA are restored to their journaled prior state.

---

### User Story 4 - Propagation delay does not fail a correct deploy (Priority: P1)

Verification resolves hostnames through authoritative answers or the proxy
edge addresses, never the controller's resolver. It checks each address,
sorts each answer into propagation, real failure or success, and spends one
bounded retry budget per hostname shared by the unauthenticated and
authenticated checks.

**Why this priority**: Edge propagation (HTTP 530 on one edge address) and a
stale macOS resolver failed seven applies that had built and delivered
correctly (feedback 6bd6bd1d, 34af9b95).

**Independent Test**: Against a simulated edge that answers 530 for a stated
time below the budget, with basic auth declared, verify ten of ten applies
succeed with per-address evidence; against a genuine 404 or an authenticated
401, verify failure within the confirmation attempts.

**Acceptance Scenarios**:

1. **Given** a new proxied hostname whose edge addresses answer 530 for under
   the budget, **When** verification runs with basic auth declared, **Then**
   both checks retry within one shared budget and the apply succeeds once
   every edge address answers correctly; the evidence lists per-address
   attempts, final answer and budget used.
2. **Given** the edge answers 404, or an authenticated request answers 401,
   **When** verification runs, **Then** the answer is a real failure after the
   minimum confirmation attempts, never success and never retried for the
   full budget, and the transaction rolls back.
3. **Given** the controller's system resolver is pinned to the origin
   address, **When** proxied hostnames are verified, **Then** the verifier
   never connects to the origin, and the evidence names the authoritative or
   edge source of every address checked.
4. **Given** a proxied hostname where the verifier sees the origin
   certificate, **When** classified, **Then** it is propagation-class, never a
   pass. **Given** a DNS-only hostname, **When** verified, **Then** it is
   checked against the origin and its certificate must be publicly trusted
   for the hostname.
5. **Given** a wildcard hostname, **When** an apply runs, **Then** its records
   appear in the plan and journal and its verification result is
   `skipped_wildcard`.
6. **Given** today's status rules (2xx and 3xx pass; an unauthenticated 401
   passes on a basic-auth route), **When** classified, **Then** those
   meanings are unchanged.

---

### User Story 5 - Adopt legacy, leftover and redirect records explicitly (Priority: P2)

Records written before attributed markers, leftovers from incomplete
rollbacks, interrupted-transaction records and operator redirect CNAMEs are
adopted only with `--adopt-records`. Adoption re-reads the live record,
journals it as the prior state, and marks it. `--confirm` never adopts.

**Why this priority**: Upgrade leaves every existing record with the legacy
comment; without one explicit adoption path, targets Sandbox already manages
would refuse forever.

**Independent Test**: On a fixture with legacy-comment records, verify the
first plan lists them all `unmarked` and adoptable, an apply without
`--adopt-records` (including with `--confirm`) changes nothing, and one
`--adopt-records` adopts them all in the apply's transaction.

**Acceptance Scenarios**:

1. **Given** legacy-comment records on a target's hostnames, **When** the
   first plan after upgrade runs, **Then** each is listed `unmarked` and
   adoptable.
2. **Given** the same, **When** apply runs without `--adopt-records`, with or
   without `--confirm`, **Then** zero provider changes are made.
3. **Given** the same, **When** apply runs with `--adopt-records`, **Then**
   every one is adopted in the apply's transaction with its prior state
   journaled.
4. **Given** a redirect route with an operator CNAME whose content is the
   redirect target, **When** plan runs, **Then** it shows `adoptable` and
   states that an adopted record is owned and removed on teardown; apply
   without `--adopt-records` refuses `conflicting_cname` with zero provider
   changes and names the remedy (adopt, or delete the CNAME); apply with
   `--adopt-records` journals the prior state, marks and proxies it, leaves
   its content unchanged, and writes no A/AAAA beside it; a forced rollback
   restores the journaled prior state.
5. **Given** a CNAME to other content, a DNS-only target, or an owned CNAME
   whose content no longer equals the redirect target, **When** plan or
   apply runs, **Then** it refuses `conflicting_cname`; CNAME content is
   never rewritten.

---

### User Story 6 - Proxied control endpoint is caught at provision (Priority: P2)

Provision tests a proxied HTTPS control endpoint with the same client the
apply uses. If the provider refuses it, provision reports the typed reason,
offers the supported modes (a DNS-only control hostname or a Tailscale-reached
endpoint), and never reports the endpoint reachable.

**Why this priority**: A browser-integrity refusal surfaced only at the first
apply as "unreachable" (feedback 075c6caf).

**Independent Test**: Provision against a fixture control hostname whose
proxy refuses the apply-time client; verify the typed reason, the offered
modes, and that reachability is not reported.

**Acceptance Scenarios**:

1. **Given** a proxied control hostname the provider refuses to the
   apply-time client, **When** provision runs, **Then** it reports the typed
   reason, offers the supported modes, corrects only with the operator's
   confirmation, and never reports the endpoint reachable.
2. **Given** a control endpoint that works through the proxy, **When**
   provision runs, **Then** it succeeds with the endpoint verified by the
   apply-time client.

---

### User Story 7 - Same guarantees on the nginx front door (Priority: P3)

On a remote using the incumbent-nginx front door, the DNS transaction,
ownership, verification and rollback reporting are identical; only the
embedded front-door result differs.

**Why this priority**: Remotes on panel servers use nginx (spec 056); they
must not get weaker edge guarantees.

**Independent Test**: Run the controlled scenarios of stories 1-4 on an
nginx remote and a Caddy remote and compare the per-record plan, journal and
rollback results.

**Acceptance Scenarios**:

1. **Given** the same controlled scenarios on nginx and Caddy remotes, **When**
   run, **Then** per-record plan, journal and rollback results are identical
   apart from the embedded front-door result.
2. **Given** an edge continuation (separately confirmed), **When** it runs,
   **Then** it follows the same ownership, journal and rollback rules as a
   full apply.

---

### Edge Cases

- A record whose marker was removed or changed at the provider is classified
  by its current marker (`unmarked` or `foreign_record`), not as drift.
- A preview record written with the legacy comment but whose hostname is not
  a recorded preview is `unmarked`.
- Lease re-acquisition for rollback waits behind another target's edge step:
  bounded; a timeout ends `rollback_incomplete` naming every change still in
  place.
- Provider unreachable during rollback: every remaining restore is attempted
  within its budget and reported `restore_failed`.
- Restore of a record whose live state no longer equals what this transaction
  wrote: `rollback_conflict`, live state kept.
- Rollback of a removed record restores it; rollback of a created record
  removes it (prior state "absent").
- Hostnames a target does not declare, and unrelated records in a zone, are
  never managed or pruned.

## Requirements *(mandatory)*

### Functional Requirements

**Plan and desired state**

- **FR-001**: `host plan` MUST compute the desired zone state for every
  declared hostname, including wildcards: A and AAAA presence by origin
  address family, redirect CNAME adoption, proxy status, TTL, aliases, and
  any zone SSL mode change. It MUST list per record one of `create`,
  `update`, `remove`, `adopt`/`adoptable`, `left_alone`, `drifted` or
  `refused`, with the reason, plus a verdict.
- **FR-002**: Covered record types MUST be A, AAAA and CNAME. Sandbox MUST
  write only A and AAAA and MUST never create a CNAME or rewrite CNAME
  content.
- **FR-003**: Absence MUST be a desired state: a family the origin lacks
  means no Sandbox-managed record of that family; such a record MUST be
  removed in the same transaction.
- **FR-004**: Only the origin addresses recorded by `remote add` and
  `set-origin` MUST be considered origin addresses.

**Ownership**

- **FR-005**: Every record Sandbox writes MUST carry the marker "managed by
  Sandbox hosting; target=<project>/<environment>".
- **FR-006**: A record with this target's project and environment MUST count
  as owned regardless of which remote wrote it.
- **FR-007**: Preflight MUST refuse, before any change, every declared
  hostname carrying a covered-type record that is foreign-marked
  (`foreign_record`), unmarked A/AAAA (`unmarked`), a CNAME outside the
  redirect adoption case (`conflicting_cname`), read-only (`read_only`) or
  provider-managed (`provider_managed`), naming the hostname, record
  identity, type and reason.
- **FR-008**: Records of uncovered types MUST be listed `left_alone` and
  never changed, removed or treated as a conflict, except that a record this
  target owned whose type was changed to an uncovered type MUST be a
  `foreign_record` refusal.
- **FR-009**: More than one record of one covered type on a declared hostname,
  any of them not owned, MUST be refused `ambiguous_records` naming each.
- **FR-010**: A legacy-comment record whose hostname matches a recorded
  preview MUST be `preview_owned`, refused and never adoptable; preview
  records MUST get a marker distinguishable from hosting ownership and stay
  outside this transaction.
- **FR-011**: An owned record whose live fields differ from what this target
  last applied MUST be reported `drifted` with the differing fields; apply
  MUST journal its live state as prior before overwriting. An owned CNAME
  whose content no longer equals the redirect target MUST be refused
  `conflicting_cname`, not treated as drift.

**Adoption**

- **FR-012**: Adoption of unmarked, leftover, interrupted or redirect-CNAME
  records MUST require `--adopt-records`; `--confirm` MUST NOT adopt.
  Without `--adopt-records`, adoption MUST make zero provider changes.
- **FR-013**: Adoption MUST re-read the live record, show it in the plan, and
  record it as the new prior state; an old journal MUST never be trusted as
  current state. Manual cleanup at the provider MUST remain supported.
- **FR-014**: An adopted redirect CNAME MUST be journaled, marked and
  proxied with content unchanged; while it stands no A/AAAA MUST be written
  beside it; the plan MUST state that it is removed on teardown.
- **FR-015**: The first plan after upgrade MUST list every legacy-comment
  record of the target as `unmarked` and adoptable, and one `--adopt-records`
  MUST adopt all of them in the apply's transaction.

**Transaction and rollback**

- **FR-016**: Every record and zone-setting change MUST be journaled durably
  before it is made; the journal MUST survive a controller crash and be
  retained with the delivery outcome and inspectable.
- **FR-017**: The mutation step (records, zone SSL mode, front-door fragment)
  MUST run under feature 060's remote-wide lease within its 60-second bound;
  verification MUST run after the lease is released.
- **FR-018**: On any failure, including verification after lease release,
  journaled changes MUST be reversed in reverse order under a re-acquired
  lease with a bounded wait, within the same 60-second bound, and the
  rollback itself MUST be journaled.
- **FR-019**: Each item MUST be restored only if its live state still equals
  what this transaction wrote and no later transaction on the same zone has
  applied a change depending on it; otherwise it MUST be reported
  `rollback_conflict` with the later owner or operation and left as it is.
- **FR-020**: Rollback results MUST be reported per item as `restored`,
  `unchanged`, `restore_failed` or `rollback_conflict` with record identity;
  the transaction result MUST be `rollback_complete` or `rollback_incomplete`
  listing exactly what remains, with the supported manual step.
- **FR-021**: If the lease expires during the mutation step, no further
  forward change MUST be made; journaled changes MUST be rolled back per
  FR-018 or reported `rollback_incomplete`.
- **FR-022**: A target with a `rollback_incomplete` result or an interrupted
  journal MUST have its next plan report each leftover with its journal
  reference, and apply MUST refuse until each is resolved or adopted.
- **FR-023**: The zone SSL mode change MUST be shown in the plan, refused
  without `--allow-zone-ssl-change`, journaled with its prior value, and
  restored on rollback.
- **FR-024**: Front-door fragment changes (Caddy, nginx) MUST be ordered
  inside the transaction and report their own rollback result in the same
  shape; the edge continuation MUST follow the same rules.

**Verification**

- **FR-025**: Verification MUST resolve declared hostnames through
  authoritative answers or proxy edge addresses and MUST NOT use the
  controller's system resolver; evidence MUST name the source of each
  address.
- **FR-026**: Each address MUST be verified, and each answer classified as
  propagation-class, real failure or success. The classification rules, the
  number of confirmation attempts and the per-hostname budget MUST be stated
  in the result.
- **FR-027**: Unauthenticated and authenticated checks for a hostname MUST
  share one bounded retry budget; propagation-class answers retry within it;
  real failures confirm with a small fixed number of attempts and fail.
- **FR-028**: An authenticated 401 and any 404 MUST be real failures. Provider
  origin-unreachable codes (such as 530), stale answers, and an origin
  certificate on a proxied hostname MUST be propagation-class, never a pass.
  Today's status meanings (2xx/3xx pass; unauthenticated 401 passes on a
  basic-auth route) MUST be unchanged.
- **FR-029**: A DNS-only hostname MUST be verified against the origin, and
  its certificate MUST be publicly trusted for the hostname.
- **FR-030**: Wildcard hostnames MUST be planned, journaled and rolled back,
  with verification reported `skipped_wildcard`.

**Provision and output**

- **FR-031**: Provision MUST verify a proxied HTTPS control endpoint with the
  apply-time client and either succeed, correct to a supported mode (DNS-only
  control hostname or Tailscale-reached endpoint) with the operator's
  confirmation, or refuse with the typed reason; it MUST NOT report the
  endpoint reachable otherwise. Provider-side security allowances MUST NOT
  be created.
- **FR-032**: Every result MUST be bounded, identical between CLI and MCP,
  and contain no provider token, zone secret or credential-shaped value,
  enforced by the existing redaction gates.
- **FR-033**: The transaction journal MUST be registered through the
  project's explicit state contracts.

### Key Entities

- **Ownership marker**: the record comment naming the owning target
  (project/environment); legacy comment means unmarked; preview marker is
  distinct.
- **Desired zone state**: per declared hostname, the intended record set
  (including absence) and zone settings, derived from the declaration and
  origin addresses.
- **Plan item**: one record or zone setting with its action or refusal reason
  and, for drift, the differing fields.
- **Edge transaction journal**: durable, ordered entries of each change with
  its prior state (or absence), what was written, and per-item rollback
  results; retained with the delivery outcome.
- **Verification evidence**: per hostname and address, the resolution source,
  attempts, classification, final answer, certificate observed, and budget
  used.
- **Transaction result**: `rollback_complete` / `rollback_incomplete` (or
  success) with per-item outcomes; consumed by feature 062.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A hostname with a foreign read-only or another target's record
  is refused in the plan, and apply makes zero provider changes, in 100% of
  runs.
- **SC-002**: After moving a target to an IPv4-only origin, a read-only zone
  listing shows zero Sandbox-managed AAAA records for its hostnames.
- **SC-003**: When the provider fails the third of four changes, the first
  two records equal their journaled prior state in every field (or are
  absent), each is reported `restored`, and the outcome is
  `rollback_complete`.
- **SC-004**: After a failed run that changed the zone SSL mode, the mode
  equals its journaled prior value.
- **SC-005**: A restore failure or unreachable provider ends
  `rollback_incomplete` naming the exact record and prior state, and the next
  plan reports it.
- **SC-006**: After a simulated controller crash mid-transaction, the next
  plan reports every journaled change and apply refuses until each is
  resolved or adopted.
- **SC-007**: Against an edge answering 530 for less than the stated budget,
  with basic auth declared, ten of ten applies succeed; a genuine 404 or an
  authenticated 401 fails within the confirmation attempts, not the full
  budget.
- **SC-008**: A verification failure after lease release ends with every
  journaled change restored or listed in `rollback_incomplete`; zero changes
  go unreported.
- **SC-009**: When a later apply relies on a changed zone setting, or a record
  was moved since by another remote, the earlier rollback leaves it
  unchanged, reports `rollback_conflict` naming the later operation, and
  ends `rollback_incomplete`.
- **SC-010**: With the controller resolver pinned to the origin, proxied
  verification never connects to the origin and evidence names the source of
  every address; DNS-only hostnames are verified against the origin.
- **SC-011**: Wildcard records appear in plan and journal and are
  `skipped_wildcard` in 100% of applies.
- **SC-012**: A proxied control hostname refused to the apply-time client is
  reported with its typed reason at provision and never reported reachable.
- **SC-013**: An apex's MX and TXT records are `left_alone` and unchanged
  after apply.
- **SC-014**: A redirect CNAME is refused `conflicting_cname` without
  `--adopt-records` with zero changes; with it, it carries this target's
  marker, its content is unchanged, and a forced rollback restores its prior
  state.
- **SC-015**: Adoption without `--adopt-records`, including under `--confirm`,
  makes zero provider changes; the first plan after upgrade lists every
  legacy record of the target and one `--adopt-records` adopts them all.
- **SC-016**: A legacy record on a recorded preview hostname is never
  adopted, changed or removed.
- **SC-017**: Two records of one covered type with one not owned are refused
  `ambiguous_records` with zero provider changes.
- **SC-018**: A hand-edited owned record is reported `drifted` with each
  differing field; after apply and forced rollback it equals the hand-edited
  state field for field.
- **SC-019**: The controlled scenarios produce identical per-record plan,
  journal and rollback results on nginx and Caddy remotes apart from the
  embedded front-door result.
- **SC-020**: No result, across CLI and MCP, contains a provider token, zone
  secret or credential-shaped value under the existing redaction gates.

## Assumptions

- The provider exposes record comments (or equivalent ownership metadata)
  and read-only or provider-managed flags readable by preflight.
- Proxy edge addresses and authoritative answers are queryable from the
  controller without the system resolver.
- Feature 060's remote-wide lease exists and covers DNS and edge mutation and
  ingress reload only, never longer than 60 seconds; until 060 lands, the
  transaction's lease points are the integration seam.
- Current baseline (declared-only DNS updates, 60 s TTL for DNS-only records,
  in-memory reverse rollback, Caddy fragment transaction with
  `rollback_complete`/`rollback_incomplete`, nginx front door route files,
  recorded origin IPv4/IPv6) is composed, not duplicated.
- The follow-up "selective host teardown" relies on this feature's marker and
  journal.

## Out of Scope

- A second DNS provider.
- Edge verification of wildcard hostnames, robots policy or basic-auth bypass
  addresses.
- Provider-side security allowances for the control endpoint.
- Preview record lifecycle (preview records stay outside the transaction).
- Changing the Caddy (spec 053) or nginx (spec 056) fragment transactions.
- Per-target locking across targets (feature 060) and fence release after an
  edge failure (feature 062).
- Managing undeclared hostnames or pruning unrelated zone records.
- Certificate issuance policy beyond verifying the presented certificate.
