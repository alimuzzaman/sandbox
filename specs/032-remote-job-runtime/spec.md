# Feature Specification: Remote Job Runtime

**Feature Branch**: `032-remote-job-runtime`  
**Created**: 2026-07-18  
**Status**: Implemented; outstanding live measurement proofs tracked in T156-T157
**Input**: User description: "Make remote execution the recommended development and testing path. Run tests and supported GitHub Actions workflows on reusable remote workspaces, with durable logs, agent-oriented streaming, process health, explicit deadlines, isolated matrix execution, artifacts, and complete CLI/MCP control."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run a remote test safely (Priority: P1)

An agent or developer runs an explicit test command against a selected remote workspace and receives a job identifier immediately. The test continues even if the local network, CLI, or agent connection is interrupted. The caller can choose live output, compact agent-oriented output, sampled output, error-only output, or a final result only.

**Why this priority**: Remote execution is the primary outcome. It must be dependable for long-running Node, PHP, WordPress, and end-to-end tests without making remote process health depend on the caller's connection.

**Independent Test**: Start a long-running remote test, disconnect the caller before it completes, reconnect with the returned job ID, and verify the job outcome and complete retained output.

**Acceptance Scenarios**:

1. **Given** a registered remote and a configured project, **When** a caller runs an explicit test command with a maximum execution time, **Then** Sandbox deploys the exact current working tree, starts a remote job, and returns a unique job ID and resolved workspace.
2. **Given** a remote test is running, **When** the caller loses its network connection, **Then** the test continues until it succeeds, fails, is cancelled, or reaches its deadline.
3. **Given** a caller reconnects with a job ID and output cursor, **When** it requests more output, **Then** it receives all retained output after that cursor without duplicate or missing events.
4. **Given** a noisy test command, **When** the caller selects smart or sampled output, **Then** the test output stored by Sandbox remains complete while only the caller's presentation is reduced.

---

### User Story 2 - Inspect a running or stalled job (Priority: P1)

An agent can inspect a running job at any time to learn whether the process is alive, producing output, making resource progress, quiet, stalled, stuck, cancelled, timed out, or unreachable. It can retrieve full stdout, stderr, combined output, metrics, artifacts, and a clear termination reason without attaching to the process itself.

**Why this priority**: Long-running E2E and CI runs need actionable mid-process evidence. Agents must be able to distinguish an unhealthy process from a healthy test that is merely quiet.

**Independent Test**: Run controlled active, quiet, stalled, and terminated jobs and verify that each job exposes the expected lifecycle, health evidence, metrics, and final result.

**Acceptance Scenarios**:

1. **Given** a live remote job, **When** an agent checks its status, **Then** the response includes lifecycle, health, elapsed time, remaining deadline, last output/activity time, process liveness, and available metrics.
2. **Given** a job has no output but continues to use resources or report progress, **When** it is inspected, **Then** it is reported as quiet rather than failed or stalled.
3. **Given** a job has no observable progress for its configured stall period, **When** it is inspected, **Then** it is reported as suspected stalled with the evidence used for that conclusion.
4. **Given** a caller explicitly requested cancellation on stall, **When** the stall period is exceeded, **Then** Sandbox records the cancellation reason and preserves the logs and workspace according to policy.
5. **Given** a job has completed, **When** an agent requests its full output or artifact list, **Then** Sandbox returns the retained result without re-running the job.

---

### User Story 3 - Reuse and isolate remote workspaces (Priority: P1)

A developer uses a named remote workspace repeatedly during development. Separate tests in that workspace run serially by default, preserving predictable state. An agent may explicitly declare a command parallel-safe or choose a new isolated workspace when parallel work is needed.

**Why this priority**: Reusable environments make remote development practical, while uncoordinated concurrent tests can corrupt shared files, databases, ports, and test fixtures.

**Independent Test**: Start two exclusive tests for one workspace and verify ordering; start isolated matrix cells and verify that each receives independent runtime state and results.

**Acceptance Scenarios**:

1. **Given** two ordinary tests target the same workspace, **When** they are started concurrently, **Then** the second waits for the first rather than sharing mutable runtime state.
2. **Given** a caller declares two commands parallel-safe, **When** the workspace permits shared execution, **Then** Sandbox runs them concurrently while retaining separate job records and logs.
3. **Given** a workspace is busy and a caller requests immediate parallel execution, **When** the command is not declared parallel-safe, **Then** Sandbox explains the queue state and suggests creating a new workspace without creating one automatically.
4. **Given** a matrix test run, **When** several cells start, **Then** every cell receives an isolated workspace, runtime state, job record, output, deadline, and cleanup result.
5. **Given** a failed reusable workspace, **When** the caller inspects it, resets it, or destroys it explicitly, **Then** Sandbox preserves or removes it only according to the requested lifecycle action.

---

### User Story 4 - Run remote CI with clear compatibility evidence (Priority: P2)

A developer submits a supported GitHub Actions workflow to the selected remote host. Sandbox runs the complete workflow graph and matrix remotely, records job and step results, and reports any workflow behavior it cannot faithfully reproduce before execution unless the caller explicitly accepts that divergence.

**Why this priority**: Reusing existing CI workflows avoids a separate test definition, while a strict compatibility gate prevents agents from trusting a result that differs silently from the hosted workflow.

**Independent Test**: Run a workflow with dependencies, a matrix, artifacts, and supported reusable actions; separately submit workflows with known incompatible behavior and verify that they fail preflight until divergence is explicitly accepted.

**Acceptance Scenarios**:

1. **Given** a compatible Linux GitHub Actions workflow, **When** it is submitted to the remote CI runner, **Then** its selected jobs, dependencies, matrix cells, logs, deadlines, artifacts, and final outcomes run on the selected remote host.
2. **Given** a workflow uses a known unsupported or behaviorally different feature, **When** the caller has not accepted that difference, **Then** Sandbox stops before execution and reports the affected workflow locations and reason.
3. **Given** a caller explicitly accepts a named compatibility difference, **When** the workflow completes, **Then** the result records that acceptance and the affected behavior.
4. **Given** a workflow contains deployment or release activity, **When** it is run in the default safe mode, **Then** the activity is not performed and the result reports the resulting semantic difference.
5. **Given** a remote CI matrix contains a failed cell, **When** the run finishes or is retried, **Then** the caller can inspect each cell independently and choose whether to reuse, reset, or replace its workspace.

---

### User Story 5 - Choose local or remote execution deliberately (Priority: P3)

A project can recommend remote execution by default while retaining a clearly visible local override. CLI and MCP callers use the same target, workspace, deadline, output, and status concepts.

**Why this priority**: The feature must improve existing workflows without silently changing a caller's execution location or removing local development capability.

**Independent Test**: Configure a project for remote execution, run a test without a target override, then repeat it with an explicit local override and verify the selected target in both results.

**Acceptance Scenarios**:

1. **Given** a project configured for remote execution, **When** a caller omits a target override, **Then** Sandbox selects the configured remote and reports that selection.
2. **Given** a caller explicitly requests local execution, **When** it runs a supported command, **Then** Sandbox runs locally and reports the local target.
3. **Given** an unknown remote or workspace label, **When** a caller starts a job, **Then** Sandbox fails before starting work and provides actionable guidance.

### Edge Cases

- A launch response is lost after the remote job was accepted; retrying the same request must not create a duplicate job.
- A process ID is reused after an earlier job exits; status inspection must not mistake the new process for the old job.
- A remote host becomes unreachable while its job may still be running; callers must see an unreachable state rather than a false terminal result.
- A test emits no newline, invalid text, terminal control codes, or more output than the retention limit; output must remain safely retrievable or fail explicitly without false success.
- A test tries to emit a secret, a requested artifact escapes the workspace, or an artifact exceeds configured limits; Sandbox must protect secrets and reject unsafe artifact collection.
- A remote host restarts while jobs are running; unfinished work must be reconciled as interrupted or lost with preserved evidence.
- A workspace reset, redeployment, or destruction is requested while it has active jobs; the conflicting operation must not modify that workspace.
- A CI workflow requests a non-Linux runner or a behavior that cannot be reproduced; preflight must report the incompatibility before any workflow side effect.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST support a project-level default execution target, named remote, named workspace, output profile, and execution profile while preserving an explicit local override.
- **FR-002**: Sandbox MUST require every execution to have a finite maximum execution time resolved from an explicit caller value, a selected profile, a supported workflow value, or an operation-specific named fallback.
- **FR-003**: Sandbox MUST remind CLI and MCP callers when a profile supplied a deadline fallback instead of an explicit caller value.
- **FR-004**: Sandbox MUST reject zero, negative, invalid, and unbounded execution-time requests before starting work.
- **FR-005**: Sandbox MUST deploy the exact current project working tree, including supported uncommitted and untracked files, before remote execution and identify the deployed source version in the result.
- **FR-006**: Sandbox MUST create a durable job record before reporting that a remote execution has started.
- **FR-007**: Sandbox MUST allow callers to use an idempotency request identifier so that retried submissions do not create duplicate jobs.
- **FR-008**: Sandbox MUST keep a running job independent from the submitting CLI, MCP client, SSH session, or network connection.
- **FR-009**: Sandbox MUST retain separate stdout and stderr, an observed combined event order, final result details, and an integrity identity for each completed job.
- **FR-010**: Sandbox MUST redact configured secret values before output is stored, streamed, downloaded, or included in an error result.
- **FR-011**: Sandbox MUST provide resumable output retrieval using an opaque cursor and support bounded output reads by stream, offset, tail size, line count, or time.
- **FR-012**: Sandbox MUST support full, smart, errors-only, sampled, quiet, and named custom output profiles without changing the executed command or deleting retained output.
- **FR-013**: Sandbox MUST allow custom output profiles to use declarative sampling, matching, context, deduplication, timestamps, prefixes, heartbeat, and output-budget rules; profiles MUST NOT run arbitrary filter commands.
- **FR-014**: Sandbox MUST report output-limit or storage-pressure failures explicitly and MUST NOT claim a complete successful result after silently truncating required output.
- **FR-015**: Sandbox MUST retain a job's lifecycle, health, process liveness, elapsed time, deadline, remaining time, last output time, last activity time, and termination reason for inspection during and after execution.
- **FR-016**: Sandbox MUST distinguish active, quiet, suspected stalled, stuck, supervisor-unresponsive, orphaned, process-missing, unreachable, and unknown health conditions from terminal job outcomes.
- **FR-017**: Sandbox MUST report the evidence used to classify a job as suspected stalled or stuck.
- **FR-018**: Sandbox MUST warn on a suspected stall by default and MUST only cancel for a stall when the caller explicitly opts in.
- **FR-019**: Sandbox MUST support graceful cancellation and explicit force cancellation, record the reason, and preserve output and artifacts produced before cancellation.
- **FR-020**: Sandbox MUST stop work that reaches its maximum execution time, record that it timed out, and clean up only resources owned by that job.
- **FR-021**: Sandbox MUST detect when a recorded process identity no longer matches the expected job and MUST not report the job as healthy or successful on that basis.
- **FR-022**: Sandbox MUST reconcile unfinished jobs after a supervisor failure, host restart, or unexpected process exit and preserve the best available evidence.
- **FR-023**: Sandbox MUST support persistent named remote workspaces, explicitly generated isolated workspaces, explicit reset, and explicit destruction.
- **FR-024**: Sandbox MUST retain failed workspaces by default and remove ephemeral workspaces only through the requested cleanup policy.
- **FR-025**: Sandbox MUST serialize ordinary tests and commands that target the same workspace unless the caller explicitly declares them parallel-safe.
- **FR-026**: Sandbox MUST report queue state and suggest a new workspace when a caller requests immediate concurrent work in a busy exclusive workspace; Sandbox MUST leave the workspace decision to the caller.
- **FR-027**: Sandbox MUST give every parallel-safe command and every matrix cell a separate job record, deadline, output, result, and cleanup status.
- **FR-028**: Sandbox MUST execute matrix cells in isolated workspaces and runtime instances, bounded by configured host capacity, and queue excess cells rather than overcommitting the host.
- **FR-029**: Sandbox MUST support parent jobs for multi-step test plans and CI runs, exposing both aggregate status and independently inspectable child jobs.
- **FR-030**: Sandbox MUST support declared multi-step test plans and explicit command arguments; automatic discovery of package scripts or test commands MUST remain disabled unless declared by the project.
- **FR-031**: Sandbox MUST collect requested artifacts only from within the deployed workspace, reject unsafe paths and objects, enforce configured limits, and provide artifact identity, retention, and bounded retrieval.
- **FR-032**: Sandbox MUST apply a documented retention policy to terminal jobs, logs, metrics, and artifacts while preserving active jobs and explicitly retained failed workspaces.
- **FR-033**: Sandbox MUST support remote execution of compatible Linux GitHub Actions workflow files on one selected remote host, including supported jobs, dependencies, matrices, logs, time limits, artifacts, retries, and cleanup.
- **FR-034**: Sandbox MUST preflight remote CI workflows and block execution when they use a known incompatible or behaviorally different feature unless the caller explicitly accepts that named divergence.
- **FR-035**: Sandbox MUST record accepted CI compatibility differences in the final workflow result.
- **FR-036**: Sandbox MUST run CI deployment, release, and publishing activity in safe mode by default and report any skipped behavior as a compatibility difference.
- **FR-037**: Sandbox MUST reject unsupported remote CI runner environments before execution rather than attempting a misleading run.
- **FR-038**: CLI and MCP callers MUST be able to start jobs, inspect status, list jobs, retrieve output, follow output, retrieve metrics, list artifacts, retrieve bounded artifacts, cancel, retry, reset, and clean up through supported interfaces.
- **FR-039**: MCP live progress messages MUST be optional and rate-limited; durable job status and output retrieval MUST remain available when a client does not consume live progress.
- **FR-040**: Existing local CLI, MCP, WordPress, generic runtime, asynchronous-job, E2E, CI, and remote-hosting behavior MUST remain available through compatible interfaces until replacement parity is verified.
- **FR-041**: Sandbox documentation and agent guidance MUST recommend remote execution when configured, explicit local overrides, deployment before remote testing, explicit deadlines, reusable workspaces, isolated matrix labels, and use of the remote MCP server for live remote operations.

### Key Entities *(include if feature involves data)*

- **Execution Profile**: A named policy that supplies a finite deadline, stall threshold, cancellation grace period, and default behavior for a class of work such as unit, integration, E2E, CI, or overnight testing.
- **Job**: One accepted execution with an identity, target, command intent, deadline, lifecycle, health, output, metrics, result, and cleanup state.
- **Job Attempt**: A retry of a prior job that preserves the relationship to the original while retaining independent output and outcome.
- **Parent Job**: An aggregate execution that owns multiple step, command, or matrix-cell jobs and reports their combined outcome.
- **Workspace**: A named, reusable or isolated remote project environment containing the deployed source and its associated runtime state.
- **Workspace Lease**: The recorded right for a job to use a workspace exclusively or in explicitly declared parallel-safe shared mode.
- **Output Event**: One ordered retained unit of output, state, metric, section, annotation, artifact, heartbeat, or completion information that can be resumed by cursor.
- **Artifact**: A validated file or collection of files produced by a job and retained for later inspection.
- **Compatibility Difference**: A known workflow behavior that Sandbox cannot reproduce faithfully and that requires explicit caller acceptance before execution.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A caller can submit a remote unit, integration, Node, PHP, or E2E test and receive a durable job identifier within 10 seconds after target validation and source acceptance.
- **SC-002**: In 100 controlled caller-disconnect tests, 100% of accepted remote jobs continue to a recorded terminal or reconciled interrupted state without dependence on the lost connection.
- **SC-003**: A caller reconnecting with a valid cursor receives all retained output after that cursor with no duplicate events in 100 consecutive reconnect tests.
- **SC-004**: A status check for an active job returns lifecycle, liveness, deadline, and last-activity information within 5 seconds when the selected remote is reachable.
- **SC-005**: Controlled active, quiet, stalled, and stopped jobs are classified correctly in at least 95% of acceptance runs, and every stalled/stuck classification includes inspectable evidence.
- **SC-006**: Two ordinary tests targeting one workspace never execute concurrently in acceptance testing, while isolated matrix cells can execute concurrently without sharing runtime state.
- **SC-007**: A caller can retrieve retained full output, metrics, and requested valid artifacts for 100% of completed acceptance jobs within their configured retention period.
- **SC-008**: A job that reaches its deadline or an opt-in stall cancellation records a non-success terminal state, reason, output completeness, and cleanup outcome in 100% of acceptance tests.
- **SC-009**: Compatible remote CI workflows with dependencies, matrices, and supported actions complete with independently inspectable cell results; known incompatible workflow features are blocked before execution unless explicitly accepted.
- **SC-010**: Existing local execution and asynchronous-job contract suites continue to pass while remote execution is enabled for configured projects.

## Assumptions

- A remote target must already be registered and provisioned; this feature does not create a remote host implicitly.
- Remote execution is recommended by project configuration but local execution remains available only through an explicit override or a project without remote configuration.
- Remote workspaces are persistent and reusable by default; cleanup is explicit or requested through an ephemeral policy.
- Every job has a finite effective deadline. Callers are expected to provide it explicitly; named execution profiles provide the documented fallback and reminder.
- A suspected stalled job is not necessarily broken. Automatic stall cancellation is opt-in because quiet tests, external waits, and long setup operations can be legitimate.
- The first remote CI release runs on one selected Linux remote host. It supports compatible workflow behavior and rejects known semantic differences unless accepted; it does not promise GitHub-hosted runner fidelity, cross-host scheduling, autoscaling, or non-Linux runners.
- Production deployment, publishing, release hosting, and destructive remote cleanup remain outside the default execution scope and require separately authorized behavior.
- Complete output means complete after mandatory secret redaction and before an explicitly reported storage or retention limit.

## Convergence amendment — 2026-08-13 (27-feedback jobs and list contract)

This dated amendment preserves the original requirements and makes the accepted
job identity and transport boundary explicit. It maps feedback `79d775b4`,
`b027d2ab`, `3da039b4`, `343d1a5a`, and `6bc4c6d5` to this feature; the network
consumer rule is repeated in the Resource Monitoring amendment.

### Normative requirements

- **FR-042**: A submission MUST durably create the job record before reporting
  acceptance and MUST return a non-empty canonical `job_id`, target identity,
  workspace label, and source/proof identity. A transport that cannot decode
  this acknowledgement MUST fail explicitly and MUST NOT claim detached
  execution (`79d775b4`, `3da039b4`, `343d1a5a`).
- **FR-043**: Status, output, metrics, cancellation, retry, and cleanup MUST
  resolve the durable record by its canonical `job_id` plus the stored target
  context. Label-only or process-only lookup MUST NOT control or report a
  different job (`79d775b4`).
- **FR-044**: Any checkout, commit, or source directory resolved by guide or
  preflight MUST be persisted in the accepted job's bounded submission snapshot
  and used by detached execution. A later caller working-directory change MUST
  not change the proof checkout (`b027d2ab`).
- **FR-045**: The public `job-list` response remains top-level. It MUST be
  decoded by one feature-owned contract parser; consumers MUST NOT require an
  invented `.data` wrapper or maintain a second incompatible decoder
  (`6bc4c6d5`).

### Acceptance evidence required before closing this amendment

The job acceptance matrix MUST exercise local and remote submission, immediate
ID visibility, control by that ID after reconnect, guide-resolved proof checkout,
and both accepted and failed acknowledgements. Each case records its feedback ID,
request/transport identity, and safe terminal evidence without credentials.
