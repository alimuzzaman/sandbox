# Tasks: Permanent hosted WordPress login

## Phase 1: Planning and contract

- [x] T001 Complete reviewed PRD, specification, clarification, plan and consistency analysis in `specs/085-permanent-host-login/`.

## Phase 2: Existing behavior remains (US1)

- [x] T002 Preserve default `render_autologin_mu_plugin` and local login source; add separate permanent renderer and backward-compatible URL option in `sandbox/core/_hosting.py`.
- [x] T003 Add explicit mutually exclusive CLI option and fail-closed scope checks in `sandbox/cli.py` and `sandbox/commands/hosting.py`, retaining owner locks and ordinary output.

## Phase 3: Permanent access and control (US2/US3)

- [x] T004 Finish all implementation and update `README.md` and `docs/remote-hosting.md` before acceptance commands or regression edits.
- [x] T005 Exercise supported CLI help/option boundaries and generated PHP behavior in isolated fixtures, preserving real secrets and live targets.
- [x] T006 After observed behavior, add focused Python/PHP regression coverage in `tests/test_host_autologin_permanent.py` for default parity, repeat use, refusal, transport and replacement/removal.
- [ ] T007 Run required targeted checks and `./sb selftest` through the designated small helper; record unavailable runtime proof precisely.

## Phase 4: Review and handoff

- [ ] T008 Independently review final source, resolve findings, verify clean branch/default parity, and commit completed feature with accurate revision/readiness evidence.

## Dependencies and execution

T001 precedes implementation. T002/T003 precede documentation completion T004. Actual supported workflow T005 precedes regression authoring T006 under repository execution-order rules. T007/T008 close the complete feature; no partial release. All work is serial in the owned branch, except read-only review/test helpers. No shared runtime, credentials, or target deployment is authorized.

## Acceptance mapping

FR-001: T002,T006,T007; FR-002/004/008: T003,T005,T006; FR-003/006/007: T002,T005,T006; FR-005: T002,T004,T005,T006. All success criteria map to T005-T008.

## Validation status (2026-11-12)

Supported CLI/generated-PHP acceptance: 10 passed. Final targeted hosting and permanent-link regression run: 298 passed, no failures/errors/skips. Full repository selftest did not finish within its 480-second harness bound; no full-suite pass is claimed. Its five observed failures/errors reproduce identically against base e04e293 (two failures, three errors). Real WordPress acceptance is blocked before provisioning by `docker_daemon_unavailable`; five runtime cases remain skipped, and no instance or live target was changed. Independent source review is complete; its HTTPS proxy prerequisite is documented without weakening the TLS guard. T007/T008 remain open until live verification and required acceptance gates finish. This is not release readiness. Evidence is retained in the task scratch reports.
