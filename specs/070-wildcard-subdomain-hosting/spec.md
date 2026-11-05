# Feature Specification: Wildcard Subdomain Hosting

**Feature Branch**: `latest`

**Created**: 2026-11-05

**Status**: Draft

**Input**: `specs/070-wildcard-subdomain-hosting/prd.md`, readiness PASS on 2026-11-05. Owner's request: "do *.asb.bd hosting so every subdomain won't need dns change. and sites can stay anonymus. if no site exists for a subdomain throw errors. also allow subdomain dns if we need to assign a subdomain to other hosts or ..."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Turn on zone coverage safely (Priority: P1)

An operator turns on shared coverage for one zone on one remote. They see a plan first, confirm it, and can inspect the state afterwards. The safe error is in place before any public DNS points new names at the server.

**Why this priority**: Every other story depends on it, and it is the only step that changes production DNS for a whole zone.

**Independent Test**: On a disposable zone and remote, run the plan, confirm enable, then inspect state. Repeat with each unmet prerequisite and confirm no DNS write happened.

**Acceptance Scenarios**:

1. **Given** a ready host and zone, **When** the operator confirms enable, **Then** the safe error is verified before the shared DNS is published, and state reports coverage active.
2. **Given** the safe error cannot be put in place, the zone or certificate is unavailable, or provider rights are missing, **When** the operator enables, **Then** enable refuses with the reason and writes no DNS.
3. **Given** a failure or lost acknowledgement after publication began, **When** enable ends, **Then** it reports completed, rolled back, or incomplete with unknown effects, gives a safe retry, and keeps the safe error in place.
4. **Given** enable already completed, **When** the operator runs it again, **Then** nothing changes and the current state is reported.
5. **Given** a panel wildcard or regex route under the zone, an ordinary Sandbox wildcard route, or a foreign wildcard, **When** the operator enables, **Then** enable refuses and names the conflict.

---

### User Story 2 - A new site needs no DNS change (Priority: P1)

With coverage active, an operator applies a hosted target at an unused first-level name and gets a working HTTPS site. No DNS record is created for it and nothing waits on DNS for that name.

**Why this priority**: This is the owner's main request. It also removes the failure where a new name loses to a cached "does not exist" answer and the delivery rolls back.

**Independent Test**: Apply a target at an unused name, compare the zone's records before and after, then fetch the site through the public edge.

**Acceptance Scenarios**:

1. **Given** active coverage and an eligible name, **When** the operator applies, **Then** the site serves over HTTPS and the zone's records are identical before and after.
2. **Given** the controller's resolver holds a cached "does not exist" answer for the name, **When** the operator applies, **Then** delivery does not fail or roll back for that reason, and public resolver state is reported as unverified rather than as passed.
3. **Given** a completed apply, **When** the operator reads delivery evidence, **Then** origin health, public edge routing and TLS, and resolver propagation are each reported separately.
4. **Given** a covered site, **When** the operator runs plan, status, recovery or destroy, **Then** the site is shown as using shared coverage, and having no record of its own is not read as an unknown or failed effect.

---

### User Story 3 - A name with no site returns an error (Priority: P1)

A visitor to a covered name that no target serves gets one generic error, and never another site's content.

**Why this priority**: Without it, a wildcard sends every unknown name to whatever the server picks by default, which could expose a panel site.

**Independent Test**: Request sampled unknown names on many paths and methods, over HTTP and HTTPS, and compare each response with the defined error.

**Acceptance Scenarios**:

1. **Given** active coverage and no target at a name, **When** a visitor requests any path, **Then** they get the generic 404 and it marks itself as not indexable.
2. **Given** an unknown name, **When** the request uses a route-marker, robots or ACME path, or any method, **Then** the outcome is the same 404. HEAD has no body.
3. **Given** two different unknown names, **When** their responses are compared, **Then** they are identical apart from standard transport metadata and name no site.
4. **Given** a multi-level name that reaches this server, or a request whose HTTP host names no site whatever its TLS name, **When** it is served, **Then** it gets the generic 404 or a TLS failure, and never another site's content.

---

### User Story 4 - Assign a subdomain elsewhere (Priority: P2)

An operator points one subdomain at another host with an ordinary DNS record. That name goes there and nothing else changes. Sandbox leaves that record alone.

**Why this priority**: The owner asked for it explicitly, and it keeps the wildcard from taking over the zone.

**Independent Test**: Add an exact record for one name, confirm it resolves to the other host, then run plan, apply and status for other targets and confirm the record is unchanged.

**Acceptance Scenarios**:

1. **Given** active coverage, **When** an exact record is added for one name, **Then** that name resolves to its own host and other names do not change.
2. **Given** a name with any TXT record, exact address record or delegation, **When** the operator applies a target there, **Then** plan and apply say why the name is not covered, and either the operator explicitly selects a managed exact record where that is permitted, or the apply refuses with no change.
3. **Given** a record Sandbox does not own, **When** any Sandbox operation runs, **Then** that record is never changed.

---

### User Story 5 - A site can still have its own record (Priority: P2)

An operator explicitly selects a managed exact record for a target, for example a DNS-only site. Sandbox manages it as it does today. If someone changes that record by hand, apply refuses and names it, and the change stays until an operator explicitly authorizes overwriting it.

**Why this priority**: Some sites need their own record, and a deliberate move to another host must never be undone silently.

**Independent Test**: Apply with a managed exact record, change the record out of band, reapply and destroy, and check each outcome.

**Acceptance Scenarios**:

1. **Given** a target that selects a managed exact record, **When** the operator applies and later reapplies, **Then** the policy is kept and the record is managed.
2. **Given** that record was changed out of band, **When** the operator applies, **Then** apply refuses, names the record and leaves the change in place.
3. **Given** that refusal, **When** the operator explicitly authorizes overwriting, **Then** the record returns to the declared value.
4. **Given** a record changed out of band, **When** the operator destroys the target, **Then** the changed record is preserved and reported.

---

### User Story 6 - Existing sites stay as they are (Priority: P2)

Sites and exact routes that exist when coverage is turned on keep serving, and keep their records. Conflicting routes are named and never modified.

**Why this priority**: Production sites and panel sites share this server.

**Independent Test**: Record fixtures for existing sites, enable, reapply one, and compare status, content identity, redirects and authentication.

**Acceptance Scenarios**:

1. **Given** existing Sandbox targets with exact records, **When** enable and later reapply run, **Then** their routes and record ownership are unchanged, including Basic Auth.
2. **Given** exact panel routes and the exact control route, **When** enable runs, **Then** they are kept and reported as permitted exceptions.
3. **Given** a hostname already owned by another exact route, **When** the operator applies a target there, **Then** apply refuses and names the owner.
4. **Given** a blocked enable, **When** it ends, **Then** existing sites are unchanged.
5. **Given** any foreign route file, **When** any operation runs, **Then** the file is never modified.

---

### User Story 7 - Remove a site (Priority: P2)

Destroying a covered site removes the site and touches no DNS, including when it is the last site relying on coverage. Destroying a site with its own record follows today's cleanup.

**Why this priority**: The shared record must not depend on any one site's lifecycle.

**Independent Test**: Destroy a covered site and a managed-record site, compare the zone, then request the removed name.

**Acceptance Scenarios**:

1. **Given** a covered site, **When** it is destroyed, **Then** zero DNS records change and its name returns the generic 404.
2. **Given** the last dependent site, **When** it is destroyed, **Then** the shared resources remain.
3. **Given** a target with an owned exact record, **When** it is destroyed, **Then** existing owned-record cleanup applies and shared resources remain.
4. **Given** a failed target apply or rollback, **When** it ends, **Then** shared resources are not removed or revoked.

---

### User Story 8 - Turn coverage off (Priority: P2)

An operator disables coverage. If sites rely on it, disable refuses and names them, unless the operator explicitly accepts that they stop resolving. Disable removes shared DNS only.

**Why this priority**: Disabling has a wide blast radius, and the owner chose a refusal with a named override.

**Independent Test**: Disable with dependents, with and without acceptance, then request a dependent site and an unknown name from a client that still reaches the server, then re-enable.

**Acceptance Scenarios**:

1. **Given** dependent sites, **When** the operator disables without acceptance, **Then** disable refuses, names them and changes nothing.
2. **Given** dependent sites, **When** the operator disables and explicitly accepts, **Then** the shared DNS is removed and each target's route stays.
3. **Given** a completed disable, **When** a client with cached DNS, or a direct request, reaches a still-declared site, **Then** it still sees that site. An unknown name still gets the generic 404.
4. **Given** a completed disable, **When** the operator re-enables, **Then** dependent sites resolve again with no target change.
5. **Given** dependency knowledge is incomplete, **When** the operator disables, **Then** disable refuses, and acceptance of named sites does not override unknowns.

---

### User Story 9 - See and handle drift (Priority: P2)

Plan and status separate what is configured from what was freshly observed. Sandbox never quietly recreates shared DNS or creates a per-site record to cover drift.

**Why this priority**: The wildcard is shared and can be changed outside Sandbox. A silent repair could override an operator's deliberate change.

**Independent Test**: Delete and repoint the wildcard, add an exact or TXT record at a target's name, and make the provider read fail, running plan, status and apply each time.

**Acceptance Scenarios**:

1. **Given** the wildcard record was deleted or repointed, **When** the operator runs plan, status or apply, **Then** the drift is shown, and apply makes no shared or per-site DNS write without explicit authorization.
2. **Given** a provider read fails, **When** the operator runs plan or status, **Then** coverage is reported as unavailable evidence, not as healthy.
3. **Given** a new exact or TXT record at a covered target's name, **When** the operator applies, **Then** the conflict is reported and handled as in User Story 4.
4. **Given** the fallback is missing while the shared DNS exists, **When** the operator runs status, **Then** this is reported as unsafe, and the operator is told how to authorize repair.

---

### User Story 10 - Mixed versions and concurrent work stay safe (Priority: P3)

An unsupported combination of controller or in-flight work refuses before any side effect, with a reason the operator can act on. An interrupted operation can be finished by re-running it.

**Why this priority**: Several controllers and revisions share this remote. It matters less often, but mistakes here would be silent.

**Independent Test**: Run an apply and a destroy from an older checkout, run enable and disable from a second controller and during a pending apply, and crash an operation at each phase.

**Acceptance Scenarios**:

1. **Given** a controller without this feature, **When** it applies a target under a zone with a fallback installed, **Then** the existing conflict check refuses before side effects. Its destroy removes only that target's own effects. If its manual route removal deletes the fallback, status reports the zone as unsafe.
2. **Given** a pending apply or a second controller, **When** an operator disables, **Then** dependency checks include that work, or disable refuses because knowledge is incomplete.
3. **Given** a controller crash at any phase of a coverage operation, **When** the operator re-runs the same action after its lease ends, **Then** it observes current state and finishes, with no duplicate write.
4. **Given** a coverage record of a newer schema, **When** this version reads it, **Then** it refuses before side effects.

---

### User Story 11 - Keep the fallback certificate valid (Priority: P3)

An operator inspects and renews the fallback's origin certificate without deploying a site, including when no sites are hosted.

**Why this priority**: The certificate belongs to shared coverage, and its expiry would break safe errors over TLS.

**Independent Test**: Inspect validity, renew with zero hosted sites, force a renewal failure, and replace the certificate.

**Acceptance Scenarios**:

1. **Given** zero hosted sites, **When** the operator renews, **Then** renewal succeeds with no site deployment.
2. **Given** a renewal failure, **When** it ends, **Then** the failure is reported as a TLS problem, the prior certificate stays where still valid, and no other site's content is exposed.
3. **Given** any target rollback or destroy, **When** it ends, **Then** the certificate is untouched.

---

### Edge Cases

- A name cached as missing at outside resolvers from before enabling keeps failing for those clients until the cache expires. This is reported as a limit, not a delivery failure. The observed SOA minimum of 1800 seconds is not a universal maximum.
- An application wildcard declared by a target is not the fallback, and stays a conflict with the fallback.
- A target at the zone apex, or at a multi-level name, is outside coverage and uses the exact-record path.
- An operator enables while a target's exact record exists at the same name. The exact record stays and that target stays on it.
- Re-enabling after out-of-band wildcard deletion. The plan shows what would be recreated, and confirmation is required.
- Provider acceptance is lost after a write. The operation reports unknown effects, and a retry observes before writing.
- Host recovery or a server rebuild loses the fallback while DNS remains. Status reports this as unsafe (User Story 9).
- The safe error is requested with a TLS name the certificate does not cover. TLS may fail. No site's content is served.
- Search engines reach an unknown name. The 404 and its non-indexable mark apply.
- A failed first apply leaves a pending dependent with no target to destroy, or an older destroy leaves a stale one. The operator can release it with an explicit confirmed action.
- The coverage record is lost while the shared DNS or fallback remains. Apply refuses for that zone, status reports it, and enable can rebuild the record, adopting a wildcard record only if it equals what would be written.
- A target with aliases under a covered zone is not eligible in this release, and needs an explicit managed exact record, unless it already has one.

## Requirements *(mandatory)*

### Functional Requirements

**Enabling and state**

- **FR-001**: Operators MUST be able to enable shared coverage for one named zone on one named remote, as an explicit action that shows a plan and requires confirmation.
- **FR-002**: Coverage MUST NOT assume `asb.bd` or `xcloud-london`. Using only that pair in this release is operator practice, not a code gate. Each zone still needs its own explicit confirmed enable.
- **FR-003**: Enable MUST verify the safe error response on the server before publishing shared DNS.
- **FR-004**: A refusal before publication MUST write no DNS.
- **FR-005**: After publication begins, enable MUST report completed, rolled back, or incomplete with unknown effects, with a safe retry. Retrying MUST observe current state before writing, and running a completed enable again MUST change nothing.
- **FR-006**: Failure at any point MUST NOT expose another site's content. The safe error MUST stay in place whenever traffic may still reach the server.
- **FR-007**: Operators MUST be able to inspect coverage state: configured dependency, freshly observed shared DNS, edge TLS, the zone's TLS mode, fallback state, certificate validity, dependent targets and permitted exceptions. Evidence that could not be gathered is shown as unavailable.
- **FR-008**: Enable MUST refuse and name a panel wildcard or regex under the zone, an ordinary Sandbox application wildcard, a foreign wildcard, or a conflicting regex route.
- **FR-009**: Enable MUST keep and report exact panel routes and the exact control route as permitted exceptions, and preserve existing exact Sandbox routes.

**Covered targets**

- **FR-010**: A new target with an eligible name MUST default to shared coverage, with no record of its own, when coverage is active.
- **FR-011**: A name is eligible only if it is a first-level name under the covered zone, has no DNS node of its own or below it (no TXT, exact address record, delegation or subordinate name) and its target's proxy and TLS policy match shared coverage. In this release a target with more than one route is not eligible, and needs an explicit managed exact record.
- **FR-012**: Plan and apply MUST state why a name is ineligible, and MUST refuse with no change unless the operator explicitly selects a managed exact record where permitted. A record at the name that already equals what the target declares is kept as that target's exact record, with no DNS change and no refusal.
- **FR-013**: A target with an existing owned exact record MUST keep it through enable and reapply. Sandbox MUST NOT migrate it to shared coverage automatically.
- **FR-014**: Applying a covered target MUST make no DNS write and MUST NOT wait on DNS propagation for that name.
- **FR-015**: Stale answers from the controller's resolver alone MUST NOT fail or roll back delivery of a covered target.
- **FR-016**: Delivery evidence MUST report target and origin health, public edge routing and TLS, and resolver propagation as separate results. Evidence that was skipped or unavailable MUST NOT be reported as verified.
- **FR-017**: Plan, status, recovery and destroy MUST distinguish shared coverage from owned exact DNS, and MUST NOT read the absence of an owned record on a covered target as an unknown or failed effect.
- **FR-018**: Coverage is inactive for a zone that has no coverage record or whose coverage is retired. There, and on a remote without this capability, target behaviour MUST remain as it is today. Once a zone has a coverage record in any other state, the drift rules in FR-025 and the state rules for dependent targets apply.

**Error response**

- **FR-019**: A covered name with no target MUST return one generic HTTP 404, on HTTP and HTTPS, for any path, including route-marker, robots and ACME paths, and for GET, HEAD, POST, PUT, PATCH, DELETE and OPTIONS. HEAD returns no body. Methods the web server itself refuses earlier, such as TRACE, get that refusal, with no site content.
- **FR-020**: The error MUST mark itself as not indexable and MUST name no site, host or panel, and MUST be the same for every unknown name.
- **FR-021**: A name under the zone with no target, including a multi-level name, whatever its TLS name, MUST NOT receive another site's content. TLS success is not promised for names the certificate does not cover. A request whose HTTP host is outside every covered zone is out of scope and behaves as it does today; docs state this limit.
- **FR-022**: The product MUST NOT claim HTTP enumeration resistance. Existing sites still differ from unknown names at HTTP.

**Explicit records and drift**

- **FR-023**: Operators MUST be able to explicitly select a managed exact record for a target, including a DNS-only target.
- **FR-024**: Sandbox MUST NOT modify a DNS record it does not own in order to make coverage work.
- **FR-025**: When a managed exact record was changed out of band, apply MUST refuse, name the record and leave it in place until an operator explicitly authorizes overwriting it.
- **FR-026**: Destroy MUST preserve and report a managed record changed out of band, and otherwise follow existing owned-record cleanup.
- **FR-027**: Plan and status MUST show deletion or repointing of the shared record, a new exact or TXT record at a covered target's name, and provider read failures, as drift or unavailable evidence.
- **FR-028**: Apply MUST NOT recreate shared DNS or create a per-site record in response to drift without explicit authorization.

**Conflicts between routes**

- **FR-029**: Only the registered error-only fallback MAY coexist with exact routes. Ordinary application wildcards, duplicate exact routes, foreign wildcards and conflicting regex routes MUST stay conflicts.
- **FR-030**: Applying a target at a hostname owned by another exact route MUST refuse and name the owner.
- **FR-031**: Sandbox MUST NOT modify a foreign route file.

**Removal and disable**

- **FR-032**: Destroying a covered target MUST change zero DNS records, and its name MUST then return the generic 404.
- **FR-033**: Target rollback or destroy, including the last dependent target, MUST NOT remove or revoke shared DNS, the fallback or its certificate.
- **FR-034**: Disable MUST refuse and name dependent targets unless the operator explicitly accepts that they stop resolving. Incomplete dependency knowledge MUST refuse.
- **FR-035**: Disable MUST remove shared DNS only. Each target's route MUST stay, and re-enabling MUST need no target change.
- **FR-036**: The fallback MUST stay after disable. It MAY be removed only by an explicit operator action, after the shared DNS is confirmed absent.

**Shared lifecycle**

- **FR-037**: Shared DNS, the fallback and its certificate MUST have a lifecycle independent of any target.
- **FR-038**: Operators MUST be able to inspect certificate validity and to renew or replace it with no site deployment, including with zero hosted sites.
- **FR-039**: A certificate failure MUST be reported as a TLS failure and MUST NOT weaken FR-006.

**Compatibility and concurrency**

- **FR-040**: Code already shipped cannot gain new refusals, so the limits are these. On the nginx front door, a controller without this feature MUST be refused by the existing route conflict check before side effects when it applies a target or configures a control route under a zone with a fallback installed. Its destroy can remove only that target's own route and records, and may leave a stale dependent. Its manual route removal could remove the fallback, and status MUST report that as unsafe. A controller with this feature MUST refuse a coverage record of a newer schema before side effects.
- **FR-041**: Dependency checks MUST include other controllers and pending applies. Incomplete knowledge MUST refuse a destructive change. An operator MUST be able to release one named stale dependent through an explicit confirmed action.
- **FR-042**: Coverage state and its operations MUST NOT depend on the remote runtime's revision, so a runtime upgrade during an apply cannot change coverage behaviour, and there is no older-runtime case to refuse. An interrupted apply or coverage operation MUST leave its real effects reported, and MUST be resumable by re-running the same action.

**Governance**

- **FR-043**: Installing the fallback and publishing shared DNS on a real zone are production-path changes, and MUST be flagged for human review before they happen.
- **FR-044**: Docs MUST state that a covered name is not published, but not secret. Cloudflare, the server, and anyone with the URL can see it, and private sites still use Basic Auth.

### Key Entities

- **Zone Coverage**: The shared arrangement for one zone on one remote: shared DNS, fallback and certificate, with its configured state and last observed state.
- **Fallback**: The registered error-only route. It is the only wildcard route that may coexist with exact routes.
- **Target DNS Policy**: A target's choice of shared coverage or a managed exact record.
- **Permitted Exception**: An exact route that may coexist with coverage, such as a panel route or the control route.
- **Dependent Target**: A target relying on Zone Coverage, counted across controllers and pending applies.
- **Coverage Evidence**: Separate, dated results for origin health, public edge routing and TLS, and resolver propagation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With coverage active, 100% of first applies at eligible new names create zero DNS records and spend no time waiting on DNS for that name.
- **SC-002**: First apply at a covered name succeeds while the controller holds a cached "does not exist" answer, in all test runs of that condition. Today this fails after about 5 minutes of retries.
- **SC-003**: 100% of sampled unknown-name requests, across HTTP, HTTPS, ordinary, route-marker, robots and ACME paths and relevant methods, return the defined error, and none contains another site's or panel's content.
- **SC-004**: Existing sites show no change in status, content identity, redirects or authentication across enable and reapply, in 100% of stable fixtures.
- **SC-005**: Destroying any covered target, including the last one, changes zero DNS records in every test run.
- **SC-006**: Every refusal before publication changes zero DNS records, and every interrupted enable, provider timeout after acceptance and failed rollback reports its real effects.
- **SC-007**: Every drift case (deletion, repointing, new exact or TXT record, provider read failure) shows in plan and status, and no apply repairs it without explicit authorization.
- **SC-008**: An operator can point one subdomain at another host with one ordinary DNS record and no Sandbox step, and other names stay unchanged.
- **SC-009**: Every combination tested (two controllers, an older checkout applying and destroying, a pending apply during disable, a crash at each phase of an operation) refuses before side effects, or reports its real effects and finishes on re-run.
- **SC-010**: Fallback certificate renewal succeeds with zero hosted sites, and no renewal failure exposes another site's content.

## Assumptions

- The zone's Cloudflare plan and account allow a proxied wildcard record, a wildcard origin certificate and edge TLS for first-level names. Unverified; must be proven before enabling.
- Exact server names outrank a wildcard in nginx selection, so the fallback and exact routes do not interfere. Must be proven live on a disposable target.
- How a DNS wildcard behaves around existing nodes must be validated, not assumed. FR-011 follows the rule that a name with any node of its own is not covered.
- The front door on `xcloud-london` is the incumbent nginx. Sandbox writes only its own files, never `default_server` and never `nginx.conf`.
- First release covers one zone on one remote, and first-level names. Other zones, other remotes and multi-level names are out of scope.
- Moving or removing existing sites' DNS records is out of scope. Exact restore, `instance delete` and `sb server` work are out of scope.
- Search-engine policy beyond the existing `robots` behaviour and the error's non-indexable mark is out of scope.
- Traffic to this server and its logs will increase, because every guessed name now reaches it. The generic error limits what scanners learn, not the traffic.
- No remote runtime migration is needed for coverage to work. This document does not authorize one.
- Each covered target keeps its own origin certificate and its existing cleanup on destroy. FR-014 and FR-032 concern DNS only.
- Installing the fallback, not publishing DNS, is the point from which controllers without this feature refuse applies under that zone. The owner approves that step knowing this.
