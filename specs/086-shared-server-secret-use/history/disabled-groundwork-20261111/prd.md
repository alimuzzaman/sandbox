# Product Requirements Draft: Shared server secret use

**Status**: Refined candidate; disabled groundwork accepted, end-to-end delivery blocked

**Created**: 2026-11-11

**Last Refined**: 2026-11-11

**Input**: "Remote host stack operations must use registered server credentials without distributing values to invoking laptops. Preserve admitted operators' full owner rights, ControlGate and actor identity. Deliver disabled useful groundwork until authenticated owner-local dispatch and admission are integrated."

**Drafting Model**: Personal Codex, gpt-6.1-sol, high; no delegates or provider changes

**Final Validation**: `PASS`, independent personal Codex gpt-6.1-sol high, read-only readiness review

**Validated On**: 2026-11-11

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

`sb host stack up --remote` checks required keys in the invoking controller's personal source. A clean laptop therefore refuses even when the intended server holds the keys. Later hosting and Cloudflare consumers also resolve implicit local credentials. Copying keys onto every operator's laptop defeats the intended server custody boundary.

The product needs explicit server credential scope across validation, admission and effects. Source availability alone cannot make server execution safe or supported. The owner accepts useful disabled groundwork while the owner-local dispatch dependency remains unfinished.

## Users and Desired Outcomes

- Any operator admitted by the existing control service can request the supported server operation with the same full Sandbox/alim rights, including currently owner-approved sudo rights. There is no new restricted member role or per-laptop credential grant.
- The server owner retains registered credentials on the server, knows which actor requested their use, and can distinguish a missing key from an unsupported execution capability.
- Personal local users retain current local behavior without adopting shared-server scope.

## Goals

- Make credential scope explicit and keep server provider values behind the server broker.
- Separate static validation on the laptop from required-key preflight and credential-consuming effects on the selected server.
- Bind reviewed intent and credential references to the same server installation, admitted actor, source revision and operation lifetime.
- Produce useful source changes that refuse unsupported server execution until all dependencies are integrated. Report blocked end-to-end acceptance honestly.

## Non-Goals

- Deployment, installation upgrades, migration, stack apply, cloud changes, host jobs, live credential mint/revoke, account-policy changes or new permission restrictions during this source-only work.
- Credential distribution to laptops, arbitrary remote secret reads or commands, dummy local presence, inherited owner environment, staged secret files, or wrong-host fallback.
- Moving `/home/alim/sandbox/sb-src/sb`, changing permissions, or adopting the unapproved `/var/lib/sandbox` managed layout.
- Changes to sibling worktrees, gate/link files, nginx/frontdoor/apex behavior, or use of unsupported `--here`, self-SSH or self-remote recipes.
- General redesign of personal local secrets, existing generated-secret behavior, or unrelated hosting flows.

## Product Scenarios

### Scenario 1: Clean laptop and capable server

- **Starting state**: The admitted operator's laptop lacks provider keys. The explicitly selected server has an integrated supported execution capability and the required registered keys.
- **User action**: Request stack up with explicit server credential scope and registered source/profile references.
- **Expected outcome**: Static validation needs no local provider keys. Server preflight checks the required names. Only intended trusted server consumers receive values. The laptop receives bounded status and identifiers. This outcome remains blocked until integration prerequisites pass.

### Scenario 2: Installed dispatch is absent

- With the currently unaccepted owner-local dispatch API, the same request returns an unsupported-capability result before staging, secret resolution, claims or effects. A declarative manifest, context schema or copied source does not enable execution. Disabled groundwork is a valid source deliverable; it does not satisfy Scenario 1.

### Scenario 3: Missing server key or unusable provider credential

- A capable server lacking a required provider key refuses before effectful work. The diagnostic names only the missing key, never characters, length, fingerprint or content.
- Presence is distinct from provider validity. A later provider rejection is reported as a bounded provider failure. It never triggers local fallback, generation of a provider key, or a claim that inventory proved provider access.

### Scenario 4: Reference or identity changes

- If installation identity, actor binding, source/profile selection, source revision, reviewed intent or deadline changes between preflight, staging, resolution and effects, the operation refuses the mismatch before subsequent effects. It cannot silently re-resolve the latest revision or switch hosts.
- Concurrent requests keep their own identities and references. An operator's grant cannot be reused as another actor's grant, even though both operators have full rights.

### Scenario 5: Disconnect, expiry and replay

- An uncertain submission remains uncertain until the same request identity is looked up. Replaying identical accepted intent yields the retained operation result rather than duplicate effects. Reusing an identity for different intent refuses. An expired or cancelled request cannot start new secret consumption or effects; diagnostic/status reads disclose no values.

### Scenario 6: Local and generated credentials

- Existing personal local commands retain their behavior. Explicit shared-server scope cannot fall back to this path.
- Required provider credentials and generated stack credentials remain distinct. Existing explicit generation authority cannot invent or replace a provider API key. Missing generated credentials report their separate status without authorizing unrequested server writes.

### Scenario 7: Inventory and control admission

- An admitted operator can request names/status-only inventory from the selected server when that capability is supported, without provisioning or generating anything. Unadmitted callers fail at ControlGate. A client-supplied actor label cannot replace authenticated actor identity.

## Proposed Product Behavior

- A request explicitly distinguishes personal-local and server-held credential scope. Server scope identifies registered server project context, source and approved consumer/profile references. Profile restrictions constrain destination and secret handling, not membership rights.
- Provider keys remain within the existing registered server source and broker-to-consumer use boundary. No new plaintext copy may reach client responses, transport envelopes, argv, logs, polls, retained jobs, artifacts, caches or storage, including temporary staged files. Trusted server consumers may receive values only for their approved purpose and lifetime. A consumer needing unsupported persistent provider-secret delivery must refuse rather than write an env file.
- Local static validation checks declarations and nonsecret intent only. It must label remote availability unverified when server preflight did not run. It must not report a local missing key as a server deficiency or claim a successful remote preflight from static validation.
- Server preflight checks all required provider names and declared consumer bindings before effectful source staging, resource claims, generation or deployment. Recheck the bound authority before resolution and effects. Reference-only staging is permitted only after the capability and preflight requirements are met.
- Preserve ControlGate admission and existing access policy. Retain authenticated actor provenance through the operation alongside server execution identity. Running a server consumer as alim does not turn the initiating operator into alim for attribution. Context-only schema evidence is not credential-use authorization.
- Reviewed intent includes source-key mappings and provider-to-consumer destinations, not just destination names. Source and profile revisions cannot drift unnoticed. Values never become part of public intent or replay identifiers.
- Requests have finite deadlines, replay boundaries, cancellation behavior and retained nonsecret lookup identifiers. Existing uncertain-admission behavior must remain recoverable without launching a new request identity.
- Generated stack credentials retain their separate explicit generation and existing runtime-delivery rules. This work must not reclassify required provider credentials as generated credentials to bypass custody restrictions.

## Constraints and Dependencies

- Baseline inspected: `e46777bf0868d4ecc38bffdbf8f228828790ddbf`, initially clean feature branch. This drafting phase changes only this PRD and `.specify/feature.json`; the scratch report is external.
- Owner-supplied inventory reports registered `personal` source in `/home/alim/sandbox` context and names `XSPEED_CF_API_TOKEN`, `XSPEED_JEV_API_KEY`, `CLOUDFLARE_API_TOKEN` at `r1_bc66c07a5d8e6232d362941fab3193f2`. This is supplied evidence, not refreshed inspection or proof of provider validity. Do not hardcode that revision as a perpetual grant.
- The current private owner installation stays at `/home/alim/sandbox/sb-src/sb`. Future identity compatibility must allow an immutable managed-server identity without requiring or authorizing `/var/lib/sandbox` migration.
- The sibling `feat/miu-site-backend` dependency has no accepted or installed authenticated owner-local dispatch/admission API. Its dirty sources are read-only inputs. Root-confirmed interface facts are listed below; they are not proof of installed support.
- `hosting_owner_context_v1` schema 1 binds canonical machine, uid, home, revision24 and policy. It supplies context only, with no dispatch or operator credential authorization. Local target schema 2 uses endpoint/local-id, `owner_host_digest` and incarnation, with remote fields null. Its executor is local with `remote_name` null. Remote target schema 1 remains unchanged.
- Root reports `target_for_owner`, `build_delivery_service(owner=...)`, `HostingAttempt`, `publish_local_source` requiring `SourceAuthority`, and shared `CanonicalTarget`/`TargetOwnership` lease semantics. Reuse requires accepted integration evidence, not guessed callable behavior.
- Enablement requires an authenticated installed owner-local dispatcher and genuine admission evidence; propagation of admitted actor and canonical installation identity; source/profile/revision binding and required-key preflight before effects; compatible source authority and target leases; lifecycle, gate and driver wiring; matching docs and revision evidence; focused tests and separately authorized runtime proof. Until then the credential-consuming remote path stays disabled.
- New mechanisms must respect explicit module manifests/contracts and existing choke points. Keep compatibility paths until parity evidence and required human review. This PRD does not prescribe a wire schema, command spelling or implementation plan.
- Feature numbering resolved by root reservation on 2026-11-11: this feature owns `086-shared-server-secret-use`; sibling work retains `085-hosted-site-baselines`. The owned feature directory and active pointer are relocated to 086. No sibling files were changed and its directory is absent from this checkout.

### Inspected source evidence

- `sandbox/commands/host_stack.py:351-368,598-613`: invoking-process secret gate, including the gate before the first remote claim.
- `sandbox/commands/hosting.py:385-399`: required/generated mappings resolve through personal local secrets; `:2758-2774` constructs a local durable job that invokes remote host apply. `:5432-5435` checks credentials again; `:5512` constructs the Cloudflare client.
- `sandbox/core/_secrets.py:61-66` and `sandbox/core/_cloudflare.py:26-28`: ambient environment/personal-file resolution and Cloudflare local fallback. These are source findings; no secret source was opened.
- `sandbox/commands/hosting.py:2718-2739`: current nonsecret intent represents secret map destination shape. Source mappings and server references need explicit reviewed binding in the new scope.
- `sandbox/secrets/context.py:17-75`, `sandbox/secrets/service.py:411-439,626-665`: registered source/profile composition, inspection and fixed consumer use. `:25-66` supplies a revision-bound staging precedent, not a ready hosting dispatch API.
- `mcp/wp-server/tailnet_access.py:161-178`, `mcp/wp-server/server.py:769-786`: current token/tailnet admission and middleware. Admission results are not currently carried here as a durable hosting credential-use grant.
- `sandbox/delivery/admission.py:26-110`: source/job/process-bound local admission; it does not itself grant effect authority. `sandbox/transports/remote_jobs.py:893-924` binds remote durable submission to identity, deadline and request ID, but is not proof of owner-local dispatch integration.
- Existing read-only source report corroborates local resolution and distinguishes the owner-Mac Cloudflare source name from the server consumer name. Do not invent aliases from names alone.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Operator rights | Any admitted tailnet/shared operator may use server-held keys with full current owner rights | Avoid per-laptop distribution and invented member restrictions | User, reconfirmed 2026-11-11 |
| Custody | Explicit server source/profile references; broker-to-trusted-server-consumer only | Keep provider values on the selected server | User |
| Execution | Disable credential-consuming remote execution until authenticated installed dispatch/admission integration | Context and declarations alone are insufficient | User coordination update 2026-11-11 |
| Deliverable | Accept disabled useful source groundwork with blocked end-to-end acceptance reported separately | Allow safe progress while dependency is unfinished | User coordination update 2026-11-11 |
| Installation | Retain private owner installation; managed layout remains unapproved | No implied migration or permission change | User |
| Local behavior | Preserve personal local resolution and separate generated-secret rules | Bound compatibility impact | User |

## Open Questions

- No consequential product choice remains unresolved. Independent readiness review passed.
- Root integration follow-ups are dependency evidence; numbering is resolved by the 086 reservation, not a new permission decision. Endpoint syntax, envelope fields and lease details belong to later specification and planning, within the confirmed boundaries above.

## Acceptance Outcomes

### Source groundwork accepted while dispatch is unavailable

1. A server-scoped request returns a stable unsupported-capability result before staging, secret resolution, claims or effects when installed dispatch/admission support is absent. Personal local behavior remains available. Declarations alone cannot change this outcome.
2. Static validation works with an empty synthetic laptop credential environment and reports server-key availability as unverified until actual server preflight. It records explicit credential scope and registered source/profile references without values.
3. Names/status-only inventory, where supported, cannot expose values, value-derived metadata or authorize mutation. Unsupported inventory reports unavailable rather than reading a laptop source.
4. Documentation states the disabled capability and exact integration prerequisites. Passing source checks is reported separately from runtime or end-to-end acceptance.

### End-to-end acceptance blocked until integration

5. On an integrated supported path, a clean laptop with absent provider keys can complete the authorized operation using present server keys. No dummy local key, inherited owner environment, staged secret file or per-laptop distribution is needed. Valid provider authority must be established separately from presence.
6. Missing any required server provider key refuses before effectful work and names only missing keys. `--generate` does not satisfy missing provider keys. Generated credentials retain explicit separate authority.
7. Denied admission, actor substitution, changed installation, changed source/profile/revision, changed source mapping or destination, expired deadline and cross-request replay each prevent unauthorized subsequent consumption/effects. Legitimate admitted operators retain equal rights.
8. An identical uncertain submission can be looked up and replayed with one request identity without duplicate effects. Changed intent with that identity refuses. Cancellation/expiry prevent new consumption; status and retained output remain secret-free.
9. Intended server consumers receive credentials only for the bound operation. Client responses, raw output, argv, logs, poll results and retained/staged storage contain zero provider-secret values. This includes provider failures, consumer failures and timeouts, with no fallback to another host or local source.
10. Installed revision/capability, authenticated admission, actor provenance, source authority, canonical target lease and lifecycle/gate/driver integration are independently evidenced before enablement. Absent evidence keeps this category blocked; this PRD run performs none of those runtime checks.

## Risks and Assumptions

- The sibling API is unfinished. Source compatibility and declarative capability names cannot establish installed execution support.
- Existing hosting code contains environment-file delivery and ambient credential lookup. Partial rerouting can pass one gate while leaking provider values later; acceptance covers the whole consumer path.
- Existing actor attribution and source admission are separate mechanisms. Neither can substitute for authenticated operator-to-operation credential binding.
- All admitted operators remain trusted with full owner rights. Broker custody limits accidental disclosure and recipient drift; it does not claim isolation from a malicious privileged server owner.
- Supplied key presence and revision may become stale. Server preflight must bind a current reviewed revision; inventory cannot establish token permissions or validity.
- Full end-to-end evidence requires later explicit operational authorization. The current source-only scope cannot establish live stack correctness under constitution principle IV.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking product questions remain; dependency and integration blockers are explicit.
- [x] No implementation plan, task list, wire contracts, or code changes are included.
- [x] The latest independent readiness validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
