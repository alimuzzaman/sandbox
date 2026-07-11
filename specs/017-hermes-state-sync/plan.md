# Implementation Plan: Hermes State Sync

**Branch**: `017-hermes-state-sync` | **Date**: 2026-07-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Add a private-repository state layer to the existing remote Hermes integration. Setup
pulls a validated sanitized snapshot before applying the Sandbox harness; explicit
sync stages the same allowlisted files, scans them, and commits/pushes using the
operator's configured GitHub CLI credential. Restore is atomic and never handles
provider credentials.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.11+ and POSIX shell commands already used by Sandbox

**Primary Dependencies**: Existing SSH/Git/GitHub CLI integration; no new runtime dependency

**Storage**: Private Git repository plus remote `$HOME/.hermes` and `$SANDBOX_HOME/runtime`

**Testing**: stdlib `unittest`, CLI parser tests, secret/path exclusion tests, remote command fixtures

**Target Platform**: Local macOS CLI controlling a Linux SSH remote

**Project Type**: CLI integration

**Performance Goals**: bounded sync/restore for state under 50 MB without loading logs or databases

**Constraints**: fail closed on secret/path violations; preserve current setup when unconfigured; no org access; no credential copying

**Scale/Scope**: one private state repository per remote; profile, metadata, memory, and user-authored harness files only

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Pass: minimal diff, explicit external push confirmation, no secrets, atomic restore,
tests before mutation, and documentation with code. Existing unrelated worktree
changes remain untouched.

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
sandbox/core/_hermes.py       # allowlist, export/import, sync/restore orchestration
sandbox/commands/hermes.py    # state subcommand dispatch
sandbox/cli.py                # state options and repository configuration
tests/test_hermes.py          # unit and command contract coverage
docs/hermes-agent.md          # operator workflow and security boundaries
```

**Structure Decision**: Extend the existing Hermes core and CLI rather than adding
another service. Repository operations stay local through the existing SSH and Git
abstractions; the remote only provides source files and Hermes setup state.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
