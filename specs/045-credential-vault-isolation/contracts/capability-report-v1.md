# Contract: Capability and Proof Report v1

## Purpose

Expose enough bounded evidence for an operator or reviewer to decide whether
outbound credential mediation may run. A report is not a claim that a runtime is
secure merely because code or a manifest exists.

## Report shape

```text
capability                 outbound_credential_mediation
runtime                    managed-native
platform                   Ubuntu 24.04 identity
support_tier               proven | implemented_unproven | blocked | unavailable
adoptable                  boolean
evidence_id                opaque evidence identity or null
prerequisites              named checks with pass/fail/unknown and safe reason
effective_isolation        namespaces, privileges, LSM, seccomp, mounts, cgroup,
                           network, route, reachability, fd/env/control checks
policy_digest              opaque digest
egress_digest              opaque digest
broker_digest              opaque digest
binding_states             IDs, versions, scopes, states, expiries only
last_transition            safe timestamp and reason
```

## Rules

- `implemented_unproven` or `adoptable=false` is a hard refusal for credential
  mediation.
- Missing, stale, drifted, or unknown effective observations are reported as
  blocked/unproven and cannot be converted to success by caller preference.
- The report never includes credential bytes, source contents, authorization
  headers, raw request bodies, or reversible hashes of those values.
- Capability identity and evidence identity are distinct: declared support is
  not live proof.
- Pre-start consumes the report as a gate. Periodic health reports a failure and
  closes credential admission without weakening unrelated default-deny network
  controls.
