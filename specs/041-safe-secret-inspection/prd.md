# Product Requirements Draft: Safe Secret Inspection for Agents

**Status**: Discovery

**Created**: 2026-08-11

**Last Refined**: 2026-08-11

**Input**: "Create a CLI/MCP command/tool/skill that can read secrets safely. Agents should not need to read a full `.env` or other secret-bearing file; support key-only output and a scrambled form that can help check key size, prefix, or suffix. Research what others do." On 2026-08-11 the user accepted the recommended v1 scope, confirmed that masking is required, added write-without-read secret-file updates, requested a warned and verified single-secret reveal, and required detailed guidance for using a secret without reading it.

**Drafting Model**: Current Codex root configuration (exact model and effort are not exposed to this workflow; preferred `gpt-5.6-terra` Medium was not verifiable)

**Final Validation**: `PASS` — `gpt-5.6-sol` High

**Validated On**: 2026-08-11

**Artifact Owner**: `speckit-refine`

**Next Stage**: `speckit-specify`

> This document captures product intent before formal specification. It must
> not contain implementation plans, task breakdowns, contracts, or source-code
> changes.

## Problem and Motivation

Agents frequently need to discover whether a required credential exists or has
the expected shape, but today the available path is often to read an entire
`.env`, shell-secret, or configuration file. That needlessly places every value
in model context, terminal output, tool traces, and possibly retained logs even
when the task only required one key name, a presence check, or a syntax check.

General redaction after output is not a sufficient control. Exact-string
masking can miss transformed values, and reversible "scrambling" or Base64 is
still disclosure. External systems consistently separate listing from value
retrieval, favor narrowly scoped injection over plaintext export, and audit
secret access without recording values. Sandbox needs the same least-privilege
interaction model across its CLI, MCP server, and agent guidance.

The feature is a least-disclosure secret broker. It must let an agent answer the
common questions with the least disclosure: which keys exist, whether one is
present or empty, whether its structure matches a known profile, and only when
still necessary, a tightly bounded masked preview. When a trusted command needs
the credential, the broker should pass only that selected value to one bounded
child process without changing the parent environment or printing it. A full
single-secret reveal remains an explicit, human-confirmed emergency path rather
than an inspection mode.

Agents also need to create or replace one selected secret without first reading
the file or any existing value. Today, an agent may inspect the entire file just
to preserve its other entries before editing one assignment. The broker must
support a targeted write in which it handles preservation internally and returns
only non-secret update status.

## Users and Desired Outcomes

- **Agent session**: Discover and check only the credential needed for the
  current task without loading unrelated values into context.
- **Developer or operator**: Let an agent diagnose secret configuration while
  retaining control over sources and higher-disclosure inspection modes.
- **Automation client**: Receive stable structured inspection and use results
  whose fields cannot accidentally contain plaintext secret material.
- **Security reviewer**: Distinguish inventory, structural validation, masked
  disclosure, live credential use, and plaintext reveal as separate risk
  levels with truthful evidence about which occurred.
- **Secret owner**: Enter or transfer a replacement value through a channel that
  does not place it in command arguments, model context, or ordinary output,
  while preserving every unrelated entry in the selected source.

## Goals

- Make key-only inventory the default and lowest-disclosure behavior.
- Allow bounded multi-key selection only for key inventory and safe metadata;
  masking, validation, use, write, and reveal each require exactly one key so
  unrelated entries are never returned or consumed.
- Report safe metadata and structural validation results without returning the
  characters being checked.
- Offer an explicit, policy-bounded masked preview for cases where validation
  results are insufficient, while preventing cumulative reconstruction.
- Provide materially equivalent safety and structured results through the CLI
  and MCP, plus an agent skill that requires the least-disclosure sequence.
- Parse secret-bearing sources as inert data, never as executable shell or
  templating input.
- Keep values, masked previews, source snippets, and secret-bearing errors out
  of audit records and diagnostic logs.
- Fail closed for unsupported formats, unsafe files, ambiguous sources, invalid
  key names, and output modes that exceed the disclosure policy.
- Allow a targeted create or replacement without returning the prior or new
  value, without requiring the caller to read the target, and without silently
  overwriting concurrent changes.
- Let a selected credential be used by one explicitly chosen child process
  without exposing it to the parent shell, command arguments, or normal output.
- Provide a separately authorized single-secret reveal when use-without-seeing
  cannot satisfy the task, with prominent warning, fresh human confirmation,
  exact source and key selection, and complete audit evidence.
- Teach agents and operators a concrete least-disclosure workflow with safe and
  unsafe examples, limitations, verification steps, and incident response.

## Non-Goals

- Returning plaintext values by default, in bulk, through inspection modes, in
  MCP responses, or through logs, errors, structured output, and audit records.
- Treating masking as encryption or claiming that a masked preview is
  non-sensitive.
- Executing, sourcing, expanding, or interpolating `.env`, shell, YAML, JSON,
  or configuration content.
- A general-purpose arbitrary-file reader or an agent-selected path bypass for
  the normal filesystem tools.
- Proving that a credential is accepted by an external provider merely because
  its local syntax, length, prefix, or structure is plausible.
- Replacing a dedicated secret manager, credential rotation, or organizational
  access-control system.
- Claiming to be an enforceable security boundary while the same agent retains
  unrestricted direct read access to the underlying files.
- Allowing noninteractive reveal, reveal of multiple keys, arbitrary validation
  URLs, or arbitrary user-supplied validation expressions.
- Accepting a new secret value as a command-line argument, MCP argument, chat
  message, or other channel normally retained in process listings, history,
  transcripts, traces, or logs.
- Live provider validation, parent-process environment mutation, arbitrary
  process-environment inspection, arbitrary YAML fields, and external
  secret-manager integration in v1.

## Product Scenarios

### Scenario 1 — Inventory keys only

- **Starting state**: A registered source contains multiple secret assignments.
- **User action**: An agent requests an inventory without naming a value mode.
- **Expected outcome**: The result contains only bounded, validated key names
  and safe source identity; no value is parsed into output or represented by a
  value-derived preview.

### Scenario 2 — Inspect one required key

- **Starting state**: The agent knows the credential name but not whether it is
  configured correctly.
- **User action**: It requests metadata for that single key.
- **Expected outcome**: The result distinguishes missing, empty, present,
  multiline, structured, and unsupported states, and reports only policy-safe
  length information without returning any characters.

### Scenario 3 — Validate expected shape

- **Starting state**: A credential is expected to follow a reviewed token,
  connection-string, key, certificate, or other structural profile.
- **User action**: The agent requests that named profile for one key.
- **Expected outcome**: Sandbox checks the value internally and reports each
  applicable presence, length, prefix, suffix class, character-set, segment,
  parseability, or expiry check as pass, fail, or not applicable. The overall
  result is explicitly described as syntax or shape evidence, not live
  validity.

### Scenario 4 — Request a masked preview

- **Starting state**: Metadata and structural checks are insufficient to
  explain a configuration mismatch.
- **User action**: The caller explicitly requests a preview for one selected
  key.
- **Expected outcome**: Sandbox identifies a recognized opaque token with a
  reviewed public type prefix and returns that public prefix plus a fixed final
  four-character identifier. An eligible unrecognized opaque token returns only
  its final four characters. Sandbox uses a constant hidden marker, never allows
  arbitrary ranges, and refuses preview for short, low-entropy, password-like,
  multiline, connection-string, JWT, private-key, certificate, binary, or
  structured credential classes. The response labels the final four characters
  as disclosed material.

### Scenario 5 — Repeated preview attempts

- **Starting state**: A caller has already received the maximum permitted
  preview for a key.
- **User action**: It retries with another position, width, mode, client, or
  equivalent request intended to reveal more characters.
- **Expected outcome**: The request cannot increase cumulative disclosure and
  returns a safe refusal without echoing prior preview content.

### Scenario 6 — Unsafe or malformed source

- **Starting state**: A source is a symlink, special file, too large, has unsafe
  permissions or ownership, changes during inspection, or contains commands,
  expansion, invalid encoding, duplicate keys, or unsupported syntax.
- **User action**: An agent requests inspection.
- **Expected outcome**: Sandbox fails closed with a bounded reason code and, when
  safe, a source alias and line number. It never includes the offending line,
  value, resolved external path, or parser excerpt.

### Scenario 7 — MCP is remotely reachable

- **Starting state**: The Sandbox MCP server is running over an authenticated
  remote transport.
- **User action**: A client requests secret inspection.
- **Expected outcome**: The same source, mode, and disclosure policies apply as
  locally; transport access alone never grants a broader preview or plaintext
  capability.

### Scenario 8 — An agent needs to use, not see, a secret

- **Starting state**: Inspection shows that a credential exists and passes its
  expected shape, but a command must consume it.
- **User action**: The agent selects one source, key, and trusted child command
  or registered use profile without requesting the value.
- **Expected outcome**: Sandbox passes only that selected credential to one
  bounded child process, does not mutate the parent environment, and returns the
  exit status plus bounded redacted output. The value is absent from argv,
  normal output, errors, audit evidence, and the agent response. The result
  verifies use through a non-secret success signal rather than echoing the
  credential.

### Scenario 9 — Reveal exactly one secret as a last resort

- **Starting state**: Inspection, masking, and use-without-seeing cannot satisfy
  a task that genuinely requires the human or local agent to receive one value.
- **User action**: At an interactive local terminal, the caller selects one
  registered source and exact key, reviews a high-risk warning, and retypes the
  requested key name as fresh confirmation.
- **Expected outcome**: Sandbox records the reveal intent, rechecks source and
  key policy, then displays only that one value directly on the controlling TTY
  while keeping stdout empty. It does not support JSON, piping, redirection,
  `--yes`, batch reveal,
  MCP reveal, or reusable approval. The warning states that the value can enter
  terminal recording, model context, scrollback, or logs and may require
  rotation if mishandled. The outcome is audited without the value.

### Scenario 10 — Replace one secret without reading the file

- **Starting state**: A registered personal or project environment source
  contains several assignments, and one selected key needs a new value.
- **User action**: The caller selects the source and key, then supplies the new
  value through a hidden local prompt or protected standard-input channel.
- **Expected outcome**: Sandbox internally patches that one assignment,
  preserves unrelated entries and source presentation, and returns only the key,
  source alias, created-or-updated status, opaque revision, and validation
  result. Neither the prior nor new value appears in output.

### Scenario 11 — Concurrent or mistaken write

- **Starting state**: The source changed after the caller inspected it, the key
  was misspelled, or the caller intended creation but the key already exists.
- **User action**: The caller attempts a targeted write with its stated creation
  or replacement expectation and known revision when available.
- **Expected outcome**: Sandbox refuses the conflicting write without modifying
  the file or returning any current value, and explains which non-secret
  expectation failed.

### Scenario 12 — Agent requests an MCP value update

- **Starting state**: An MCP client can call secret tools but no separately
  trusted, one-time input broker is available.
- **User action**: The agent attempts to pass a new value as a tool argument.
- **Expected outcome**: MCP refuses the plaintext argument path and directs the
  human to the hidden-prompt or protected-stdin CLI flow. MCP may update only
  from an approved opaque one-time handle or registered source reference when a
  separate secure broker exists.

## Proposed Product Behavior

- The product calls the operation **inspection** or **masking**, never
  scrambling. Reversible encoding, substitution, or permutation is rejected as
  unsafe.
- The disclosure ladder is explicit and monotonic:
  1. key inventory;
  2. presence and safe metadata;
  3. named structural validation performed internally;
  4. explicitly requested, policy-bounded masked preview;
  5. use without seeing through a bounded child process;
  6. explicitly warned and freshly confirmed one-secret reveal through the
     interactive local CLI only.
- A request with no mode returns key inventory only. Safe inventory and metadata
  may select a bounded set; validation, masking, use, write, and reveal require
  exactly one selected key and never implicitly inspect every value.
- Metadata distinguishes exact facts from withheld facts. Length is bucketed by
  default. Named profiles may check exact length internally without returning
  it; a caller may explicitly request exact length for one selected key, and the
  response marks that field as disclosed metadata rather than an automatic
  consequence of masking.
- Structural validation uses reviewed, named profiles. It may internally check
  exact prefix, suffix class, length, character set, segments, encoding,
  parseability, and embedded expiry, but returns checks and booleans rather than
  the matched characters.
- Structural results use labels such as `syntax_pass` and `live_checked: false`;
  they never report a generic `valid: true` that could be mistaken for provider
  acceptance.
- Masked preview is not a formatting template. For a recognized high-entropy
  opaque token it may show only a reviewed public provider/type prefix and the
  fixed final four characters. For an eligible unrecognized opaque token it may
  show only the final four characters. The hidden middle uses one constant
  marker rather than a length-revealing run of mask characters. Caller-selected
  lengths, offsets, raw prefix characters, suffix guesses, arbitrary regular
  expressions, and repeated-call expansion are unavailable.
- Masked preview is denied for short or low-entropy values, passwords,
  connection strings, JWTs, structured documents, multiline material, PEM,
  private keys, certificates, and binary data. Structured inspection may return
  a bounded key tree whose leaf values are replaced by null, and key material
  may return only safe type and parse evidence.
- Sources are selected by registered aliases. The agent cannot supply an
  arbitrary filesystem path. Each alias resolves only within an approved
  project or Sandbox-owned secret location and is checked again when opened.
- V1 source aliases cover the personal Sandbox secret file and explicitly
  registered project `.env*` files. It does not enumerate the current process
  environment, infer secret fields from mixed YAML, or connect to external
  secret managers.
- Secret files are treated as bounded inert records. Command substitution,
  shell expansion, executable YAML tags, interpolation, external includes, and
  other active constructs are rejected rather than evaluated.
- Inspection is operationally read-only. Invoking it must not regenerate
  runtime files, reconcile an instance, rewrite environment state, or otherwise
  perform the initialization side effects used by ordinary runtime commands.
- Human-readable output and structured output carry the same disclosure. JSON
  is not a path to additional fields or exact values.
- CLI and MCP access are audited as security-relevant inspection events. The
  owner-only audit records caller/session identity when available, source alias,
  selected key names, mode, profile, decision, counts, timestamp, and safe reason
  codes. It records no values, preview text, exact lengths, hashes, or source
  excerpts; access to this name-bearing audit is itself security-sensitive.
  Inspection and use fail closed when their required audit intent cannot be
  recorded, just as writes and reveal do.
- The agent skill requires the sequence: keys first, then one-key metadata or
  structural validation, then masked preview only if necessary, then use without
  seeing when a command needs the value, and single-secret reveal only as the
  final exception. It explicitly forbids raw reads of a registered source and
  never claims that the skill alone prevents filesystem bypass.
- Local use-without-seeing selects one registered source and key plus an exact
  direct-argv child command. It never invokes an implicit shell, changes the
  parent environment, places the value in argv, or exposes unrelated keys. The
  broker registers the selected raw value with output redaction before launch,
  returns a bounded redacted result and exit code, and discards its reference
  after exit where practical without claiming guaranteed memory zeroization.
- A use child starts from a reviewed minimal environment rather than inheriting
  every parent variable. The selected secret is injected only under an approved
  destination name; loader, interpreter, shell, and runtime-control variables
  such as dynamic-library preload or option variables are denied unless a
  registered profile explicitly proves a safe non-environment delivery channel.
- The child command is an intentional secret recipient, so the product cannot
  protect against a malicious command that prints, transforms, persists, or
  exfiltrates the value. Documentation requires a trusted executable, disables
  shell tracing and verbose modes, prohibits environment-dump commands, and
  verifies success through non-secret behavior. Exact-string redaction is only
  defense in depth because encoded or transformed values may evade it.
- MCP never receives a secret value and never offers reveal. A project-scoped
  MCP client may request use only through a registered, reviewed use profile
  that fixes the executable, argument shape, environment destination, output
  bounds, and allowed source/key scope. Arbitrary MCP-supplied commands are not
  available.
- Single-secret reveal is a distinct local CLI operation, not an inspection
  output format. It requires an interactive TTY, exact source and key, a
  prominent warning, and fresh confirmation by retyping the key name. It has no
  JSON, stdin/stdout pipeline, redirection, `--yes`, batch, wildcard, cached
  approval, or MCP form. The value is written only to the controlling TTY and
  stdout remains empty. Reveal intent must be audited before access and outcome
  afterward; audit failure refuses the reveal.
- The reveal warning identifies the selected source alias and key, explains
  that plaintext will become visible and may enter scrollback, screen capture,
  terminal recording, model context, or logs, and recommends use-without-seeing
  instead. The reveal response contains exactly the selected value and no source
  excerpt, adjacent assignment, metadata wrapper, or repeatable masked form.
- The shipped agent skill and operator guide require this least-disclosure
  sequence, using placeholders rather than real credentials:
  1. choose a registered source alias; never pass or open an arbitrary path;
  2. list names only and select the one required key;
  3. request one-key metadata and a reviewed shape profile before disclosing
     characters;
  4. request the fixed-policy mask only when metadata cannot resolve the issue;
  5. when software needs the credential, run one trusted direct-argv child with
     only the selected key injected, tracing and verbose output disabled, and
     verify a non-secret result such as an authenticated status or exit code;
  6. when the value must change, use hidden TTY input, protected stdin,
     registered-reference copy, or broker generation and verify only the
     non-secret revision and shape result;
  7. reveal one value only when the preceding paths cannot satisfy the task,
     after reviewing the warning and retyping the exact key name; and
  8. if a value appears in a transcript, log, screenshot, command argument, or
     other durable channel, stop using it, report the exposure through the
     appropriate private channel, rotate it, and remove only safely removable
     copies without concealing the incident.
- The guide states that a safe-use child must be trusted and narrowly scoped. It
  forbids raw source reads, parent-shell export, secrets in argv, implicit shell
  evaluation, `env`, `printenv`, `set`, shell xtrace, verbose HTTP diagnostics,
  unreviewed upload commands, and commands that serialize their environment. It
  also explains that output masking cannot reliably catch transformed secrets.
- Terms that affect testable safety limits—including bounded output, low-entropy
  classification, protected stdin, minimal child environment, and dangerous
  destination names—must receive exact measurable definitions in the formal
  specification rather than remaining implementation discretion.
- Targeted writes are distinct from inspection. The caller selects exactly one
  registered target and key, states whether creation, replacement, or either is
  intended, and supplies the new value through a hidden local prompt or
  protected standard input. Plaintext command arguments are unavailable.
- A targeted write may read and parse the target inside the trusted broker only
  to preserve unrelated entries. It never returns the file, prior value, new
  value, source excerpt, or masked preview as part of the write response.
- Human-entered local writes use hidden terminal input by default or an explicit
  protected-standard-input mode. Broker-side copy between registered references
  and profile-based generation are allowed because the value never enters the
  agent response. Environment-variable input, plaintext flags, `KEY=value`
  arguments, arbitrary input paths, and shell-command input are unavailable.
- V1 writes single-line literal assignments only. Multiline keys, certificates,
  PEM, private keys, binary values, and structured documents require a future
  source format designed for them rather than ambiguous dotenv rewriting.
- A successful write preserves unrelated keys, comments, ordering, newline
  style, ownership, and restrictive permissions; it replaces the file as one
  atomic outcome. A lock and opaque revision expectation prevent lost updates,
  and a failed write leaves the prior file intact without a plaintext backup
  artifact exposed to the caller.
- Duplicate target assignments, unsafe permissions, links or non-regular files,
  unexpected concurrent revisions, and candidate values that fail the selected
  profile are refused before replacement. New files are owner-only; existing
  files must already meet the approved permission policy rather than being
  silently broadened or normalized.
- MCP inspection follows the same disclosure policy as CLI. MCP does not accept
  a plaintext new-value parameter. Until a separately trusted one-time input
  broker exists, human-supplied writes remain a CLI capability; MCP may only
  refer to an approved opaque handle or registered source-to-target transfer.
- MCP may prepare a short-lived update request and report its non-secret status,
  while the human completes value entry through the local hidden-input CLI. It
  may also request broker-side generation or copy between registered references.
  Request identifiers are one-use coordination references, not bearer authority.

## Constraints and Dependencies

- Repository policy already forbids printing passwords or tokens to stdout,
  commits, comments, memory files, and chat. This feature must preserve that
  stronger rule even when a caller asks for a broader result.
- Sandbox already has a personal secret-file resolver with literal-only parsing
  and environment precedence, and an existing `secrets` CLI namespace used for
  migration. The new behavior must be compatible with current consumers and
  must not change secret resolution as a side effect of inspection.
- New CLI commands and MCP groups must register through their explicit
  manifests/contracts and share a service boundary rather than duplicating
  secret parsing or disclosure policy.
- MCP catalogs are runtime-scoped. Secret inspection must not silently expose
  machine-wide sources merely because a project-scoped server is active.
- Remote MCP transport authentication is not, by itself, permission to inspect
  secrets. A remote or scoped catalog must omit the capability unless an
  explicit inspection authorization policy grants the selected source and mode.
- Secret key names can reveal infrastructure, vendors, environments, or account
  roles. Key inventory is lower risk than values but still scoped and audited.
- Exact-string log masking is a defense in depth measure only; transformed,
  structured, encoded, truncated, or previously emitted values may evade it.
- Every output surface includes errors, exceptions, debug traces, test failure
  diffs, durable job output, and human-readable summaries, not only successful
  JSON responses.
- The tool can reduce accidental disclosure but cannot enforce exclusive access
  while agents retain arbitrary filesystem commands against the same source.
- V1 is a safer convenience and policy workflow, not a direct-read enforcement
  layer. Enforcing exclusive broker use requires a separate filesystem or tool
  policy that denies agents direct access to registered sources.
- Remote secret managers and live provider checks introduce separate
  authorization and network boundaries and remain outside v1. Local bounded
  use and registered MCP use profiles are included, but arbitrary remote command
  execution is not.
- Preserving unrelated assignments means the broker necessarily processes the
  target internally; "write without read" means the caller receives no read
  capability or target contents, not that the broker blindly appends text.
- Local CLI and MCP processes run with the user's filesystem authority, so v1
  does not create a genuine OS-level patch-without-read permission boundary.
  Such enforcement requires a separately authorized daemon/identity or external
  secret store and remains outside this feature.
- A safe local replacement may briefly create a second owner-only plaintext
  inode and can leave a temporary artifact after a crash. The product must clean
  safe owned remnants but cannot claim plaintext-file atomic replacement offers
  encrypted-store guarantees.
- The mutation attempt must be recorded in the non-secret audit channel before
  replacement, followed by its outcome. If required audit evidence cannot be
  recorded, the write fails without modifying the source.

## Decisions

| Decision | Choice | Rationale | Confirmed by |
|----------|--------|-----------|--------------|
| Default result | Key names only | Most agent tasks require discovery rather than values | User request, 2026-08-11 |
| Value terminology | Masked inspection, not scrambling | Reversible scrambling and Base64 do not provide confidentiality | Research plus existing secret policy |
| Plaintext output | One selected value is displayed only through the controlling TTY by an interactive, warned, freshly confirmed local CLI reveal; stdout stays empty, and reveal is unavailable through inspection, JSON, batch, pipes, or MCP | The user explicitly requires a single-secret read, while separating it from ordinary inspection limits accidental disclosure and preserves the repository ban on echoing secrets to stdout | User, 2026-08-11 plus repository policy |
| Validation semantics | Shape/syntax checks are distinct from live provider validity | Prefix and length can be checked internally without claiming the credential works | Research evidence |
| Surface parity | CLI-first inspection with MCP parity and an agent skill; human-supplied write input stays out of MCP | Requested surfaces share inspection policy while value entry respects channel risk | User and repository policy |
| Source selection | Registered aliases only; no arbitrary agent-supplied path | Prevent a safe inspector from becoming a general file exfiltration tool | Security constraint plus accepted v1 scope |
| Parsing | Inert, bounded, literal data only | Sourcing or expansion would execute untrusted content and can leak adjacent state | Existing Sandbox parser policy |
| Audit content | Owner-only audit includes selected key names and operation metadata, but no values, previews, exact lengths, hashes, or excerpts | Secret access needs actionable accountability without creating a value-derived disclosure channel | Research evidence, 2026-08-11 |
| V1 source scope | Personal Sandbox secret file plus explicitly registered project `.env*` sources | Covers the immediate local need without arbitrary paths, mixed YAML inference, process-wide enumeration, or external integrations | User accepted recommendation, 2026-08-11 |
| V1 enforcement | Safer broker workflow with an explicit non-enforcement limitation | A CLI/MCP/skill cannot prevent direct reads while agents retain general filesystem tools | User accepted recommendation, 2026-08-11 |
| V1 validity | Offline metadata and reviewed shape profiles only | A child process may consume a selected secret, but provider acceptance is not generalized into an inspector validity claim | User accepted recommendation, 2026-08-11 |
| Mask availability | Explicit fixed-policy masked mode in CLI and MCP | The user confirmed masking is required for agent diagnostics | User, 2026-08-11 |
| Mask identity | Reviewed public provider/type prefix plus fixed `last4`; unrecognized eligible opaque values expose only `last4` | Comparable systems favor full concealment; Datadog provides the strongest fixed partial-identifier precedent, while public prefixes add type evidence without disclosing unique prefix entropy | Research evidence, 2026-08-11 |
| Protected classes | No partial preview for passwords, short/low-entropy values, connection strings, JWTs, structured data, multiline material, PEM/private keys, certificates, or binary | Prefix/suffix disclosure is disproportionately risky or misleading for these classes | Research evidence, 2026-08-11 |
| Write-without-read | Targeted one-key create/replace that returns status only and preserves unrelated content internally | Mirrors Vault patch, GitHub/Doppler hidden-input set, and SOPS targeted set patterns | User requirement plus research, 2026-08-11 |
| Secret input | Hidden TTY or protected stdin locally; never CLI arguments or ordinary MCP values | Arguments, histories, process listings, and tool transcripts are durable disclosure channels | Research and repository policy |
| MCP writes | No human-supplied plaintext value until a separate one-time broker exists | MCP tool arguments normally enter model/tool transcripts | User accepted recommended scope, 2026-08-11 |
| Write input modes | Hidden TTY, protected stdin, broker-side registered-reference copy, or internal generation | Keeps values out of argv, process listings, model/tool transcripts, and ordinary output | Research evidence, 2026-08-11 |
| Write format scope | Single-line literal assignments only in v1 | Existing dotenv/personal-secret parsing is literal and multiline rewriting introduces ambiguity and larger leakage surfaces | User accepted recommended scope, 2026-08-11 |
| Write conflicts | Duplicate keys, unsafe targets, revision drift, intent mismatches, and failed profile checks refuse before replacement | A targeted safety broker must not guess which value to replace or silently lose concurrent edits | Research evidence |
| Use without seeing | Local CLI passes one selected key to one exact direct-argv child; MCP permits registered reviewed use profiles only | Mature tools inject secrets into bounded child processes; keeping MCP profile-based avoids turning secret access into arbitrary remote execution | User requirement plus research, 2026-08-11 |
| Reveal authorization | CLI-only, one key, interactive TTY, warning plus retyped key, no reusable bypass | Full reveal is qualitatively different from masking and must require fresh human intent at the moment of disclosure | User requirement plus security constraint, 2026-08-11 |
| Reveal audit | Record intent before access and outcome after, never the value; fail closed if audit is unavailable | A high-risk disclosure needs accountable evidence without creating another secret store | Security constraint, 2026-08-11 |
| Agent guidance | Ship a detailed least-disclosure runbook with placeholder-only safe/unsafe examples and incident response | The safest feature path must be easier and clearer than reading the raw file | User requirement, 2026-08-11 |

## Open Questions

No blocking product questions remain for specification. Command names, profile
schema, exact warning copy, audit storage, and platform-specific TTY mechanics
belong to specification and planning so long as they preserve the decisions and
acceptance boundaries in this draft.

## Acceptance Outcomes

- A default inspection of a registered source returns its eligible key names
  and no value-derived content.
- Selecting one key returns only that key's safe state and does not parse or
  serialize unrelated values into the result.
- A reviewed profile can determine whether a value has an expected exact
  length, prefix, suffix class, character set, structure, and parseability while
  returning none of those characters.
- Every shape-only result clearly states that no live provider check occurred.
- A masked preview never reveals more than its fixed disclosure budget, never
  reveals arbitrary ranges or raw prefix characters, refuses protected value
  classes, uses a constant hidden marker, and cannot be expanded by repeated
  requests.
- A recognized eligible opaque token may show its reviewed public type prefix
  and fixed final four characters; an eligible unrecognized opaque token may
  show only its final four characters. No other secret character is returned.
- No inspection, structured-output, MCP, error, audit, debug, or test-failure
  path returns a complete secret or secret-bearing source excerpt; only the
  dedicated confirmed local reveal path may emit its one selected value.
- A source with commands, expansion, unsafe file type, unsafe ownership or
  permissions, excessive size, invalid encoding, or a replacement race fails
  closed before returning entries.
- A project-scoped MCP server cannot inspect an unregistered machine-wide or
  other-project source.
- Equivalent CLI and MCP requests produce materially equivalent fields,
  omissions, validation semantics, and refusal reasons.
- The agent skill directs callers through key inventory, one-key metadata or
  validation, optional masked preview, use without seeing, and only then an
  explicitly warned single-secret reveal; it never directs an agent to read a
  raw secret file.
- A local use operation passes only the selected value to one explicit
  direct-argv child process, leaves the parent environment unchanged, supplies
  no secret through argv, starts from a reviewed minimal child environment,
  refuses dangerous environment destinations, and returns only an exit status
  plus bounded redacted output.
- An MCP use request can invoke only a registered reviewed profile and cannot
  supply an arbitrary command, receive the value, or broaden the profile's
  approved source/key scope.
- The shipped skill and documentation provide a complete placeholder-only
  workflow: select a source alias, list keys, inspect or validate one key, prefer
  bounded use, verify via a non-secret result, update through hidden input when
  needed, reveal only as a last resort, and rotate/report accidental exposure.
- Documentation identifies unsafe actions including raw file reads, secrets in
  argv or chat, parent-shell export, `env` or `printenv`, shell tracing, verbose
  HTTP output, redirection, transcript capture, and pasting values into durable
  artifacts.
- A reveal of one selected key succeeds only from an interactive local TTY after
  the warning and exact key-name confirmation, writes the value only to the
  controlling TTY while stdout remains empty; JSON, piping, redirection,
  noninteractive approval, multiple keys, wildcards, cached confirmation, and
  MCP reveal are refused.
- Reveal intent and outcome are auditable without storing the value, and reveal
  fails before disclosure when the required audit cannot be recorded.
- Inspection and use also fail before secret processing or child launch when
  their required audit intent cannot be recorded.
- Audit evidence can show that an inspection was attempted, its risk mode, its
  policy result, and its success or failure without recording a value or masked
  preview.
- A targeted create or replacement can succeed without the caller receiving the
  target contents, prior value, new value, or source excerpt, and the response
  reports only non-secret status and an opaque revision.
- Secret input in a command argument or ordinary MCP parameter is rejected;
  hidden prompt and protected standard input are the supported human input
  channels.
- A successful targeted write preserves every unrelated assignment and the
  source's comments, ordering, newline style, ownership, and restrictive mode;
  a conflict or failure leaves the prior source intact.
- A stale revision, creation/replacement expectation mismatch, duplicate key,
  unsafe target, or concurrent change fails without overwriting either value.
- MCP can prepare and observe an update without receiving its value, and can
  perform approved registered-reference copy or internal generation; no MCP
  response contains value-derived output.
- V1 refuses multiline, binary, private-key, certificate, and structured-value
  writes rather than rewriting them ambiguously.
- A write does not proceed when its required pre-mutation audit evidence cannot
  be recorded.
- Documentation states plainly that the feature reduces accidental disclosure
  but is not an exclusive security boundary unless direct source access is also
  restricted.

## Risks and Assumptions

- **Risk**: Secret names, exact lengths, prefixes, suffixes, and format classes
  can each leak useful information even when the full value is hidden.
- **Risk**: Repeated previews across processes, transports, tasks, or source
  aliases could reconstruct a value unless disclosure accounting is shared at
  the correct scope.
- **Risk**: A malicious or compromised source can exploit parser ambiguity,
  terminal control characters, replacement races, or error handling to smuggle
  content into output.
- **Risk**: A provider-specific format can change, causing a structurally valid
  credential to be rejected or an invalid one to appear plausible.
- **Risk**: A remote MCP bearer with broad machine access could turn even key
  inventory into infrastructure reconnaissance.
- **Risk**: Users may interpret a masked string as safe to paste into issues,
  logs, or chats even though the visible characters remain secret material.
- **Risk**: A one-key local update can still lose data if concurrent changes are
  not detected or if the broker rewrites comments, ordering, permissions, or
  newline semantics incorrectly.
- **Risk**: Standard input is safer than command arguments but can still be
  captured by an untrusted parent process, shell pipeline, terminal recorder, or
  debug wrapper; the product cannot label every stdin source trustworthy.
- **Risk**: A child command that legitimately receives a secret can print,
  transform, persist, forward, or leak it; exact-match output redaction cannot
  make an untrusted command safe.
- **Risk**: Interactive reveal intentionally crosses the ordinary no-plaintext
  boundary and can expose the value through terminal scrollback, screen capture,
  recording, accessibility tools, model context, or subsequent copy/paste.
- **Risk**: A recovery copy containing the old value would create another secret
  store and retention problem; failure safety must not silently proliferate
  plaintext backups.
- **Risk**: Atomic replacement of a plaintext source can briefly create a second
  owner-only inode and a crash can leave a temporary artifact even when no
  user-visible backup is created.
- **Assumption**: Most diagnostic needs can be satisfied by key presence,
  metadata, and internal structural checks without exposing characters.
- **Assumption**: A separately reviewed source registry can describe which
  secret-bearing files and fields are eligible without changing existing secret
  resolution precedence.
- **Assumption**: Most tasks that require a credential can use one bounded child
  process and verify a non-secret outcome without revealing the value.
- **Assumption**: Fresh interactive confirmation and complete auditing make a
  one-key reveal safer and more accountable than directing the caller to read
  the entire source, though they cannot make plaintext disclosure harmless.

## Readiness for Specification

- [x] Problem, affected users, and desired outcomes are explicit.
- [x] Goals and non-goals bound the product scope.
- [x] Primary and negative scenarios are covered.
- [x] Material constraints, dependencies, and risks are recorded.
- [x] Consequential choices are confirmed rather than inferred.
- [x] Acceptance outcomes are measurable and implementation-independent.
- [x] No blocking open questions remain.
- [x] No implementation plan, task list, contracts, or code changes are included.
- [x] The latest independent Sol High validation verdict is `PASS`.

**Readiness**: `READY FOR SPECKIT`

<!-- Set to READY FOR SPECKIT only when every readiness item passes. -->
