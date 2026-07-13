# Feature Specification: Default Reader.md Bootstrap

**Feature Branch**: `[024-reader-md-install]`

**Created**: 2026-07-13

**Status**: Complete

**Input**: User description: "Add Reader.md to the default macOS Sandbox bootstrap so documentation can be opened locally and remote documentation folders can be added through the installed command-line tool."

## User Scenarios & Testing *(mandatory)*

<!--
  IMPORTANT: User stories should be PRIORITIZED as user journeys ordered by importance.
  Each user story/journey must be INDEPENDENTLY TESTABLE - meaning if you implement just ONE of them,
  you should still have a viable MVP (Minimum Viable Product) that delivers value.

  Assign priorities (P1, P2, P3, etc.) to each story, where P1 is the most critical.
  Think of each story as a standalone slice of functionality that can be:
  - Developed independently
  - Tested independently
  - Deployed independently
  - Demonstrated to users independently
-->

### User Story 1 - Start Sandbox with a documentation reader (Priority: P1)

A macOS developer runs the documented Sandbox bootstrap and receives Reader.md
alongside the normal prerequisites, so `reader <folder>` works when setup ends.

**Why this priority**: The requested local and remote documentation workflow
cannot start without the command-line tool.

**Independent Test**: Inspect the bootstrap and run its static regression test
to confirm that it installs the documented cask unless the user opts out.

**Acceptance Scenarios**:

1. **Given** Homebrew and no `reader` command, **When** the macOS bootstrap runs,
   **Then** it installs Reader.md from its maintained cask before handing off to Sandbox setup.
2. **Given** `SANDBOX_SKIP_READER_MD=1`, **When** the bootstrap runs,
   **Then** it does not install Reader.md and continues.
3. **Given** a Reader.md installation failure, **When** the bootstrap runs,
   **Then** it reports a retry command and continues to Sandbox setup.

### Edge Cases

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right edge cases.
-->

- Homebrew is unavailable: skip Reader.md with an actionable install command.
- Reader.md is already installed: do not reinstall it.
- A cask operation fails: preserve the normal Sandbox bootstrap path.

## Requirements *(mandatory)*

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right functional requirements.
-->

### Functional Requirements

- **FR-001**: The macOS bootstrap MUST install Reader.md by default after the
  existing prerequisites when Homebrew is available and the `reader` command is absent.
- **FR-002**: The bootstrap MUST allow an explicit environment-variable opt-out.
- **FR-003**: The bootstrap MUST not fail Sandbox setup solely because Reader.md
  cannot be installed.
- **FR-004**: The installation instructions MUST describe the default, the opt-out,
  and how the installed command is used.

## Success Criteria *(mandatory)*

<!--
  ACTION REQUIRED: Define measurable success criteria.
  These must be technology-agnostic and measurable.
-->

### Measurable Outcomes

- **SC-001**: A fresh macOS bootstrap with Homebrew reaches the Sandbox handoff
  after attempting exactly one Reader.md installation.
- **SC-002**: An opted-out or failed Reader.md installation never prevents the
  user from continuing Sandbox setup.
- **SC-003**: A user can find the Reader.md default and opt-out behavior from the
  documented macOS setup instructions.

## Assumptions

<!--
  ACTION REQUIRED: The content in this section represents placeholders.
  Fill them out with the right assumptions based on reasonable defaults
  chosen when the feature description did not specify certain details.
-->

- The upstream Reader.md Homebrew cask remains the supported distribution path.
- This feature is macOS-only; it does not install a GUI reader on servers or other platforms.
- The opt-out is intentionally environment-based so non-interactive automation can skip it.
