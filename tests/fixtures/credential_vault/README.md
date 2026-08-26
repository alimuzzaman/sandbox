# Credential Vault contract fixtures

This fixture metadata is deliberately fake and contains no credential value.
It is safe to use in unit and contract tests before the managed-native proof
gate is closed.

## Redacted binding example

```text
capability: outbound_credential_mediation
runtime: managed-native
instance_id: instance-fixture-only
source_reference: ref:test:credential-vault:fixture
scheme: https
host: api.invalid.example
port: 443
method: GET
path: /v1/fixture
auth_form: authorization_bearer
policy_digest: <64-hex-redacted>
egress_digest: <64-hex-redacted>
broker_digest: <64-hex-redacted>
state: credential_pending
evidence_id: <null-until-live-proof>
credential_value: <redacted-never-fixture-data>
```

Tests must assert that durable and caller-visible records retain only the
opaque reference, scope, digests, lifecycle state, and bounded reason codes.
Never replace the redacted markers with a real token, API key, header, or
secret-derived reversible value.
