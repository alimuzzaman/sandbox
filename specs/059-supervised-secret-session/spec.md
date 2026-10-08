# Feature Specification: Supervised Long-Running Secret Session

**Feature Branch**: `059-supervised-secret-session`

**Created**: 2026-10-08

**Status**: Draft

**Input**: `specs/059-supervised-secret-session/prd.md` (READY FOR SPECKIT; feedback 2cfab06f). Amends spec 041 FR-036 and the display clause of FR-037 for session mode only.

## Clarifications

### Session 2026-10-08

Product decisions were delegated by the user to an independent reviewer. Each
answer below keeps the PRD's broker-enforced bound intact, grounded in the
current `secrets run` and `secrets reveal` behavior. The three answers first
marked provisional were confirmed or revised by a Fable reviewer on 2026-10-08.

- Q: What happens if the developer suspends the broker from the keyboard or starts the session as a background job? → A: Keyboard suspend is ignored for the whole session, so the broker never stops enforcing the lifetime; a start from a process that is not the terminal's foreground job is refused with `tty_required` before any secret is read.
- Q: Does time the machine spends asleep count toward the lifetime? → A: Yes. The lifetime is wall-clock time; the session ends at the stated end time even if the machine slept, and ends on wake if that time has already passed.
- Q: How do terminal loss without a hangup signal, and other catchable termination requests to the broker, end a session? → A: A failed write to the terminal ends the session with `hangup`. A catchable termination request other than interrupt or hangup ends it with `interrupted` (confirmed by Fable, 2026-10-08). The four end reasons stay the complete set.
- Q: How are partial lines and terminal control characters in the child's output shown? → A: Output is redacted fail-closed: text after the last whitespace is held until the line completes or the session ends, with no idle flush. Complete SGR colour and style sequences pass through; every other control character or escape sequence is removed whole (revised by Fable, 2026-10-08: ordinary use strips only the escape byte, which would leave visible junk such as `[31m` in a long-running stream).
- Q: What exit status does the broker return when the session did not end by the child's own exit? → A: `lifetime_expired` exits 0 (a planned end; ordinary use also exits 0 on its timeout), `interrupted` exits 130 and `hangup` exits 129, following the shell convention for those signals (confirmed by Fable, 2026-10-08).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a dev server with a brokered secret for a working session (Priority: P1)

A local developer, at an interactive terminal, starts a dev server (for example
a local web framework's development server) in session mode with one or more
brokered secrets. The server keeps running well past 30 minutes, its redacted
output appears in the terminal as it is produced, and the session states up
front how long it may live and when it will end. No secret value is written to
disk or shown on screen by Sandbox.

**Why this priority**: This is the whole reason for the feature. Without it the
developer either relaunches the server every 30 minutes or writes the key into
a project `.env` file, which is the higher-disclosure outcome the broker exists
to prevent.

**Independent Test**: From a terminal, start session mode for a trusted fixture
that prints a heartbeat line every few seconds (and once prints the selected
value) and runs past 30 minutes; confirm the heartbeat lines appear live, the
printed value appears only as a redaction marker, the start banner names the
lifetime and end time, and the child is still running at minute 31.

**Acceptance Scenarios**:

1. **Given** a registered source and key and an interactive terminal, **When** the developer starts session mode for a direct command with no explicit lifetime, **Then** the child runs with each selected secret in its destination variable, and the session reports a lifetime of 8 hours and the local time at which it will end.
2. **Given** a running session, **When** the child writes output, **Then** the redacted output appears in the terminal while the child is still running, not only after it exits.
3. **Given** a running session, **When** the child prints the exact selected value, **Then** the terminal shows a redaction marker in its place and never the value.
4. **Given** a running session started 31 minutes ago with the default lifetime, **When** the developer checks the server, **Then** it is still running and serving.
5. **Given** a session whose child exits by itself, **When** it exits, **Then** the session ends with reason `child_exited`, any remaining members of the child's process group are ended, and the broker exits with the child's exit status mapped as ordinary secret use maps it.

---

### User Story 2 - The session always ends within its bound (Priority: P1)

The secret owner relies on the broker, not the child, to bound how long a
secret recipient lives. A session ends when its lifetime expires, when the
developer presses Ctrl-C, or when the terminal is closed, and in each case the
child's whole process group is gone within 5 seconds. The end reason is shown
and audited without any value-derived data.

**Why this priority**: The 30-minute cap of ordinary use is a lease. Removing it
is only acceptable if a different, broker-enforced bound replaces it; this
story is that bound.

**Independent Test**: Start sessions with a short explicit lifetime for a
fixture that spawns a grandchild and ignores nothing; end one by lifetime
expiry, one by Ctrl-C and one by closing its terminal; confirm for each that the
process group is gone within 5 seconds, the reported and audited reason matches,
and no audit record contains value-derived data.

**Acceptance Scenarios**:

1. **Given** a session with a short explicit lifetime, **When** the lifetime expires, **Then** the session ends with reason `lifetime_expired` and the child's process group, including grandchildren, is gone within 5 seconds of expiry.
2. **Given** a running session, **When** the developer presses Ctrl-C, **Then** the session ends with reason `interrupted` and the child's process group is gone within 5 seconds.
3. **Given** a running session, **When** the terminal that started it is closed, **Then** the session ends with reason `hangup` and the child's process group is gone within 5 seconds.
4. **Given** any ended session, **When** its audit trail is read, **Then** it holds an intent record before any value was read and an outcome record carrying the end reason, and neither contains a value, preview, length, hash or child output.
5. **Given** a child that ignores the polite termination request, **When** the session ends for any reason, **Then** the child's process group is forcibly ended so that it is still gone within 5 seconds.

---

### User Story 3 - Unsafe or invalid starts are refused before any secret is read (Priority: P2)

Session mode refuses to start in any setting where its bounds cannot hold or its
arguments are wrong, and it does so before any secret value is read and without
delivering a value to any child.

**Why this priority**: The interrupt and hangup bounds only exist when a person
is at an interactive terminal. Refusing everywhere else keeps agents, CI,
durable jobs and MCP on bounded ordinary use.

**Independent Test**: Attempt session starts with output redirected to a file,
from a context with no controlling terminal, with lifetimes of 0, 1.5 and
43,201 seconds, with both a session lifetime and an ordinary timeout, with a
denied destination and with an unknown source; confirm each is refused with a
stable reason code and that the source was never read and no child launched.

**Acceptance Scenarios**:

1. **Given** standard output that is not a terminal, or no controlling terminal that can be opened, **When** session mode is requested, **Then** it is refused with `tty_required` before any secret is read.
2. **Given** a lifetime below 1 second, not a whole number of seconds, or above 12 hours, **When** session mode is requested, **Then** it is refused before any secret is read.
3. **Given** both a session lifetime and an ordinary use timeout, **When** the request is made, **Then** it is refused before any secret is read.
4. **Given** a destination on the deny list or an unknown source alias, **When** session mode is requested, **Then** it is refused before any secret is read.
5. **Given** an unknown key in a known source, **When** session mode is requested, **Then** it is refused as ordinary use refuses it, and no value is delivered to any child.
6. **Given** a request through MCP, a durable job or a remote execution path, **When** session mode is requested, **Then** it is unavailable or refused, and no value is read.

---

### User Story 4 - Guidance keeps agents on bounded use (Priority: P3)

The shipped secret guidance and the command help describe session mode as an
operator-run, foreground, terminal-only mode, and tell agents to keep using
bounded ordinary use.

**Why this priority**: The mode is safe for a person at a terminal; it is not a
tool for agents, and the guidance is what keeps agents from reaching for it.

**Independent Test**: Read the shipped skill, the operator documentation and the
command help; confirm each marks session mode as operator-only, lists its bound
and end reasons, repeats the intentional-recipient warning, and contains no
instruction for an agent to start a session.

**Acceptance Scenarios**:

1. **Given** the shipped agent skill, **When** an agent looks for a way to run a long-lived child with a secret, **Then** the skill directs it to ask the human to run session mode in their own terminal and to use bounded ordinary use itself.
2. **Given** the operator documentation, **When** an operator reads the session section, **Then** it states the default and maximum lifetime, the four end reasons, the terminal requirement, and that the child can still print or persist the value.

### Edge Cases

- The developer suspends the broker from the keyboard while a session runs: the suspend is ignored and the session keeps running under its lifetime.
- The developer starts the session as a background job of the shell: refused with `tty_required` before any secret is read.
- The machine sleeps during a session: sleep counts toward the lifetime; if the end time passed during sleep, the session ends on wake with `lifetime_expired`.
- The terminal disappears and writes to it fail before or without a hangup signal: the session ends with `hangup`.
- The broker receives a catchable termination request other than interrupt or hangup: the session ends with `interrupted`.
- An interrupt or hangup arrives after secrets are read but before the child has started: the session ends with that reason, and the child is ended within the same 5-second bound if it was started.
- The child exits while a grandchild keeps running and holds the output stream open: the session ends with `child_exited` and the grandchild is ended with the rest of the process group.
- The child prints a partial line with no trailing newline (for example a prompt) and then goes quiet: the partial text is held until the line completes or the session ends.
- The child emits terminal control sequences: colour codes are kept; all other control sequences are removed whole.
- The child prints the selected value split across two output chunks.
- The child prints more than any retention bound would allow over a long session.
- Redaction of one output chunk fails.
- The child is killed by a signal rather than exiting with a status.
- The broker is killed uncatchably; the child then outlives it (accepted risk, see Assumptions).
- The audit intent record cannot be written, or the outcome record cannot be written after the session ends.
- A lifetime option is given without session mode.

## Requirements *(mandatory)*

### Functional Requirements

#### Mode and scope

- **FR-001**: Session mode MUST be an explicit opt-in on the local secret-use command; ordinary use without the opt-in MUST keep spec 041 FR-036 unchanged (default 5 minutes, 1 second to 30 minutes).
- **FR-002**: Session mode MUST be available only on the local CLI; it MUST NOT be offered by MCP, accepted by registered use profiles, or exposed through durable-job or remote execution paths.
- **FR-003**: Session mode MUST run in the foreground of the terminal that started it; the product MUST NOT provide a background daemon, a session restart, or a separate status or stop command.
- **FR-004**: Session mode MUST accept the same source, key and destination bindings as ordinary local use, including more than one key per child, and MUST reuse the same destination deny list and direct-argument command form with no implicit shell.

#### Lifetime

- **FR-005**: The session lifetime MUST be given in whole seconds through one explicit option, MUST default to 28,800 seconds (8 hours), MUST accept 1 through 43,200 seconds (12 hours), and MUST have no unbounded value.
- **FR-006**: A request MUST be refused when the lifetime is below 1, above 43,200, or not a whole number of seconds; when both a session lifetime and an ordinary use timeout are given; or when a lifetime is given without session mode.
- **FR-007**: The broker MUST enforce the lifetime itself and MUST end the session when it expires, independent of anything the child does.
- **FR-007a**: The lifetime MUST be measured in wall-clock time, so time the machine spends asleep counts toward it; a session whose end time passed during sleep MUST end on wake with `lifetime_expired`.

#### Terminal requirement

- **FR-008**: A session start MUST be refused with `tty_required` when standard output is not a terminal, when no controlling terminal can be opened, or when the broker is not in the terminal's foreground job.
- **FR-008a**: Keyboard suspend of the broker MUST be ignored for the whole session so that the broker never stops enforcing the lifetime.
- **FR-009**: Every argument check (lifetime range and form, option conflicts, destination deny list, terminal check, source registration) MUST complete before any secret value is read. An unknown key MUST be refused as ordinary use refuses it, and no refused start may deliver a value to any child.

#### Session end

- **FR-010**: A session MUST end on the first of: lifetime expiry, an interrupt delivered to the broker, a hangup delivered to the broker, or the child's own exit; the end reason MUST be exactly one of `lifetime_expired`, `interrupted`, `hangup`, `child_exited`. A failed write to the terminal MUST end the session with `hangup`; any other catchable termination request to the broker MUST end it with `interrupted`.
- **FR-011**: When a session ends for any reason, the child's entire process group MUST be gone within 5 seconds; the broker MUST first request polite termination and then force termination of anything still running inside that window.
- **FR-012**: On `child_exited` the broker MUST exit with the child's exit status mapped exactly as ordinary local use maps it. On `lifetime_expired` it MUST exit 0, on `interrupted` 130 and on `hangup` 129.
- **FR-013**: A crashed or exited child MUST NOT be restarted.

#### Output

- **FR-014**: Redacted child output MUST be shown in the terminal as it is produced, using the same exact-value and pattern redaction as ordinary use, including values split across output chunks. Redaction MUST stay fail-closed: text that could still be part of a secret is held until it is disambiguated or the session ends, with no time-based flush. Terminal control characters and escape sequences MUST be removed before display, except complete SGR colour and style sequences (`ESC [ <parameters> m`), which MUST pass through unchanged; any other escape sequence, including an incomplete or malformed SGR, MUST be removed whole, not just its escape byte. Ordinary use is unchanged.
- **FR-015**: Session mode MUST NOT retain child output in its result, audit, or any file written by Sandbox; spec 041 FR-037's 1 MiB display bound does not apply to the live stream, and its retention bound is met trivially because nothing is retained.
- **FR-016**: When redaction of an output chunk fails, that chunk MUST be dropped rather than shown, and the number of dropped chunks MUST be reported when the session ends.
- **FR-017**: The child's standard input MUST be closed, as in ordinary use.

#### Result and audit

- **FR-018**: At start, the session MUST state its lifetime and the local time at which it will end; at end, it MUST state the end reason, the exit status when the child exited, an elapsed-time class, and the dropped-chunk count.
- **FR-019**: Elapsed-time classes MUST cover sessions up to and beyond 12 hours; the classes reported by ordinary use MUST NOT change.
- **FR-020**: The session result MUST contain metadata only (source alias, key names, end reason, exit status, elapsed-time class, dropped-chunk count, lifetime) and no value-derived data.
- **FR-021**: Audit MUST follow ordinary use: intent recorded before any secret is read, refusing to proceed if intent cannot be recorded, and a non-secret outcome recorded after the session ends carrying the end reason.

#### Guarantees carried from spec 041

- **FR-022**: Spec 041 FR-030 through FR-035 and FR-041 MUST hold unchanged in session mode: redaction registered before the child starts, no value in argv, output, structured result, errors, audit or the parent environment, a minimal child environment, the destination deny list, and the intentional-recipient warning.

#### Guidance

- **FR-023**: The shipped agent skill, the operator documentation and the command help MUST mark session mode as operator-run from a terminal, MUST tell agents to use bounded ordinary use instead, and MUST state the lifetime bound, the end reasons and the accepted risk that an uncatchably killed broker leaves its child running.

### Key Entities

- **Session Request**: one local, foreground request naming a source, key/destination bindings, a direct command, and a lifetime; validated completely before any value is read.
- **Session Lifetime**: the broker-enforced maximum duration of one session in whole seconds (default 8 hours, maximum 12 hours) and its computed local end time.
- **Session End**: exactly one end reason, the child's exit status when it exited, the elapsed-time class, and the dropped-chunk count.
- **Session Audit Event**: the ordinary intent/outcome audit pair for one session, with the end reason as its outcome reason and no value-derived content.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A dev server started in session mode is still serving 31 minutes after start, and with a short test lifetime is terminated with reason `lifetime_expired`, its whole process group gone within 5 seconds of expiry; the same is shown once with a run that passes 30 minutes.
- **SC-002**: In 100% of tested interrupt and hangup endings, the child's process group is gone within 5 seconds and the reported reason is `interrupted` or `hangup` respectively.
- **SC-003**: Across a full session with a fixture that prints the selected value whole and split across chunks, the value appears in 0 places among argv, terminal output, the result, audit, errors and files written by Sandbox.
- **SC-004**: 100% of start attempts without a terminal on standard output or without an openable controlling terminal are refused with `tty_required`, and the source is never read.
- **SC-005**: 100% of refused starts deliver no value to any child. Every argument, terminal, destination and unknown-source refusal happens before the source is read; only refusals that depend on source content (unknown key, unsafe or unparsable source) happen after it.
- **SC-006**: Live output from the child appears in the terminal within 1 second of a newline-terminated line being written, for the duration of the session.
- **SC-007**: An operator following the shipped documentation can start a dev server in session mode and stop it with Ctrl-C in under two minutes, without reading or exporting the value.

## Assumptions

- Session mode is for a person at a local interactive terminal; interrupt and hangup delivery to the broker is a meaningful bound for that use.
- The child is a trusted, intentional secret recipient (spec 041 FR-041); it can still print, transform, persist or exfiltrate the value, and redaction is defense in depth.
- If the broker is killed uncatchably, the child outlives it, bounded only by its own exit, and the audit trail keeps the intent with no outcome. This is an accepted risk.
- Descendants that deliberately leave the child's process group are outside the broker's reach, as with ordinary use.
- Rotating or re-reading the secret during a session, interactive input to the child, and detection of what the child exposes on the network are out of scope.
- Passing the child's own flags after `--` already works and is out of scope.
- Exact option names, reason codes beyond those named here, and structured field names are finalized in planning, provided they preserve these behaviors and disclosure boundaries.
