# Provider Adapter Contract

## Internal boundary

The provider adapter exposes bounded operations to `SecretService`, not to CLI or MCP
directly:

- `status(policy) -> CapabilityStatus`: optional unauthenticated availability check; no
  credential lookup and no value read.
- `resolve_exact(policy, credential) -> secret bytes`: exactly one authentication exchange
  and one exact item read.

Provider adapters receive an injected transport and credential handle. They cannot inspect
the parent environment, discover endpoints, list store objects, choose a child command, or
write audit records.

## Infisical request sequence

1. `POST {origin}/api/v1/auth/universal-auth/login`
   - JSON body contains only `clientId` and `clientSecret`.
   - The client secret is sourced from the approved local store immediately before the
     request.
   - The response must contain a bounded access token and positive provider `expiresIn`.
2. `GET {origin}/api/v4/secrets/{percent-encoded-secret-name}`
   - Query contains exact `projectId`, `environment`, `secretPath`, `type=shared`,
     `expandSecretReferences=false`, and `includeImports=false`.
   - Authorization uses the in-memory Bearer token.
   - The response must identify the requested item and contain one value within 64 KiB.
3. Discard client-secret buffers, response buffers, and access token references before
   returning the broker outcome.

No other provider route is in scope. The adapter does not list, search, batch, import,
export, write, delete, rotate, administer identities, or infer a moved item.

## Transport invariants

- TLS certificate and hostname verification are mandatory using the OS trust store.
- Redirects are errors. Environment proxies are disabled. No alternate/public origin is
  attempted.
- A single finite deadline covers credential lookup, login, exact read, parsing, and
  cleanup. Socket bounds cannot exceed the remaining deadline.
- Response bodies are streamed into a capped 1 MiB buffer and are never logged or returned
  in error detail.
- No automatic retry occurs for connection failure, timeout, 401/403, 404, 429, 5xx, or
  malformed responses.

## Stable outcome mapping

| Condition | Public code | Retry automatically |
|---|---|---:|
| unsupported server/API contract | `provider_unsupported` | no |
| DNS/connect failure | `endpoint_unreachable` | no |
| TLS/identity failure | `endpoint_untrusted` | no |
| login 401/403 | `authentication_denied` | no |
| read 401/403 | `provider_denied` | no |
| exact read 404 | `item_unavailable` | no |
| 429 | `provider_rate_limited` | no |
| provider 5xx | `provider_failed` | no |
| invalid schema/ambiguous identity | `response_invalid` | no |
| response or value over cap | `response_too_large` | no |
| deadline exceeded | `operation_timeout` | no |

Because Infisical can intentionally conflate missing and unauthorized resources, 404 is
not described as proof that an item does not exist. Provider messages and response bodies
do not cross the adapter boundary.

## Compatibility evidence

The implementation pins the reviewed route contract. Registration performs only local,
non-secret schema checks and never claims provider conformance. A separately authorized
live gate writes a versioned, secret-free acceptance record containing the tested server
release, observation time, private-route class, built-in Viewer role result, negative
cross-project result, distinct controller-credential result, and capacity-boundary result.
It contains no endpoint
credential, token, secret value, provider body, or credential-store identifier. Provider
role, capacity, project scope, and private routing cannot be inferred from `/api/status`.
