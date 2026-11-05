# Product Requirements Draft: Wildcard Subdomain Hosting

**Status**: Ready for specification

**Created**: 2026-11-05

**Last Refined**: 2026-11-05

**Input**: "do *.asb.bd hosting so every subdomain won't need dns change. and sites can stay anonymus. if no site exists for a subdomain throw errors. also allow subdomain dns if we need to assign a subdomain to other hosts or ..."

**Drafting Configuration**: Claude Opus 5.5, main session

**Final Validation**: `PASS` — GPT-6.1-Sol, high reasoning, independent read-only review, round 3

**Validated On**: 2026-11-05

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Every site hosted under `asb.bd` on `xcloud-london` needs its own public DNS record today, because target planning emits a DNS intent for each declared route.

- A new hostname can fail its first delivery. Reported on 2026-11-04 and earlier: a new name failed verification and its DNS change was rolled back. Current verification resolves the name through the controller, with bounded retries, and rollback restores recorded DNS effects. Cached negative answers are the likely cause, not independently proven. The zone's SOA minimum was 1800 on 2026-11-05.
- Each site adds a public DNS record, which the owner does not want for temporary and private sites.
- Each site needs a DNS change the operator has no other reason to make.

Wildcard serve aliases and proxied wildcard TLS already exist. An existing application wildcard is not an error-only fallback, because it would send unknown names to an application. Separate overlapping route files conflict in preflight. Today the zone has no wildcard: Cloudflare's authoritative servers answered `NXDOMAIN` for an unused name and for `*.asb.bd` on 2026-11-05.

The product gap is shared DNS coverage for separately owned exact targets, a safe error for unknown names, and the lifecycle and verification around them. Reuse existing wildcard capability where compatible, and do not broaden ordinary application wildcard behaviour.

## Users and Desired Outcomes

- **Operator hosting a site**: chooses any unused name under the zone and gets a working HTTPS site, with no DNS step and no wait for DNS.
- **Owner**: temporary and private sites do not each publish a DNS record.
- **Visitor to a name with no site**: gets a clear error, never another site's content.
- **Operator assigning a name elsewhere**: can point one subdomain at a different host with an ordinary DNS record, and that wins.

## Goals

- An eligible first-level name with no overriding DNS node can use the active shared wildcard with no per-name DNS write.
- A name with no site returns a defined error, on every path, method and protocol that reaches this server for an in-scope name, and never another site's content.
- An explicit DNS record for a subdomain takes precedence in resolution, whether it points at this server or elsewhere.
- Stale controller DNS answers alone cannot fail delivery of a covered name or roll it back. Public reachability is reported separately, and remains subject to caches that existed before enabling.

## Non-Goals

- Other zones or other remotes. The design must not assume `asb.bd`, but only this pair is enabled.
- Multi-level names such as `a.b.asb.bd`: not covered by a first-level wildcard.
- Moving or removing DNS records of existing sites, or changing what they serve.
- Hiding a site from a visitor who already has its URL. Access control is a separate concern, covered by existing Basic Auth.
- Search-engine policy changes beyond keeping existing `robots` behaviour.
- Exact restore, `instance delete` or `sb server` work.

## Product Scenarios

### Scenario 1 — New site, no DNS

- **Starting state**: the zone wildcard is active and the name has no record.
- **User action**: the operator applies a hosted target at an unused name.
- **Expected outcome**: the site serves over HTTPS, no DNS record is created for it, and there is no per-target DNS propagation wait. Delivery evidence separates target and origin health, public edge routing and TLS, and resolver propagation.

### Scenario 2 — Name with no site

- **Starting state**: the zone wildcard is active and no target serves the name.
- **User action**: a visitor requests any path on that name, over HTTP or HTTPS.
- **Expected outcome**: the same generic error for any path and relevant method, including route-marker, robots and ACME paths, unless an operational exception is explicitly justified in the specification. It names no other site and is not indexable. The server's other sites and the host panel's sites are never shown.

### Scenario 3 — Subdomain on another host

- **Starting state**: the wildcard is active.
- **User action**: the operator adds an exact DNS record for one subdomain pointing elsewhere.
- **Expected outcome**: that subdomain goes to the other host, and all other names are unchanged. Sandbox does not modify or remove that record.

### Scenario 4 — A declared site wants its own record

- **Starting state**: a target needs its own managed address record, for example DNS-only.
- **User action**: the operator explicitly selects managed exact-record behaviour.
- **Expected outcome**: Sandbox manages the exact record as it does today, including on destroy.

### Scenario 5 — Existing sites

- **Starting state**: sites with exact records exist when the wildcard is turned on.
- **User action**: the operator enables the wildcard, then later reapplies or deliberately destroys an existing target.
- **Expected outcome**: a permitted enable and later reapply preserve existing serving routes and exact DNS ownership, subject to the refusal on managed-record drift. A blocked enable leaves them unchanged. Deliberate target destroy follows the existing target-owned route and exact-record cleanup policy, preserves out-of-band changed records, and never removes shared wildcard resources.

### Scenario 6 — Removing a site

- **Starting state**: a wildcard-covered site with no record of its own.
- **User action**: the operator destroys the target.
- **Expected outcome**: the name returns the Scenario 2 error, and no DNS record is touched. The wildcard stays.

### Scenario 7 — Enabling on a host that is not ready

- **Starting state**: the catch-all response is not in place, the zone or the certificate is unavailable, or the operator lacks provider rights.
- **User action**: the operator enables the wildcard.
- **Expected outcome**: a refusal before publication makes no DNS write. After publication or an uncertain acknowledgement, enable reports completed, rolled back, or incomplete and unknown effects, with a safe retry. Failure never exposes another site's content. The safe error stays in place whenever traffic may still reach this server, including cached DNS after a disable or rollback.

### Scenario 8 — Disabling

- **Starting state**: the wildcard is active and sites rely on it.
- **User action**: the operator disables it.
- **Expected outcome**: refused while any target relies on it, naming them, unless the operator explicitly accepts that those sites stop resolving. Disable removes shared DNS coverage only. Each target's route stays, so a client with cached DNS, or a direct request, still sees that site, and re-enabling restores resolution with no target change. Stopping a site is destroy or Basic Auth. The error response stays for unknown names while traffic may still reach this server.

### Scenario 9 — Conflicts

- **Starting state**: exact, wildcard or regex routes already exist under the zone.
- **User action**: the operator enables the shared fallback or applies a target.
- **Expected outcome**: enable keeps and reports existing exact panel routes and the exact control route as permitted exceptions, and preserves existing exact Sandbox routes. A panel wildcard or regex under the zone blocks enable. Ordinary Sandbox application wildcards, foreign wildcards, conflicting regex routes and duplicate exact routes are named conflicts and cause refusal. Applying a target at a hostname already owned by another exact route refuses and names that owner. Only the registered error-only fallback receives the coexistence exemption. Foreign files are never modified.

### Scenario 10 — The wildcard changes out of band

- **Starting state**: a target relies on the wildcard, then the record is deleted or repointed, or a new TXT or exact record appears at the target's name.
- **User action**: the operator runs plan, status or apply, or the provider read fails.
- **Expected outcome**: plan and status separate the configured dependency from freshly observed shared DNS and TLS coverage, and show drift or unavailable evidence. Apply does not silently recreate shared DNS or create a per-site record: it reports the conflict and needs an explicitly authorized repair or an explicitly chosen exact-record policy.

### Scenario 11 — An ineligible name

- **Starting state**: the wildcard is active and the name has only a TXT record, an exact record, or a delegation.
- **User action**: the operator applies a target at it.
- **Expected outcome**: plan and apply say why the name is not covered, and require an explicitly chosen exact-record policy or refuse with no change. A foreign record is never silently changed.

### Scenario 12 — Mixed versions and concurrent work

- **Starting state**: an older controller or runtime, a second controller, a pending apply, or a runtime upgrade in progress.
- **User action**: any of enable, disable, upgrade, apply, recovery or destroy.
- **Expected outcome**: an unsupported combination refuses before side effects, with an actionable reason. An older controller does not silently create exact records for a dependent target or remove shared resources. Dependency checks include other controllers and pending applies, and incomplete knowledge refuses a destructive change.

### Scenario 13 — Fallback certificate

- **Starting state**: the fallback's origin certificate nears expiry, possibly with no sites hosted, or renewal fails.
- **User action**: the operator inspects or renews it.
- **Expected outcome**: the shared DNS, route and certificate have a lifecycle independent of any target. The operator can inspect validity and renew or replace with no site deployment. Target rollback or destroy never removes or revokes shared resources. A TLS failure is reported as that, and unknown-name safety holds.


## Proposed Product Behavior

- An operator enables the wildcard for one zone on one remote, as an explicit, confirmed action that shows its plan first, and can inspect its state.
- Enabling puts the error response in place before publishing DNS. Failure never exposes another site's content. Any changed DNS, uncertain effects and retained safety resources are reported, and the safe error response stays whenever traffic may still reach this server, including after disable or rollback.
- A target whose name is covered needs no record by default. Plan, status, recovery and destroy distinguish shared coverage from owned exact DNS.
- A target can explicitly select managed exact-record behaviour.
- DNS resolution gives any exact record precedence. Sandbox's own mutation policy is separate: it does not change a record it does not own to make the wildcard work.
- The error response is the same for every unknown name, so it does not reveal which names exist.
- Delivery verification of a covered site does not fail on the controller's cached answers alone, and skipping public evidence is never reported as verified public reachability.
- Behaviour is defined for out-of-scope apex and multi-level names, and for a mismatched TLS name and HTTP host, with no promise of TLS success for uncovered names. No HTTP enumeration resistance is promised: existing sites still differ at HTTP.

## Constraints and Dependencies

- The front door is the incumbent nginx on `xcloud-london`: Sandbox writes only its own files, never `default_server` and never `nginx.conf`; panel sites must remain unaffected.
- A wildcard-dependent target is proxied through Cloudflare with strict TLS, and needs a matching origin certificate and edge coverage. The existing DNS-only exact-record path stays available outside this.
- Eligibility includes target proxy and TLS policy and authoritative DNS exceptions. How a wildcard behaves around existing nodes must be validated, not assumed.
- `sandbox/hosting` recovery, delivery evidence and destroy behaviour apply: a site with no record of its own must not make destroy or recovery read that as an unknown or failed effect.
- A remote runtime migration may be needed if this changes code that runs on the remote or a payload shape (repo gotcha 28). Whether one is needed is for the plan. No migration is authorized by this document.
- Changing the zone is a production-path change for `asb.bd` and is flagged for human review before it is enabled.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Zone-wide wildcard | `*.asb.bd` to this server | Owner asked for no per-subdomain DNS change | User, 2026-11-05 |
| Name with no site | An error response | "if no site exists for a subdomain throw errors" | User, 2026-11-05 |
| Assigning a subdomain elsewhere | An exact DNS record wins | "allow subdomain dns if we need to assign a subdomain to other hosts" | User, 2026-11-05 |
| Default for a covered site | No record of its own; may opt in to one. Existing exact ownership is preserved | Follows from "won't need dns change" and "anonymous" | User, 2026-11-05 |
| Meaning of "anonymous" | No per-site public DNS record, and no difference between an existing and an unknown name in DNS. Names are not secret: no promise against HTTP enumeration | Owner chose this over hiding that a site exists | User, 2026-11-05 |
| Disable with dependent sites | Refuses and names them, unless the operator explicitly accepts that they stop resolving. The error response stays while traffic may still arrive | Owner chose this over always refusing | User, 2026-11-05 |
| Requests reaching a dependent site after disable | The site keeps serving; only shared DNS is removed, and re-enabling needs no target change | Owner chose this over suspending routes | User, 2026-11-05 |
| Managed exact record changed out of band | Apply refuses and names the record, and the change stays. Overwriting needs explicit authorization | Owner chose this over today's overwrite, so a deliberate move to another host is never undone silently | User, 2026-11-05 |
| Error form | One generic HTTP 404 page, not indexable | A readable error, per "throw errors" | Assumption, stated to the user |
| Scope | One zone on one remote, first-level names | Smallest that meets the request | Assumption, stated to the user |
| Exact panel routes under the zone | Kept as exceptions and reported; a panel wildcard or regex blocks enable | Keeps what exists unchanged; foreign files are never modified | Assumption, accepted for specification |
| Control route | A permitted exact exception | It already serves this host | Assumption, accepted for specification |

## Open Questions

None blocking.

## Acceptance Outcomes

- With the wildcard active, a new eligible name serves over HTTPS on first apply with zero DNS records created for it, and delivery evidence reports origin, public edge and resolver state separately.
- First apply of a covered new name succeeds when the controller's resolver holds a cached "does not exist" answer, and public reachability is reported as unverified if it was skipped.
- Sampled unknown names, over HTTP and HTTPS, on ordinary, route-marker, robots and ACME paths and relevant methods, return the defined error, with no content from any other site or panel.
- An exact record created for one subdomain resolves to its own host, and other names do not change. A managed exact record changed out of band makes a later apply refuse and name it, leaving the change in place, until an operator explicitly authorizes overwriting it.
- A target opting into a managed exact record, including the DNS-only path, keeps that policy through reapply and gets existing owned-record cleanup on destroy. After an out-of-band repointing, apply refuses without overwriting and destroy preserves the changed record. Neither removes shared resources.
- Existing declared routes and exact DNS ownership stay unchanged through enable and reapply, checked with stable fixtures or site-appropriate expected status, content identity, redirects and authentication, including Basic Auth.
- Destroying a covered site, including the last dependent one, changes zero DNS records and leaves shared resources, and its name then returns the defined error.
- A refusal before publication changes zero DNS records. An interrupted enable, a provider timeout after acceptance and a failed rollback each report their real effects and a safe retry.
- Wildcard deletion, repointing, a new exact or TXT record, and a provider read failure after a successful apply each show as drift or unavailable evidence.
- Disable with dependents refuses and names them. With explicit acceptance it removes shared DNS coverage and keeps the safe error response for unknown names. A cached client or direct request reaching a still-declared dependent target keeps seeing that site, and re-enabling restores resolution with no target change.
- Panel exact, wildcard and regex routes, an ordinary Sandbox wildcard, a duplicate exact name and the control route each get their defined outcome.
- Two controllers, a mid-apply upgrade, an older checkout applying a dependent target and an older runtime each refuse or proceed as Scenario 12 says.
- Fallback certificate renewal works with zero hosted sites, and renewal failure and replacement behave as Scenario 13 says.

## Risks and Assumptions

- **Risk**: every mistyped or guessed name reaches this server and its logs, which also widens the surface for scanners. The generic error limits what they learn; it does not stop the traffic.
- **Risk**: if the catch-all is removed while DNS remains, unknown names reach whatever nginx selects by default, potentially a panel site. Enable, disable and host recovery must keep them paired.
- **Risk**: a name can stay cached as missing at some resolvers after enabling. The SOA minimum observed on 2026-11-05 was 1800 seconds; that is not a universal maximum.
- **Risk**: names are still visible to Cloudflare, to the server, and in any passive DNS record of a lookup. "Anonymous" here means not published, not secret.
- **Assumption**: the zone's Cloudflare plan and account allow a proxied wildcard record, a wildcard origin certificate, and edge TLS for first-level names. Unverified; must be proven before enabling.
- **Assumption**: exact server names outrank a wildcard in nginx selection, so catch-all and exact routes do not interfere. Must be proven live on the disposable target.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
