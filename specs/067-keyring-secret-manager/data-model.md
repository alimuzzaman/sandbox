# Data Model: Cross-Platform Open-Source Secret Manager

## SourceDescriptor

The authoritative non-secret registration consumed by the broker.

- `type`: absent or `file` for existing behavior; `infisical` for this feature.
- `mcpModes`: allowed broker operations from the existing mode vocabulary.
- File-specific fields remain unchanged.
- Provider-specific fields are represented by `InfisicalSourcePolicy`.

Validation rejects unknown types, mixed file/provider fields, unknown keys, secret-looking
values in metadata fields, unsupported modes, and incomplete provider selectors.

## InfisicalSourcePolicy

- `endpoint`: canonical HTTPS origin; no userinfo, query, fragment, redirect, or fallback.
- `projectId`: exact provider project identifier.
- `environment`: exact environment slug.
- `path`: canonical absolute provider path.
- `key`: one exact item name.
- `clientId`: non-secret Universal Auth client identifier.
- `mcpModes`: subset of `source_info`, `keys`, `metadata`, `validate`, and `use`.
- `timeoutSeconds`: finite operation timeout within the configured product bound.

The tuple `(projectId, environment, path, key)` is immutable for an individual operation.
No request-time override is accepted.

## ProjectIdentity

A provider-side machine identity assigned the built-in Viewer role in exactly one
Infisical project. Sandbox stores only its non-secret `clientId`; role and project binding
are external provider state proved by acceptance evidence.

One identity belongs to one isolated Sandbox project. Exhausted identity capacity is a
registration refusal, not permission to share an identity.

## ControllerCredential

A controller-local Universal Auth client secret.

- Locator: fixed Sandbox service label plus an account derived from stable, non-secret
  source/project/client identity.
- Material: client secret, accessible only inside the credential adapter and authentication
  request builder.
- Backend: explicitly approved macOS Keychain or Linux Secret Service adapter.
- State: `missing`, `locked`, `unavailable`, `present`, or `failed`.

Each Linux or macOS controller has a distinct credential. Credential creation and
replacement are explicit local actions. Sandbox never synchronizes or deletes one as a
registration side effect.

## ProviderSession

A short-lived access token and provider-reported expiry returned by one Universal Auth
login. It exists only in memory inside one broker operation, is never serialized or logged,
and is discarded after the exact read or failure. It is not cached across operations.

State transition:

```text
not_created -> authenticated -> exact_read_started -> discarded
            -> authentication_denied -> discarded
```

## FixedUseProfile

Existing broker policy that binds a source to a fixed executable, fixed arguments, timeout,
minimal environment, and output bounds. Provider sources may deliver a value only through
this entity. Callers cannot replace the child or selector at request time.

## CapabilityStatus

Ephemeral secret-free inspection result containing source class, configured operation modes,
credential backend class/state, endpoint reachability state, and remedies. It never
contains store object identifiers, tokens, response bodies, values, or an assertion that
reachability proves authorization.

## ProviderAcceptanceRecord

Versioned, retained, secret-free evidence from a separately authorized live acceptance.
It records the tested provider release, observation time, private-route class, built-in
Viewer result, negative cross-project result, controller separation result, capacity
boundary result, and pass/fail/unknown states. It is not returned by local status and does
not authorize provider use.

## ResolutionOutcome

A stable code, safe message, optional remedy, retryability classification, and audit
correlation metadata. Defined provider outcomes include:

- `provider_unsupported`
- `credential_backend_unavailable`
- `credential_locked`
- `credential_missing`
- `credential_store_failed`
- `endpoint_unreachable`
- `endpoint_untrusted`
- `authentication_denied`
- `provider_denied`
- `item_unavailable`
- `provider_rate_limited`
- `provider_failed`
- `response_invalid`
- `response_too_large`
- `operation_timeout`
- `capacity_exhausted`

The outcome is written only after durable audit intent exists. Messages and remedies are
constant or derived solely from approved non-secret metadata.

## Operation lifecycle

```text
registration parsed
  -> policy validated
  -> source capability authorized
  -> durable audit intent
  -> credential state checked
  -> one provider login
  -> one exact item read
  -> fixed child receives value
  -> in-memory credentials discarded
  -> secret-free audit outcome
```

Any failure moves directly to discard and secret-free outcome. There is no plaintext,
public-endpoint, ambient-credential, alternate-item, or automatic-retry transition.
