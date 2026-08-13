# Feature Specification: CLI-first Sandbox operation

**Feature Branch**: `030-cli-first-operation`

**Created**: 2026-07-18

**Status**: Complete

**Input**: User description: "Commit and push automatically after work is done; find more improvements; provide a skill plus CLI alternative to MCP."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Operate a generic project without MCP (Priority: P1)

A developer or agent can create, inspect, execute a command in, and deploy a
generic Compose project using Sandbox commands without registering or starting
an MCP server.

**Why this priority**: Generic projects otherwise have an MCP-only execution
gap even though their lifecycle and remote deployment are CLI-capable.

**Independent Test**: A configured generic Compose project reports a
Compose-specific guide and `sb exec` invokes an explicit argv list in its
declared public service.

**Acceptance Scenarios**:

1. **Given** a generic Compose project, **When** the user requests its guide,
   **Then** it receives lifecycle, explicit execution, and remote deploy
   commands without WordPress-only instructions.
2. **Given** a running generic Compose instance, **When** the user passes an
   argv list to the execution command, **Then** Sandbox runs that exact list in
   the declared service without inferring a shell.

---

### User Story 2 - Learn a CLI-first workflow from a skill (Priority: P2)

A developer or agent can load a shipped skill and choose commands appropriate
to the detected project runtime without needing MCP tool discovery.

**Why this priority**: It makes the alternate interface discoverable and keeps
runtime-specific tools from leaking into unrelated work.

**Independent Test**: `sb skill show sandbox-cli` and `sb guide` both identify
the CLI workflow and distinguish WordPress from generic Compose operations.

**Acceptance Scenarios**:

1. **Given** any Sandbox checkout, **When** the user shows the CLI skill,
   **Then** it describes local and remote workflows for each supported runtime.
2. **Given** a WordPress project, **When** the user requests the guide, **Then**
   it provides WordPress commands rather than generic service execution.

---

### User Story 3 - Ship completed work automatically (Priority: P3)

After required verification succeeds, agents commit and push completed relevant
work without a further confirmation step, while retaining protections for
destructive or release actions.

**Why this priority**: The requested delivery policy removes repetitive manual
handoff work while preserving clearly consequential operations.

**Independent Test**: Project operating guidance consistently instructs
automatic commit/push after verification and lists protected actions.

**Acceptance Scenarios**:

1. **Given** a verified completed change, **When** an agent reaches handoff,
   **Then** it stages, commits, and pushes the active branch.
2. **Given** a force push, tag, release, deploy, or PR action, **When** an
   agent considers it, **Then** explicit approval is still required.

### Edge Cases

- A WordPress project requests generic service execution: Sandbox rejects it
  before execution with a capability error.
- No project descriptor is available for `sb guide`: it supplies a safe generic
  Compose starter catalog rather than touching an instance.
- An execution command is empty or contains a NUL byte: Sandbox rejects it
  without invoking the runtime.
- An MCP client is present: the CLI path remains available and MCP stays
  runtime-scoped rather than being removed.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Sandbox MUST provide a CLI command catalog that is tailored to a
  detected project runtime and may be consumed without MCP.
- **FR-002**: Sandbox MUST provide a shipped CLI-first skill discoverable with
  the existing skill command.
- **FR-003**: Generic Compose projects MUST be able to execute an explicit argv
  list in their declared public service through the CLI.
- **FR-004**: CLI execution MUST reject empty or malformed argv input before a
  runtime side effect.
- **FR-005**: WordPress projects MUST NOT receive generic Compose execution
  permission.
- **FR-006**: MCP MUST remain optional and runtime-scoped; this feature MUST
  NOT remove current MCP integration.
- **FR-007**: After required checks pass, project guidance MUST require
  automatic commit and push of completed relevant work on the active branch.
- **FR-008**: Project guidance MUST continue to require explicit approval for
  force pushes, tags, releases, deployments, PR creation, and PR merges.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user can obtain a runtime-specific CLI catalog with one local
  command and no MCP setup.
- **SC-002**: A generic project can execute a declared-service argv command
  through the CLI with no raw Docker command required.
- **SC-003**: The skill and guide contain no WordPress execution recommendation
  for a generic project and no generic execution recommendation for WordPress.
- **SC-004**: The command and composition test suites pass with the new CLI
  commands represented in their public command inventory.

## Assumptions

- The active branch is an approved destination for normal verified work, as
  explicitly requested; release and destructive Git operations remain separate.
- Existing local and remote deploy contracts remain the supported deployment
  path for both project runtimes.
- MCP continues to serve MCP-capable clients and is not a dependency of the
  CLI-first workflow.

## Convergence amendment — 2026-08-13 (27-feedback CLI/config/output)

This amendment preserves the original CLI-first scope and records the shared
selection, guide, output, and feedback contracts. Config placement is delegated
to the unchanged `specs/042-config-subdirectory/prd.md`; this feature owns only
label intent and command-surface semantics. Feedback mapped here is
`e11914b5`, `64811859`, `15d1625b`, `b6905052`, `0e2d74b6`, `b0d1a1e5`,
`2b080bf5`, `ad190c71`, and `f90c6712`.

### Normative requirements

- **FR-009**: Project configuration discovery MUST follow the two-home rule in
  the unchanged Spec 042 PRD (project root or `.config/sandbox/`, one complete
  selected home, never a cross-home merge). CLI guide and status output MUST
  identify the selected home without exposing secrets (`e11914b5`).
- **FR-010**: An explicitly supplied `--label` is intent, not a hint. If that
  label does not resolve to an existing or explicitly creatable target for the
  requested operation, Sandbox MUST fail with a stable nonzero error and MUST
  NOT fall back to `default`, an unlabeled instance, or another label. An
  omitted label retains the documented default/disambiguation behavior
  (`e11914b5`).
- **FR-011**: Global `--label` MUST survive parser and subcommand normalization
  unchanged. A subcommand default may fill an absent value but MUST NOT
  overwrite an explicitly supplied value, regardless of flag position
  (`64811859`).
- **FR-012**: `sb guide` MUST resolve the supported Sandbox executable/entry
  point from the current installation or PATH and MUST work when a checkout-local
  `./sb` wrapper is absent (`15d1625b`).
- **FR-013**: Public guide output MUST be generated or checked against the
  feature-owned command registry. Any omitted internal command MUST appear in a
  small explicit exclusion set; hand-curated public commands that drift from
  the registry are invalid (`b6905052`).
- **FR-014**: WordPress output assertions MUST capture child stdout, stderr, and
  exit status and assert on those values. Wrapper/object truthiness or a returned
  process object alone is not evidence of command output (`0e2d74b6`).
- **FR-015**: `status --json` and every JSON-mode CLI surface MUST use the
  canonical renderer and emit exactly one JSON document on stdout. Human text,
  diagnostics, and progress belong on stderr; mixed text/JSON and duplicate
  envelopes are failures (`b0d1a1e5`).
- **FR-016**: CLI and MCP MUST obtain project identity and target selection from
  the same kind-neutral identity service and expose equivalent identity,
  label, project-root, and capability fields; adapters MUST not derive a second
  plugin-shaped identity (`2b080bf5`).
- **FR-017**: Feedback operations MUST support bounded submit/list semantics plus
  detail-by-ID, safe filtered export, and an explicit retention/cleanup plan.
  Listing/filtering/export are read-only; retention deletion is never implicit,
  and stored feedback remains untrusted data (`ad190c71`).
- **FR-018**: Count/limit arguments MUST distinguish omitted/default values from
  explicit invalid values (including zero, negatives, booleans, non-integers,
  and out-of-range values) and fail before storage/provider access with a stable
  nonzero error (`f90c6712`).

### Acceptance evidence required before closing this amendment

The matrix MUST cover root and `.config/sandbox` homes, labeled and unlabeled
targets, explicit missing labels, both flag positions, wrapper-less guide
discovery, registry drift, captured WP output, one-document JSON parsing, CLI /
MCP identity parity, feedback detail/filter/export/retention safety, and invalid
limits. Each case records its feedback ID and does not mark an existing task
complete merely because a fixture passes.

## Convergence amendment — 2026-08-13 (PHP extension CLI contract)

This amendment records how the CLI exposes the additive WordPress extension feature;
normalization/provisioning ownership remains with Specs 021 and 039.

### Normative requirements

- **FR-019**: New WordPress initialization MUST emit an explicit reviewable
  `phpExtensions` profile (`wordpress@1` or an explicit no-profile choice); it MUST
  not silently alter an existing project configuration.
- **FR-020**: CLI status/doctor surfaces MUST expose canonical requested state,
  profile/catalog revision, normalized digest, safe provenance, and observations for
  web PHP, WP-CLI, bounded exec, and PHPUnit when extensions are configured. Omission
  preserves the legacy response shape.
- **FR-021**: Extension failures MUST distinguish missing, version mismatch,
  unobservable version, unsupported provisioning, unsupported disable, and plane drift
  with stable nonzero results; JSON remains one parseable document on stdout.
- **FR-022**: Generic Compose projects MUST refuse `phpExtensions` in v1 before any
  image/package/runtime mutation, with a documented safe alternative (omit the field
  or use a future PHP-specific adapter).
- **FR-023**: Extension diagnostics and build progress MUST follow the CLI secret and
  stdout contracts; no credentials, private source contents, arbitrary URLs, package
  instructions, or shell fragments may be emitted or persisted.

### Acceptance evidence required before closing this amendment

The CLI matrix MUST cover fresh init, existing-config omission, profile/no-profile
choice, status/doctor text and JSON, all structured failure classes, four-plane
observations, generic Compose refusal, and secret-safe stdout/stderr capture.
