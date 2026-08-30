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
