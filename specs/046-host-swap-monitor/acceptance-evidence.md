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
