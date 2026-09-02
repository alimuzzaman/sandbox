# Feature Specification: Instance-Scoped Server Configuration Fragments

**Feature Branch**: `codex/server-config-fragments`

**Created**: 2026-08-31

**Status**: Draft

**Input**: Reviewed product requirements in `prd.md` for a bounded capability to apply, inspect, replace, and revert plugin-emitted server configuration on one Sandbox instance.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Prove and Revert a Server-Owned nginx Cache Hit (Priority: P1)

A plugin developer applies the cache-routing fragment emitted by a WordPress
plugin to one ready nginx instance. The developer can inspect its active state,
prove that nginx serves a warmed cached page without invoking PHP, and revert the
fragment so the same route returns to PHP.

**Why this priority**: This is the smallest complete journey that closes the
current verification gap while proving safe activation, inspection, live server
behavior, and reversibility.

**Independent Test**: On one disposable nginx instance, apply a named
`wordpress-cache-v1` fragment for a warmed static page, observe the server-owned
hit marker while an independent PHP execution sentinel remains unchanged, list
and show the fragment, revert it, and observe that the next request executes PHP.

**Acceptance Scenarios**:

1. **Given** a ready nginx instance and a valid plugin-emitted cache-routing fragment in a regular file, **When** the user applies it under a valid unused name, **Then** the complete candidate is accepted, activated only for that instance, the web tier is reloaded, readiness is proven, and the result identifies the active fragment and successful phase outcomes without exposing its content.
2. **Given** an active fragment and a warmed matching static page, **When** the user requests that page, **Then** the response carries the fragment's server-owned hit marker and the independent PHP execution sentinel does not change.
3. **Given** active fragments on the selected instance, **When** the user lists them or shows one by exact name in default mode, **Then** the operation reports bounded metadata and current health without changing persistent state or revealing fragment content.
4. **Given** an active fragment, **When** the user reverts it, **Then** the candidate without that fragment is validated and activated, the selected web tier becomes ready, the fragment disappears from the active list, and a request to the former cache route executes PHP.
5. **Given** the same active name and byte-identical content, **When** the user applies it again, **Then** the operation is a proven no-op with no reload and no duplicate fragment.
6. **Given** the same active name and different valid content, **When** the user applies it, **Then** exactly one fragment with that name remains and it represents only the newly proven content.

---

### User Story 2 - Prove and Revert OpenLiteSpeed Cache Behavior (Priority: P1)

A testing agent exercises plugin-compatible cache behavior on an OpenLiteSpeed
instance with the same supported workflow and obtains evidence that distinguishes
server-cache hits, plugin purge effects, cache rewarming, and PHP fallback.

**Why this priority**: OpenLiteSpeed is a required minimum server and its accepted
configuration can be ignored or misapplied without obvious syntax errors. Its
behavior needs independent proof rather than inference from nginx.

**Independent Test**: On one disposable OpenLiteSpeed instance, apply a valid
fragment, observe origin/PHP, warm the page, observe a server hit without PHP,
invoke the plugin's purge action, observe a non-hit with PHP, rewarm and observe a
hit again, then revert and observe origin/PHP.

**Acceptance Scenarios**:

1. **Given** a ready OpenLiteSpeed instance and a compatible fragment, **When** the user applies it, **Then** the complete candidate is proven using an isolated validation environment that has the exact active server image and no access to the running instance's network, data, or mutable configuration before the running instance is changed.
2. **Given** a proven active fragment and a warmed page, **When** the page is requested, **Then** the response contains an OpenLiteSpeed server-cache hit marker and the independent PHP execution sentinel remains unchanged.
3. **Given** a proven server-cache hit, **When** the plugin performs its supported purge and the page is requested again, **Then** the response is not a server-cache hit and the PHP execution sentinel advances; after rewarming, a subsequent request again proves a server-cache hit without PHP execution.
4. **Given** an active OpenLiteSpeed fragment, **When** it is reverted, **Then** the selected instance returns to a ready origin/PHP response and the fragment is absent from its active list.

---

### User Story 3 - Refuse Unsafe Input Without Disrupting Service (Priority: P1)

A Sandbox operator can give the capability malformed, incompatible, oversized,
or out-of-authority input and know it will fail before changing the running server.

**Why this priority**: Accepting native server configuration is privileged. Safe
refusal is required before the capability can be used on shared development hosts.

**Independent Test**: Attempt invalid syntax, a server-type mismatch, forbidden
directives, unsafe input sources, invalid names, and an oversized fragment on a
healthy target while monitoring a second instance. Every attempt must fail before
activation and both instances must remain unchanged and ready.

**Acceptance Scenarios**:

1. **Given** malformed syntax or a fragment that the active server would ignore rather than honor, **When** apply is requested, **Then** it is refused with a bounded reason, no running configuration or active state changes, no reload occurs, and the instance remains ready.
2. **Given** a fragment outside the `wordpress-cache-v1` authority, **When** apply is requested, **Then** it is refused before server validation or activation and identifies the violated authority rule without echoing content.
3. **Given** an nginx fragment and an OpenLiteSpeed instance, or the reverse, **When** apply is requested, **Then** the server mismatch is refused without writes or reload.
4. **Given** a symlink, special file, oversized source, conflicting file-and-standard-input request, or invalid name, **When** apply is requested, **Then** input is refused as data-validation failure and no instance is changed.
5. **Given** an unsupported active server type, **When** any mutation is requested, **Then** it is refused with the supported server types and leaves the instance unchanged.

---

### User Story 4 - Recover a Failed or Interrupted Activation (Priority: P1)

An operator receives a truthful terminal result when validation succeeds but
activation, reload, readiness, or rollback does not. Later operations cannot build
on ambiguous state.

**Why this priority**: Full-candidate validation cannot prevent every runtime
failure. Automatic restoration and fail-closed recovery make the capability
reversible in practice.

**Independent Test**: Inject a post-validation activation failure, interrupt an
operation at each durable phase boundary, and simulate rollback timeout or state
drift. Verify exact restoration when possible and recovery-needed refusal otherwise.

**Acceptance Scenarios**:

1. **Given** a known-good active fragment set and a candidate that passes validation, **When** activation, reload, or readiness fails, **Then** Sandbox restores the exact prior set, performs one bounded recovery activation, proves readiness, and reports the candidate as rolled back rather than active.
2. **Given** rollback cannot be proven ready within its bound, **When** the operation ends, **Then** the result is recovery-needed, preserves evidence for both failures, and does not claim either candidate or prior state as active.
3. **Given** a process interruption with a retained in-progress operation, **When** a later mutation starts, **Then** Sandbox first reconciles to one exact known-good set or refuses recovery-needed; it never applies a new candidate on ambiguous state.
4. **Given** retained state that is corrupt or disagrees with the observed runtime, **When** list or show is requested, **Then** it reports degraded or recovery-needed state without repairing or otherwise writing.
5. **Given** two concurrent mutation requests for one instance, **When** they overlap, **Then** they are serialized or one receives a bounded conflict, and neither update is lost or combined with uncommitted state.

---

### User Story 5 - Preserve Instance and Lifecycle Isolation (Priority: P2)

An operator can run multiple instances without a server fragment, recovery action,
server switch, deletion, or reused display name leaking configuration across them.

**Why this priority**: Isolation is a core Sandbox promise and configuration that
escapes its owning instance could affect unrelated projects.

**Independent Test**: Record fragment-set identity, runtime identity, readiness,
and a control response for two ready instances; mutate one; attempt prohibited
switch and delete actions; then delete and recreate an instance with the same
display name. Verify that no fragment crosses the instance identity boundary.

**Acceptance Scenarios**:

1. **Given** two running instances, **When** one instance applies, replaces, reverts, or rolls back a fragment, **Then** the other instance's fragment-set identity, runtime identity, response marker, and readiness result remain unchanged.
2. **Given** an active fragment or unresolved fragment transaction, **When** the user attempts to switch the instance's server type, **Then** the switch is refused with guidance to reach a clean fragment state first and no server or fragment state changes.
3. **Given** an active fragment, unresolved transaction, or recovery-needed state, **When** managed instance deletion is requested, **Then** deletion is refused unless the normal destructive confirmation also explicitly authorizes removal of the instance-scoped fragment state.
4. **Given** confirmed deletion of an instance and its fragment state, **When** a new instance later reuses the display name, **Then** the new instance starts with no inherited fragments or transaction evidence.
5. **Given** an instance stops and later starts without changing identity or server type, **When** it returns ready, **Then** its last proven fragment set remains authoritative and is reconciled before use.

---

### User Story 6 - Inspect Exact Content Deliberately (Priority: P3)

A developer can retrieve one stored fragment for comparison while routine status,
machine output, errors, and logs remain content-free.

**Why this priority**: Exact inspection is useful for debugging, but it must not
turn normal automation output into a channel for arbitrary caller-provided text.

**Independent Test**: Compare default show, list, structured output, explicit
human content output, file output, errors, and logs for a fragment containing
recognizable non-secret markers.

**Acceptance Scenarios**:

1. **Given** an active fragment, **When** default show, list, or structured output is requested, **Then** only bounded metadata is returned and the fragment bytes do not appear in output, errors, or logs.
2. **Given** an active fragment and explicit human content mode, **When** exact content is requested by name, **Then** only that fragment is written to standard output and no descriptive text is mixed with it.
3. **Given** explicit content mode combined with structured output, **When** the command is evaluated, **Then** it is refused before content is emitted.
4. **Given** an explicit output file that is not a safe owner-only regular-file destination, **When** content export is requested, **Then** the export is refused without creating or replacing the destination.

### Edge Cases

- Empty fragments are refused; the maximum accepted fragment is 262,144 bytes,
  and one byte over that limit is refused before validation or state mutation.
- Fragment names are 1–64 characters, start and end with a lowercase ASCII letter
  or digit, and contain only lowercase ASCII letters, digits, and single hyphens.
  Traversal, separators, whitespace, control characters, values recognized as
  credential material by the existing redaction boundary, and names that fail
  normalization are refused rather than rewritten.
- A file that changes while being read is refused unless one stable bounded byte
  sequence can be proven as the submitted candidate.
- End-of-input, validation, activation/readiness, and rollback each have a finite
  deadline. Timeout is an explicit failed or recovery-needed phase, never success.
- Reverting a missing name is a proven no-op only when state is healthy and no
  unresolved transaction exists; otherwise the underlying degraded state wins.
- A fragment may be syntactically valid alone but invalid in combination with an
  existing fragment. The full ordered candidate set must pass validation.
- A candidate that shadows protected WordPress login, Sandbox autologin,
  readiness, health, or clean-URL routes is outside authority even if the server
  accepts it.
- A server process can acknowledge reload and then fail. Readiness must be
  observed after activation and after rollback, not inferred from acknowledgement.
- A runtime identity or active server image change during validation or activation
  invalidates the operation and requires reconciliation before retry.
- List and show against missing, ambiguous, deleted, or identity-mismatched
  instances fail closed and never adopt retained state by display name.

## Requirements *(mandatory)*

### Functional Requirements

#### Scope and Authority

- **FR-001**: Sandbox MUST provide CLI-first operations to apply, list, show, and revert named server-configuration fragments for one explicitly resolved existing instance.
- **FR-002**: Every operation MUST resolve exactly one instance through the existing ownership rules and MUST refuse missing, ambiguous, or identity-mismatched ownership before reading or changing fragment state.
- **FR-003**: The first supported fragment authority MUST be identified as `wordpress-cache-v1` and MUST be bound to the selected instance's current server type.
- **FR-004**: `wordpress-cache-v1` MUST accept only cache routing and cache-response behavior inside the selected instance's existing WordPress site context: same-site request matching, direct delivery of cache artifacts beneath that instance's WordPress document root, bounded response markers, and server-native cache controls needed for plugin purge verification.
- **FR-005**: `wordpress-cache-v1` MUST refuse complete server or virtual-host declarations, listeners, arbitrary upstreams or proxy targets, paths outside the selected WordPress document root, process identity changes, module or code loading, program execution, unrestricted file inclusion, host-global configuration, and changes to protected readiness, health, login, autologin, TLS, DNS, or clean-URL routing.
- **FR-006**: The capability MUST support nginx and OpenLiteSpeed as separate, non-translatable server types. Apache MAY be reported as supported only after satisfying every requirement and success criterion that applies to the minimum servers.
- **FR-007**: A fragment accepted for one server type MUST never be applied, translated, retained as active, or silently reactivated for another server type.
- **FR-008**: Fragment names MUST satisfy the 1–64 character normalized identifier boundary defined in Edge Cases and MUST be unique within one instance identity and server type.

#### Input and Safe Inspection

- **FR-009**: Apply MUST accept exactly one bounded regular file or bounded standard input, treat its bytes only as configuration data, and never interpret any part as a shell command, command template, argument expansion, or executable instruction.
- **FR-010**: Apply MUST refuse symlinks, directories, devices, sockets, other special files, unstable file reads, empty input, and input larger than 262,144 bytes.
- **FR-011**: Routine results, structured output, logs, phase evidence, list, and default show MUST omit fragment content and MUST not expose caller paths beyond a safe basename when a path is needed for human diagnostics.
- **FR-012**: Within the standard content-free result envelope required by FR-025, List's fragment payload MUST contain only the selected instance's active server type, health state, fragment names, authority versions, bounded content identities, activation state, and safe timestamps.
- **FR-013**: Default show MUST be read-only and return bounded metadata for exactly one normalized name; it MUST distinguish absent, active, degraded, and recovery-needed state.
- **FR-014**: Exact content show MUST require an explicit human-output mode, MUST emit only the selected fragment on standard output, MUST be incompatible with structured output, and MUST not copy content into logs, errors, or other output channels.
- **FR-015**: Optional exact-content file output MUST create or replace only an explicitly selected owner-only regular file, MUST refuse unsafe destinations, and MUST not weaken the content-free structured-output contract.
- **FR-016**: List and show MUST perform zero persistent writes, including when they observe corruption, drift, an interrupted transaction, or a missing runtime.

#### Validation and Activation

- **FR-017**: Apply MUST build and validate the complete effective candidate fragment set for the active server before replacing any running configuration or committed active state.
- **FR-018**: Revert MUST validate the complete effective candidate set with the named fragment removed before replacing running configuration or committed active state.
- **FR-019**: Validation MUST use the selected instance's declared server implementation and exact active server version or image; a host-global or merely compatible substitute is not sufficient.
- **FR-020**: nginx validation MUST prove that the complete candidate is accepted in the same bounded site context used by the selected instance and that every fragment is actually included there.
- **FR-021**: OpenLiteSpeed validation MUST boot the complete candidate in an isolated environment using the exact active image, MUST have no access to the running instance's network, data, secrets, or mutable configuration, and MUST treat ignored, unreachable, or unproven directives as validation failure.
- **FR-022**: Validation MUST recheck instance identity, active server type, runtime identity, and exact server image immediately before activation; any change MUST invalidate the candidate without activation.
- **FR-023**: A validation or authority failure MUST leave the running configuration and committed active set byte-for-byte unchanged, MUST not reload the web tier, and MUST return a bounded reason and phase result.
- **FR-024**: Successful apply or revert MUST activate only the selected instance's proven candidate, reload or restart only that instance's web tier, and prove post-activation readiness before committing the new active state.
- **FR-025**: Each result MUST identify the selected instance identity, active server type, operation, fragment name, authority version, safe content identity, mutation outcome, validation outcome, reload outcome, readiness outcome, and rollback outcome when applicable.
- **FR-026**: Applying byte-identical content under the same active name MUST be a proven no-op with no validation boot, activation, or reload; applying different valid content under that name MUST replace rather than append and leave exactly one active fragment.
- **FR-027**: Reverting an absent name MUST be a proven no-op only after healthy state and absence are established; it MUST not conceal degradation or an unresolved transaction.

#### Rollback, Recovery, and Concurrency

- **FR-028**: Before activation, every mutation MUST retain sufficient instance-bound evidence to restore the exact prior known-good fragment set after process interruption or activation failure.
- **FR-029**: If activation, reload, or readiness fails after running state may have changed, Sandbox MUST automatically restore the exact prior known-good set, perform no more than one recovery activation, and prove readiness within the rollback bound.
- **FR-030**: A successful rollback MUST report the requested mutation as rolled back, not successful, and MUST preserve bounded evidence for the original failure and recovery result.
- **FR-031**: An unproven or failed rollback MUST report recovery-needed, MUST preserve the last provable identities and phase evidence, and MUST prevent any new candidate from being applied.
- **FR-032**: A later mutation that finds an interrupted transaction, corrupt state, or runtime drift MUST first reconcile to exactly one known-good state or refuse recovery-needed; it MUST never select a state by recency alone or overwrite ambiguous evidence.
- **FR-033**: Fragment mutations MUST be serialized per instance. An overlapping writer MUST either wait within the operation bound and evaluate the committed result or receive a bounded conflict without modifying state.
- **FR-034**: Validation, activation/readiness, and rollback MUST each end within 60 seconds, and the complete mutation MUST end within 180 seconds. A phase timeout MUST be reported explicitly and MUST never be treated as success.
- **FR-035**: A stopped instance MUST not be reported ready. An unavailable readiness observation MUST be unknown or recovery-needed, not successful.

#### Isolation and Lifecycle

- **FR-036**: Stored fragments, candidate validation, activation, reload, rollback, inspection, and recovery MUST be scoped to one immutable instance identity and MUST not change host-global ingress or any other instance.
- **FR-037**: Fragment state MAY survive ordinary stop/start only for the same instance identity and server type; readiness and the effective fragment-set identity MUST be reconciled before dependent traffic is accepted as proven.
- **FR-038**: Server switching MUST be refused while any fragment is active or any fragment transaction is unresolved, degraded, or recovery-needed. The refusal MUST leave runtime identity, server type, and fragment state unchanged.
- **FR-039**: Managed deletion MUST be refused for active, unresolved, degraded, or recovery-needed fragment state unless the existing destructive confirmation explicitly includes removal of that exact instance's fragment state.
- **FR-040**: Confirmed instance deletion MUST remove or permanently disassociate all fragment and transaction state for that instance identity so a later instance with the same display name cannot inherit it.
- **FR-041**: Runtime regeneration, reconciliation, relocation, or server restart MUST not silently drop, broaden, or apply fragments beyond their proven instance identity and server type.
- **FR-042**: No supported user journey for this feature may require raw container commands, SSH, hand-edits under generated runtime directories, or direct access to retained fragment state.

#### Evidence and Compatibility

- **FR-043**: Live acceptance for each minimum server MUST distinguish a server-owned cache hit from a PHP response using both a server-owned response marker and an independent request-scoped PHP execution sentinel.
- **FR-044**: OpenLiteSpeed live acceptance MUST prove the ordered sequence origin/PHP, warm, server hit without PHP, plugin purge, non-hit with PHP, rewarm, server hit without PHP, and revert to origin/PHP.
- **FR-045**: nginx live acceptance MUST prove a warmed server hit without PHP and, after revert, PHP fallback on the same route.
- **FR-046**: Isolation acceptance MUST record a second running instance's fragment-set identity, runtime identity, response marker, and readiness before and after every target mutation and MUST prove all four remain unchanged.
- **FR-047**: Invalid-syntax and out-of-authority acceptance for nginx and OpenLiteSpeed MUST prove refusal before activation, zero reloads, unchanged active-state identity, and continued readiness of the target and control instances.
- **FR-048**: Automatic-rollback acceptance MUST use a candidate that passes pre-activation validation but fails activation or readiness, and MUST prove either exact prior-state restoration plus readiness or a truthful recovery-needed terminal result.
- **FR-049**: Public command behavior, structured result fields, bounds, supported authority versions, server compatibility, and recovery meanings MUST be documented together and remain consistent across human and structured output.
- **FR-050**: Fragments MUST NOT be represented or accepted as a secret transport. Names, routine metadata, errors, logs, and structured output MUST pass the existing secret-redaction boundary without exposing fragment bytes.

### Key Entities

- **Server-Config Fragment**: One bounded byte sequence identified by normalized
  name, authority version, safe content identity, owning immutable instance
  identity, active server type, and proven activation state.
- **Fragment Set**: The complete ordered collection of fragments whose combined
  configuration is validated and activated as one candidate for an instance.
- **Activation Transaction**: The bounded evidence for one apply or revert,
  including prior and candidate set identities, runtime/server identities, phase
  outcomes, deadlines, and terminal active, no-op, rolled-back, conflict, or
  recovery-needed result.
- **Known-Good State**: An exact fragment set and runtime identity that previously
  passed validation, activation, reload, and readiness and is eligible for recovery.
- **Instance Identity**: The durable ownership identity that distinguishes an
  instance from another instance or a later reuse of the same display name.
- **Behavior Evidence**: A bounded live observation pairing the server-owned
  response marker, PHP execution sentinel, fragment-set identity, runtime identity,
  and readiness result for the target and control instances.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In 100% of the required nginx acceptance sequence, a named valid fragment can be applied, listed, shown as metadata, used to prove a server-owned hit without PHP execution, reverted, and followed by proven PHP fallback without raw infrastructure access.
- **SC-002**: In 100% of the required OpenLiteSpeed acceptance sequence, observations prove origin/PHP, warm, server hit without PHP, purge, non-hit with PHP, rewarm, server hit without PHP, and revert to origin/PHP in that order.
- **SC-003**: Every deliberately invalid, wrong-server, oversized, unsafe-source, and out-of-authority test case is refused before activation with zero reloads and no change to the active fragment-set identity.
- **SC-004**: For every injected post-validation activation or readiness failure, the operation reaches a terminal rolled-back or recovery-needed result within 180 seconds and never reports the failed candidate active.
- **SC-005**: Every successful rollback restores the exact prior fragment-set identity and proves target readiness within 60 seconds; every unproven rollback blocks later mutation until recovery is resolved.
- **SC-006**: Across all target apply, replace, revert, refusal, rollback, and recovery acceptance cases, the control instance shows zero changes to fragment-set identity, runtime identity, response marker, and readiness.
- **SC-007**: Reapplying identical content produces zero reloads and one active record; replacing same-name content and reverting it each leave exactly the expected fragment count with no duplicates.
- **SC-008**: List, default show, degraded-state inspection, and missing-name inspection produce zero persistent writes in all acceptance cases.
- **SC-009**: Fragment content appears in zero routine result, structured output, error, phase-evidence, or log captures across the acceptance matrix; it appears only in an explicitly requested exact-content destination.
- **SC-010**: Every interrupted-phase, concurrent-writer, corrupt-state, runtime-drift, server-switch, deletion, and display-name-reuse test fails closed or reconciles to one exact known-good state without cross-instance adoption.
- **SC-011**: A fresh agent with only the published guide and prepared acceptance fixture can complete apply, inspect, live proof, and revert on nginx and OpenLiteSpeed without undocumented commands or infrastructure access; every injected failure identifies the failed phase and one safe next action.
- **SC-012**: All read-only list and default-show operations on healthy local instance state finish within 5 seconds, and all mutation operations finish or return a truthful bounded terminal state within 180 seconds.

## Assumptions

- The selected instance already exists, has an authoritative immutable identity,
  and is ready on one declared server type before apply or revert begins.
- WordPress, the plugin, its emitted fragment, cache warmup, cache artifacts, purge
  action, and PHP execution sentinel are prepared by the caller; this feature owns
  only safe fragment lifecycle and evidence collection boundaries.
- nginx and OpenLiteSpeed expose deterministic instance-local inclusion contexts.
  If an active image cannot prove that boundary, its apply operation is unsupported
  and fails closed rather than broadening authority.
- The existing instance resolver, lifecycle confirmation, readiness definition,
  structured-output conventions, safe identifier handling, and redaction policy
  remain authoritative dependencies.
- Fragment text is untrusted configuration data and is not suitable for secrets.
  Exact-content inspection is an explicit operator action, not routine telemetry.
- A server-owned response marker paired with an independent PHP execution sentinel
  is sufficient evidence that PHP did not serve that request.
- Apache is optional for the initial release. Herd and host-global server
  configuration remain outside this feature.
