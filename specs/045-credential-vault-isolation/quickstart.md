# Quickstart: Credential Vault and Isolation Proof

This is a validation-oriented quickstart for the first implementation. It does
not claim that the planned credential commands exist yet.

## Preconditions

1. Use a non-`main` checkout on a supported Ubuntu 24.04 host.
2. Confirm the existing managed-native support matrix and proof status. The
   feature must remain blocked while the matrix is
   `implemented_unproven`/`adoptable=false`.
3. Confirm that the operator has an approved opaque source reference. Do not
   paste a secret into a command, environment variable, issue, log, or test
   fixture.

## Existing proof gate

Run the existing read-only checks before attempting any binding:

```sh
./sb native support --json
./sb native preflight --project-dir . --json
```

The existing acceptance harness is the predecessor gate for this feature:

```sh
python3 tests/live_native_acceptance.py --help
```

Run it only on an authorized supported host with its documented instance and
cleanup prerequisites. A partial or missing hostile/grant/revoke/exhaustion/
warm-start/cleanup result is a blocked feature, not a passing result.

## Planned binding flow

The implementation must document the final CLI/MCP names before enabling them.
The flow is intentionally explicit:

1. Inspect a non-secret capability/proof report.
2. Create a binding from an existing opaque reference and exact request scope.
3. Start or reconcile the broker; observe `credential_pending` until source,
   policy, egress, broker, and effective-isolation proofs match.
4. Send a request through the reviewed guest client contract.
5. Inspect only IDs, digests, state, expiry, decision, and reason codes.
6. Revoke, restart, and clean up; verify no future request is admitted until
   fresh recovery proof completes.

## Acceptance matrix

The implementation quickstart must exercise at least:

| Case | Expected result |
|---|---|
| Exact scheme/host/port/method/path and active binding | Bounded upstream result or bounded upstream error |
| Wrong host/port/method/path/scheme | Refused before credential resolution/use |
| Unknown or stale source reference | Refused; no plaintext output |
| Expired/revoked binding | Refused; active sessions closed within bound |
| Redirect or DNS pin drift | Refused unless separately bound and proven |
| Oversized/unsupported request or response | Stable bounded error |
| Broker/instance restart | `credential_pending`, then ready only after fresh proof |
| Missing or drifted native proof | Capability reports blocked/unproven; no workload entry |
| Hostile guest inspection | Zero credential bytes on enumerated exposure surfaces |

## Foundational local evidence

The contract-only slice is available for review without enabling the managed
runtime or resolving a real credential:

```sh
python3 -m unittest tests.test_credential_capability_report \
  tests.test_credential_binding tests.test_credential_resolver
python3 -m unittest tests.test_native_ownership tests.test_native_network_reservation \
  tests.test_managed_plan tests.test_managed_native_adapter tests.test_isolation_credentials \
  tests.test_native_destroy tests.test_native_recovery
```

The first command currently passes 19 tests and the regression command passes
53 tests. These are local model/repository/source-policy checks only. They do
not close the Ubuntu 24.04 predecessor proof gate, start a broker, or authorize
credential-bearing use. The capability remains `implemented_unproven` with
`adoptable=false` until T003 and the later hostile live matrix are independently
verified.

## Regression evidence

Run the narrow unit/contract suites for the new resolver, binding, broker,
lifecycle, report, and hostile-probe seams, followed by the existing secret,
isolation, managed-native, and CLI suites. The final acceptance report must
include commands, host/runtime identity, evidence ID, elapsed bounds, and cleanup
result. It must not substitute local Compose tests for managed-native proof.
