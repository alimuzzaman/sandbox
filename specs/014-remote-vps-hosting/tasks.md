---

description: "Task list for remote VPS hosting for sandbox instances"
---

# Tasks: Remote VPS hosting for sandbox instances

**Input**: Design documents from `/specs/014-remote-vps-hosting/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-and-mcp.md, quickstart.md — all present.

**Tests**: included (mirrors this repo's established pattern for spec 013/plugin-check — mock-based, no docker/VPS needed for unit coverage).

**Organization**: grouped by user story (US1/US2/US3, matching spec.md's priorities: US1=P1, US2=P1, US3=P2).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependencies)
- **[Story]**: which user story this task belongs to

## Path Conventions

Single project (this IS the single project — sandbox is one Python package). Real paths
per `plan.md`'s Project Structure section — no placeholders below.

---

## Phase 1: Setup

**Purpose**: new module skeletons so later tasks have somewhere to land.

- [x] T001 Create `sandbox/core/_remote.py` (module docstring only, matching `_licensing.py`'s shape)
- [x] T002 [P] Create `sandbox/commands/remote.py` (module docstring citing `docs/remote-hosting.md` + `specs/014-remote-vps-hosting/`)
- [x] T003 [P] Create `sandbox/commands/deploy.py` (module docstring, same citation convention)
- [x] T004 [P] Create `tests/test_remote.py` (imports, mirrors `tests/test_ci.py`'s top-of-file shape)
- [x] T005 [P] Create `scripts/install-remote.sh` (shebang + `set -euo pipefail`, executable bit, mirrors `scripts/install-ubuntu.sh`'s header)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the `RemoteTarget` config layer every user story reads or writes.

**⚠️ CRITICAL**: No user story task may start until this phase is complete.

- [x] T006 Implement `_remote_block()` / `_write_remote_block()` in `sandbox/core/_remote.py` — read-modify-write the `remotes:` block in `sandbox.local.yml`, `chmod 0o600`, never echo `bearer_token`, verbatim mirror of `sandbox/core/_licensing.py`'s `_licensing_block()`/`_write_licensing_block()`
- [x] T007 Implement remote-name validation in `sandbox/core/_remote.py` — same character class as `_project_slug` (lowercase letters, numbers, hyphen, underscore), raising `ConfigError` on violation
- [x] T008 Implement an SSH command-runner helper in `sandbox/core/_remote.py` (e.g. `ssh_run(remote: dict, command: str) -> subprocess.CompletedProcess`) — shells to the system `ssh` binary using the stored `ssh` connection string, `check=False`, captures stdout/stderr for callers to interpret
- [x] T009 Wire the `remote` subparser group (`add`, `list`, `provision`, `up`, `down`, `remove`) and the `deploy` subparser into `sandbox/cli.py` (argparse structure only — flags per `contracts/cli-and-mcp.md`, handlers stubbed to `NotImplementedError` for now)
- [x] T010 [P] Unit tests for `_remote_block`/`_write_remote_block`/name validation in `tests/test_remote.py` (round-trip read-modify-write preserves unrelated `sandbox.local.yml` content, matches `_licensing.py`'s own test coverage shape)

**Checkpoint**: config layer works and is tested; CLI parses all new flags; user story work can begin.

---

## Phase 3: User Story 1 - Register and provision a remote VPS (Priority: P1) 🎯 MVP (part 1)

**Goal**: a developer can register a VPS, provision it with one command, list/remove it — no manual SSH install steps (spec FR-001, FR-002, FR-003, FR-004, FR-005; SC-001, SC-005).

**Independent Test**: register a VPS, provision it, confirm `remote list` reports it reachable + provisioned — without running any project on it yet.

### Tests for User Story 1

- [x] T011 [P] [US1] Unit test: `cmd_remote_add` registers a new remote and is idempotent on re-add (same name updates, doesn't error) in `tests/test_remote.py`
- [x] T012 [P] [US1] Unit test: `cmd_remote_list` reports reachability + provisioned status per remote (mock the SSH ping) in `tests/test_remote.py`
- [x] T013 [P] [US1] Unit test: `cmd_remote_remove` only touches local config, never issues an SSH teardown command, in `tests/test_remote.py`
- [x] T014 [P] [US1] Unit test: provisioning a remote a second time succeeds cleanly (idempotency, FR-005) — mock two successive `cmd_remote_provision` calls in `tests/test_remote.py`

### Implementation for User Story 1

- [x] T015 [US1] Implement `cmd_remote_add` in `sandbox/commands/remote.py` (depends on T006, T007)
- [x] T016 [US1] Implement `cmd_remote_list` in `sandbox/commands/remote.py` — live SSH reachability ping per remote (depends on T008)
- [x] T017 [US1] Implement `cmd_remote_remove` in `sandbox/commands/remote.py` — local-config-only, with an explicit confirmation message that any VPS-side instance is unaffected (spec FR-003, Edge Cases)
- [x] T018 [US1] Write `scripts/install-remote.sh`: install Docker CE + compose plugin (reuse the package-manager-detection logic already in `sandbox/core/_docker.py` for apt/dnf/pacman/zypper), optionally install/join Tailscale when `--control tailscale` is selected, install the `sb` runtime, provision the `visit` tools venv (Playwright + headless Chromium)
- [x] T019 [US1] Implement `cmd_remote_provision` in `sandbox/commands/remote.py` — SSH in, ask/default to public HTTPS unless `--control tailscale` is selected, run `scripts/install-remote.sh`, mint a bearer token, record `control_transport`/`control_url`/`mcp_port`/`bearer_token`/`provisioned: true` via T006's write function (depends on T008, T018)
- [x] T020 [US1] Implement `cmd_remote_up` / `cmd_remote_down` in `sandbox/commands/remote.py` (start/stop the remote MCP server process over SSH; does not touch WordPress instances)
- [x] T021 [US1] `register()` all `remote` subcommands at the bottom of `sandbox/commands/remote.py`, per the existing command-registry pattern

**Checkpoint**: User Story 1 fully functional and independently testable (register → provision → list → remove).

---

## Phase 4: User Story 2 - Deploy local code to a remote target on demand (Priority: P1) 🎯 MVP (part 2)

**Goal**: `sb deploy` pushes committed + uncommitted local state to a provisioned remote, replacing (not stacking) on every run, working even for unpushed branches (spec FR-006, FR-007, FR-008, FR-009; SC-002).

**Independent Test**: make a local change (including an uncommitted one), deploy it, confirm the remote's code matches the local working tree exactly.

### Tests for User Story 2

- [x] T022 [P] [US2] Unit test: git push command construction (`HEAD:refs/heads/<branch>` against the correct VPS-side path) in `tests/test_remote.py`
- [x] T023 [P] [US2] Unit test: uncommitted-diff capture includes both tracked-file changes AND untracked files (plain `git diff` alone must NOT be treated as sufficient) in `tests/test_remote.py`
- [x] T024 [P] [US2] Unit test: a second deploy fully replaces the first deploy's uncommitted layer (no stacking) — simulate two deploys with different uncommitted diffs and assert only the second's changes remain applied, in `tests/test_remote.py`
- [x] T025 [P] [US2] Unit test: deploying to an unprovisioned remote fails with a clear, actionable error naming provisioning as the missing step, in `tests/test_remote.py`
- [x] T026 [P] [US2] Unit test: a deploy that fails partway (mocked SSH failure) leaves no half-updated state — the next deploy attempt is unaffected, in `tests/test_remote.py`

### Implementation for User Story 2

- [x] T027 [US2] Implement deploy-target path resolution in `sandbox/core/_remote.py` — `$SANDBOX_HOME/deploy-src/<canonical-project-slug>` on the VPS, reusing `sandbox_core`'s existing `_project_slug`/`_canonical` resolution so both sides derive the same path with no extra bookkeeping
- [x] T028 [US2] Implement lazy deploy-target git repo creation in `sandbox/core/_remote.py` — on a project's first deploy to a given remote, `git init` + `git config receive.denyCurrentBranch updateInstead` at the resolved path (depends on T027)
- [x] T029 [US2] Implement the committed-layer push in `sandbox/core/_remote.py` — `git push <vps-remote-url> HEAD:refs/heads/<branch>` (depends on T028)
- [x] T030 [US2] Implement the uncommitted-layer capture + apply in `sandbox/core/_remote.py` — `git diff` for tracked changes, a `git status --porcelain` pass for untracked files, `git reset --hard <sha>` on the VPS BEFORE applying, then `git apply` (tracked) + file transfer (untracked) (depends on T029)
- [x] T031 [US2] Implement `cmd_deploy` orchestration in `sandbox/commands/deploy.py` — resolves the remote, calls T028-T030 in sequence, prints/returns the JSON shape from `contracts/cli-and-mcp.md` (depends on T027-T030)
- [x] T032 [US2] Implement deploy failure handling in `sandbox/commands/deploy.py` per spec FR-009 — any failure before the VPS reset leaves the VPS untouched; any failure after is safely recoverable by re-running deploy
- [x] T033 [US2] `register()` `cmd_deploy` at the bottom of `sandbox/commands/deploy.py`
- [x] T034 [US2] Implement the `remote_deploy(project_dir, remote)` MCP tool in `mcp/wp-server/tools/remote.py` — thin subprocess wrapper mirroring `run_tests`/`run_plugin_check`'s exact calling convention (shells to `./sb deploy --project-dir ... --remote ... --json`, parses the last JSON line)
- [x] T034A [US2/US3] Extend `sb deploy` and `remote_deploy` with one-shot `ensure`/`expose` support — after deploy, boot/refresh the remote WP instance, activate the plugin slug, configure the public HTTPS route, update WordPress URLs, and return `instance` + `url` in JSON (spec FR-011)

**Checkpoint**: User Stories 1 AND 2 both independently functional — register, provision, and deploy code to a remote.

---

## Phase 5: User Story 3 - Run a full sandbox instance on the remote (Priority: P2)

**Goal**: a booted remote instance is reachable via a second, separately-registered MCP server and behaves identically to a local instance for every existing operation, with zero change to existing MCP tool files (spec FR-010, FR-011, FR-012, FR-013, FR-014; SC-003).

**Independent Test**: boot an instance on a provisioned, deployed remote and successfully run a representative operation set (a WP-CLI command, a log read, a screenshot) against it.

### Tests for User Story 3

- [x] T035 [P] [US3] Unit test: targeting a remote with a project configured for the Docker-less local-only runtime mode fails cleanly (FR-014) in `tests/test_remote.py`
- [x] T036 [P] [US3] Unit test: `mcp/wp-server/server.py`'s transport-selection branch defaults to stdio when invoked without remote-server-mode flags (zero behavior change, FR-016) in `tests/test_remote.py` or a new `tests/test_server_transport.py`, matching wherever `server.py`'s existing tests (if any) live

### Implementation for User Story 3

- [x] T037 [US3] Implement the transport-selection branch in `mcp/wp-server/server.py` — default unchanged `mcp.run()` over stdio; a new remote-server-mode path serving streamable HTTP with bearer-token auth, binding to `127.0.0.1` behind public HTTPS by default or to a Tailscale address when explicitly selected (never `0.0.0.0`, per FR-015)
- [x] T038 [US3] Wire remote-server-mode startup into `cmd_remote_provision` in `sandbox/commands/remote.py` — starts the remote MCP server in streamable-http mode as part of provisioning (depends on T037, T019)
- [x] T039 [US3] Implement the Herd hard-error (FR-014) — reject cleanly when a project configured for `server: herd` is targeted with any remote command, in `sandbox/core/_remote.py`
- [x] T040 [US3] Document the second-MCP-server registration step (how a user adds `sandbox-<remote-name>` as a Claude Code MCP server pointed at the HTTPS control URL or optional Tailscale address + bearer token) in `docs/remote-hosting.md`

**Checkpoint**: all 3 user stories independently functional — a remote instance behaves exactly like a local one from the user's perspective.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T041 [P] Write `docs/remote-hosting.md` (quick-reference design doc companion, matches `docs/plugin-check.md`'s pattern; does NOT replace `docs/remote-hosting-prd.md`, which stays as the deeper research/rationale doc)
- [x] T042 [P] Add a `README.md` mention of remote hosting (`./sb remote`/`./sb deploy`), matching this session's existing per-feature README rows
- [x] T043 Run the full local test suite (`.cli-venv/bin/python -m unittest discover -s tests`) and confirm zero regressions — this is the FR-016/SC-004 release gate
- [x] T044 Run `quickstart.md`'s Phase 0 spike + all 5 scenarios against a REAL, disposable VPS — Constitution Principle IV. HTTPS MCP acceptance completed on 2026-07-16 against `scaleway-sandbox`: authenticated streamable HTTP at `https://sandbox-control.asb.bd/mcp` ran `ensure_instance`, `fs_read`, `wp_cli`, `visit`, and `run_tests` for `/home/alim/sandbox/deploy-src/html-social-share-buttons`. Results: file read succeeded, WP-CLI returned 7.0, browser visit returned HTTP 200 with no errors, and PHPUnit passed 6 tests / 12 assertions.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies, can start immediately.
- **Foundational (Phase 2)**: depends on Setup — BLOCKS all user stories (the config layer every story reads/writes).
- **User Story 1 (Phase 3)**: depends on Foundational only.
- **User Story 2 (Phase 4)**: depends on Foundational; ALSO functionally depends on User Story 1 existing (you can't deploy to a remote that was never registered/provisioned) even though both are P1 — implement in the order shown, not in parallel, despite the shared priority.
- **User Story 3 (Phase 5)**: depends on Foundational; functionally depends on User Story 2 (an instance needs deployed code to be useful) though its own MCP-transport work (T037) is independent and CAN be built in parallel with Phase 4.
- **Polish (Phase 6)**: depends on all three user stories being complete.

### Parallel Opportunities

- All `[P]`-marked Setup tasks (T002-T005) run in parallel.
- All `[P]`-marked test tasks within a phase run in parallel (different test methods, same file — genuinely independent assertions).
- T037 (MCP transport branch) has no dependency on Phase 4's deploy work and can be built in parallel with it, even though Phase 5 is sequenced after Phase 4 above for narrative clarity.
- T041/T042 (docs) run in parallel with each other and with T043/T044.

---

## Parallel Example: User Story 1's tests

```bash
Task: "Unit test: cmd_remote_add idempotency in tests/test_remote.py"
Task: "Unit test: cmd_remote_list reachability reporting in tests/test_remote.py"
Task: "Unit test: cmd_remote_remove is local-only in tests/test_remote.py"
Task: "Unit test: provisioning idempotency in tests/test_remote.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1 + 2 only)

1. Complete Phase 1 (Setup) + Phase 2 (Foundational).
2. Complete Phase 3 (US1: register/provision/list/remove).
3. Complete Phase 4 (US2: deploy).
4. **STOP and VALIDATE**: run `quickstart.md`'s Scenarios 1-3 against a real VPS. This
   alone (register a VPS, provision it, deploy code to it, confirm it landed correctly)
   is a legitimately useful, demoable increment even before a full WordPress instance
   ever boots there.

### Incremental Delivery

1. Setup + Foundational → config layer ready.
2. US1 → test independently (Scenario 1) → this alone lets a developer prepare a remote in advance.
3. US2 → test independently (Scenarios 2-3) → deploying code now works end-to-end.
4. US3 → test independently (Scenario 4) → the full "use it like a local instance" promise is realized.
5. Phase 6 → Scenario 5 (zero local behavior change) + docs + the mandatory live Phase 0 spike.

---

## Notes

- `[P]` tasks = different files or independent assertions, no ordering dependency.
- Tests are written before their corresponding implementation task within each story,
  per this repo's `tests/test_ci.py`/`tests/test_plugin_check.py` precedent, though they
  are not a strict TDD gate — run them red-then-green where practical.
- Run the FULL suite (`.cli-venv/bin/python -m unittest discover -s tests`) after each
  completed task, not just at the end of a phase — this repo's established discipline
  from every feature built earlier this session.
- Per Constitution Principle IV, T044 (the real-VPS live-verification pass) is NOT
  optional polish — the feature is unproven without it, no matter how green the unit
  suite is.
