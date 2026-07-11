# Tasks: Managed Hosting with Cloudflare DNS and TLS

**Input**: Design documents from `specs/015-managed-hosting-cloudflare/`

## Phase 1: Setup

- [X] T001 Create feature design artifacts in `specs/015-managed-hosting-cloudflare/`
- [X] T002 Update the managed Spec-Kit references in `AGENTS.md` and `CLAUDE.md`

## Phase 2: Foundational Hosting Contract

- [X] T003 Add manifest models, IDN normalization, alias validation, and Caddy rendering in `sandbox/core/_hosting.py`
- [X] T004 Add Cloudflare token, DNS, strict-mode, and Origin CA client in `sandbox/core/_cloudflare.py`
- [X] T005 Add remote origin-address persistence in `sandbox/core/_remote.py` and `sandbox/commands/remote.py`
- [X] T006 Add Cloudflare connection flow in `sandbox/commands/config_setup.py`
- [X] T007 Register host validate/plan/apply CLI parsing in `sandbox/cli.py` and behavior in `sandbox/commands/hosting.py`
- [X] T008 Add foundational unit coverage in `tests/test_hosting.py`

## Phase 3: User Story 1 - Validate a hosted project (P1)

**Independent Test**: `./sb host validate` accepts each supplied manifest and rejects
invalid aliases, wildcards, and policies without a token or remote.

- [X] T009 [US1] Implement offline `host validate` JSON and text output in `sandbox/commands/hosting.py`
- [X] T010 [P] [US1] Add static, WordPress, and Lenzora manifest fixtures to `tests/test_hosting.py`

## Phase 4: User Story 2 - Review and apply a protected hosting change (P2)

**Independent Test**: mocked plan output includes scoped changes and apply exits before
mutation when confirmation is absent.

- [X] T011 [US2] Implement remote state, Compose rendering, and guarded apply helpers in `sandbox/core/_hosting.py`
- [X] T012 [US2] Implement Cloudflare drift planning and confirmation-gated apply in `sandbox/commands/hosting.py`

## Phase 5: User Story 3 - Describe real hosted projects (P3)

**Independent Test**: each project manifest validates and its Compose configuration
renders isolated routes and runtime names.

- [X] T014 [P] [US3] Add static-site Docker/Compose/hosting files in `/Users/alim/Sites/git/alimuzzaman.me/`
- [X] T015 [P] [US3] Add WordPress multisite repository and hosting files in `/Users/alim/Sites/git/amarsonar-bangla/`
- [X] T016 [US3] Add Lenzora production/development Compose and hosting files in `/Users/alim/Sites/git/lenzora/`

## Phase 6: Polish and Verification

- [X] T017 Update hosting documentation in `docs/remote-hosting.md` and `README.md`
- [X] T018 Run targeted Python tests and all offline host validation commands
- [X] T019 Run safe Compose configuration/build checks and local WordPress network verification
- [X] T020 Confirm no Cloudflare credential, DNS, container, or production mutation was performed

## Phase 7: Permanent deployment readiness

- [X] T021 Add owner-only personal secret-file migration and host secret status/generation.
- [X] T022 Implement guarded remote Compose/Caddy/Origin CA/DNS apply with rollback state.
- [X] T023 Harden the static and WordPress production image/configuration paths.
- [X] T024 Save the permanent deployment plan and Lenzora-agent Dockerization handoff prompt.

## Phase 8: WordPress direct-update filesystem ownership

**Independent Test**: render the WordPress Compose configuration, verify the
permissions job precedes the non-root initializer, and confirm the image and existing
uploads volume are owned by Apache's runtime identity after deployment.

- [X] T025 Add a root-only, idempotent WordPress permissions job that repairs core and named uploads-volume ownership before initialization.
- [X] T026 Run the WordPress initializer as the Apache runtime identity and bake the application image with that identity's ownership.
- [X] T027 Validate the Compose configuration and perform a disposable direct-filesystem writability check without changing WordPress content.

## Dependencies

T003-T008 block all user stories. User Story 1 precedes live planning. User Story 2
depends on the contract from User Story 1. Project manifests depend on the validated
contract and can be verified independently after User Story 1.
