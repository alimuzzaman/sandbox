# Feature Specification: Secrets unset and remote copy

**Feature Branch**: `feat/secrets-unset-copy`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "Two new Sandbox secrets-broker CLI commands (owner request 2026-11-10). (1) `sb secrets unset --source ALIAS KEY`: remove exactly one key from a registered local file source, mirroring `sb secrets set`: local CLI only, refuses provider sources, validates the key, honours an optional revision precondition, audited, never prints a value. A missing key is a clear `key_missing` refusal. (2) `sb secrets copy --source ALIAS --key KEY --remote NAME --target-source ALIAS [--as NEWKEY]`: copy one value from a local source into a Sandbox secret source on a registered remote, optionally renaming it. The value travels only on stdin of the remote's own `sb secrets set ... --stdin` over the remote's existing transport; never in argv, env, logs, output, audit records or temp files. Idempotent: an identical value is reported `already_current` without writing; otherwise create/replace under the same intent semantics as `set`. Audited on both sides. Security-control change flagged for human review."

## Clarifications

### Session 2026-11-10

These answers were chosen by the implementing agent under the owner's brief (no
owner reply was requested); the owner can overrule any of them at review.

- Q: Which context resolves the target source alias on the remote? → A: The remote's
  own Sandbox home context by default (its `personal` source and user-globally
  registered sources); an optional remote project directory selects a project's
  registered sources instead.
- Q: Create-only intent when the remote already holds an identical value? → A:
  `already_current` (the requested end state already holds, so reruns converge); a
  different existing value still refuses with `intent_conflict`.
- Q: Must the remote be provisioned? → A: Yes; a registered but unprovisioned remote
  is refused before the local value is read.
- Q: What happens when the remote Sandbox is too old for the equality-check option?
  → A: Refuse with `remote_unsupported`; never fall back to an unconditional write.
- Q: Should a timed-out copy be retried automatically? → A: No. A timeout is
  ambiguous; it is reported as `remote_timeout` and the operator reruns, which
  converges.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Remove one stale secret from a local source (Priority: P1)

An operator has a registered local dotenv secret source (for example `personal`)
that holds a key they no longer need. Today the only ways to drop it are editing the
file by hand (which exposes every value on screen) or the ownership cleanup flow
(which needs a migrated profile and approval). The operator runs one command naming
the source and the key; the key's line is removed, every other line stays
byte-identical, the result names the key and the new opaque revision, and no value
is ever displayed.

**Why this priority**: It is the smallest useful slice, local only, and removes the
current incentive to open secret files in an editor.

**Independent Test**: Create a temporary registered dotenv source with three keys,
run unset on the middle key, then confirm with key listing that only two keys remain,
the other two lines are byte-identical, the audit journal holds an intent and a
succeeded outcome for operation `unset`, and no output contains any value.

**Acceptance Scenarios**:

1. **Given** a registered local dotenv source holding `KEY_A`, `KEY_B`, `KEY_C`,
   **When** the operator unsets `KEY_B`, **Then** the source holds `KEY_A` and `KEY_C`
   with their original bytes, the command reports `removed` with a new opaque
   revision, and the audit journal records the request and its success.
2. **Given** the same source after `KEY_B` was removed, **When** the operator runs
   the same unset again, **Then** the command refuses with `key_missing`, the file is
   unchanged, and the audit journal records a refused outcome.
3. **Given** a revision captured before another writer changed the source, **When**
   the operator unsets with that stale revision as a precondition, **Then** the
   command refuses with `revision_conflict` and the file is unchanged.
4. **Given** a registered provider (Infisical) source, **When** the operator runs
   unset against it, **Then** the command refuses before any read or audit write.
5. **Given** an invalid key name, **When** the operator runs unset, **Then** the
   command refuses with the same key-validation error `set` gives.

---

### User Story 2 - Copy one secret to a remote host under a new name (Priority: P2)

An operator keeps a token in a local source under one name and a remote Sandbox
host needs the same value under another name in one of its own registered sources
(for example `EXAMPLE_CF_API_TOKEN` locally becomes `CLOUDFLARE_API_TOKEN` in a
remote source). The operator runs one command naming the local source and key, the
registered remote, the remote target source and, optionally, the new key name. The
value reaches the remote host's own secret-set command through that command's input
stream only; the operator sees only whether the remote key was created, updated or
already current.

**Why this priority**: It replaces a manual multi-step copy (reveal or copy-paste a
value into an SSH session) that discloses the value to the terminal and shell
history. It depends on the remote host running a version that supports the
comparison step, so it ships after the local-only slice.

**Independent Test**: With a local temporary source and a stubbed remote transport
that records what it receives, run copy and confirm the value appears only on the
transport's input stream, never in the command line, environment, output or either
audit journal, and that the result maps the remote's answer to `created`,
`updated` or `already_current`.

**Acceptance Scenarios**:

1. **Given** a local source holding `KEY_X` and a remote target source without
   `KEY_Y`, **When** the operator copies `KEY_X` to the remote as `KEY_Y`, **Then**
   the remote source gains `KEY_Y` with the same value and the command reports
   `created`.
2. **Given** the remote already holds `KEY_Y` with an identical value, **When** the
   operator runs the same copy again, **Then** the remote source is not rewritten and
   the command reports `already_current`.
3. **Given** the remote holds `KEY_Y` with a different value, **When** the operator
   copies without an intent flag, **Then** the remote value is replaced and the
   command reports `updated`; **When** the operator copies with create-only intent,
   **Then** the command refuses with `intent_conflict`.
4. **Given** the remote target source does not contain `KEY_Y`, **When** the
   operator copies with replace-only intent, **Then** the command refuses with
   `intent_conflict` and nothing is written.
5. **Given** an unregistered remote name, **When** the operator runs copy, **Then**
   the command refuses before reading the local value.
6. **Given** a local key that does not exist, **When** the operator runs copy,
   **Then** the command refuses with `key_missing` and nothing is sent.
7. **Given** any failure on the remote (unknown target source, refused write,
   unreachable host, older remote version), **When** copy runs, **Then** the
   operator receives a bounded error code with no value and no raw remote output.

---

### Edge Cases

- The local value contains a line break or is larger than the single-value limit:
  copy refuses locally before connecting, because the remote input channel accepts
  exactly one line.
- The key appears more than once in the local source: the source is refused as
  unparseable, as it is for every other operation.
- The source file changes between the operator's read and the removal (another
  writer, editor save): unset refuses with a conflict and leaves the file as the other
  writer left it.
- The source's filesystem cannot provide trustworthy change metadata (coarse
  timestamps, broad permissions, hard links): unset refuses rather than removing with
  weaker guarantees.
- Another process holds the source update lock: unset refuses promptly instead of
  waiting.
- Source format is not dotenv (JSON, YAML): unset refuses as unsupported, as set does.
- Removing the last key leaves an empty or comment-only file, which stays registered.
- Copy to the same remote where the transport drops mid-stream: the remote's own
  set either completed atomically or did not; a rerun converges.
- The remote runs an older Sandbox that does not support the comparison step: copy
  refuses with a clear "remote does not support this" error and writes nothing,
  rather than silently overwriting.

## Requirements *(mandatory)*

### Functional Requirements

**Unset**

- **FR-001**: The system MUST provide an operator command that removes exactly one
  named key from one registered local file source.
- **FR-002**: Unset MUST be available only from the local CLI surface, never from MCP
  or a remote-control surface.
- **FR-003**: Unset MUST refuse provider sources and non-dotenv file formats before
  changing anything.
- **FR-004**: Unset MUST validate the key name with the same rules `set` applies.
- **FR-005**: Unset MUST accept an optional opaque revision precondition identical in
  meaning to `set`'s, and refuse with `revision_conflict` when it does not match.
- **FR-006**: Unset MUST refuse with `key_missing` when the key is absent and MUST
  NOT report success in that case.
- **FR-007**: Unset MUST remove the key atomically, leave every other byte of the
  source unchanged, preserve the file's ownership and permissions, and refuse if the
  source changed after it was read.
- **FR-008**: Unset MUST write an audit intent before reading the source and a
  matching outcome (succeeded, refused or failed) after, under operation `unset`.
- **FR-009**: Unset MUST never print, log or audit any secret value, and its output
  MUST contain only the source alias, key name, action and opaque revision.

**Copy**

- **FR-010**: The system MUST provide an operator command that copies one key's value
  from one registered local file source to one registered Sandbox secret source on
  one registered remote, with an optional new key name (default: the same name).
- **FR-011**: Copy MUST be available only from the local CLI surface.
- **FR-012**: Copy MUST refuse provider sources as the local source, MUST validate the
  local key, the target alias and the target key name before any read, and MUST
  refuse an unregistered remote before reading the value.
- **FR-013**: The value MUST reach the remote only through the input stream of the
  remote host's own secret-set command, over the remote's existing registered
  transport. It MUST NOT appear in any command line, environment variable, temporary
  file, log, console output or audit record on either host.
- **FR-014**: Copy MUST support the same intent choices as set: create-only,
  replace-only, or either (default).
- **FR-015**: When the remote already holds the target key with an identical value,
  the remote MUST NOT rewrite its source and copy MUST report `already_current`. The
  equality check MUST happen on the remote host and MUST NOT return the stored value
  or any value-derived digest to the controller.
- **FR-016**: Otherwise copy MUST report `created` or `updated` as the remote
  reports it, together with the remote's opaque revision.
- **FR-017**: The controller MUST record an audit intent and outcome under operation
  `copy`, naming the local source and key and a non-secret destination descriptor
  (remote name, target source, target key). The remote host MUST record its own set
  audit through its existing broker.
- **FR-018**: Copy failures MUST be reported as bounded error codes; raw remote
  output and error streams MUST NOT be shown.
- **FR-019**: A remote that does not support the equality-check option MUST cause a
  refusal, not an unconditional overwrite.
- **FR-020**: Rerunning a completed copy with the same arguments MUST converge
  (`already_current`) and change nothing, including under create-only intent.
- **FR-020a**: The remote target source alias MUST resolve in the remote's own
  Sandbox home context unless the operator names a remote project directory.
- **FR-020b**: Copy MUST refuse an unprovisioned remote before reading the value, and
  MUST NOT retry automatically after a timeout.

**Set (supporting change)**

- **FR-021**: `set` MUST gain an opt-in option that skips the write and reports an
  unchanged result when the key already holds the identical value. Without the
  option, `set` behaviour is unchanged.

### Key Entities

- **Local file source**: a registered alias pointing to an owner-only dotenv file;
  has an opaque revision derived from its contents.
- **Remote**: a registered remote host record with its existing transport.
- **Destination descriptor**: remote name, target source alias and target key name;
  non-secret, recorded in the controller audit.
- **Audit record**: append-only intent and outcome events with names, decision,
  reason code and revision; never values.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator removes one key from a local source with one command and no
  value displayed, in under 5 seconds on a local source.
- **SC-002**: After unset, 100% of the other lines in the source are byte-identical to
  before.
- **SC-003**: In tests that plant a unique canary value, the canary appears in 0 of:
  command lines, environment, console output, error output and audit journals on
  either side; it appears only in the remote input stream and the remote source.
- **SC-004**: Running the same copy twice in a row reports `already_current` on the
  second run and leaves the remote source's revision unchanged.
- **SC-005**: Every refusal path named in the acceptance scenarios produces a named
  error code and leaves both sources unchanged.

## Assumptions

- The flag mirroring `set`'s revision precondition is `--if-revision`, the name `set`
  and `organize` already use (the owner's request called it "expected revision").
- "Safe to rerun" for unset means a rerun refuses with `key_missing` and changes
  nothing; it does not mean a rerun reports success.
- The remote runs the same Sandbox CLI with the new set option available; copy needs
  the remote's checkout or runtime updated to a version that includes this feature.
- The target source on the remote is a dotenv file source; provider targets are
  refused by the remote's own set.
- Values are single-line, as `set --stdin` already requires.
- MCP exposure of either command is out of scope.
- Copying between two local sources is already served by `set --from-ref` and is out
  of scope.
