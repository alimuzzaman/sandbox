# Product Requirements Draft: Unified tailnet sharing

**Status**: Ready for specification

**Created**: 2026-11-11

**Last Refined**: 2026-11-11

**Input**: "Create a script for generating share links. There should be only one share system: sb host share must work for whole stacks, individual links, and anything under the tailnet."

**Drafting Model**: Personal Codex GPT-6.1-Sol, high; root-authored, no execution helpers.

**Final Validation**: `PASS`, independent personal Codex GPT-6.1-Sol high readiness review.

**Validated On**: 2026-11-11

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`, only after independent readiness PASS.

> Product intent only. No implementation, wire contract, runtime activation or claim that arbitrary tailnet URLs work today.

## Problem and Motivation

Operators need to give a teammate access to a complete development stack or one particular application, route, inbox, or tool reachable through their tailnet. Choosing different link mechanisms for each destination creates inconsistent access, expiry and revocation, and encourages ad hoc proxies or reusable login secrets. The existing canonical share gate must cover these product choices through one operator workflow.

The user explicitly requested one share system and a convenience script. Root owns a thin script using the current SDK for supported stack and individual-entry operations. This feature owns the formal requirements for extending that same engine to verified tailnet HTTP(S) destinations. A shell wrapper cannot enable generic URL sharing before its target contract is implemented and reviewed.

## Users and Desired Outcomes

- **Authorized operator**: Select a stack, recorded target and declared entry/path, or verified tailnet web URL; obtain one manageable share with an explicit scope and lifetime.
- **Teammate outside the tailnet**: Open the share through the existing public gate and use the intended application without joining the tailnet. Existing application authentication still applies.
- **Owner/administrator**: Understand who authorized each share, what destination it can reach, its remaining lifetime/use allowance, and whether revocation completed.
- **Integration owner**: Test exact committed source and installed behavior separately, preserving incomplete results and recovery identities.

## Goals

- One canonical `sb host share` engine and common create/list/revoke lifecycle for all supported target kinds.
- A target choice of stack, recorded target with declared service/entry/path, or verified tailnet HTTP(S) URL.
- Explicit delegation of the operator's allowed destination scope, with no increase in their existing rights.
- Useful web application behavior through the actual receiver, including scoped navigation, assets and supported WebSockets.
- Finite expiry, explicit use control, revocable gate sessions, safe uncertainty reporting and recovery.

## Non-Goals

- A second authentication server, ad hoc token issuer, legacy autologin fallback, or separate URL-only share product.
- Unrestricted Internet proxying, arbitrary TCP/UDP access, tailnet administration, or exposing the full panel's mutating API.
- Bypassing an application's authentication, changing credentials, or making an otherwise unauthorized application accessible to the operator.
- Publishing raw Mailpit/mailbox listeners without the canonical gate and target-scoped authority.
- Promising support for every application protocol or silently widening a path to make an incompatible application work.
- Runtime installation, deployment, credentials access or live probes under this planning authorization.

## Product Scenarios

### Scenario 1: Whole stack with direct shared services

- **Starting state**: An operator has an existing stack with its declared routes, inbox and direct shared-stack relationships.
- **User action**: Create a stack share through `sb host share` or root's thin wrapper.
- **Expected outcome**: One link opens the scoped stack page and its authorized pieces. Existing companion isolation, shared-service access, sign-in boundaries and retained recovery identities continue to apply.

### Scenario 2: One recorded entry or path

- **Starting state**: A recorded target declares a supported service/entry and the operator selects a bounded path.
- **User action**: Share that selection through the same command family.
- **Expected outcome**: The recipient can use that selection, while other paths, entries, stacks and panel APIs remain outside the grant. Existing operator opt-in and rights still govern issuance.

### Scenario 3: A verified tailnet web destination

- **Starting state**: A destination is HTTP(S), its actual server-side Tailscale node identity and DNS-to-IP binding can be established, and the operator has authority for that node/service/scope.
- **User action**: Request a share for the URL with an explicit scope, expiry and use allowance.
- **Expected outcome**: The canonical gate admits a target-bound share. The recipient can use the approved scope without receiving the operator's tailnet credentials or authority over other node ports or services.

### Scenario 4: Changed destination or uncertain authority

- **Starting state**: A public host resembles a tailnet name, DNS answers change, an address is reused by another node, ACL evidence is missing/denied, or the receiver cannot establish the saved actor/target binding.
- **User action**: Create or use the affected share.
- **Expected outcome**: Admission or access refuses before contacting an unverified destination. No hostname suffix, address prefix or server-wide reachability alone establishes operator authority. Failure never silently retargets an existing grant.

### Scenario 5: Navigation, assets and application sign-in

- **Starting state**: The application uses relative/absolute navigation, assets, redirects, cookies or WebSockets.
- **User action**: Browse through a share, including the application's normal sign-in when required.
- **Expected outcome**: Supported requests remain within the approved destination and path authority. External redirects, sibling ports/hosts, encoded traversal and unsupported flows cannot become escape routes. A flow that needs broader scope is refused or explained; authority never expands automatically.

### Scenario 6: Revocation, exhaustion and uncertainty

- **Starting state**: Multiple browser sessions use a live share, or creation/revocation loses its acknowledgement.
- **User action**: Revoke, exhaust its finite uses, wait for expiry, or retry recovery.
- **Expected outcome**: New admission and subsequent gate access obey the grant's lifetime and revocation. Existing streams/WebSockets cannot retain gate access beyond that boundary. Unknown effects remain visible and reconcile original identities; retries do not create extra grants or revoke another actor/parent's grants. Existing application sessions have their own lifetime, not a promise of global application logout.

## Proposed Product Behavior

1. The canonical target choice is stack, recorded target plus entry/path, or verified tailnet URL. The same management and authenticated transport serve each kind. A URL share uses the existing authentication gate and session lifecycle, not a parallel listener or login system.
2. Generic URL admission requires evidence from the actual receiving server's Tailscale node inventory, an unambiguous address-to-node binding and applicable ACL/actor authority for the selected scheme, service/port and path. A caller's assertion or merely successful server reachability is insufficient. Missing, stale or contradictory evidence refuses safely.
3. Approved proxy destination configuration and actor/target identity are immutable for a grant. DNS changes, redirects, node replacement or changed authorization must not redirect its authority. Loss of required binding/authorization prevents further access; an intended destination change requires a newly authorized grant.
4. HTTP and HTTPS are the only generic URL protocols. Unsupported schemes, embedded user credentials, invalid/ambiguous authorities and unverified destinations refuse. HTTPS preserves destination identity verification; the gate must not disable TLS checks to make a target work.
5. Every new generic-URL grant has finite expiry and a finite use allowance. Existing stack/recorded-target use policies retain their current semantics. Shared reporting makes lifetime, allowance, scope and recovery status clear without claiming that established application sessions are revoked globally.
6. A path-scoped grant authorizes only its declared web behavior. Normalization, asset retrieval, redirects, cookies, WebSocket handshakes and subsequent forwarding must preserve that boundary at the actual receiver. An incompatible application receives a bounded unsupported result rather than a broader grant.
7. Application authentication remains the application's responsibility. Existing declared stack sign-ins can remain explicit capabilities of their existing target kind; a generic URL does not inherit WordPress admin login or application credential authority.
8. An inbox is shared only through canonical gated access. Shared Mailpit contents outside the authorized target must not become publicly reachable through an arbitrary URL or fallback route.
9. No admission/diagnostic probe fetches or redeems a user-supplied secret link. Target verification uses authority/identity evidence independently of sensitive URL content. Tokens, credentials, sensitive queries/fragments and private target configuration do not enter ordinary logs, audits or public list/error output.
10. Creation, listing, revocation, use limits and unknown-effect recovery remain one system. Public IDs and bounded safe status remain manageable after local process or parent metadata loss. A partial result is not success; no blind remint follows unknown acceptance.

## Constraints and Dependencies

- This scope is explicitly requested by the user and assigned to feature 090 by the coordinator. Preserve unrelated features 085, 086 and 089.
- Spec 088 checkpoint `be1e965b811464fbc0a60f00e402c659619be14b` is immutable. Root reports selected shadow shards passing and a controller/panel failure still under triage; those supplied results are not final authentication acceptance.
- Current source `ShareKind` is site/stack. No generic tailnet URL target or public URL option is established by this PRD. The wrapper must not advertise such an option prematurely.
- Source SDK release, host jobs, runtime activation, credentials, deployments and unpark operations remain unauthorized. Root owns integration and all test execution for this work.
- Independent readiness review precedes formal specification. Authentication/authorization review and owner sign-off precede any eventual release; source and fixture results do not establish installed capability.
- Existing canonical short links, per-host gate sessions, operator opt-in, companion recovery fences, target leases and token-free audits must retain their guarantees. No parity-sensitive fallback is removed as a side effect of planning.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Canonical system | Extend `sb host share` and its existing gate/management transport | One creation and recovery model for every target kind | Explicit user/coordinator requirement |
| Target kinds | Stack; recorded target plus entry/path; verified tailnet HTTP(S) URL | Covers whole-stack and individual-link intent | Explicit user/coordinator requirement |
| Network authority | Actual receiver node/DNS-IP binding plus ACL and actor authority | A reachable server must not act as a confused deputy | Explicit coordinator requirement |
| Scope and compatibility | Bounded scope at receiver; refuse unsupported flows without widening | Preserve operator rights and existing companion fences | Explicit coordinator requirement |
| URL grant lifetime | Finite expiry and finite use allowance; existing kinds retain current policies | Bound new delegation while preserving current operator behavior | Coordinator finite-use requirement and preservation constraint |
| Application login | Retain application authentication; no generic URL autologin fallback | Sharing transport is not authority to bypass the application | Explicit coordinator requirement |
| Wrapper owner | Root, using the current SDK only | Convenience must not create another share engine | Explicit coordinator ownership |
| Verification | Root executes tests; planning does not activate source or runtime support | Separate formal scope, source correctness and installed acceptance | Explicit latest instruction |

## Open Questions

No blocking product choice is introduced beyond the confirmed scope above. Precise admissible evidence freshness, lifetime/use bounds, receiver compatibility rules and capability negotiation require formal specification and security design. They must not be invented by the wrapper or treated as runtime approval. Independent readiness review may reopen a product decision before specification.

## Acceptance Outcomes

- **AO1, unified management**: A supported example of each target kind can be created, listed and revoked through the same operator workflow; the wrapper delegates to that engine, with no separate issuer or authentication endpoint.
- **AO2, verified delegation**: Creation and later access refuse public lookalikes, node/address replacement, changed DNS binding, missing/stale proof and actor/ACL-denied destinations. Authorized URL examples work through the receiving server without wider operator rights.
- **AO3, scope isolation**: A recipient can browse a declared path and its supported assets while attempts through traversal, alternate host/port, redirects or cookies cannot reach another path/service or the panel. Unsupported browser flows refuse without widened scope.
- **AO4, application boundaries**: An application requiring its own login still requires it through the URL share. No generic URL triggers WordPress administrator login or exposes a raw mailbox; existing explicitly declared stack access remains intact.
- **AO5, lifetime and revoke**: Concurrent admission attempts enforce each grant's applicable use policy; every generic-URL grant has a finite allowance. Expiry/revoke deny new sessions and further gated HTTP/WebSocket access, including already-open gated streams, within a specified acceptance bound. Recovery after a lost reply uses original IDs and reports incomplete cleanup honestly.
- **AO6, secrecy**: Verification does not request a supplied secret-link path; observed request/proxy/audit/browser/list/error evidence contains no unintended raw bearer, credential or sensitive URL disclosure. Parent cookies alone do not authorize another host/target.
- **AO7, compatibility and provenance**: Existing stack/service shares retain their rights and recovery behavior. Any runtime support claim names the installed revision, verified capabilities and actual receiver/browser evidence, separately from source checks.

## Risks and Assumptions

- **Risk**: Receiver ACL authority can exceed the initiating operator's rights. Reachability alone must never authorize delegation.
- **Risk**: A verified node can serve many ports, applications or users. Binding the node alone is insufficient without service and path authority.
- **Risk**: DNS churn, node reuse and redirects can change the real destination after issuance. Access must fail safely rather than follow unverified changes.
- **Risk**: Absolute URLs, cross-origin sign-in, cookies, WebSockets and streaming can be incompatible with strict scope; compatibility must be demonstrated, not promised universally.
- **Risk**: A secret already embedded in a URL can leak through an upstream access log or browser navigation. Formal design must identify safe supported input/forwarding behavior before claiming AO6.
- **Assumption**: Supported receiving servers can obtain sufficient authenticated Tailscale and operator authority evidence. If not, that receiver cannot support generic URL grants; the existing target kinds remain available.
- **Assumption**: Non-tailnet recipients are trusted teammates, as decided for 088. That does not authorize another actor's targets, change application login rights or remove the canonical gate's lifetime boundary.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices trace to user/coordinator requirements.
- [x] Acceptance outcomes identify observable positive and negative results.
- [x] No blocking open product question is currently identified.
- [x] No implementation plan, task list, wire contracts, or code changes are included.
- [x] The latest independent readiness review verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`. Document readiness does not establish implementation, installed support or authentication acceptance. The next explicit workflow is `speckit-specify`; no downstream artifacts or runtime enablement are created by this refinement.
