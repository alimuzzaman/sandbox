# Feature Specification: Safe Secret Inspection

**Feature Branch**: `latest`

**Created**: 2026-08-11

**Status**: Draft

**Input**: Ready product requirements in `prd.md` for least-disclosure secret inspection, masking, use without seeing, targeted updates, and a warned single-secret reveal.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Discover and inspect only the needed secret (Priority: P1)

An agent or operator can list secret names from an approved source and inspect the state of one selected key without receiving any secret characters or unrelated values.

**Why this priority**: Names-only discovery and one-key metadata eliminate the most common reason agents read an entire secret-bearing file.

**Independent Test**: Register a source containing several representative assignments, request its default inspection and then one-key metadata, and verify that only bounded names and non-character metadata are returned.

**Acceptance Scenarios**:

1. **Given** an approved source containing several assignments, **When** no inspection mode or key is supplied, **Then** the result contains only eligible key names and safe source identity.
2. **Given** a selected key, **When** metadata is requested, **Then** the result distinguishes missing, empty, present, multiline, structured, and unsupported states without returning any value character.
3. **Given** an unapproved path or source alias, **When** inspection is requested, **Then** the request is refused without resolving or displaying the external path.
4. **Given** inspection through CLI or MCP, **When** required audit intent cannot be recorded, **Then** no source value is processed and the request fails with a non-secret reason.

---

### User Story 2 - Validate or mask one selected value (Priority: P1)

An agent can check the expected shape of one credential and, only when necessary, request a fixed masked identifier that cannot be expanded through repeated calls.

**Why this priority**: Shape checks resolve prefix, length, and format questions without character disclosure, while a tightly bounded mask supports the remaining identification cases.

**Independent Test**: Validate representative recognized, unrecognized, short, structured, multiline, and password-like values and verify the returned checks and fixed preview policy.

**Acceptance Scenarios**:

1. **Given** a reviewed validation profile and one selected key, **When** validation runs, **Then** each expected check reports pass, fail, or not applicable and the result states that live provider validity was not checked.
2. **Given** an eligible recognized opaque token, **When** masking is explicitly requested, **Then** the result contains only its reviewed public type prefix, a constant hidden marker, and its final four characters.
3. **Given** an eligible unrecognized opaque token, **When** masking is explicitly requested, **Then** the result contains only a constant hidden marker and its final four characters.
4. **Given** a protected value class or an ineligible short value, **When** masking is requested, **Then** the request is refused without returning any value character.
5. **Given** repeated or varied preview attempts, **When** the caller requests different widths, offsets, or modes, **Then** cumulative disclosure never exceeds the single fixed preview.

---

### User Story 3 - Use a secret without seeing it (Priority: P1)

An agent can give one selected secret to one trusted child process without placing the value in arguments, the parent environment, normal output, or the agent response.

**Why this priority**: Most tasks need a credential to be consumed, not displayed; this is the preferred alternative to plaintext reveal.

**Independent Test**: Launch a trusted fixture command that reports only whether its expected environment value is present, and verify child receipt, parent isolation, redacted bounded output, dangerous-name refusal, and audit behavior.

**Acceptance Scenarios**:

1. **Given** a safe destination name and trusted direct command, **When** local use is requested for one key, **Then** only that selected value is available to the child and the parent environment remains unchanged.
2. **Given** a command that prints the exact selected value, **When** it runs, **Then** output is redacted before display or retention and the result still reports its exit status.
3. **Given** a command that transforms the value, **When** it emits the transformed form, **Then** documentation and warnings do not claim the transformation is guaranteed to be redacted.
4. **Given** a dangerous destination name, an implicit shell request, or an unapproved MCP use profile, **When** use is requested, **Then** the request is refused before child launch.
5. **Given** a remotely reachable MCP server, **When** use is requested, **Then** only a registered profile with fixed command, argument shape, destination, source/key scope, output bound, and timeout may run.

---

### User Story 4 - Update one secret without reading its file (Priority: P2)

An operator or broker can create or replace one literal assignment while receiving only non-secret status and preserving every unrelated source entry.

**Why this priority**: Targeted mutation avoids whole-file disclosure and prevents agents from reconstructing or rewriting unrelated credentials.

**Independent Test**: Update one assignment in a source with comments, quoting, ordering, and unrelated keys, then compare all non-target content and verify conflict, permission, audit, and response behavior.

**Acceptance Scenarios**:

1. **Given** an approved source and hidden interactive input, **When** one key is updated, **Then** only the target assignment changes and the response contains non-secret status plus an opaque revision.
2. **Given** protected standard input, a registered reference, or an approved generator, **When** one key is created or replaced, **Then** neither the input nor stored value appears in arguments, output, errors, or audit data.
3. **Given** a stale revision, intent mismatch, duplicate target, unsafe source, or failed selected profile, **When** update is attempted, **Then** the source remains unchanged.
4. **Given** a human-supplied value through ordinary MCP arguments, **When** update is attempted, **Then** MCP refuses it and offers only the approved out-of-band completion path.

---

### User Story 5 - Reveal exactly one secret as a last resort (Priority: P3)

A human operator can intentionally display one selected value only after a fresh local warning and confirmation, without making reveal available to pipes, structured output, or MCP.

**Why this priority**: A narrowly governed reveal is safer than telling an operator to open the entire file, but it remains the highest-risk and least-preferred operation.

**Independent Test**: Attempt reveal with and without a controlling TTY, exact-key confirmation, audit availability, pipes, redirection, structured output, multiple keys, and MCP; verify that only the fully confirmed local case displays one value directly to the TTY while stdout stays empty.

**Acceptance Scenarios**:

1. **Given** a local controlling TTY, one source, one key, available audit, and exact key-name confirmation, **When** reveal is requested, **Then** only that value is displayed directly on the TTY and stdout remains empty.
2. **Given** missing or incorrect confirmation, no TTY, a pipe, redirection, noninteractive approval, multiple keys, a wildcard, structured output, or MCP, **When** reveal is attempted, **Then** no value is read for display.
3. **Given** a reveal request, **When** the warning is shown, **Then** it identifies the source alias and key, recommends use without seeing, and explains transcript, scrollback, capture, and rotation risks before confirmation.

---

### User Story 6 - Follow the least-disclosure agent workflow (Priority: P2)

An agent receives a detailed operational skill that orders safe inspection, validation, masking, use, update, and human reveal and explicitly identifies unsafe practices.

**Why this priority**: The safer capability only reduces exposure if agents consistently choose it instead of raw file reads and plaintext transfer.

**Independent Test**: Give an agent representative discovery, validation, execution, update, and reveal tasks and verify that the skill selects the lowest-disclosure path and never requests a pasted secret.

**Acceptance Scenarios**:

1. **Given** a task that needs only presence or format evidence, **When** the skill is followed, **Then** it stops after names, metadata, or validation and does not request masking or reveal.
2. **Given** a trusted program that needs a credential, **When** the skill is followed, **Then** it selects bounded use rather than parent-shell export or reveal.
3. **Given** a value that must be replaced, **When** the skill is followed, **Then** it directs the human to hidden or protected input and verifies only non-secret status.
4. **Given** accidental plaintext exposure, **When** the skill is followed, **Then** it instructs the agent to stop use, report privately, rotate, and avoid concealing the incident.

### Edge Cases

- The registered source does not exist, is empty, exceeds 1 MiB, contains more than 4,096 assignments, or changes between validation and use.
- The source is a symlink, hard-linked unexpectedly, a directory, device, socket, or other non-regular file.
- The source owner differs from the current local owner or grants group/other read or write access.
- Input is invalid UTF-8, contains NUL or terminal-control characters, uses command substitution or expansion, contains duplicate keys, or exceeds the per-value limit.
- A key name is empty, longer than 128 characters, contains control characters, or is not a portable environment identifier.
- A recognized public prefix is present on an otherwise malformed token.
- An unrecognized token meets the length threshold but resembles a JWT, URL, connection string, password, structured document, or encoded key material.
- A selected value is empty, exactly 23 or 24 characters, or contains only one character class.
- A write receives an empty value, multiple lines after newline normalization, a final CR without LF, or more than 64 KiB.
- A child command exits nonzero, times out, exceeds the output budget, spawns descendants, prints the secret across chunk boundaries, or receives a termination signal.
- A child requests a destination such as `LD_PRELOAD`, `DYLD_INSERT_LIBRARIES`, `NODE_OPTIONS`, `PYTHONPATH`, `BASH_ENV`, `ENV`, `PROMPT_COMMAND`, or another loader/interpreter/shell control.
- Audit intent succeeds but outcome recording fails after a read-only operation, child completion, reveal, or successful atomic replacement.
- An MCP catalog is local, project-scoped remote, or broader remote and the requested source or mode is not explicitly authorized for that catalog.

## Requirements *(mandatory)*

### Functional Requirements

#### Sources and parsing

- **FR-001**: The product MUST expose secret-bearing data only through registered source aliases and MUST reject arbitrary caller-supplied paths.
- **FR-002**: V1 source aliases MUST cover the personal Sandbox secret assignment file and explicitly registered project `.env*` assignment files only.
- **FR-003**: The product MUST NOT enumerate the current process environment, infer secret fields from mixed configuration documents, or connect to external secret managers in v1.
- **FR-004**: A source MUST be rejected unless it is a regular, owner-controlled file with no unsafe link resolution and no group/other access.
- **FR-005**: A source MUST be rejected when it exceeds 1 MiB, contains more than 4,096 assignments, contains a value larger than 64 KiB, or changes during a security-relevant operation.
- **FR-006**: Source content MUST be interpreted as inert literal assignments; expansion, interpolation, command substitution, includes, executable tags, and shell evaluation MUST never run.
- **FR-007**: Key names MUST be portable environment identifiers no longer than 128 characters and MUST be sanitized before human-readable or structured output.
- **FR-008**: Duplicate assignments, invalid encoding, NUL bytes, terminal-control characters, and unsupported syntax MUST fail closed without returning an offending line or source excerpt.
- **FR-009**: Read-only inspection MUST NOT regenerate runtime files, reconcile an instance, mutate secret resolution, or trigger ordinary runtime initialization side effects.

#### Inspection, validation, and masking

- **FR-010**: Inspection with no explicit mode MUST return only bounded eligible key names and safe source identity.
- **FR-011**: Inventory and metadata MAY select up to 100 keys per request; validation, masking, use, write, and reveal MUST accept exactly one key.
- **FR-012**: One-key metadata MUST distinguish missing, empty, present, multiline, structured, and unsupported states without returning secret characters.
- **FR-013**: Length MUST be reported as `0`, `1-7`, `8-15`, `16-23`, `24-31`, `32-63`, `64-127`, `128-255`, or `256+` by default.
- **FR-014**: Exact length MAY be returned only for one explicitly selected key after an explicit request, and MUST be labeled disclosed metadata.
- **FR-015**: Shape validation MUST use reviewed named profiles and MUST return only pass, fail, or not-applicable results for checks such as presence, exact length, public prefix class, suffix class, character set, segment structure, parseability, and embedded expiry.
- **FR-016**: Shape results MUST state `live_checked: false` and MUST NOT claim provider acceptance or generic credential validity.
- **FR-017**: Masking MUST be explicit and MUST NOT accept caller-selected offsets, widths, templates, regular expressions, guesses, or arbitrary prefix/suffix checks.
- **FR-018**: A recognized eligible opaque token MAY disclose only its reviewed public provider/type prefix, the constant marker `<redacted>`, and its final four characters.
- **FR-019**: An unrecognized token is mask-eligible only when it is at least 24 single-line printable ASCII characters, contains at least three of uppercase, lowercase, digit, and approved punctuation, hides at least 16 characters, and is not classified as a protected value type; it MAY disclose only `<redacted>` and its final four characters.
- **FR-020**: Password-like values, values shorter than 24 characters, low-variety values, connection strings, URLs containing credentials, JWTs, structured documents, multiline values, PEM or private keys, certificates, and binary material MUST receive no character preview.
- **FR-021**: Repeated masking through any supported client MUST return the same fixed disclosure and MUST NOT provide an interface capable of increasing cumulative disclosure.
- **FR-022**: Structured inspection MAY return a bounded key tree with every leaf replaced by null; key material MAY return only non-secret type and parse evidence.
- **FR-023**: Human-readable and structured inspection MUST expose materially equivalent fields, omissions, labels, and refusal reasons.

#### Audit and output safety

- **FR-024**: Every inventory, metadata, validation, mask, use, update, and reveal attempt MUST record audit intent before processing secret values and MUST record a non-secret outcome afterward.
- **FR-025**: A required audit record MUST contain the operation, source alias, selected key names, caller/session identity when available, client surface, profile, decision, timestamp, safe reason code, counts, and opaque revision when relevant.
- **FR-026**: Audit data MUST NOT contain a value, masked preview, exact length, hash, source excerpt, candidate input, child output, provider body, or temporary-file content.
- **FR-027**: Audit storage MUST be owner-only and its access MUST be treated as security-sensitive because key names reveal infrastructure information.
- **FR-028**: An operation MUST fail before secret processing, child launch, display, or mutation when its required audit intent cannot be durably recorded.
- **FR-029**: Errors, exceptions, debug output, durable job output, and test failures MUST use bounded reason codes and MUST NOT contain secret-bearing source lines or values.
- **FR-030**: Exact-match redaction MUST be registered before a selected value can reach a child process, but product guidance MUST identify redaction as defense in depth that may miss transformed values.

#### Use without seeing

- **FR-031**: Local use MUST accept one approved source/key and one explicit direct-argument command without invoking an implicit shell.
- **FR-032**: The selected value MUST NOT appear in the child command arguments, parent environment, normal output, structured response, error, or audit record.
- **FR-033**: A local use child MUST start with a minimal environment containing only a reviewed baseline needed for executable lookup, user directory, temporary storage, locale, and terminal behavior, plus the one approved secret destination.
- **FR-034**: The child MUST NOT inherit other registered secret variables from the parent, and the use response MUST NOT claim that unknown unregistered parent data has been perfectly classified or removed.
- **FR-035**: Secret destinations that control loaders, interpreters, runtimes, shells, prompts, or credential helpers MUST be denied. The deny policy MUST include `LD_*`, `DYLD_*`, `NODE_OPTIONS`, `PYTHONPATH`, `PYTHONHOME`, `PERL5OPT`, `RUBYOPT`, `BASH_ENV`, `ENV`, `SHELLOPTS`, `PS4`, `PROMPT_COMMAND`, `GIT_ASKPASS`, and `SSH_ASKPASS`.
- **FR-036**: Local use MUST default to a 5-minute wall-time limit, MUST allow an explicit limit from 1 second through 30 minutes, and MUST terminate the child process group when the limit expires.
- **FR-037**: Local use MUST display or retain no more than 1 MiB of combined redacted child output and MUST report when additional output was suppressed.
- **FR-038**: The use result MUST report exit status or termination reason, elapsed-time class, truncation state, source alias, and key name without returning value-derived metadata.
- **FR-039**: MCP use MUST be unavailable for arbitrary caller-supplied commands and MUST run only a registered reviewed profile that fixes command, argument shape, destination or non-environment delivery channel, source/key scope, output limit, and timeout.
- **FR-040**: Remote transport authentication alone MUST NOT authorize inspection or use; each MCP catalog MUST explicitly grant the source and operation, otherwise the capability MUST be absent or refuse the request.
- **FR-041**: Documentation MUST warn that the child is an intentional secret recipient and can print, transform, persist, or exfiltrate the selected value.

#### Targeted update without read

- **FR-042**: Targeted update MUST accept one approved source, one key, explicit create/replace/either intent, and an optional expected opaque revision.
- **FR-043**: Human input MUST use a hidden controlling-TTY prompt by default or an explicit protected-standard-input mode; plaintext arguments, `KEY=value`, ordinary environment input, arbitrary input paths, shell-command input, and ordinary MCP value fields MUST be rejected.
- **FR-044**: Protected standard input MUST read at most 64 KiB to end-of-file, remove exactly one final LF or CRLF, reject any remaining CR, LF, or NUL, reject an empty result, and never echo, log, or include the bytes in an error.
- **FR-045**: Broker-side copy MUST accept only registered source/key references, and generation MUST accept only reviewed named profiles; neither operation may return the generated or copied value.
- **FR-046**: V1 update MUST support only one single-line literal assignment and MUST refuse multiline, structured, binary, private-key, certificate, and PEM values.
- **FR-047**: The broker MAY process the complete target internally to preserve unrelated content but MUST never return the file, prior value, new value, source excerpt, or masked preview in the update result.
- **FR-048**: A successful update MUST preserve unrelated assignments, comments, order, newline style, ownership, and restrictive permissions and MUST present the replacement as one atomic outcome.
- **FR-049**: Update MUST refuse duplicate targets, unsafe files, unexpected links, stale revisions, concurrent change, create/replace intent mismatch, or a candidate that fails its selected profile before replacement.
- **FR-050**: A failed update MUST leave the prior source intact and MUST NOT create a caller-visible plaintext backup; safe owner-only temporary remnants MUST be cleaned when possible and documented as a plaintext-file crash risk.
- **FR-051**: Update output MUST contain only source alias, key name, created/updated status, validation result, and opaque revision.
- **FR-052**: MCP MUST NOT accept a plaintext new-value argument; it MAY prepare and observe a short-lived bound update request or request registered-reference copy or generation without receiving value-derived output.

#### Single-secret reveal

- **FR-053**: Reveal MUST be a separate local CLI operation and MUST NOT be an inspection mode, structured-output option, MCP tool, batch operation, wildcard operation, or reusable approval.
- **FR-054**: Reveal MUST require a controlling TTY, exactly one source and key, available audit, a prominent warning, and fresh confirmation by retyping the exact key name.
- **FR-055**: The reveal warning MUST show only source alias and key name, recommend use without seeing, and explain risks from terminal scrollback, screen capture, recording, accessibility tools, model context, logs, and copy/paste.
- **FR-056**: After successful confirmation, reveal MUST display only the selected value directly on the controlling TTY, MUST keep stdout empty, and MUST NOT add a wrapper, source excerpt, adjacent assignment, or automatic copy action.
- **FR-057**: Reveal MUST refuse `--yes`, noninteractive input, pipes, redirection, structured output, multiple keys, wildcards, cached confirmation, and any request without a verified controlling TTY.
- **FR-058**: The shipped agent skill MUST direct agents to ask a human to perform reveal outside captured agent tooling and MUST never instruct an agent to place the displayed value in chat, tool arguments, or durable artifacts.

#### Guidance and compatibility

- **FR-059**: The shipped skill and operator documentation MUST prescribe the sequence: choose source alias, list names, inspect or validate one key, request mask only when needed, prefer bounded use, update through protected input, and reveal only as a final human exception.
- **FR-060**: Guidance MUST prohibit raw source reads, parent-shell export, secrets in arguments or chat, implicit shell evaluation, `env`, `printenv`, `set`, shell tracing, verbose HTTP diagnostics, unreviewed uploads, environment serialization, and durable plaintext artifacts.
- **FR-061**: Guidance MUST include placeholder-only safe and unsafe examples, non-secret verification steps, the limits of masking/redaction, and incident response requiring stop, private report, rotation, and non-concealment.
- **FR-062**: The feature MUST preserve existing secret resolution precedence and the existing secret-migration behavior.
- **FR-063**: CLI and MCP behavior MUST share one disclosure policy so equivalent authorized requests cannot diverge in fields, masking, validation labels, or refusal reasons.
- **FR-064**: Documentation MUST state that v1 reduces accidental disclosure but is not an exclusive security boundary while the same identity can directly read registered files.

### Key Entities

- **Registered Secret Source**: An approved alias bound to one personal or project literal-assignment file, with scope, ownership, permission, size, and operation policy.
- **Secret Entry**: A validated key and internally held value plus classification; its raw value is never part of ordinary result entities.
- **Validation Profile**: A reviewed named set of internal shape checks and optional public provider/type prefix classification.
- **Disclosure Decision**: The selected operation, permitted output fields, refusal reason, and cumulative mask policy for one caller/source/key context.
- **Use Profile**: A reviewed MCP-safe command definition with fixed executable, argument shape, delivery destination, allowed source/key scope, output budget, and timeout.
- **Update Request**: A one-key mutation intent, input channel, expected revision, validation profile, and non-secret status; a request identifier is coordination, not bearer authority.
- **Audit Event**: An owner-only intent or outcome record containing operation metadata and no value-derived content.
- **Source Revision**: An opaque identifier used to detect concurrent or mistaken updates without revealing source content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Across a representative corpus of valid, malformed, and adversarial sources, 100% of default inspections return key names only and no value-derived content.
- **SC-002**: Across every supported output surface and failure path, automated leak tests find zero complete secrets, source excerpts, candidate update values, or secret-bearing child output outside the dedicated confirmed TTY reveal.
- **SC-003**: Repeating or varying a mask request 100 times for the same key reveals no additional character beyond the initial fixed public prefix and/or final four characters.
- **SC-004**: For every protected value class and every ineligible boundary value, masking exposes zero characters.
- **SC-005**: A use-without-seeing fixture receives exactly one selected value in 100% of successful runs, while the parent environment and arguments contain no copy of that value and returned output remains within the defined bound.
- **SC-006**: Every dangerous destination in the required deny set is refused before child launch, and every unregistered MCP command request is refused before secret access.
- **SC-007**: In a formatting corpus containing comments, quoting, ordering, and both newline styles, successful targeted updates preserve 100% of non-target bytes and metadata required by the source policy.
- **SC-008**: All tested stale revisions, intent mismatches, duplicate keys, unsafe files, audit failures, and simulated replacement races leave the prior source intact and return no secret-derived output.
- **SC-009**: Only the exact local interactive reveal case displays a value; 100% of non-TTY, piped, redirected, structured, wildcard, multi-key, incorrectly confirmed, audit-unavailable, and MCP attempts disclose zero characters.
- **SC-010**: Equivalent authorized CLI and MCP inspection requests agree on returned fields, omissions, validation semantics, fixed masks, and refusal reasons in 100% of contract cases.
- **SC-011**: An operator following the shipped guide can discover, validate, and run a trusted credential-consuming placeholder command without reading the value in under five minutes.
- **SC-012**: Every successful and refused security-relevant operation produces intent and outcome audit evidence containing all required metadata and zero forbidden value-derived fields.
- **SC-013**: Inspection of a maximum-size compliant source completes within two seconds on a supported local development machine without triggering runtime reconciliation or file mutation.

## Assumptions

- V1 is a least-disclosure guardrail for local agents and operators, not an operating-system access-control boundary.
- Registered project sources are configured explicitly; the product does not automatically expose every `.env*` file found under a project.
- Existing Sandbox secret resolution and migration continue to own precedence and compatibility behavior.
- Reviewed validation and use profiles are maintained with the product and changed through normal code review.
- Live provider validation, external secret managers, arbitrary remote command execution, mixed configuration documents, multiline secret updates, private-key updates, and perfect memory zeroization are outside v1.
- A child process selected for local use is trusted by the caller; output redaction cannot make a malicious recipient safe.
- Exact user-facing command names and structured field names may be finalized during planning provided they preserve these observable behavior and disclosure boundaries.

## Convergence amendment — 2026-08-13 (27-feedback redaction corpus)

Feedback `81f43e6f` identified gaps in common token forms and credentials
embedded in URLs. This amendment is additive to the existing least-disclosure
policy and does not authorize reading a source or revealing a value.

### Normative requirements

- **FR-065**: Redaction MUST cover bearer/API credentials in headers and text,
  assignment forms such as `token=`, `password=`, `api_key=`, and common provider
  token prefixes (including GitHub, OpenAI-style, Slack-style, and comparable
  opaque tokens) before data reaches stdout, stderr, JSON, audit, feedback,
  telemetry, exception chains, or durable files.
- **FR-066**: URLs containing userinfo or credential query parameters MUST be
  normalized before display or persistence: remove or replace the user/password
  component and redact token-like query values while retaining only safe scheme,
  host, path, and non-sensitive query names where policy permits. A Basic Auth
  URL MUST never be emitted verbatim (`81f43e6f`).
- **FR-067**: The same redaction service and corpus MUST be used by CLI, MCP,
  feedback, job output, remote verification, and child-process error paths. A
  failure in redaction is fail-closed for the affected output; it MUST NOT fall
  back to raw exception or command rendering.
- **FR-068**: Redaction tests MUST assert complete-value absence and safe
  handling of nested exceptions, serialized argv, URLs, and mixed case/spacing;
  partial masking MUST NOT be treated as proof that a high-entropy credential is
  safe.

### Acceptance evidence required before closing this amendment

The corpus MUST include bearer/API assignment variants, provider prefixes,
Basic Auth URLs, token query strings, nested exception/traceback output, and
CLI/MCP/feedback/remote-probe surfaces. Evidence records only pattern names and
pass/fail counts, never fixture values.
