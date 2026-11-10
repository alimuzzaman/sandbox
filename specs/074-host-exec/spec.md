# Feature Specification: Host Exec

**Feature Branch**: `feat/host-exec`

**Created**: 2026-11-10

**Status**: Draft

**Input**: User description: "sb host exec: run a command inside a running service of a hosted target that this controller recorded via sb host apply or sb host stack up, with the controller's stdin streamed to the container. Usage: sb host exec --remote R --target <project>/<environment> --service <name> [--timeout N] [--json] -- <argv...>. Runs docker compose exec -T in the target's own Compose project (same env file and Compose files as host apply), as the service's configured user (no caller-supplied user). --service is checked against the target's rendered Compose services. stdin bytes never reach argv, environment, logs, job output records, audit, feedback or --json output. stdout/stderr stream back; child exit code becomes sb's exit code; --json gives {ok, exit_code, ...} with ok=false whenever the child failed. Scope: only targets recorded in this controller's hosting state for that remote (unknown target refused with a clear code); a target with a stack or apply in flight is refused under the per-target lock rules (documented); bounded --timeout with a default and maximum; audit records only argv, service, target, exit code and duration. Also fix feedback da5d522e: read-only host commands (logs, status, diagnose) given --target on a stack target are refused with stack_template_requires_stack_up; they must resolve the stack's rendered manifest instead, and host exec must resolve stack targets the same way. Primary use: piping a WordPress nonce into a Hub service command and running WP-CLI in a WordPress hosted target's wordpress service. Owner approved 2026-11-10."

## Clarifications

### Session 2026-11-10

No question was put to the owner: the brief fixed the behaviour and left the
choices below to the implementer ("pick one and document it").

- Q: Refuse or wait when the target's apply, recovery or stack deployment is in flight? → A: Refuse with a busy code; never wait (implementer choice).
- Q: Timeout default and maximum? → A: 300 seconds default, 1 to 3600 accepted (implementer choice).
- Q: Where does the audit record live? → A: One line per run in an append-only file under the controller's Sandbox home (implementer choice).
- Q: How is output returned with `--json`? → A: Captured into the envelope, 1 MiB per stream, with truncation flags; plain mode streams it (implementer choice).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a one-off command in a hosted service with a secret on stdin (Priority: P1)

An operator (or an agent on the operator's controller) needs to run a one-off
command inside a running service of a target that this controller deployed,
for example attaching a hosted Hub to a WordPress site with a short-lived
nonce. The nonce is produced by another command and piped in; it must reach
only the command's input, never the command line, logs or records.

**Why this priority**: It is the top blocker for the Hub's end-to-end setup.
Without it, the only way to run such a command is to log in to the server by
hand, which bypasses Sandbox's target scoping and leaks the secret into shell
history or process listings.

**Independent Test**: Deploy a target, pipe a known marker string into
`host exec` running a command that reads its input, and confirm that the
command received it, that the command's output and exit status come back, and
that the marker appears in no Sandbox-owned record.

**Acceptance Scenarios**:

1. **Given** a target this controller applied to remote R with a running
   service `hub`, **When** the operator pipes bytes into
   `host exec --remote R --target P/E --service hub -- <argv>`, **Then** the
   command runs inside that service of that target, reads exactly those bytes
   on its input, its output is shown to the operator, and `sb` exits with the
   command's own exit status.
2. **Given** the same target, **When** the command exits non-zero, **Then**
   `sb` exits with that same non-zero status, and with `--json` it prints a
   result whose `ok` is false and whose `exit_code` is that status.
3. **Given** the same target, **When** the operator runs it with `--json` and
   the command succeeds, **Then** the printed result has `ok` true and
   `exit_code` 0 and includes the command's captured output.
4. **Given** a marker string sent on input, **When** the run finishes,
   **Then** the marker is absent from the command line run on the server, the
   environment passed to it, Sandbox logs, job records, the audit record,
   feedback and the `--json` envelope (except inside the command's own captured
   output, if the command itself prints it).

---

### User Story 2 - Run WP-CLI in a hosted WordPress target (Priority: P1)

An operator runs WP-CLI (or any other command) in the `wordpress` service of
a hosted WordPress target, the same way as in story 1.

**Why this priority**: The same capability covers WP-CLI on hosted targets,
which today has no supported path.

**Independent Test**: Run `host exec ... --service wordpress -- wp option get siteurl`
against a hosted WordPress target and confirm the output and exit status.

**Acceptance Scenarios**:

1. **Given** a hosted WordPress target, **When** the operator runs a WP-CLI
   command through `host exec --service wordpress`, **Then** it runs as the
   service's own configured user and its result is returned as in story 1.

---

### User Story 3 - Read-only host commands and exec on stack targets (Priority: P2)

An operator works from a project checkout whose hosting manifest is a stack
template. They ask for logs, status or diagnosis of one deployed stack by its
target name, or run `host exec` on it. Today those reads are refused because
Sandbox reads the template instead of the stack's rendered manifest
(feedback `da5d522e`).

**Why this priority**: Stack targets are where the Hub runs; exec must work on
them, and the same resolution fixes the existing read-only commands.

**Independent Test**: With a stack recorded for remote R, run
`host logs --remote R --target <stack project>/<env>` from a directory holding
the stack template and confirm logs come back instead of
`stack_template_requires_stack_up`.

**Acceptance Scenarios**:

1. **Given** a stack recorded on remote R whose target is P/E, **When** the
   operator runs `host logs`, `host status` or `host diagnose` with
   `--remote R --target P/E` (from any directory), **Then** Sandbox uses that
   stack's rendered manifest and returns the same result as passing the
   rendered manifest's directory explicitly.
2. **Given** the same stack, **When** the operator runs `host exec` with
   `--target P/E`, **Then** it resolves the stack the same way.
3. **Given** `--target` names a target with no record for remote R, **When**
   any of these commands runs, **Then** it is refused with a stable
   "unknown target" code and nothing runs on the server.

---

### Edge Cases

- `--service` names a service the target does not have: refused with a stable
  code that lists the target's services; nothing runs.
- The service exists but is not running: Compose's own failure comes back as
  a failed run (non-zero exit, `ok` false).
- An apply, recovery or stack deployment for the same target is in flight:
  refused with a stable "target busy" code; nothing runs.
- No argv after `--`: refused as a usage error.
- `--timeout` outside the allowed range: refused as a usage error.
- The command runs past its timeout: it is stopped on the server, `sb` exits
  non-zero with a stable timeout code, `ok` false.
- The connection to the remote fails: `sb` exits non-zero with a stable
  transport code; it never reports success.
- No input is piped (interactive terminal or empty input): the command sees
  end of input; Sandbox never waits for terminal input on its own behalf.
- Very large output under `--json`: captured output is bounded and marked as
  truncated.
- The operator passes options meant to change the user, working directory or
  environment of the run: there are no such options; argv after `--` is passed
  to the command verbatim and never interpreted by Sandbox.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide `host exec` taking `--remote`,
  `--target <project>/<environment>`, `--service`, optional `--timeout`,
  optional `--json`, and the command's argv after `--`.
- **FR-002**: The system MUST run the command only in a target recorded in this
  controller's hosting records for the named remote (an applied target or a
  recorded stack), and MUST refuse any other target with a stable
  unknown-target code before contacting the remote's runtime.
- **FR-003**: The system MUST run the command inside the named service of that
  target's own deployed Compose project, using the same project identity and
  configuration files that `host apply` uses for that target.
- **FR-004**: The system MUST check `--service` against the services of the
  target's deployed configuration and refuse an unknown service with a stable
  code before running anything.
- **FR-005**: The command MUST run as the service's own configured user; the
  system MUST NOT offer a user option or add a user override of its own.
- **FR-006**: The system MUST pass the caller's input bytes to the command's
  input unchanged and close it at end of input; it MUST NOT allocate a
  terminal.
- **FR-007**: Input bytes MUST NOT appear in the remote command line, the
  command's environment, Sandbox logs, job output records, the audit record,
  feedback, or the `--json` envelope; the only exception is output the command
  itself prints.
- **FR-008**: The command's standard output and standard error MUST be returned
  to the caller (streamed in plain mode; captured with a bound in `--json`
  mode).
- **FR-009**: `sb` MUST exit with the command's exit status; it MUST exit
  non-zero for every failure, refusal, timeout or transport error, and MUST
  NOT exit 0 unless the command exited 0.
- **FR-010**: With `--json`, the system MUST print one JSON object containing at
  least `ok` and `exit_code`, where `ok` is true only when the command ran and
  exited 0; refusals carry a stable `code`.
- **FR-011**: The system MUST refuse to run while an apply, recovery or stack
  deployment for the same target is in flight, using the same per-target
  ownership that apply uses, and MUST NOT wait for it; the refusal carries a
  stable busy code.
- **FR-012**: `--timeout` MUST default to 300 seconds and accept 1 to 3600
  seconds; at the limit the system MUST end the run's session on the server
  and report the run as a timeout failure.
- **FR-013**: The system MUST append one audit record per attempted run on the
  controller containing only the remote, target, service, argv, exit code,
  result code, and duration.
- **FR-014**: `host logs`, `host status` and `host diagnose` MUST accept
  `--target <project>/<environment>` (or a stack's `--name`) with `--remote`.
  When the target is a recorded stack they MUST use that stack's rendered
  manifest, never the current directory's template. Otherwise they MUST use
  the manifest in `--project-dir` (default: the current directory), which must
  declare that project and environment. `host exec` MUST resolve targets the
  same way.
- **FR-015**: Documentation (remote hosting guide, configuration reference and
  both copies of the CLI skill) MUST describe `host exec`, its limits, the
  busy rule and the `--target` resolution for read-only commands.

### Key Entities

- **Hosted target**: one `<project>/<environment>` deployed to one remote by
  this controller; found in the hosting records or the stack registry.
- **Resolved target**: the manifest location, Compose project identity,
  configuration files and service names Sandbox derives for a hosted target.
- **Exec audit record**: remote, target, service, argv, exit code, result code
  and duration of one run; never input bytes, environment or output.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can run the Hub attach command with a piped nonce in
  one `sb` invocation, with no manual server login.
- **SC-002**: In an automated test that sends a unique marker on input, the
  marker is found in 0 Sandbox-owned records, command lines or envelopes, and
  is received by the command exactly once.
- **SC-003**: For every tested failure (command failure, timeout, refusal,
  transport error) `sb` exits non-zero, and `--json` reports `ok` false; 0
  cases report success on failure.
- **SC-004**: `host logs`, `host status` and `host diagnose` with `--target` or `--name`
  on a recorded stack succeed from a directory holding the stack template in
  100% of tested cases.

## Assumptions

- The remote is registered and provisioned, and the target's runtime files
  exist on it from a previous apply or stack deployment.
- Busy means refuse, not wait: an exec is short and an operator can retry,
  while waiting could hold a terminal for an apply's full duration.
- The busy check is made at start; Sandbox does not hold the target's
  ownership for the whole run, so an apply started after the check may restart
  the service under a running command. This keeps a long exec from blocking
  deliveries.
- An applied plain target resolves through the manifest in `--project-dir`
  (default: the current directory), because hosting records do not store a
  manifest location; the manifest must name the same project and environment.
- Ending the run's session on the server stops the exec client; a container
  does not signal a process started this way when its session ends, so a
  command that ignores end of input can outlive the timeout inside the
  container. This is a documented limit, not a guarantee.
- The audit record lives on the controller under the Sandbox home; there is no
  remote-side audit for this command.
- Output bounds for `--json` are 1 MiB per stream.
- Out of scope: an interactive terminal (no TTY allocation), a user, working
  directory or environment override, an MCP tool for exec, remote-side audit,
  and `host plan` / `host login-url` resolution by `--target`.
