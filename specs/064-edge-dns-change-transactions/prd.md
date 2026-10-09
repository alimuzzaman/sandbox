# Product Requirements Draft: Transactional Edge and DNS Changes

**Status**: Refined

**Created**: 2026-10-09

**Last Refined**: 2026-10-09

**Input**: "Transactional edge and DNS changes for hosted targets: a zone-level plan that covers every record family a hostname needs, preflight refusal on records Sandbox does not own, a journaled apply whose rollback restores every changed record, propagation-aware verification that does not use the controller's own resolver, and a control endpoint that works or is refused at provision time rather than at apply time"

**Drafting Configuration**: Claude Fable 5.1 root drafting under delegated product authority (user, 2026-10-08); evidence from the feedback backlog, `docs/remote-hosting.md` (DNS, edge verification, rollback, nginx front door), and `origin/latest` commits `e5fc88b`, `ac9070b`, `7d04606`. Revised 2026-10-09 by Claude Opus 5.5 root (speckit-refine) applying an independent Opus readiness review (verdict `REOPEN`) and Fable decisions delegated by the user; cited code re-verified read-only on `origin/latest`.

**Final Validation**: `PENDING` — fresh independent readiness review of this revision

**Validated On**: N/A

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

A hosted apply ends by making the target public: it upserts the declared
hostnames' DNS records at the provider, may switch the zone's SSL mode to
strict, converges the front door (Caddy or nginx), and verifies the edge
answers. These steps are the only ones in an apply that touch state outside
the remote, and they are the ones that failed most often during the
2026-10-06 to 2026-10-08 move of Lenzora, xspeed-hub and alimuzzaman.me from
`scaleway-sandbox` to `xcloud-london`:

- The authenticated edge check is a single request after the unauthenticated
  check's thirty retries. A freshly created proxied record propagates to the
  provider's edge addresses at different moments; the unauthenticated probe
  passed on one edge, the single authenticated probe hit the other, got HTTP
  530, and the apply failed and rolled back DNS. Seen twice on xspeed-hub, on
  a primary hostname and on an alias (feedback `6bd6bd1d`, high). The
  workaround was a scratch sleep before every first lookup.
- Moving a target to a remote with no IPv6 upserted the A record to the new
  origin but left the Sandbox-managed AAAA record pointing at the old server;
  proxied hostnames then had A at the new origin and AAAA at the stopped old
  one, and the provider could pick either (feedback `50735fc8`, high).
- When a later upsert in the zone loop failed (`HTTP 400: A DNS record
  managed by Workers already exists on that host`, an apex with a
  Worker-managed read-only record), the records changed earlier were not
  restored and no rollback lines appeared; the operator deleted the stray
  record by id (feedback `83cca354`, high). The foreign record was knowable
  before the first change.
- On macOS the controller's own resolver kept the wildcard origin address for
  more than ten minutes after the authoritative servers had switched, so the
  verifier connected to the origin, received the origin certificate, failed
  TLS verification, and five applies failed in a row (feedback `34af9b95`,
  high, in progress).
- A remote provisioned with an HTTPS control endpoint behind the provider's
  proxy reported the endpoint reachable at provision time; at apply time the
  control client was refused by the provider's browser-integrity check (403,
  error 1010), surfaced as "unreachable", and the apply failed with
  `recovery_target_identity_unavailable` (feedback `075c6caf`, high).

Today's edge step has partial answers to each of these, and they are not
enough. Rollback exists, but it is held only in memory for the life of one
apply, replays changes in reverse order, and reports failures as one joined
string; it does not survive a controller crash, does not check who owns a
record, and does not report per record. Every Sandbox-written record carries
the same comment, "managed by Sandbox hosting", which does not say which
target wrote it, and an existing record of the same type is overwritten
without an ownership check. Edge verification checks the HTTP status class
only and skips wildcard hostnames.

Each failure rolled back or half-rolled-back a deploy that had already built,
delivered and started correctly, or left a record that nobody owned. The edge
step is the last one, so its failures waste the whole apply, and because it
mutates a shared external zone, a half-done edge step is the one kind of
failure that can take a different, unrelated hostname off the air. The cost
grows with every hostname moved and every alias added.

## Users and Desired Outcomes

- **Agent deploying a hosted target**: an apply that built and delivered
  correctly does not fail because the edge took a minute to propagate; when
  the edge step does fail, every record and zone setting it changed is back
  where it was, and the result says which record and why.
- **Operator moving targets between servers**: the plan shows, per hostname,
  every record that will change, be removed, or be refused, and every zone
  setting that will change, before anything is changed; a new origin without
  IPv6 does not leave a stale AAAA.
- **Owner of a hostname Sandbox does not manage**: Sandbox never modifies or
  removes a record it does not own; a collision is a refusal in the plan, not
  a provider error mid-apply.
- **Operator provisioning a remote**: a control endpoint that will not work
  through the provider's proxy is refused or corrected at provision time,
  with the reason, not discovered during the first apply.
- **Reviewer of an edge failure**: sees the journal of record and zone-setting
  changes, the rollback result per record, and the verification evidence per
  edge address, all bounded and secret-free.

## Goals

- `host plan` computes the complete desired zone state for every declared
  hostname, including wildcards: A and AAAA (present or absent) by origin
  address family, CNAME for redirect routes only, proxy status, TTL, aliases,
  and the zone SSL mode change when one is needed. It lists per record
  whether it will be created, updated, removed, left alone, or refused, with
  the reason.
- Preflight detects records Sandbox does not own on any declared hostname
  (read-only, Worker-managed, another target's marker, any record type other
  than A, AAAA, or a redirect route's CNAME) and refuses the apply before any
  change, naming the hostnames and records.
- Ownership is attributable: every record Sandbox writes carries a marker
  naming the target (project and environment) that owns it.
- The edge step is a transaction: every record and zone-setting change is
  journaled durably before it is made; on any failure, every journaled change
  is reversed in reverse order; the result reports per record `restored`,
  `unchanged`, or `restore_failed` with the record identity, and a
  `rollback_incomplete` result lists exactly what remains.
- A Sandbox-managed record whose address family the new origin lacks is
  removed in the same transaction, never left pointing at a previous origin.
- Verification is propagation-aware: it resolves declared hostnames through
  the provider's authoritative answers or the proxy edge addresses, never the
  controller's operating-system resolver; it distinguishes propagation-class
  results (provider origin-unreachable codes such as 530, stale answers,
  certificate from the origin instead of the edge) from real failures; and
  the authenticated and unauthenticated checks share one bounded retry budget
  per hostname.
- A failed verification rolls the transaction back even though it runs after
  the mutation step has released feature 060's remote-wide lease.
- A proxied control endpoint is verified at provision with the same client
  the apply uses, and provision either succeeds with a working endpoint,
  corrects to a supported mode with the operator's confirmation, or refuses
  with the typed reason.
- CLI and MCP agree on every result shape in this feature; no result carries
  a provider token, zone id secret, or credential-shaped value.

## Non-Goals

- Supporting a second DNS provider. Cloudflare remains the provider; the
  transaction and ownership rules are written so a second provider could
  adopt them, but adding one is separate work.
- Verifying wildcard hostnames at the edge. Wildcard records are planned,
  journaled and rolled back like any record; their verification is reported
  as `verification: skipped_wildcard`.
- Adding verification of the robots policy or basic-auth bypass addresses.
  Today's verification checks HTTP status only, and this feature keeps the
  meaning of those status checks.
- Provider-side allowances (firewall rules, browser-integrity exceptions, or
  any other weakening of the provider's security settings) for the control
  endpoint.
- Preview records. Preview environments create and delete their own DNS
  records outside the hosted apply; they stay out of this transaction and get
  a marker distinguishable from hosting ownership, so a hosting plan never
  treats them as owned or adoptable.
- Changing the front-door fragment transactions themselves (Caddy, spec 053;
  nginx, spec 056). This feature orders them inside the edge transaction and
  consumes their rollback results.
- Per-target locking and concurrency of edge steps across targets on one
  remote (feature 060 serializes the shared edge step; this feature defines
  what one edge step does).
- Delivery outcome reconciliation and fence release after an edge failure
  (feature 062).
- Managing records for hostnames a target does not declare, or pruning
  unrelated records in a zone.
- Certificate issuance policy (Origin CA versus publicly trusted) beyond
  verifying the certificate the edge presents.

## Product Scenarios

### Scenario 1 — Plan shows every family and every refusal

- **Starting state**: A target declares three hostnames; one apex has a
  Worker-managed read-only AAAA; the new origin has IPv4 only; one hostname
  has a Sandbox-managed AAAA from the old origin; the zone's SSL mode is not
  strict.
- **User action**: `host plan`.
- **Expected outcome**: Per hostname the plan lists A (create or update to
  the new origin), AAAA (remove the Sandbox-managed stale one; leave the
  Worker-managed one and mark the apex `refused: foreign_record`), proxy
  status and TTL, and the zone SSL mode change with its prior value. The
  plan's verdict is `would_refuse` naming the apex, and nothing has changed.

### Scenario 2 — Move to an IPv4-only origin

- **Starting state**: A hostname has Sandbox-managed A and AAAA at the old
  origin, written by the same project and environment on the old remote.
- **User action**: Apply to the new remote.
- **Expected outcome**: The records count as owned (a move). In one
  transaction, A is updated and the Sandbox-managed AAAA is removed; the
  journal records both; after verification the hostname resolves only to the
  new origin's family. No record at the old origin remains under Sandbox's
  marker.

### Scenario 3 — Later upsert fails, earlier changes restored (negative)

- **Starting state**: Four hostnames; the third fails at the provider
  mid-transaction.
- **User action**: Apply.
- **Expected outcome**: The first two hostnames' records are restored to
  their journaled prior state (including prior absence), the result lists
  each record as `restored`, the failing record with the provider's reason,
  and the apply ends failed with `rollback_complete`. The remote runtime is
  left as the apply's delivery phase defined, and the retained outcome says
  so.

### Scenario 4 — Restore itself fails (negative)

- **Starting state**: As in Scenario 3, but restoring the second record also
  fails.
- **User action**: None; the transaction is unwinding.
- **Expected outcome**: The result is `rollback_incomplete` and names the
  exact record identity, its journaled prior state, and the supported manual
  step. No other record is touched after the restore failure beyond the
  remaining reverse-order attempts, each reported.

### Scenario 5 — Propagation delay does not fail the apply

- **Starting state**: A new proxied hostname whose record was created
  seconds ago; the provider's edge addresses answer inconsistently, some with
  530, for a minute.
- **User action**: Apply proceeds to verification with basic auth declared.
- **Expected outcome**: Both unauthenticated and authenticated checks run
  against each edge address with a shared bounded budget; propagation-class
  answers are retried within the budget; the apply succeeds once every edge
  address answers correctly, and the evidence lists per-address attempts, the
  final answer, and the budget used.

### Scenario 6 — Real edge failure is not retried as propagation (negative)

- **Starting state**: The front door is misconfigured so the hostname answers
  404 from the edge, or an authenticated request answers 401.
- **User action**: Apply proceeds to verification.
- **Expected outcome**: The answer is classified as a real failure after the
  minimum confirmation attempts, not retried for the whole budget and never
  counted as success; the apply fails with the per-address evidence and the
  transaction rolls back.

### Scenario 7 — Controller resolver is stale (negative today)

- **Starting state**: The controller machine's system resolver still returns
  the wildcard origin address for a hostname whose authoritative answer is
  the proxy edge.
- **User action**: Apply proceeds to verification.
- **Expected outcome**: Verification uses the authoritative or edge answer and
  never the system resolver, so the origin certificate is never presented to
  the verifier. The evidence records which addresses were verified and their
  source.

### Scenario 8 — Proxied control endpoint at provision

- **Starting state**: The operator provisions a remote with an HTTPS control
  hostname that the provider proxies.
- **User action**: Provision.
- **Expected outcome**: Provision tests the control route with the apply-time
  client. If the provider refuses it, provision reports the typed reason and
  offers the supported modes (a DNS-only control hostname, or a
  Tailscale-reached control endpoint), and does not report the endpoint
  reachable. An apply never discovers this first.

### Scenario 9 — nginx front door, same guarantees

- **Starting state**: The remote uses the incumbent-nginx front door.
- **User action**: Apply with a DNS change.
- **Expected outcome**: The DNS transaction, ownership rules, propagation-aware
  verification and rollback reporting are identical; only the front-door
  fragment step differs and its own rollback result is embedded.

### Scenario 10 — Plan after a previous rollback_incomplete (negative)

- **Starting state**: A previous apply ended `rollback_incomplete` on one
  record.
- **User action**: `host plan`, apply, then an explicit adoption.
- **Expected outcome**: The leftover is reported as a prior incomplete
  rollback on that hostname with the journal reference. Apply refuses until
  the leftover is resolved manually or adopted. Adoption re-reads the live
  record, shows it in the plan, and records it as the new prior state only
  after an explicit `--confirm`; the old journal's view of the record is never
  trusted.

### Scenario 11 — Verification fails after the lease is released (negative)

- **Starting state**: The mutation step completed under feature 060's
  remote-wide lease and released it; verification then fails with a real
  failure.
- **User action**: None; the apply is finishing.
- **Expected outcome**: The apply re-acquires the lease within a bounded wait,
  rolls DNS and zone-setting changes back, and journals the rollback. If the
  lease cannot be acquired within the bound, the result is
  `rollback_incomplete` naming every journaled change still in place.

### Scenario 12 — Zone SSL mode change

- **Starting state**: A proxied hostname's zone SSL mode is `full`.
- **User action**: Apply without, then with, `--allow-zone-ssl-change`.
- **Expected outcome**: Without the flag the plan shows the required change
  and the apply refuses before any change. With it, the change is journaled
  with its prior value; if the transaction later fails, the zone is restored
  to `full` and the result reports it per setting.

### Scenario 13 — Hostname owned by another target (negative)

- **Starting state**: Two targets declare the same hostname; the record
  carries the first target's marker.
- **User action**: Plan or apply the second target.
- **Expected outcome**: The record is `refused: foreign_record` naming the
  owning target; nothing changes.

### Scenario 14 — Legacy and hand-edited records

- **Starting state**: One declared hostname has a record with the old
  unattributed comment; another has a record with this target's marker whose
  content was edited by hand at the provider.
- **User action**: `host plan`, then apply.
- **Expected outcome**: The legacy record is reported `unmarked` and refused
  until adopted with explicit confirmation. The hand-edited record is
  reported `drifted` with the differing fields; apply journals its live
  state as the prior state before changing it, so a rollback restores the
  hand edit.

### Scenario 15 — Provider unreachable during rollback (negative)

- **Starting state**: A transaction is unwinding and the provider stops
  answering.
- **User action**: None.
- **Expected outcome**: Each remaining restore is attempted within a bounded
  budget and reported `restore_failed` with the reason; the result is
  `rollback_incomplete` and the next plan reports every leftover.

### Scenario 16 — Controller crashes mid-transaction (negative)

- **Starting state**: The controller dies after journaling and making two of
  four changes.
- **User action**: The next `host plan` or apply for that target.
- **Expected outcome**: The journal survived the crash. The plan reports an
  interrupted transaction with each journaled change and its prior state;
  apply refuses until the leftovers are resolved or adopted as in
  Scenario 10.

## Proposed Product Behavior

- Desired zone state is computed per declared hostname from the target's
  declaration and the remote's origin addresses. Covered record types are A,
  AAAA, and CNAME for redirect routes only. Absence is a desired state: a
  family the origin lacks means "no Sandbox-managed record of that family".
  Wildcard hostnames are planned, journaled and rolled back like any other.
- Ownership is explicit. Sandbox writes the marker "managed by Sandbox
  hosting; target=<project>/<environment>". A record with this target's
  project and environment is owned, including one written from another remote
  (a move). A record marked for a different project or environment, any
  record type other than the covered ones, and read-only or provider-managed
  records are preflight refusals with the record identity, type and reason
  (`foreign_record`, `read_only`, `provider_managed`). A record carrying the
  old unattributed comment is `unmarked` and claimable only by adoption.
- Edge changes run as one durably journaled transaction per apply. The
  mutation step (record changes, zone SSL mode change, front-door fragment)
  runs under feature 060's remote-wide lease; verification runs after the
  lease is released. A verification failure still rolls back: rollback
  re-acquires the lease within a bounded wait and is itself journaled. The
  front-door fragment change reports its own rollback result in the same
  shape. Journals are retained with the delivery outcome and are inspectable.
- The zone SSL mode change is a journaled step: shown in the plan, refused
  without `--allow-zone-ssl-change`, and restored on rollback.
- The edge continuation path follows the same ownership, journal and
  rollback rules as a full apply.
- Adoption of a leftover, unmarked or interrupted record re-reads the live
  record, shows it in the plan, and records it as the new prior state only
  after explicit `--confirm`. An old journal is never trusted as the record's
  current state. Manual cleanup at the provider remains supported.
- Verification resolves through the authoritative answer or the proxy edge
  addresses, verifies each address, classifies results into propagation,
  real failure, or success, and applies one bounded budget per hostname
  shared by all checks for that hostname. An authenticated 401 and any 404
  are real failures, never success. Classification rules and the budget are
  set in specification and stated in the result.
- Provision verifies a proxied control endpoint with the apply-time client
  and refuses or corrects before reporting reachability.
- Every result is bounded, secret-free, and shared between CLI and MCP.

## Constraints and Dependencies

- Current behavior to build on: `apply` "updates only declared DNS records";
  DNS-only records use a 60 s TTL (`e5fc88b`); DNS, zone SSL mode and front
  door are rolled back after a failed Compose or health step, in memory and
  in reverse order; the Caddy fragment transaction holds a host-global lock
  and records `rollback_complete` or `rollback_incomplete`; the nginx front
  door (`ac9070b`, spec 056) has its own route files and reload. The edge
  transaction must compose these, not duplicate them.
- `remote add` and `set-origin` record the origin's IPv4 and IPv6 (`7d04606`);
  the desired-state computation depends on those being the only origin
  addresses considered.
- Feature 060 serializes the shared edge step across targets with a
  remote-wide lease that covers DNS and edge mutation and ingress reload only,
  never verification waits, and is never held longer than 60 seconds. This
  feature keeps its mutation step inside that bound.
- Feature 062 consumes `rollback_complete` and `rollback_incomplete` to
  release or keep the fence; the shapes defined here are its input.
- Edge verification today checks the HTTP status class only: 2xx and 3xx
  pass, and an unauthenticated 401 passes on a basic-auth route. The
  propagation classification must not weaken those checks.
- The provider's proxy edge presents its own certificate; verification of the
  edge certificate is the correctness check, and an origin certificate seen
  by the verifier is a propagation-class result, never a pass.
- Constitution and module boundaries: the transaction journal is new state
  registered through the project's explicit state contracts.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Ownership rule | Only records carrying this target's marker are ever changed or removed; anything else on a declared hostname is a preflight refusal | A record Sandbox did not create may be someone's production; a provider error mid-apply is too late | Fable decision (delegated by user), 2026-10-09 |
| Ownership marker | "managed by Sandbox hosting; target=<project>/<environment>". Same project and environment on another remote is owned (a move, `50735fc8`); different is `foreign_record`; the legacy comment is `unmarked`, claimable by adoption | The current shared comment cannot tell two targets apart | Fable decision (delegated by user), 2026-10-09 |
| Record types | A, AAAA, and CNAME (CNAME only for redirect routes); any other type on a declared hostname is `foreign_record` | These are the types hosting writes today | Fable decision (delegated by user), 2026-10-09 |
| Zone SSL mode | A journaled step: shown in the plan, still refused without `--allow-zone-ssl-change`, restored on rollback | It is a zone-wide change made by the apply and must be undone with it | Fable decision (delegated by user), 2026-10-09 |
| Wildcard hostnames | Planned, journaled and rolled back like any record; verification is a v1 Non-Goal, reported as `verification: skipped_wildcard` | A wildcard has no single hostname to probe, but its record still changes | Fable decision (delegated by user), 2026-10-09 |
| Stale family records | A Sandbox-managed record of a family the new origin lacks is removed in the transaction | Leaving it points traffic at a stopped server | Fable decision (delegated by user), 2026-10-09 |
| Rollback | Durably journaled, reverse-order, per-record reporting; `rollback_incomplete` names leftovers and blocks the next apply until resolved or adopted | A half-restored zone must be visible and must not be papered over by the next apply | Fable decision (delegated by user), 2026-10-09 |
| Verification after lease release | A verification failure after the lease is released rolls DNS back; rollback re-acquires the lease (bounded) and is journaled | Leaving a failed edge change in place is worse than a short second lease | Fable decision (delegated by user), 2026-10-09 |
| Adoption | Re-read the live record, show it in the plan, record it as the new prior state only after explicit `--confirm`; the old journal is never trusted; manual cleanup stays available | The live record is the only trustworthy state after an incomplete rollback | Fable decision (delegated by user), 2026-10-09 |
| Resolution source | Authoritative or proxy edge answers; never the controller's system resolver | The controller's cache cost five applies; the edge is what users reach | Fable decision (delegated by user), 2026-10-09 |
| Retry budget | One bounded budget per hostname shared by unauthenticated and authenticated checks; propagation-class results retry, real failures confirm then fail | Removes the single-request trap without retrying genuine misconfiguration for minutes | Fable decision (delegated by user), 2026-10-09 |
| Proxied control endpoint | Verified at provision with the apply-time client; supported alternatives are a DNS-only control hostname or a Tailscale-reached control endpoint; provider-side allowances are a Non-Goal | Provision is where the operator can still change the mode cheaply, without weakening the provider's security settings | Fable decision (delegated by user), 2026-10-09 |
| Provider scope | Cloudflare only, with provider-neutral transaction and ownership rules | One provider in use; rules written so a second can adopt them | Fable decision (delegated by user), 2026-10-09 |

## Open Questions

- None.

## Acceptance Outcomes

- For a target declaring a hostname with a foreign read-only record, or a
  record marked for another target, `plan` reports the refusal and `apply`
  makes zero provider changes.
- Moving a target to an IPv4-only origin leaves zero Sandbox-managed AAAA
  records for its hostnames, verified by a read-only zone listing after apply.
- In a controlled run where the provider fails the third of four hostname
  changes, the first two hostnames' records afterward have type, name,
  content, proxied status, TTL and comment equal to their journaled prior
  state (or are absent where they were absent), the result lists each as
  `restored`, and the retained outcome is `rollback_complete`.
- In a controlled run that changes the zone SSL mode and then fails, the
  zone's SSL mode afterward equals its journaled prior value.
- In a controlled restore failure, or with the provider unreachable during
  rollback, the result is `rollback_incomplete` naming the exact record and
  prior state, and the next `plan` reports it.
- After a simulated controller crash mid-transaction, the next `plan` reports
  every journaled change of the interrupted transaction, and apply refuses
  until each is resolved or adopted.
- Against a simulated edge that answers 530 for a stated number of seconds
  below the budget stated in the result, with basic auth declared, ten of ten
  applies succeed; with a genuine 404, or an authenticated 401, from the edge
  the apply fails within the confirmation attempts rather than the full
  budget.
- A verification failure after the lease is released ends with every
  journaled change restored, or `rollback_incomplete` naming each one still in
  place; zero changes remain unreported.
- With the controller's system resolver deliberately pinned to the origin
  address, verification never connects to the origin and the evidence names
  the authoritative or edge source for every address checked.
- Wildcard records appear in the plan and journal, and their verification
  result is `skipped_wildcard` in 100% of applies.
- Provisioning a remote with a proxied control hostname that the provider
  refuses to the apply-time client reports the typed reason at provision and
  never reports the endpoint reachable.
- Every result in the feature, across CLI and MCP, contains no provider
  token, zone secret, or credential-shaped value, checked by the existing
  redaction gates.

## Risks and Assumptions

- **Risk**: Propagation classification is too generous and retries a real
  outage for the whole budget. Mitigation: real-failure classes confirm with
  a small fixed number of attempts and fail; budget and classes are stated
  in the result so a wrong classification is visible.
- **Risk**: Removing a stale AAAA is itself a change to a record someone
  relied on. Mitigation: only records carrying this target's marker are
  removed, the plan shows the removal, and the journal allows restore.
- **Risk**: Records written before attributed markers carry only the legacy
  comment and are refused on hostnames Sandbox does manage. Mitigation: plan
  reports them as `unmarked`; explicit adoption with confirmation claims them.
- **Risk**: Re-acquiring the remote-wide lease for a rollback can wait behind
  another target. Mitigation: the wait is bounded, and a timeout ends as
  `rollback_incomplete` naming every change still in place.
- **Assumption**: A record carrying this target's marker that was edited by
  hand at the provider is still Sandbox-owned; the hand edit is reported as
  drift and preserved in the journal as prior state, not refused.
- **Assumption**: The provider exposes record ownership metadata (comment or
  equivalent) and read-only or provider-managed flags that preflight can
  read.
- **Assumption**: The proxy edge addresses and authoritative answers are
  queryable from the controller without the system resolver.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred (delegated
      decisions, recorded above; user may overturn).
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [ ] The latest independent readiness review verdict is `PASS`.

**Readiness**: `NOT READY`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
