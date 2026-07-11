# Implementation Plan: Google Drive Full Backup

**Branch**: `018-drive-full-backup` | **Date**: 2026-07-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Add a `sb hermes drive` control plane. It packages full remote state into a
manifested tar archive, encrypts it with an operator-supplied passphrase, and
uses a Drive adapter with resumable transfer. Restore verifies/decrypts into a
staging root and replaces only after integrity checks pass.

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.11+, POSIX shell, OpenSSL AES-256-GCM fallback

**Primary Dependencies**: existing SSH layer; Google Drive connector/API; system `tar`, `sha256sum`, and `openssl`

**Storage**: encrypted Drive blobs; remote temporary staging; non-secret local remote configuration

**Testing**: stdlib `unittest` with adapter fixtures; disposable remote restore acceptance

**Target Platform**: macOS controller and Linux remote

**Project Type**: CLI and remote recovery integration

**Performance Goals**: streaming/resumable transfer; do not retain a decrypted archive after operation

**Constraints**: full is default; client-side encryption; zero secret output; restore confirmation; no Docker layer backup

**Scale/Scope**: one private Drive backup root per remote; bounded recovery point retention

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Pass: no plaintext Drive upload; explicit restore confirmation; least privilege;
live destructive recovery only on a disposable remote; preserve unrelated edits.

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
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
