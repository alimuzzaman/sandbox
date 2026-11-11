# Feature Specification: Shared server secret use

**Feature Branch**: `feat/shared-server-secret-use`

**Created**: 2026-11-11

**Status**: Specified and clarified for planning. Credential-consuming remote execution remains DISABLED pending accepted installed integration and evidence.

**Input**: Authoritative [ready PRD](prd.md), independent personal Sol high PASS, and user-confirmed coordination decisions dated 2026-11-11. Specification authored by personal Codex gpt-6.1-sol high, without helpers or model/provider substitution.

## User Scenarios & Testing *(mandatory)*

The scenarios below specify future observable behavior. No execution or runtime verification was performed in this specification phase. Source groundwork and end-to-end acceptance are separate outcomes.

### User Story 1 - Receive an honest capability result (Priority: P1)

An admitted shared-server operator uses a laptop without provider keys to declare an operation with explicit server credential scope. The operator can validate nonsecret intent and learn whether the selected installation supports execution without copying keys onto the laptop.

**Why this priority**: Useful disabled groundwork must prevent the existing local-key gate from masquerading as a server failure or an unfinished dependency from masquerading as working execution.

**Independent Test**: With an empty synthetic laptop credential environment and unavailable installed dispatch, validate declarations and request server scope. Observe unverified key availability and a stable unsupported-capability result with zero staging, value resolution, claims or effects.

**Acceptance Scenarios**:

1. **Given** valid server-scoped declarations and no laptop provider keys, **When** static validation runs, **Then** it validates nonsecret intent without reading laptop provider values and labels server-key availability unverified.
2. **Given** absent or unaccepted installed authenticated dispatch/admission, **When** execution is requested, **Then** it refuses as unsupported capability before staging, value resolution, resource claims, generation or provider/runtime effects.
3. **Given** a context frame, copied source or declarative capability manifest only, **When** the operator requests execution, **Then** the same disabled result remains; these observations grant no execution or credential authority.
4. **Given** explicit server scope, **When** the selected server or inventory capability is unavailable, **Then** the result is unavailable for that server, with no local or alternate-host fallback.

### User Story 2 - Use intended server credentials without distribution (Priority: P1)

After installed integration is accepted, any operator admitted by the existing control service requests the authorized operation using registered server source/profile references. Intended trusted server consumers receive provider values for that operation. The operator receives bounded nonsecret status and identifiers.

**Why this priority**: This is the intended product outcome. It remains blocked while the required installed execution dependency is unavailable.

**Independent Test**: On an accepted integrated installation, exercise one authorized operation from a clean laptop and inspect permitted evidence for server-only consumption, authenticated actor attribution and absence of disclosure. Source checks alone cannot satisfy this story.

**Acceptance Scenarios**:

1. **Given** accepted installed integration and all required provider keys, **When** an admitted operator requests the operation, **Then** it can complete using server-held credentials without dummy laptop keys, inherited owner environment or distributed values.
2. **Given** two independently admitted operators, **When** each requests a supported operation, **Then** both retain full current owner rights, including currently owner-approved sudo consumers, and each operation retains its own authenticated initiating actor.
3. **Given** an unadmitted caller or a client-supplied substitute actor, **When** credential use is requested, **Then** ControlGate denies the caller or rejects substitution before value consumption or effects.
4. **Given** success, provider rejection, consumer failure or timeout, **When** output or retained status is inspected, **Then** no provider value appears outside the broker-to-intended-consumer boundary.
5. **Given** a consumer requiring unsupported persistent provider-secret delivery, **When** execution reaches consumer validation, **Then** it refuses without creating a secret-bearing file or switching to a different consumer.

### User Story 3 - Refuse missing keys and recover safely (Priority: P1)

An admitted operator receives a terminal no-effect refusal for missing required server keys and can inspect it after a disconnect. Repairing the source does not silently reopen the refused request.

**Why this priority**: Uncertain submission and key repair must not duplicate effects or turn an old refusal into new execution authority.

**Independent Test**: Exercise missing-key preflight, replay, source repair and explicit linked retry with synthetic references. Observe a frozen original result and no effects before successful preflight plus committed admission.

**Acceptance Scenarios**:

1. **Given** a reserved request with one missing provider key, **When** server preflight runs, **Then** it persists a bounded terminal no-effect refusal naming only missing keys/status before source staging, lifetime claims, generation, provider calls or runtime effects.
2. **Given** that known terminal refusal and a repaired source, **When** the same identity is replayed, **Then** the original refusal remains frozen. An explicit new identity linked to that refusal may request freshly reviewed intent.
3. **Given** uncertain reservation, refusal persistence or admission, **When** the caller resumes, **Then** it looks up the original identity before replay and cannot automatically obtain a fresh identity or redispatch.
4. **Given** identical accepted intent, **When** replayed, **Then** it returns the retained operation identity/outcome without repeating effects. Changed intent under the same scoped identity refuses.
5. **Given** an expired or cancelled request, **When** inspected or replayed, **Then** retained nonsecret outcomes remain inspectable but no new consumption or effect starts.
6. **Given** present keys rejected by the provider, **When** the provider call fails, **Then** the result is a bounded provider failure, not missing inventory, local fallback or authority to generate a provider key.

### User Story 4 - Keep identity and intent bound throughout use (Priority: P1)

The server owner can attribute use to the real operator and know that each consumer used only the reviewed source mapping on the intended installation and target.

**Why this priority**: A successful early presence check must not authorize a different actor, source revision, consumer or target later.

**Independent Test**: Change each bound identity or mapping between reservation, preflight, admission, staging and consumption. Each mismatch must prevent subsequent consumption/effects; concurrent requests must retain separate grants and outcomes.

**Acceptance Scenarios**:

1. **Given** reviewed intent, **When** any installation, actor, runtime, target/copy/incarnation, committed source/artifact digest, source/profile identity or revision, source-key mapping, destination or consumer differs, **Then** intent reuse refuses or subsequent use stops before the next consumption/effect.
2. **Given** successful presence preflight without genuine retained committed admission, **When** execution is attempted, **Then** reservation and preflight alone grant no value use or lifecycle authority.
3. **Given** committed admission, **When** consumption or any later effect is attempted, **Then** the operation revalidates its grant, source/profile revisions, intended consumer, finite deadline, cancellation and current target fence as applicable before proceeding.
4. **Given** simultaneous requests from different actors, **When** one grant is substituted into the other request, **Then** substitution refuses even though both actors have equal operator rights.

### User Story 5 - Inspect status and preserve local compatibility (Priority: P2)

An admitted operator can inspect supported server inventory without provisioning anything. Personal local users keep their current workflow; generated stack credentials retain separate authority and delivery rules.

**Why this priority**: The new scope must remain explicit and avoid changing unrelated personal use or confusing generated runtime secrets with provider keys.

**Independent Test**: Compare existing personal-local behavior, supported/unsupported server inventory and missing provider/generated cases under explicit generation requests.

**Acceptance Scenarios**:

1. **Given** supported inventory and an admitted caller, **When** names/status inventory is requested, **Then** only bounded key names/status are returned with no value-derived metadata, provisioning or generation. Unsupported inventory returns unavailable.
2. **Given** personal-local scope, **When** an existing local operation runs, **Then** current local behavior remains available without requiring shared-server configuration.
3. **Given** missing provider keys and `--generate`, **When** server preflight runs, **Then** missing provider keys still cause refusal and are never invented or replaced by generated stack credentials.
4. **Given** successful provider preflight and genuine committed admission, **When** separately authorized stack generation runs, **Then** generated credentials receive confidential delivery only to intended stack consumers under that admitted operation. Missing generated credentials have a distinct status and never imply permission for unrequested writes.

### Edge Cases

- A source or profile is unavailable, unregistered, changed or revoked after metadata inspection. Refuse the bound request rather than selecting the latest or a different source.
- A key's presence does not prove provider permissions or validity. Later provider failure remains distinct and secret-free.
- An identity is reserved but admission never commits. No staging or effects become authorized; uncertain state requires original-identity inspection.
- A response is lost while a terminal preflight refusal is being persisted. Do not infer a known refusal or start a new identity until the original ledger outcome is known.
- A source is repaired after refusal. Same replay stays frozen; only explicit linked new-id retry after known terminal refusal is permitted.
- Expiry, cancellation, source/profile change or loss of a target fence occurs after preflight or between effects. Stop before the next consumption/effect; retained evidence grants no new authority.
- A private installation is relocated or replaced. Machine/uid/home context alone is not complete stable installation identity; old grants must not silently attach to the replacement.
- Failure output, polling and timeout paths remain subject to custody restrictions, including when a trusted consumer misbehaves.
- Equal operator privileges do not make actor grants interchangeable. Privileged-owner malicious access is outside the claimed custody isolation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Requests MUST distinguish personal-local from explicitly selected server-held credential scope. Server scope MUST identify registered project context, source and approved consumer/profile references without provider values.
- **FR-002**: Local static validation MUST inspect declarations and nonsecret intent only. It MUST neither require local provider keys for server scope nor present unperformed remote key preflight as verified.
- **FR-003**: Credential-consuming remote execution MUST remain disabled unless accepted installed authenticated owner dispatch/admission and all integration evidence in FR-023 exist. Unsupported capability MUST refuse before staging, value resolution, claims, generation or effects. Context proof, source copies and manifests MUST NOT enable it.
- **FR-004**: Existing ControlGate admission and authenticated actor identity MUST be preserved through broker grants and operation outcomes alongside server execution identity. Client actor labels MUST NOT grant authority or replace provenance.
- **FR-005**: All currently admitted tailnet/shared operators MUST retain the same full current owner rights, including currently owner-approved sudo rights. Consumer/profile constraints MUST limit custody and purpose, not create restricted membership roles, per-laptop grants or new operator-policy restrictions.
- **FR-006**: Immutable reviewed intent MUST bind canonical installation identity, authenticated actor, runtime revision, target/copy/incarnation, exact committed source identity and artifact digest, source/profile identities and revisions, and every exact source-key mapping, destination and intended consumer. Destination names alone MUST NOT suffice. Values MUST NOT enter public intent or replay identities.
- **FR-007**: The server MUST resolve nonsecret references before scoped request lookup/reservation, without resolving values or performing provider/lifecycle effects. Lookup/reservation MUST bind the original identity atomically to immutable intent. A reservation MUST NOT confer admission or value/effect authority.
- **FR-008**: Server preflight MUST check all mapped required provider keys and declared consumer bindings under the bound installation, actor, intent and revisions before effectful staging, resource/lifetime claims, generated secrets, provider calls or runtime effects. Reference-only staging MUST also wait for capability and preflight requirements.
- **FR-009**: Missing/unavailable required provider keys MUST persist a bounded terminal preflight refusal against the reservation with names/status-only diagnostics and no-effect evidence. Diagnostics MUST NOT expose characters, lengths, fingerprints or other value-derived metadata.
- **FR-010**: A terminal preflight refusal MUST remain frozen on replay, including after source repair. Only an explicit retry with a new identity linked to a KNOWN terminal preflight refusal may resolve fresh intent. Uncertain persistence MUST require original-identity lookup rather than a fresh identity.
- **FR-011**: Only successful required-key preflight followed by genuine retained committed server admission may authorize execution. The admitted operation MUST be typed and server-created from that receipt and canonical installation binding; context frames and client-supplied fields MUST NOT construct admission authority.
- **FR-012**: Before value consumption the server MUST revalidate the operation grant, source/profile identities and revisions, intended mappings/consumer, deadline and cancellation. Each subsequent effect MUST check current deadline, cancellation and required host/target fence. A mismatch MUST prevent subsequent consumption/effects without silently rebinding intent.
- **FR-013**: Provider values MUST remain broker-owned and reach only the intended trusted server consumer for the approved purpose and lifetime. No raw provider value may reach client responses, transport envelopes, argv, logs, polling, retained jobs, artifacts, caches, staged files or retained storage. Consumer-internal use within the bound lifetime is not permission to retain a copy.
- **FR-014**: Server provider consumption MUST NOT depend on inherited owner environment, dummy local presence, arbitrary remote secret reads/commands, local fallback or alternate-host resolution. Unsupported persistent provider-secret delivery MUST refuse rather than create an environment file.
- **FR-015**: Each operation MUST have an original finite request/job identity, finite deadline, retained nonsecret lookup outcome and cancellation boundary. Uncertain submission/admission MUST remain uncertain until original-identity lookup establishes an outcome; neither lost responses nor observation receipts authorize automatic redispatch.
- **FR-016**: Same scoped identity and identical accepted intent MUST return the retained operation identity/outcome without duplicate effects. Changed intent MUST refuse. Expired/cancelled replay MUST be inspection-only and MUST NOT start new consumption/effects.
- **FR-017**: Concurrent requests MUST retain separate actor/intent/grant/outcome bindings. A grant MUST NOT be reusable across actors, installations, targets or requests.
- **FR-018**: Supported server inventory MUST return names/status only under existing admission, without value-derived metadata, provisioning, generation or new effect authority. Unsupported inventory MUST report unavailable without laptop fallback.
- **FR-019**: Key presence MUST remain distinct from provider validity. Provider rejection MUST produce bounded failure without alternate credentials, provider-key generation or claims that inventory established provider access.
- **FR-020**: Required provider credentials and generated stack credentials MUST remain separate. Explicit generation MUST NOT satisfy missing provider mappings, overwrite provider sources or convert preflight refusal into admission. Generation and confidential delivery MUST occur only after successful provider preflight and committed admission with separate generation authority bound to that operation. Existing generated-runtime delivery rules MUST remain compatible.
- **FR-021**: Personal-local behavior MUST remain available and unchanged. Explicit server scope MUST NOT fall back into that behavior on any success, failure, cancellation or timeout path.
- **FR-022**: The existing private installation layout MUST remain supported. Stable canonical installation binding MUST allow future immutable managed identity without requiring or authorizing migration, relocation, permission changes, `--here`, self-SSH or self-remote execution.
- **FR-023**: Enablement MUST require independent evidence of installed revision/capability agreement, authenticated owner dispatch and committed admission, preserved operator provenance, broker grant binding, required-key ordering, compatible source writer authority, canonical target leases/fences, complete lifecycle/gate/driver wiring, matching docs/revision evidence, focused checks and separately authorized runtime acceptance. Unaccepted evidence MUST leave execution disabled.
- **FR-024**: Source groundwork reports and documentation MUST separate static validation, supported inventory and disabled capability checks from blocked end-to-end acceptance. They MUST NOT claim the remote problem fixed from source-only results.

### Key Entities *(include if feature involves data)*

- **Admitted operator**: Actor authenticated by existing ControlGate, with full current operator rights. Distinct from the server owner execution identity.
- **Canonical installation**: Stable selected installation identity plus checked runtime revision and owner context. Machine/uid/home observation alone is insufficient for a credential grant.
- **Credential reference**: Registered project, source/profile identities and revisions, required provider names, exact mappings, destinations and intended consumers. Contains no values.
- **Reviewed intent**: Immutable operation binding joining installation, actor, runtime, target/copy/incarnation, committed source/artifact digest and credential references under the original finite request identity.
- **Request reservation**: Atomic scoped identity-to-intent record used for lookup and preflight. May become a terminal preflight refusal or proceed to committed admission; never itself grants authority.
- **Terminal preflight refusal**: Retained bounded names/status result with no-effect evidence. Frozen on replay. An explicit fresh retry is linked only after this state is known.
- **Committed admission and admitted operation**: Genuine retained server receipt plus canonical installation binding, from which the server creates the typed operation. Carries finite authority subject to cancellation and live fences.
- **Broker grant**: Authenticated operator-to-operation credential-use authority limited to bound references, intended consumer, purpose and lifetime. Separate from membership policy and lifecycle writer authority.
- **Target/source authority**: Sibling-owned lifecycle target fence/lease and source writer authority consumed by execution. Neither replaces authenticated broker admission.
- **Provider credential / generated credential**: Existing provider access value versus separately authorized generated runtime value. Different preflight, generation and delivery obligations.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every valid server-scoped static-validation scenario succeeds without laptop provider keys and reports availability as unverified if server preflight did not run. Every unsupported dispatch scenario refuses before staging, value resolution, claims or effects.
- **SC-002**: Every supported names/status inventory scenario exposes zero values or value-derived metadata and performs zero provisioning/generation effects. Unsupported cases return unavailable without fallback.
- **SC-003**: Across integrated clean-laptop success scenarios, authorized operators complete the intended operation with zero laptop credential distribution and zero dummy local keys. Each result retains the actual authenticated actor; all admitted operator classes retain equal current rights.
- **SC-004**: Every missing-provider scenario, including explicit generation, yields a retained terminal no-effect refusal before staging, claims, generation or deployment. Every source-repair replay preserves the original refusal; each permitted fresh retry is explicitly linked to a known refusal.
- **SC-005**: Every changed-identity, mapping, destination, revision or actor scenario refuses subsequent unauthorized consumption/effects. Expiry, cancellation and lost-fence cases start zero subsequent unauthorized effects.
- **SC-006**: Across disconnect and replay scenarios, identical accepted requests produce no duplicate effects, changed intent refuses, and uncertainty is resolved under the original identity. Expired/cancelled requests expose retained evidence only.
- **SC-007**: Across success, provider failure, consumer failure and timeout scenarios, prohibited outputs/storage contain zero provider values. Unsupported persistent delivery refuses without producing a secret-bearing file.
- **SC-008**: Existing personal-local and explicitly authorized generated-credential behavior passes its compatibility scenarios without server-scope fallback or provider/generated substitution.
- **SC-009**: Every enablement prerequisite in FR-023 has independently accepted evidence before credential-consuming remote execution is enabled. Source groundwork may meet SC-001 and SC-002 where supported; SC-003 through SC-008 integrated coverage remains blocked until integration, and none is reported passed without a run.

## Assumptions

- The ready PRD and user-confirmed coordination decisions are authoritative. Supplied server source names/revisions are context, not refreshed inventory, provider-validity proof or perpetual grants. No alias is inferred from similar names.
- All admitted operators remain trusted with full owner rights. Custody prevents unintended distribution and recipient drift; it does not promise isolation from a malicious privileged server owner.
- The sibling owner-dispatch proposal is PROPOSED/UNAVAILABLE, read-only dependency context. Secret-use owns authenticated operator/broker grant binding; the sibling owns lifecycle, target and source-writer authority. Neither context proof nor reservation establishes installed authenticated dispatch or complete installation identity.
- Exact wire fields, command spelling, storage mechanics, timeout sizes, scale/performance budgets and protocol composition are planning concerns. No new latency, throughput, retention-duration or regulatory promise is inferred. Required retained evidence must remain available through supported replay/inspection lifetimes; pruning must never reopen an identity or grant fresh authority.
- Bounded status/diagnostics use the existing command-facing conventions. No new graphical UI, localization system or accessibility interaction is introduced.
- Current private layout remains. No production operation, migration, installation change, runtime authority or credential access is authorized by this spec.
- Source mechanisms must respect module contracts, existing choke points and compatibility controls. Constitution principle IV requires later observed live evidence; this specification is not runtime acceptance or permission to disable an already working personal-local path.
- Root reservation on 2026-11-11 resolves numbering: this feature uses 086 and sibling hosted-site-baselines retains 085. The owned feature directory and active metadata point to 086; sibling files and historical review snapshots remain untouched.
