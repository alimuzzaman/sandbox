# Implementation Plan: Default Reader.md Bootstrap

**Branch**: `[024-reader-md-install]` | **Date**: 2026-07-13 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/024-reader-md-install/spec.md`

## Summary

Extend the existing macOS bootstrap with a fourth, macOS-only prerequisite
stage. It detects the `reader` command, honors an opt-out, and otherwise
installs the upstream Reader.md cask through Homebrew. Failure is non-fatal so
the existing Sandbox setup path remains available.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: POSIX-compatible Bash

**Primary Dependencies**: Homebrew (macOS only), upstream `jnahian/reader.md` cask

**Storage**: N/A

**Testing**: Python `unittest` static bootstrap assertions and Bash syntax check

**Target Platform**: macOS developer workstations

**Project Type**: CLI/bootstrap script

**Performance Goals**: No additional work when `reader` already exists; at most one install attempt otherwise

**Constraints**: no Linux/server change; no install failure may block Sandbox setup; an automation opt-out is required

**Scale/Scope**: one bootstrap script, README, and focused regression test

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Pass. The change is local, reversible, and touches no auth, personal data,
production infrastructure, or credentials. The user explicitly requested the
new default. Upstream cask checksum verification remains Homebrew's responsibility.

## Project Structure

### Documentation (this feature)

```text
specs/024-reader-md-install/
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
scripts/install-macos.sh              # bootstrap behavior
tests/test_install_macos_script.py    # static regression coverage
README.md                             # user-facing setup documentation
specs/024-reader-md-install/          # delivery artifacts
```

**Structure Decision**: Extend the existing installer rather than adding a new
installer command. The existing script already owns macOS prerequisite ordering.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |
