# Implementation Plan: Google Drive Full Backup

**Branch**: `018-drive-full-backup` | **Date**: 2026-07-11 | **Spec**: [spec.md](./spec.md)

> This plan is retained for historical traceability and is superseded by
> [`specs/023-scoped-recovery-profiles/plan.md`](../023-scoped-recovery-profiles/plan.md).

**Input**: Historical feature specification from `/specs/018-drive-full-backup/spec.md`; current implementation authority is `/specs/023-scoped-recovery-profiles/`.

**Note**: This template is filled in by the `/speckit-plan` command. See `.specify/templates/plan-template.md` for the execution workflow.

## Summary

Add a `sb hermes drive` control plane. It packages full remote state into a
manifested tar archive, encrypts it with an operator-supplied passphrase, and
uses a Drive adapter with resumable transfer. Restore verifies/decrypts into a
staging root and replaces only after integrity checks pass.

## Technical Context

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
specs/023-scoped-recovery-profiles/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
sandbox/core/_hermes.py       # historical Hermes Drive adapter
sandbox/commands/hermes.py    # historical CLI dispatch
tests/test_hermes.py          # adapter and safety fixtures
sandbox/recovery/             # canonical scoped-recovery implementation
specs/023-scoped-recovery-profiles/  # canonical recovery specification and evidence
```

**Structure Decision**: Do not extend this superseded feature independently. New recovery
profiles, manifest verification, retention planning, and restore safety belong to the canonical
`sandbox/recovery/` modules and the 023 Spec-Kit artifacts.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | The historical feature is superseded rather than separately implemented. | A second backup boundary would duplicate recovery policy and weaken consistency. |
