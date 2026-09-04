# Feature 046 Acceptance Evidence

This ledger separates evidence classes. Local and synthetic results do not prove live-host
safety or release readiness.

## Local RED/GREEN evidence

- Foundational RED was captured against the pre-existing scaffold with
  `python3 -m unittest -v tests.test_host_memory_models tests.test_host_memory_policy
  tests.test_host_memory_repository tests.test_host_memory_remote`: 22 tests ran with five
  failures and two errors. Missing behavior was specific to strict model types, receipt
  persistence, immutable operation identity, total history retention, consecutive warning
  semantics, request range/budget validation, and response-envelope bounds.
- After the bounded foundational implementation, the same four-module command ran 27 tests
  and passed in 0.015 seconds.
- User Story 1 RED was captured against the pre-existing provider/service scaffold with
  `python3 -m unittest -v tests.test_host_memory_provider tests.test_host_memory_service
  tests.test_host_memory_remote tests.test_resource_interfaces.TestHostMemoryResourceInterfaces
  tests.test_host_memory_interfaces`: 26 tests ran with one failure and four errors. Missing
  behavior was specific to cgroup v1/v2 normalization, receipt/monitor observation, malformed
  evidence handling, and service freshness/warning composition.
- After the bounded read-only implementation, the combined foundational and User Story 1
  command ran 52 tests and passed in 0.104 seconds. This is local synthetic evidence only;
  it does not prove a deployed service, a live Linux host, reboot persistence, or mutation.
- The exact T027 module set, including the full adjacent resource-interface module, ran 62
  tests and passed in 0.283 seconds.
- After fetching `origin/latest`, the normal merge reported `Already up to date`. The final
  foundational command ran 29 tests and passed in 0.014 seconds; the exact T027 module set
  then ran 63 tests and passed in 0.248 seconds. The focused controller transport class ran
  two tests and passed in 0.025 seconds. Python compilation and `git diff --check` passed.
- `tests.test_server_transport` could not run under system Python because `httpx` is absent,
  and `mcp/wp-server/.venv/bin/python` does not exist in this worktree. This is an explicit
  local dependency gap, not a passing server-transport result.
- Second independent-review correction RED was captured before its production fixes with
  `python3 -m unittest -q tests.test_host_memory_models tests.test_host_memory_provider
  tests.test_host_memory_repository tests.test_host_memory_interfaces
  tests.test_resource_interfaces.TestHostMemoryResourceInterfaces`: 29 tests ran with eight
  failures and five errors. Missing behavior covered contradictory/path-bearing typed status,
  fixed owner-safe receipt and artifact attestation, fixed bounded history with correct
  truncation, projection-only authority exposure, and exact zero-budget propagation.
- After the second correction, the exact T027 module set ran 70 tests and passed in 0.219
  seconds. The broader foundation, User Story 1, focused transport, interface, and help gate
  ran 97 tests and passed in 1.030 seconds. Python compilation and `git diff --check` passed.
  The evidence is local and synthetic only; no remote, apply, live-host, or reboot action ran.
- After the required normal merge of `origin/latest` at
  `a0845e0f9438788820199ee4229f4484a93466f9`, the exact T027 set ran 70 tests and
  passed in 0.285 seconds; the broader 97-test gate passed in 1.167 seconds. The 50 focused
  tests for the newly merged bounded remote-ensure transport passed in 1.114 seconds, proving
  the Feature 046 merge did not overwrite that reviewed behavior. Compilation and diff checks
  remained clean.
- Final history-reader review RED was captured with two focused repository tests: one failed
  because four ordinary retained samples falsely marked status retention truncated, and one
  errored because the repository lacked an identity-bound ancestor-root reader. After the
  correction, status derives its last three warning samples independently from retention
  completeness, while fixed history opens walk every trusted ancestor by directory FD and
  verify the final `O_NOFOLLOW` FD identity, type, owner, mode, link count, size, deadline,
  and read bound. Deterministic symlink and oversized replacement races now refuse.
- With current `origin/latest` still at `a0845e0f9438788820199ee4229f4484a93466f9`,
  the exact T027 set ran 70 tests and passed in 0.259 seconds; the broader Feature 046,
  transport, interface, and help gate ran 98 tests and passed in 1.091 seconds; and the 50
  bounded remote-ensure adjacent tests passed in 1.118 seconds. Compilation and diff checks
  passed. No remote or live host was accessed.
- Post-open race and cgroup-v1 re-review RED was captured with three focused tests: leaf or
  ancestor disappearance after a successful stat was misreported as clean missing, 40
  rejected unsafe-ancestor calls leaked 40 directory descriptors, and contradictory v1
  memsw limit/usage arithmetic was reported as known. All three failed before correction.
- After narrowing clean-missing to only the initial leaf stat, making every opened directory
  FD use explicit ownership transfer/final cleanup, and rejecting per-level v1 memsw
  contradictions, the exact T027 gate ran 71 tests and passed in 0.253 seconds. The broader
  Feature 046, focused transport, interface, and help gate ran 101 tests and passed in 1.144
  seconds; 50 adjacent bounded remote-ensure tests passed in 1.095 seconds. Current
  `origin/latest` remained `a0845e0f9438788820199ee4229f4484a93466f9`; compilation and
  diff checks passed, with no remote or live-host action.
- Independent-review correction RED was captured before the production fixes with
  `python3 -m unittest -v tests.test_host_memory_remote tests.test_host_memory_service
  tests.test_host_memory_provider tests.test_host_memory_repository
  tests.test_host_memory_interfaces tests.test_resource_interfaces.TestHostMemoryResourceInterfaces`:
  42 tests ran with three failures and 15 errors. The failures covered strict full-status
  validation, target-bound swap ownership, retained-history status composition, hierarchical
  cgroup evidence, and status-only reachability. A follow-up nested retention-type regression
  also failed alone before its validator was tightened.
- After those bounded corrections, the exact T027 module set ran 65 tests and passed in
  0.247 seconds. The broader foundation, User Story 1, focused transport, interface, and help
  command ran 90 tests and passed in 1.128 seconds. Future `swap-plan`, `swap-apply`, and
  `swap-history` parser attempts intentionally emitted argparse rejection text during these
  passing tests. A fresh `origin/latest` fetch followed by the required normal merge reported
  `Already up to date`. Python compilation and `git diff --check` passed. This remains local
  synthetic evidence only; no remote, live host, apply, or reboot action was performed.

- The requested `.cli-venv/bin/python` runtime was absent (`no such file or directory`).
- `python3 -m unittest -v tests.test_host_memory_models tests.test_host_memory_policy
  tests.test_host_memory_repository tests.test_host_memory_remote
  tests.test_host_memory_provider tests.test_host_memory_service
  tests.test_host_memory_interfaces`: first run had 23 tests with one failure and one error;
  after bounded model/error-code fixes the same 23 tests passed.
- `python3 -m unittest -v
  tests.test_resource_interfaces.TestHostMemoryResourceInterfaces
  tests.test_resource_remote.TestHostMemoryRemoteTransport`: 5 passed.
- The broader `tests.test_resource_interfaces tests.test_resource_remote
  tests.test_remote_service_help` run was stopped after 150 seconds while an existing
  remote-probe test executed large local engine inventories. No terminal suite verdict was
  obtained; it is not recorded as a pass.
- After tightening apply reachability and zero-value CLI validation, the focused current-
  source command ran 30 tests and passed in 0.141 seconds. Python compilation and
  `git diff --check` also passed.
- `python3 -m unittest -q tests.test_resource_interfaces
  tests.test_remote_service_help`: 36 passed in 1.254 seconds.
- The seven-suite adjacent gate ran 352 tests in 8.900 seconds and ended with 42 environment
  errors because the worktree has no CLI venv and system Python lacks PyYAML. This is not a
  pass and no failing assertion was attributed to Feature 046.
- The original scaffold tests were not executed against a wholly unimplemented baseline.
  The later strict foundational and User Story 1 tests were executed against that scaffold
  before their corresponding bounded implementation changes, producing the RED results
  above. No RED claim is made for later user stories.
- User Story 2 planning RED (T033) was captured against the pre-planning scaffold with
  `python3 -m unittest tests.test_host_memory_policy tests.test_host_memory_provider
  tests.test_host_memory_service tests.test_host_memory_remote
  tests.test_resource_remote.TestHostMemoryRemoteTransport
  tests.test_resource_interfaces.TestHostMemoryResourceInterfaces`: 60 tests ran with
  nine failures and thirteen errors in 0.119 seconds. Every failing assertion belongs
  to the fifteen new T028-T032 tests; the new requested/effective-policy, inventory,
  confirmation, expiry, and plan-expiry/drift tests already pass against the existing
  `build_plan`/`plan_current` scaffold. Missing behavior was specific to fail-closed
  invalid-size handling in `build_plan` (raw `ValueError` escapes instead of
  `PolicyRefusal`), already-enabled convergence, controller-owned service plan
  orchestration, provider enable transactions, strict apply canonical-plan schemas
  and typed results, pre-control apply refusal, and `swap-plan`/`swap-apply` CLI
  parsing and confirmation. No pre-existing test regressed. Local synthetic evidence
  only.
- User Story 2 planning GREEN (T038) after the read-only planning implementation
  (T034 policy, T036 service, T037 CLI; T035 repository deferred): `python3
  -m unittest tests.test_host_memory_policy tests.test_host_memory_repository
  tests.test_host_memory_service tests.test_resource_interfaces` ran 71 tests and
  passed in 0.336 seconds. The read-only planning path is GREEN while protected
  apply remains unavailable (`swap-apply` refuses with `confirmation_required`
  without `--confirm` and `apply_unavailable` with it, pending the US3 safety
  gate). T035 repository work is deferred because the parallel server-config
  branch already modifies `sandbox/resources/host_memory/repository.py`; merging
  that file concurrently would conflict. Local synthetic evidence only.
- User Story 3 safety-gate RED (T043) was captured against the pre-gate scaffold
  with `python3 -m unittest tests.test_host_memory_policy
  tests.test_host_memory_provider tests.test_host_memory_remote
  tests.test_resource_remote.TestHostMemoryRemoteTransport
  tests.test_resource_interfaces.TestHostMemoryResourceInterfaces`: 60 tests ran
  with seventeen failures and six errors in 0.128 seconds. Every failing assertion
  belongs to the new T039-T041 tests plus the still-open T029/T031 planning tests
  (provider enable transactions and strict apply contracts land in Phase 6 and the
  US3 protocol gate). Missing behavior was specific to unregistered/unsafe target
  refusal, stale-observation refusal, ambiguous-ownership refusal, provider
  preflight without side effects, and pre-control apply refusal. T042 interface
  composition tests are deferred: the parallel server-config branch already
  modifies `tests/test_host_memory_interfaces.py`. Local synthetic evidence only.
- User Story 3 fail-closed GREEN (T047, complete) after the refusal and preflight
  implementation and reconciliation with `latest`: following the clean merge of
  `latest` (which landed Features 052 and 053), all previously deferred collision
  points in `tests/test_host_memory_interfaces.py`, `mcp/wp-server/server.py`,
  and `sandbox/core/_remote.py` are resolved. The focused User Story 3 test suite
  (`tests.test_host_memory_policy`, `tests.test_host_memory_remote`,
  `tests.test_host_memory_interfaces`,
  `tests.test_resource_remote.TestHostMemoryRemoteTransport`, and
  `tests.test_resource_interfaces.TestHostMemoryResourceInterfaces`) ran 46 tests
  and passed in 0.111 seconds. The US3 refusal matrix, expiry/drift/plan-current,
  preflight-before-side-effects, exact wire allowlist with no remote plan action,
  and bounded apply-result contracts are fully GREEN. Protected apply remains
  unregistered and fail-closed; no mutation is reachable. Local synthetic evidence
  only.

- User Story 2 protected apply GREEN (T053): with the fail-closed safety gate
  (Phase 5) fully green, the protected apply implementation is completed:
  1. `HostProvider.enable(plan)` (T048) executes the fixed-path swap creation
     and monitor enable transaction behind the completed preflight gate with
     restrictive permissions (0600 on swap file, 0644 on units/configs),
     policy-selected size, command validation restricted exclusively to fixed
     artifact paths, and idempotent convergence (`already_current` without side
     effects for identical plans).
  2. `HostMemoryService.apply` (T049) orchestrates authorization, exact confirmation
     requirement (`confirmation_required`), current plan and repository lookup
     (`plan_not_found`), plan expiry checks against `expires_at` (`plan_expired`),
     canonical plan envelope translation, replay-safe operation identity generation,
     and mapping of normative outcomes (`applied`, `already_current`, `refused`,
     `failed`).
  3. `mcp/wp-server/server.py` (T050) registers `host_memory_apply` alongside
     `host_memory_status`, rejecting any uncanonical or foreign-target payloads,
     and dispatching through the provider's enable transaction.
  4. `sandbox/core/_remote.py` (T051) validates request payloads, rejecting
     arbitrary paths or shell overrides before control invocation, and routes
     strictly over authenticated control with no direct SSH fallback.
  5. `sandbox/commands/resources.py` (T052) provides `resources swap-apply`
     CLI wiring with confirmation enforcement, `--plan-id` lookup, invalid-option
     refusal (`invalid_mode`), and text/JSON envelope rendering.
  6. Test verification (T053): the complete User Story 2 test suite
     (`tests.test_host_memory_models`, `tests.test_host_memory_policy`,
     `tests.test_host_memory_repository`, `tests.test_host_memory_provider`,
     `tests.test_host_memory_service`, `tests.test_host_memory_remote`,
     `tests.test_host_memory_interfaces`, and `tests.test_resource_interfaces`)
     ran 126 tests and passed in 0.391s. Adjacent regression suites
     (`test_storage_monitor_policy`, `test_storage_monitor_schedule`,
     `test_storage_monitor_runner`, `test_mcp_resource_tier`,
     `test_resource_service`, `test_workspace_contracts`) ran 146 tests and passed
     in 0.239s. CLI end-to-end invocations verify refusal on missing confirmation,
     missing remote, missing plan ID, and invalid options. Local synthetic
     evidence only.

- User Story 4 aggregate history RED (T059): failing tests added for bounded
  history samples, 5-second sample collector deadline, lock non-overlap, service
  history orchestration, remote history response schema validation, and CLI
  swap-history dispatch:
  1. `tests.test_host_memory_models`: `test_history_window_enforces_ordering_and_bounds`
     failed with `AssertionError: ValueError not raised` when testing chronological
     descending sample ordering enforcement and sample limit checks.
  2. `tests.test_host_memory_provider`: `test_sample_deadline_and_lock_no_overlap`
     failed with `AttributeError: 'HostProvider' object has no attribute 'collect_sample'`
     before lock/deadline collector implementation.
  3. `tests.test_host_memory_service`: `test_history_retrieves_bounded_window_and_enforces_limits`
     failed with `AttributeError: 'HostMemoryService' object has no attribute 'history'`
     before service history orchestration.
  4. `tests.test_host_memory_remote`: `test_remote_history_response_validation_enforces_history_window_schema`
     failed with `AssertionError: RemoteProtocolError not raised` when verifying
     untyped/malformed history payloads.
  5. `tests.test_resource_interfaces`: `test_only_completed_status_action_is_registered`
     and `test_swap_history_cli_registered_and_parses_arguments` failed with
     `argparse.ArgumentError: argument action: invalid choice: 'swap-history'`
     before CLI registration.
  Local synthetic evidence only.


- User Story 4 aggregate history GREEN (T066):
  1. `HistoryWindow` (T060) in `sandbox/resources/host_memory/models.py` implements
     bounded sample and warning serialization, chronological descending sample ordering
     enforcement, max 1,000 samples, and 1 MiB bounding with strict rejection of
     sensitive raw paths/filesystems.
  2. `HostProvider.collect_sample` (T061, T062) in `sandbox/resources/host_memory/provider.py`
     implements aggregate-only monitor sampling with a hard 5-second deadline, durable
     `partial` status with `collector_timeout` error on budget overrun, non-overlapping
     execution locks, and automatic next-run recovery.
  3. `HostMemoryRepository.record_sample` and `history_window` (T063) in
     `sandbox/resources/host_memory/repository.py` provide atomic append, deterministic
     retention and rotation (max 10,000 samples / 1 MiB), corrupt tail isolation, and
     range/limit filtering.
  4. `HostMemoryService.history` (T064) in `sandbox/resources/host_memory/service.py`
     orchestrates range validation (`invalid_range`), limit bounding (`invalid_limit`),
     remote control invocation, and normative outcome envelopes (`complete`, `partial`,
     `refused`, `failed`). `mcp/wp-server/server.py` registers `host_memory_history`
     alongside status and apply under `_resource_contract` and dispatches via repository.
  5. `sandbox/core/_remote.py` and `sandbox/commands/resources.py` (T065) provide typed
     history transport and `resources swap-history` CLI wiring with `--since`, `--until`,
     `--limit`, `--budget`, and text/JSON envelope formatting. Rejection of invalid modes
     and unconfirmed/conflicting options is verified.
  6. Test verification (T066): 136 tests ran across all 8 host-memory suites
     (`tests.test_host_memory_models`, `tests.test_host_memory_policy`,
     `tests.test_host_memory_repository`, `tests.test_host_memory_provider`,
     `tests.test_host_memory_service`, `tests.test_host_memory_remote`,
     `tests.test_host_memory_interfaces`, `tests.test_resource_interfaces`, and
     `tests.test_resource_remote.TestHostMemoryRemoteTransport`) and all passed in
     0.440s. CLI invocations verified for refusal on missing remote, invalid options,
     and invalid limits. Local synthetic evidence only.


- User Story 5 owned disable RED (T071): failing tests added for disable policy
  rules (reverse teardown ordering, strict headroom, ownership refusal), provider
  disable transaction (reverse teardown, history preservation), service disable
  planning and confirmation orchestration, CLI swap-disable parsing, and repository
  disabled-state receipt recording:
  1. `tests.test_host_memory_policy`: `test_disable_plan_policy_rules` failed with
     `AssertionError: 'swap_file' != '/etc/systemd/system/sandbox-host-memory-monitor.timer'`
     verifying reverse teardown ordering in intended changes.
  2. `tests.test_host_memory_provider`: `test_disable_transaction_enforces_reverse_order_and_preserves_history`
     failed with `AttributeError: 'HostProvider' object has no attribute 'disable'`
     before disable transaction implementation.
  3. `tests.test_host_memory_service`: `test_disable_planning_and_apply_orchestration`
     failed with `AttributeError: 'HostMemoryService' object has no attribute 'disable_plan'`
     before service disable plan and apply orchestration.
  4. `tests.test_resource_interfaces`: `test_only_completed_status_action_is_registered`
     and `test_swap_disable_requires_remote_and_confirmation` failed with
     `argparse.ArgumentError: argument action: invalid choice: 'swap-disable'`
     before CLI registration.
  5. `tests.test_host_memory_repository`: `test_disable_phase_journaling_and_disabled_receipt`
     failed with `AttributeError: 'HostMemoryRepository' object has no attribute 'record_disable_receipt'`
     before repository disabled receipt recording.
  Local synthetic evidence only.

- User Story 5 owned disable GREEN (T076): owned disable planning, reverse teardown
  execution, history preservation, disabled-state receipt, and swap-disable CLI:
  1. `tests.test_host_memory_policy`: `test_disable_plan_policy_rules` verified reverse
     teardown ordering (`monitor_timer` through `swap_file`), strict RAM headroom
     calculation (`available <= required` refusal), and ownership checks.
  2. `tests.test_host_memory_provider`: `test_disable_transaction_enforces_reverse_order_and_preserves_history`
     verified reverse order teardown (timer disable, service stop, swapoff, swap_unit disable,
     swappiness sysctl removal, swapfile removal) with applied outcome.
  3. `tests.test_host_memory_service`: `test_disable_planning_and_apply_orchestration`
     verified disable planning, unconfirmed refusal (`confirmation_required`), and confirmed
     application success.
  4. `tests.test_resource_interfaces`: `test_only_completed_status_action_is_registered`
     and `test_swap_disable_requires_remote_and_confirmation` verified `swap-disable` CLI
     registration and confirmation gating.
  5. `tests.test_host_memory_repository`: `test_disable_phase_journaling_and_disabled_receipt`
     verified phase journaling and minimal disabled-state receipt recording (`lifecycle_state="disabled"`).
  Suite run: 141 tests in 0.33s, 0 failures, 0 errors. Local synthetic evidence only.

## Fixed authenticated synthetic-provider evidence

Pending T096.

## Human review

T095 open. Consequential authentication, authorization, cryptographic identity, privileged
fixed-path, ownership, rollback, privacy, dependency-trust, and production-path review has
not been approved by a human.

## Live Linux evidence

T097-T098 open. No remote was accessed, updated, or mutated.

## Reboot evidence

T099 open. Reboot persistence is unverified.

## Reconciliation

T100 remains open until performed evidence is reconciled. No release-readiness claim exists.
