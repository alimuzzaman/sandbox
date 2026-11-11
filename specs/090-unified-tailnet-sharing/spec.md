# Feature Specification: Unified tailnet sharing

**Feature Branch**: `feat/stack-page-access` (090 artifacts separate from 088 fixture branch)

**Created**: 2026-11-11

**Status**: Draft, source planning only

**Input**: Reviewed [PRD](prd.md), independent personal Sol high readiness PASS on 2026-11-11. Extend one canonical sharing system to whole stacks, recorded entries/paths and verified tailnet web destinations. No runtime enablement is implied.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Share one existing selection (Priority: P1)

An authorized operator shares a whole stack or one recorded entry/path through the existing share workflow and root-owned convenience script. A teammate outside the tailnet uses the declared access without obtaining wider panel or sibling authority.

**Why this priority**: Existing supported sharing must remain useful while target selection grows; a new destination kind must not create a separate product or break current grants.

**Independent Test**: Root exercises create/list/revoke for an existing stack and one recorded service/path, including its ordinary application authentication and companion fences.

**Acceptance Scenarios**:

1. **Given** a declared, authorized stack, **When** an operator creates a stack share, **Then** one link provides its scoped page and declared own/direct-shared access under the existing canonical lifecycle.
2. **Given** an authorized recorded service and path, **When** it is shared individually, **Then** that selection works while a sibling path, service, stack and full-panel mutation are outside the grant.
3. **Given** a wrapper invocation, **When** it selects a supported target, **Then** it delegates issuance and management to the canonical engine and exposes no independently issued token or alternative login fallback.
4. **Given** no implemented generic URL contract, **When** a wrapper is used, **Then** it does not advertise or accept generic URL sharing as supported.

### User Story 2 - Share a verified tailnet web destination (Priority: P1)

A currently admitted owner operator selects an HTTP(S) destination and bounded path that the receiving server can verify and reach under its own network identity. A teammate uses it through the same sharing gate without joining the tailnet or inheriting the operator's credentials.

**Why this priority**: Individual tailnet tools are the requested extension and require positive proof of authority, not merely a plausible hostname.

**Independent Test**: Root uses an explicitly authorized disposable target and negative controls for public lookalikes, conflicting DNS/address identities, denied owner admission and unavailable receiver connectivity; no supplied secret-link path is probed for admission.

**Acceptance Scenarios**:

1. **Given** current receiver-side node identity, DNS/address binding, genuine current owner admission and receiver service reachability, **When** a generic URL grant is created, **Then** the common engine issues an individually manageable bounded grant for that immutable selection.
2. **Given** missing, stale, conflicting or denied authority evidence, **When** creation or later access is attempted, **Then** it refuses before forwarding to an unverified destination and reports a bounded reason.
3. **Given** the operator is currently owner-admitted and the receiver may reach a verified target that the operator's own node cannot, **When** a bounded share is requested, **Then** owner-network delegation is permitted under the explicitly bound policy revision, without claiming that the operator's direct target ACL is preserved.
4. **Given** a node/address replacement, DNS rebinding or changed owner-delegation policy after issuance, **When** further access is attempted, **Then** the old grant cannot retarget, widen or continue using invalid authority.

### User Story 3 - Use supported browser behavior within scope (Priority: P2)

A recipient navigates a scoped application, loads its assets and uses supported WebSockets while the application retains its own authentication requirements.

**Why this priority**: A link that opens a shell page but cannot load required resources is not working access, while compatibility must never widen authority silently.

**Independent Test**: Root exercises a supported application through its actual receiver/browser and checks same-scope navigation, assets, forms, redirects, cookies and a supported WebSocket against out-of-scope controls.

**Acceptance Scenarios**:

1. **Given** a supported scoped application, **When** a recipient navigates and uses its required resources, **Then** the supported workflow completes without escape to an unauthorized host, port, path or service.
2. **Given** an incompatible resource, cross-origin login or redirect, **When** it requires authority outside the grant, **Then** access refuses or explains the incompatibility without expanding scope or exposing an upstream address/credential.
3. **Given** an application requiring login, **When** a generic URL share is opened, **Then** normal application login still applies and no WordPress admin/autologin authority is inferred.
4. **Given** a mailbox destination, **When** access is shared, **Then** it remains canonical gated access to the authorized mailbox scope, never a raw public mailbox listener or another stack's inbox.

### User Story 4 - Revoke and recover the original grant (Priority: P1)

An operator can see and revoke each grant, including uncertain creation/cleanup, without accidentally minting another grant or affecting a different actor/parent.

**Why this priority**: Generic destination support cannot weaken the existing durable recovery and revocation guarantees.

**Independent Test**: Root exercises finite admission counts, expiry, concurrent sessions, active streams and lost acknowledgements against exact original IDs.

**Acceptance Scenarios**:

1. **Given** a generic URL grant with allowance N, **When** concurrent successful admissions exhaust N, **Then** no N+1 admission succeeds; ordinary authorized resource requests do not consume new admissions.
2. **Given** expiry or accepted revocation, **When** another admission or gated request is made, **Then** access is denied; open gated streams/WebSockets are closed before revocation reports complete.
3. **Given** a lost create/revoke acknowledgement or missing parent metadata, **When** inventory/recovery is requested, **Then** original IDs and uncertainty remain visible and retry does not remint or target a replacement service.
4. **Given** another parent or actor's grant on the same receiver, **When** a scoped revoke is performed, **Then** it affects only the authorized selected original grants and their gate sessions.

### Edge Cases

- Public names resembling tailnet names; literal addresses without node ownership proof; reused node addresses; multi-answer DNS with inconsistent or mixed authority; unavailable verification sources; arbitrary public, LAN, loopback and metadata destinations.
- Invalid schemes, user-info credentials, alternate authority/port spelling, traversal, double encoding, ambiguous paths, cross-origin redirects and forged forwarding headers.
- Cookies or application redirects that require a larger domain/path; unsupported assets; cross-origin authentication; streaming or WebSocket access spanning expiry/revocation.
- Expiry and exhaustion during concurrent admission; source/target replacement while a request waits; unknown mint acceptance, controller restart and cleanup acknowledgement loss.
- Sensitive query/fragment values and user-supplied one-time URLs: verification must not fetch/redeem them or disclose them through diagnostics.
- Old receivers lacking required capabilities and parked login or missing application-auth readiness: no generic-target fallback or success claim.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide one canonical creation/list/revoke workflow for stack, recorded target plus declared entry/path, and verified tailnet HTTP(S) URL targets, using the existing share gate and management authority.
- **FR-002**: The convenience script MUST delegate to that engine and MUST NOT provide a generic URL option before the corresponding contract and receiver support are implemented and reviewed.
- **FR-003**: Issuance MUST require genuine current admission under the server's owner policy, preserve existing opt-in and companion fences, and bind that owner delegation explicitly. A recipient bearer MUST NOT confer general receiver control, panel mutation authority or another grant's management. The operator's direct target ACL MUST NOT be imposed or claimed as preserved.
- **FR-004**: A generic grant MUST bind the genuine owner-admitted actor, actual receiver, original tailnet node identity, exact approved service/scheme/port, bounded path and explicit delegation-policy revision. Its target/proxy selection MUST remain immutable for its lifetime.
- **FR-005**: Generic admission MUST consume an actual ControlGate-issued receipt and current member/owner revalidation. Generic forwarding MUST use current trusted receiver Tailscale node/netmap and DNS-address binding plus known receiver connectivity. Unknown, stale, contradictory or denied evidence MUST refuse before unverified forwarding. Neither a client actor field, hostname suffix, address prefix nor tailnet-name configuration alone is proof. ACL enforcement applies to the receiver's own network identity; no actor-target ACL is inferred from reachability.
- **FR-006**: Node/address replacement, DNS rebinding, changed owner/delegation policy and redirects MUST NOT retarget an existing grant. Further access without valid original authority MUST fail closed; intended target changes require a new authorized grant.
- **FR-007**: Generic targets MUST be verified tailnet-node HTTP(S) services. Public, LAN, loopback, metadata, invalid-scheme, embedded-userinfo and ambiguous destination/scope selections MUST refuse. HTTPS MUST retain destination identity verification. No global Tailscale ACL edit is required or authorized.
- **FR-008**: Every generic URL grant MUST have finite expiry and a finite admission allowance. Existing target kinds MUST retain their current use policies. A use MUST mean a successful gate admission, not each asset or application request; exhaustion MUST reject subsequent admissions.
- **FR-009**: Subsequent requests MUST be authorized by the gate session and original target/scope. Host/domain/path cookies MUST NOT confer sibling-host, another target or panel authority.
- **FR-010**: Supported navigation, assets, forms, redirects and WebSockets MUST work through the actual receiver within scope. Unsupported flows MUST produce a bounded refusal/explanation and MUST NOT widen scope or bypass application authentication.
- **FR-011**: A generic URL MUST NOT infer application credentials, WordPress admin login or a legacy autologin fallback. Existing declared sign-ins retain only their explicit target kind and authority.
- **FR-012**: Inbox access MUST remain canonical gated access. Arbitrary URL selection MUST NOT expose a raw mailbox listener or another stack's messages without that authorized inbox scope.
- **FR-013**: Expiry/revocation MUST deny new admissions and subsequent forwarding, including already authenticated gate sessions. Open gated streams and WebSockets MUST cease before cleanup/revocation is reported complete. Existing application sessions retain the application's own lifetime outside the gate; global application logout MUST NOT be promised.
- **FR-014**: Create/list/revoke/recovery MUST retain durable original IDs and bounded status across acknowledgement loss, controller restart and parent metadata loss. Unknown acceptance MUST NOT authorize blind remint, replacement-runtime cleanup or a success response.
- **FR-015**: Selectors and revoke scope MUST bind to the original authorized grant; same-receiver grants of other actors/parents MUST remain unaffected.
- **FR-016**: Verification and diagnostics MUST NOT fetch or redeem user-supplied secret links. Tokens, credentials, sensitive URL content and private proxy configuration MUST NOT enter public inventories, ordinary logs, audits or error output.
- **FR-017**: Capability/readiness admission MUST precede issuance/effects. Missing genuine owner admission, target/receiver proof, unsupported browser/application behavior, absent application-auth readiness or an old receiver MUST remain explicit unavailability, without an alternate auth server or fallback token path.
- **FR-018**: Partial inventory or cleanup MUST retain safe original identities and report non-success. Operators MUST be able to distinguish planned support, source evidence, receiver-installed capability and actual acceptance.
- **FR-019**: Mandatory human authentication/release review MUST explicitly assess that the bearer delegates receiver-owner network reachability and can permit access beyond the initiating person's direct target ACL. No implementation or release claim MUST rely solely on documentation or prior fixture counts. Root-owned regressions, independent authentication/source review and required owner approval MUST precede release; this specification authorizes no live operation.

### Key Entities

- **Share target**: A stack, recorded entry/path or verified tailnet service/path selection; target kinds share one lifecycle but retain their declared rights.
- **Authorizing actor**: An operator currently admitted under the receiver-owner policy by a genuine ControlGate receipt; that owner policy intentionally permits delegation of receiver reachability.
- **Receiver authority evidence**: Current authenticated Tailscale node/address/netmap observations and known receiver service connectivity, bound separately from genuine owner admission and delegation-policy revision; unavailable evidence permits no forwarding.
- **Share grant**: Original public ID, immutable authorized target, scope, lifetime, applicable admission policy and management owner, with secret material kept private.
- **Gate session**: Recipient admission bound to a live grant/target/scope; distinct from the application's login session.
- **Recovery record**: Original effect identities and acknowledgement state needed to reconcile uncertain creation or cleanup without retargeting or reminting.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A root-run acceptance matrix demonstrates all three target kinds using one create/list/revoke workflow, with zero independent issuers or generic-login fallbacks.
- **SC-002**: Every denied-admission, missing/stale-proof, rebinding and replacement case refuses unverified forwarding; zero out-of-scope destinations or management authority are reached. A positive case demonstrates explicit owner delegation where the receiver can reach a target but the initiating node cannot.
- **SC-003**: Each documented supported browser flow completes its declared navigation/resource/authentication scenario, while every unsupported escape attempt refuses without broader authority.
- **SC-004**: For every finite allowance N under concurrent admission, at most N admissions succeed; after expiry or completed revoke, zero further gated requests or surviving gated streams are accepted.
- **SC-005**: Every acknowledgement-loss/restart recovery scenario retains the original IDs, reports unresolved facts honestly and produces zero duplicate grants or unrelated revocations.
- **SC-006**: The verification matrix observes zero secret-link probe requests or unintended bearer/credential/sensitive-URL disclosure, including in public lists, audit records and failure output.
- **SC-007**: Existing stack and individual-service regression scenarios retain their target rights, admission policies and companion isolation; absent readiness is displayed as unavailable, never accepted.

## Assumptions

- Recipients are trusted teammates, as confirmed for 088; that does not authorize widening actor/service scope or bypassing application login.
- The revised independently reviewed PRD supplies the explicit receiver-owner delegation policy and product decisions. Precise evidence freshness, resource limits, supported browser profiles and capability contracts belong to security design; no support is enabled until those rules are explicit and testable.
- The actual receiving server must supply genuine ControlGate admission, trusted node/address/netmap identity and known receiver connectivity. No direct per-operator destination ACL proof is required. Receivers unable to establish the required facts remain unavailable for generic targets while preserving existing kinds.
- Root owns the wrapper, integration and all test execution. No helpers, test runs, release, deployment, credentials access or remote host jobs are authorized by this document.
- 088 review gaps and parked/missing login readiness remain open until separately demonstrated and approved. Earlier checkpoint and supplied shadow counts do not establish full authentication acceptance.
