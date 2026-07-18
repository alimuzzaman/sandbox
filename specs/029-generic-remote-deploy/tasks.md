# Tasks: Generic Remote Deploy

**Input**: Design documents from `specs/029-generic-remote-deploy/`

## Phase 1: Contract Foundation

- [X] T001 Add the generic remote-deploy capability in `sandbox/application/context.py` and cover it in `tests/test_runtime_contracts.py`.
- [X] T002 Make the deploy CLI and MCP descriptions runtime-neutral in `sandbox/cli.py` and `mcp/wp-server/tools/remote.py`.

## Phase 2: User Story 1 - Start a Non-WordPress Project Locally (Priority: P1)

**Goal**: Preserve and prove the existing explicit generic Compose local lifecycle.

**Independent Test**: The fixture ensures twice and reports a ready generic URL.

- [X] T003 [P] [US1] Add/adjust local generic lifecycle assertion in `tests/test_generic_compose.py`.
- [X] T004 [US1] Verify descriptor and generic status fields stay aligned with remote deploy needs in `sandbox/runtimes/compose.py`.

## Phase 3: User Story 2 - Deploy a Non-WordPress Project to a Registered Remote (Priority: P1)

**Goal**: Deploy, ensure, and expose explicit generic Compose projects through the
standard remote command.

**Independent Test**: Mocked remote workflow proves transfer, generic ensure, Caddy
routing to `http_port`, and no WordPress-only helper call.

- [X] T005 [P] [US2] Add generic ensure/expose, bad-port, and WordPress-regression tests in `tests/test_remote.py`.
- [X] T006 [US2] Select the runtime deploy capability and kind-aware post-transfer policy in `sandbox/commands/deploy.py`.
- [X] T007 [US2] Add a kind-neutral declared-port validator/helper in `sandbox/core/_remote.py` if needed by the deploy contract.
- [X] T008 [US2] Update MCP capability selection and forwarding coverage in `mcp/wp-server/tools/remote.py` and `tests/test_remote.py`.

## Phase 4: User Story 3 - Receive Consistent, Safe Deployment Guidance (Priority: P2)

**Goal**: Explain the one workflow and fail locally for incompatible arguments.

**Independent Test**: Generic plugin flags and invalid contract data fail before
remote mutation, while output remains redacted and shaped consistently.

- [X] T009 [US3] Add early generic-validation tests in `tests/test_remote.py`.
- [X] T010 [US3] Document generic remote prerequisites and one-command deploy in `docs/remote-hosting.md` and `docs/sandbox-config-reference.md`.

## Phase 5: User Story 4 - Use a Runtime-Relevant MCP Catalog (Priority: P1)

**Goal**: Expose only runtime-useful tool groups when MCP is started for one project.

**Independent Test**: Compose and WordPress profile tests prove mutually exclusive
runtime groups are hidden and shared remote support remains present.

- [X] T011 [US4] Define explicit Compose and WordPress MCP group profiles in `mcp/wp-server/tools/manifest.py` and cover them in `tests/test_mcp_composition.py`.
- [X] T012 [US4] Resolve a project-scoped profile at startup in `mcp/wp-server/server.py` and forward `--project-dir` from `sandbox/commands/integ.py`.
- [X] T013 [US4] Document project-scoped MCP registration in `mcp/wp-server/README.md`.

## Phase 5: Verification and Handoff

- [X] T014 Run focused remote/runtime/catalog tests and record observed output in `specs/029-generic-remote-deploy/quickstart.md`.
- [X] T015 Run the complete Python suite and `git diff --check`; inspect the local generic fixture status through the supported Sandbox lifecycle.

## Dependencies & Execution Order

- T001–T002 establish the capability/contract boundary.
- T003–T004 preserve the local foundation.
- T005 must precede T006–T008 to establish the remote behavior contract.
- T009–T010 follow the core deploy behavior.
- T011–T012 are final gates.

## Implementation Strategy

Deliver the generic remote branch as one thin vertical slice: capability selection,
remote ensure, generic-port routing, and response URL. Keep WordPress-specific
activation/URL mutation in its existing branch, then document and verify both paths.
