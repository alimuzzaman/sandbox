# Feature Specification: Front-door-only refresh for hosted targets

**Feature Branch**: `feat/front-door-refresh`

**Created**: 2026-11-11

**Status**: Draft

**Input**: User description: "Spec 084, short name front-door-refresh. Existing hosted targets cannot pick up Sandbox front-door changes, such as spec 081's default `X-Robots-Tag: noindex, nofollow, noarchive`, without a new application revision. Re-applying the same revision and config is refused (`unproven_staged_revision`) whenever the running application cannot be proven exact, which is every target whose source revision is not declared (all Hub stacks; feedback e674a79e, live examples xspeed-hub-test-stacks and xspeed-wp-test-stacks). Build a refresh that re-renders and reloads only the front-door route, and the gate routes, from the target's recorded state and the current renderer: the same loopback port, hostnames, certificates and share block. No Compose, container, initializer or DNS changes, so no runtime proof is needed. Fail closed: a private target never becomes public, and a failure restores the previous marked route. Respect the hosting locks, fences, delivery journal and audit; a fenced target refuses. CLI and MCP entry points. Document it in docs/remote-hosting.md. Additional scope from the orchestrator: say whether the refresh may run on a target whose last attempt was refused before any effect, and make `delivery inspect` / `retire-delivery` usable for stack targets or document the right selector."

## Background and evidence

- Spec 081 made `X-Robots-Tag: noindex, nofollow, noarchive` the default header on every
  hosted response. The renderer change reaches a target only when its front-door route is
  rendered again, which today happens only inside a full `sb host apply`.
- A full apply of an unchanged revision and configuration may skip Compose (edge replay)
  only when it freshly proves that the exact revision is running. A target whose source
  revision is not declared can never give that proof, so the apply refuses with
  `unproven_staged_revision`. Both Hub test stacks on xspeed-dev are healthy and in this
  state (feedback `e674a79e`, jobs `40ab97ea` and `8b474591`).
- Spec 081's rollout added the pieces a safe refresh needs: marked private routes, the
  per-apply route tag that verification requires, strict route reads, the remote target
  lease, and the fail-closed rollback that never leaves a privacy-weaker route live.

## Clarifications

### Session 2026-11-11

Resolved by the implementing agent from code and controller evidence (the brief came from
the orchestrator, with no interactive owner); each answer states its reason.

- Q: May the refresh run on a target fenced only by a refused attempt? → A: Yes. The
  fence is a delivery record left `unknown` (`effect_unknown`) by the refused apply; it
  blocks the next apply's predecessor check, not the target. A refresh creates no delivery
  attempt, holds the same effect lock and remote lease an apply holds (so no apply or
  recovery can be running), reads no runtime evidence, binds to fields only a successful
  delivery writes, and leaves that record untouched. Active operations, uncertain recovery
  and image activation still refuse.
- Q: Does the refresh need `host retire-delivery` first? → A: No, and retiring is not
  free: it marks an unproven runtime retired, so the next apply recreates the containers.
  Retire only before the next real apply.
- Q: Entry point? → A: A new `sb host refresh-front-door` action with the existing target
  selectors (`--target P/E`, `--name <stack>`, or `--project-dir`), `--confirm` to act
  (without it: a read-only plan), `--json`; plus an MCP tool calling the same function.
- Q: Must it run as a durable job? → A: No. It is one render, one reload and one
  verification, and it records no delivery attempt; it may still run inside a job.
- Q: How do stack targets reach their delivery records? → A: `delivery inspect` and
  `host retire-delivery` accept `--name <stack>` / `--target P/E`, and resolve the
  attempt through the scope the target's own hosting record names. Evidence: the Hub
  stack's refused attempt (job `40ab97ea`) is stored under checkout
  `source/10c2f692ff93-…`, which a later `stack up` replaced, so no current directory
  resolves to its scope; the generated checkout holds the stack template, which the
  manifest reader refuses, and inspect reported that as `delivery_store_unavailable`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Refresh a public target's front door (Priority: P1)

An operator runs one command against an existing hosted target. Sandbox renders the
target's front-door route again with the current Sandbox renderer, from what the target
already has (loopback port, hostnames, certificate, gate), reloads the front door,
verifies every public name answers as before, and reports success. The application, its
containers and its DNS records are not touched.

**Why this priority**: This is the reported gap. Every existing target needs the noindex
header now, and none can get it without a new application revision.

**Independent Test**: With a target applied by an older Sandbox (route without the
noindex header), run the refresh. The installed route now carries the header, the
application's containers were not recreated, no DNS call changed anything, and the
target answers through its public names.

**Acceptance Scenarios**:

1. **Given** a healthy target whose route lacks the noindex header, **When** the operator
   runs the refresh, **Then** the route is re-rendered with the header, the front door is
   reloaded, every declared name verifies, and the result reports `refreshed`.
2. **Given** a target whose route already matches the current renderer, **When** the
   operator runs the refresh, **Then** nothing is reloaded, the names are still verified,
   and the result reports `unchanged`.
3. **Given** a refresh whose verification fails, **When** the failure is detected,
   **Then** the previous route is put back, the front door is reloaded, and the command
   fails with the verification reason and the restore outcome.
4. **Given** any refresh, **Then** no container, Compose project, initializer, DNS record,
   zone setting or certificate issuance is created, changed or removed.

---

### User Story 2 - Refresh never weakens a private target (Priority: P1)

A target declared `share.private` is refreshed the same way, and it stays private: every
non-gate name answers 404, and the gate keeps answering only for itself. A refresh can
never turn a private target public, and a failure leaves the previous private route.

**Why this priority**: Spec 081's hard rule. A refresh that could publish a private site
would be worse than no refresh.

**Independent Test**: Refresh a private target and confirm the new route is a marked
private route and verification required the tagged 404 and the gate's own answer. Then
make verification fail and confirm the previous marked route is back. Then change the
manifest to public and confirm the refresh refuses before any change.

**Acceptance Scenarios**:

1. **Given** a private target whose installed route is a marked private route, **When**
   refreshed, **Then** the new route is marked private, carries a fresh route tag, and
   verification proves the private names answer the tagged 404 and every gate name
   answers the gate's own tagged 404.
2. **Given** a private target whose installed route is not a marked private route,
   **When** refreshed, **Then** the refresh refuses before any change and tells the
   operator to run a full apply (with a new revision when the target's source revision
   is not declared, since a same-revision apply is refused).
3. **Given** a target whose installed route is a marked private route and a manifest that
   is no longer private, **When** refreshed, **Then** the refresh refuses before any
   change (a refresh never publishes a private target).
4. **Given** a private target any of whose names is proxied by Cloudflare, **When**
   refreshed, **Then** the refresh refuses before any change.
5. **Given** a private refresh that fails after the new route is installed, **Then** the
   previous marked private route is restored.

---

### User Story 3 - Refresh respects ownership, fences and the record (Priority: P1)

A refresh runs only when nothing else owns the target and the target's record proves what
the route should contain. It refuses, before any remote change, when another apply,
recovery, destroy or image activation owns the target, when recovery is uncertain, when
another controller holds the target lease, or when the manifest no longer matches what the
target was last delivered with in any way the front door depends on.

**Why this priority**: A refresh that raced an apply, or that silently delivered new
hostnames or weaker access rules, would bypass the guarantees the delivery journal gives.

**Independent Test**: Put the target in each owning state in turn and confirm the refresh
refuses with a stable code and changes nothing. Change a hostname, the Cloudflare proxy
setting or the basic-auth declaration in the manifest and confirm the refresh refuses.

**Acceptance Scenarios**:

1. **Given** a target with an active operation, uncertain recovery or active image
   activation, **When** refreshed, **Then** it refuses with `front_door_refresh_fenced`.
2. **Given** another controller's target lease, **When** refreshed, **Then** it refuses
   with `host_apply_lease_held`.
3. **Given** a manifest whose hostnames, DNS records, proxy setting, certificate names or
   loopback port differ from the target's last successful delivery, **When** refreshed,
   **Then** it refuses with `front_door_refresh_declaration_changed` and names the
   differing part.
4. **Given** a target with no successful delivery on record, or no installed route,
   **When** refreshed, **Then** it refuses with `front_door_refresh_no_route`.
5. **Given** a target whose last delivery attempt is not running (its record may be
   `unknown` with `effect_unknown`, failed, refused or succeeded) and that has no active
   operation or uncertain recovery, **When** refreshed, **Then** the refresh runs, and it
   neither consumes, settles nor clears that attempt.
6. **Given** any refresh outcome, **Then** an audit line records the target, controller,
   outcome, stable code and the route digests before and after, and never a secret.

---

### User Story 4 - Inspect and retire deliveries of stack targets (Priority: P2)

An operator who needs to inspect or retire a hosted stack's delivery can name the stack
instead of guessing its generated source directory, and gets the delivery the stack's
last apply recorded.

**Why this priority**: The two first targets of the refresh are stacks, and the
orchestrator could not inspect their deliveries (`delivery_store_unavailable`,
`binding_mismatch`). It does not block the refresh itself.

**Independent Test**: For a stack target, run inspect with the stack selector and get the
same answer as with the exact project root the stack's apply was admitted with.

**Acceptance Scenarios**:

1. **Given** a hosted stack, **When** the operator inspects its delivery by stack name,
   **Then** the delivery recorded by the stack's last apply is shown.
2. **Given** an unknown stack name, **Then** the command refuses with the existing
   target-resolution code (`host_target_unknown`) and writes nothing.

### Edge Cases

- The refresh is interrupted after the new route is installed and before verification:
  the target keeps a route rendered from its own record; running the refresh again is
  safe and converges, and the audit shows the started run without an outcome.
- The certificate on record no longer covers a declared name, or the certificate files
  are missing: the refresh refuses rather than issue one.
- The route would lose its HTTPS listener: refused before any change.
- The remote's front door is not the one the target was delivered through (Caddy versus
  nginx): refused.
- Basic auth is declared but its password secret is missing: refused before any change.
- The installed route has basic auth and the rendered route would not: refused.
- A target on shared zone coverage: verified through the shared coverage evidence, with
  no DNS record read for writing.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Operators MUST be able to request a front-door-only refresh of one hosted
  target from the command line and from the MCP integration, with a reviewable dry run.
- **FR-002**: The refresh MUST render the route with the current Sandbox renderer from the
  current manifest bound to the target's last successful delivery: the recorded loopback
  port, the recorded DNS records (same names, addresses and proxy setting), the recorded
  certificate, the recorded front-door kind and route name.
- **FR-003**: The refresh MUST refuse, before any remote change, when the current manifest
  disagrees with that record in hostnames, DNS records, proxy setting, certificate names,
  loopback port or front-door kind (`front_door_refresh_declaration_changed`).
- **FR-004**: The refresh MUST NOT create, change or remove containers, Compose projects,
  initializers, source checkouts, DNS records, zone settings or certificates, and MUST
  NOT require runtime revision proof.
- **FR-005**: The refresh MUST refuse while another apply, recovery, destroy or image
  activation owns the target, while recovery is uncertain, and while another controller
  holds the target lease; it MUST hold the same local serialization and remote target
  lease as `host apply` for its whole run. The read-only plan takes no lock and writes
  nothing.
- **FR-006**: The refresh MUST read the installed route strictly (a failed read is not
  "no route") and MUST refuse when there is no installed route.
- **FR-007**: For a `share.private` manifest, the refresh MUST refuse unless the installed
  route is a marked private route, MUST refuse when any of the target's names is proxied,
  and MUST verify privacy with a fresh route tag exactly as an apply does.
- **FR-008**: The refresh MUST refuse when the installed route is a marked private route
  and the manifest is not private, and when the installed route has basic auth and the
  rendered route would not.
- **FR-009**: The refresh MUST refuse when the installed route serves HTTPS and the
  rendered route would not.
- **FR-009a**: The refresh MUST refuse unless the rendered route serves exactly the
  hostnames the installed route serves (`front_door_refresh_names_changed`), so a reload
  can never add a name (and with it a certificate request) or drop one.
- **FR-010**: On any failure after the route is installed, the refresh MUST restore the
  previous route through the same fail-closed restore the apply uses, and report the
  restore outcome; a restore failure MUST be reported, never hidden.
- **FR-011**: A route identical to the installed one MUST NOT be reloaded; the names are
  still verified.
- **FR-012**: The refresh MUST NOT create, settle, retire or rewrite a delivery attempt or
  the target's hosting operation. Whether it may run does not depend on the last attempt's
  record state (`unknown`, failed, refused, succeeded): the effect lock and remote lease it
  holds prove no apply or recovery is running, it binds only fields a successful delivery
  writes plus the installed route, and it reads no runtime evidence.
- **FR-013**: Every refresh MUST append an audit line before its first remote change and
  one with its outcome, with no secret, token, password or address in it; the target's
  record MUST keep the last refresh outcome.
- **FR-014**: Operators MUST be able to inspect and retire a hosted stack's delivery by
  naming the stack (or the target), resolved through the delivery scope the target's own
  hosting record names; a retire through that scope MUST reach only the exact attempt that
  record names, which is the attempt the next apply's predecessor check reads.
- **FR-016**: Every successful apply, edge continuation and refresh MUST record a digest
  of the target's non-secret front-door declaration (routes, basic-auth identity, share
  block, indexing and robots policy, proxy setting, DNS coverage, health path); a refresh
  MUST refuse when a recorded digest differs from the current manifest's
  (`front_door_refresh_declaration_changed`, part `declaration`; FR-003's field checks
  name their own part). A target with no recorded digest (delivered before this feature)
  is bound by FR-002 to FR-009a alone; the plan reports `legacy_binding: true`, and the
  refresh then records the digest. On such a target, front-door-only manifest edits made
  since its last successful apply that FR-002 to FR-009a do not detect (indexing, robots,
  basic-auth bypass lists, non-privacy share settings, health path) are rendered once;
  this is a documented limit.
- **FR-015**: The behaviour, refusal codes and the operator commands MUST be documented in
  the remote hosting guide and the Sandbox CLI skill.

### Key Entities

- **Delivered front-door record**: what the target's last successful delivery recorded
  for its front door: loopback port, route name, front-door kind, DNS records,
  certificate and its names.
- **Installed route**: the route file the front door serves now, possibly marked private.
- **Refresh outcome**: `refreshed`, `unchanged` or `failed` (with the restore outcome:
  `restored` = previous route back, `removed` = route removed because the previous one was
  not a marked private route on a private target, or `restore_failed`), kept on the
  target's record and in the audit log; `refused` (with code) goes to the audit log only,
  since a refusal changes nothing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An existing target with a pre-081 route carries the noindex header on every
  public answer after one refresh, without a new application revision.
- **SC-002**: Zero container, Compose, initializer, DNS or certificate changes during a
  refresh, in every tested path.
- **SC-003**: In every tested failure path for a private target, every non-gate name still
  answers 404 afterwards.
- **SC-004**: Every refusal happens before the first remote change and names a stable code.
- **SC-005**: Every refresh leaves an audit trail an operator can read with no secret in it.

## Assumptions

- The refresh is synchronous and short (one route render, one reload, one verification);
  it does not need a durable job, though it may run inside one.
- The route tag, privacy verification and fail-closed restore from spec 081's rollout are
  reused unchanged.
- "Recorded state" means the fields only a successful delivery writes (loopback port,
  records, certificate, route name, front-door kind); fields a refused attempt rewrites
  (requested revision, hostnames list, hosting operation) are not trusted as proof of
  what is live.
- Targets applied before this feature have no recorded front-door declaration digest; the
  refresh binds them through the success-only fields plus the installed route's privacy
  and basic-auth checks, then records the digest.
- Live targets are refreshed by the orchestrator after merge; this work never applies to
  a live target.
