# Research: Cross-Platform Open-Source Secret Manager

## Decision: Integrate Infisical through a narrow direct HTTP adapter

Use the two reviewed endpoints required by the product: Universal Auth login and exact
secret read. Pin compatibility to reviewed Infisical commit
`fc624ad1a4e04ad4eb8882a11bcfb7183ce0880d` and a tested server release before shipping.

The exact read is `GET /api/v4/secrets/{secretName}` with `projectId`, `environment`, and
`secretPath`, plus `type=shared`, `expandSecretReferences=false`, and
`includeImports=false`. Authentication is `POST /api/v1/auth/universal-auth/login` with a
client ID and client secret. One operation performs one login and at most one exact read;
the returned access token remains memory-only and is discarded afterward.

**Rationale**: This is the smallest auditable boundary that satisfies exact-item use. It
allows Sandbox to prohibit redirects, proxy inheritance, broad SDK behavior, response
dumps, retries, and collection APIs.

**Rejected alternatives**:

- The Infisical CLI exposes a general-purpose process and output surface.
- The official Python SDK adds broad dependencies and behaviors beyond the two required
  calls.
- A new synchronized vault duplicates mature provider security and lifecycle work.
- OS keyrings alone do not provide a shared Linux/macOS source of truth.

**Primary evidence**:

- [Infisical exact-secret route](https://github.com/Infisical/infisical/blob/fc624ad1a4e04ad4eb8882a11bcfb7183ce0880d/backend/src/server/routes/v4/secret-router.ts#L310-L383)
- [Universal Auth login route](https://github.com/Infisical/infisical/blob/fc624ad1a4e04ad4eb8882a11bcfb7183ce0880d/backend/src/server/routes/v1/identity-universal-auth-router.ts#L31-L55)
- [Machine identities](https://infisical.com/docs/documentation/platform/identities/machine-identities)
- [Universal Auth](https://infisical.com/docs/documentation/platform/identities/universal-auth)
- [Built-in roles](https://infisical.com/docs/documentation/platform/access-controls/role-based-access-controls)
- [API errors](https://infisical.com/docs/api-reference/overview/errors)
- [API rate limits](https://infisical.com/docs/api-reference/overview/rate-limits)

## Decision: Treat provider project scope and exact selection separately

Use one Infisical identity with the built-in Viewer role per Sandbox project. Treat the
environment, path, and key as Sandbox selection policy, not provider authorization.

**Rationale**: Community built-in roles provide project-level read scope. Claiming that a
path or key is a provider authorization boundary would overstate the deployment's
guarantees.

**Rejected alternative**: Sharing one provider identity across projects weakens revocation
and cross-project isolation. Depending on custom fine-grained roles risks requiring paid or
enterprise functionality.

## Decision: Use explicit native credential-store adapters

Pin `keyring` 25.7.0 for the macOS Keychain implementation and `SecretStorage` 3.5.0 on
Python 3.10+ / 3.3.3 on Python 3.9, plus
`jeepney` 0.9.0 for Linux. Instantiate the approved macOS backend explicitly. On Linux,
query the existing Secret Service directly, check collection lock state, and fail without
calling unlock or starting a service.

**Rationale**: Bootstrap credentials need OS-protected at-rest storage, but unattended
broker operations must never trigger a prompt, backend discovery, daemon startup, or
plaintext fallback.

**Rejected alternatives**:

- Automatic `keyring` discovery can be influenced by environment, config, or third-party
  entry points and can select unsuitable backends.
- `keyrings.alt` includes plaintext or weak fallback stores.
- Invoking the macOS `security` command places plaintext in a subprocess channel.
- Environment variables and repository configuration are exposed to ambient processes or
  persistence surfaces.

**Primary evidence**:

- [keyring 25.7.0](https://pypi.org/project/keyring/)
- [keyring configuration and backend behavior](https://keyring.readthedocs.io/en/stable/)
- [SecretStorage 3.5.0](https://pypi.org/project/SecretStorage/)
- [jeepney 0.9.0](https://pypi.org/project/jeepney/)
- [Secret Service locking](https://specifications.freedesktop.org/secret-service/latest/unlocking.html)

## Decision: Disable ambient transport behavior

Use a standard-library HTTPS client with verified TLS, system trust, redirects disabled,
and `ProxyHandler({})` so `*_PROXY` environment variables cannot redirect secrets. Reject
userinfo, query, and fragments in the registered endpoint. Do not add a custom CA-file
field in the first release; operators install a private CA in the OS trust store.

Cap provider responses at 1 MiB and the resolved value at the broker's existing 64 KiB
limit. Map timeouts, TLS failures, DNS/network failures, 401/403, 404, 429, 5xx, malformed
JSON, and excessive bodies to stable secret-free outcomes. Never automatically retry.

**Rationale**: A private Tailscale route supplies reachability, not authenticated endpoint
identity. Ambient proxies, redirects, and response dumps create unnecessary exfiltration
paths.

## Decision: Preserve the broker as the only public boundary

`SecretService` retains durable audit-before-callback ordering. A source-adapter manifest
selects file or provider behavior; provider and credential adapters are injected beneath
the service. CLI and MCP call only the service.

Existing file registrations remain valid without a `type` discriminator. Provider support
adds a fixed-profile CLI action matching the current MCP authority and a local credential
placement action with explicit create-versus-replace intent. It adds no reveal or provider
administration commands.

**Rationale**: The existing broker already owns source modes, fixed-child execution,
minimal environment, redaction, output bounds, and secret-free auditing. Parallel command
surfaces would bypass those controls.

## Decision: Make status non-authoritative and value-free

Local source information reports configuration validity, approved credential backend class,
and credential presence/lock state without returning an object identifier. It may make a
bounded unauthenticated request to Infisical's public `/api/status` endpoint. It does not
log in or retrieve a secret.

**Rationale**: Availability is useful operational evidence but does not prove Viewer role,
project authorization, item existence, or controller credential validity. Those are proven
only by a separately authorized exact-item acceptance.

## Known limitations and required live evidence

- Static code and documentation cannot prove the operator's deployed license, capacity,
  role assignment, TLS trust, Tailscale routing, or revocation behavior.
- macOS Keychain and Linux Secret Service protect data at rest; neither isolates a secret
  from all programs running with the same user's authority.
- Infisical 404 responses can conflate missing and unauthorized state. Sandbox reports the
  bounded `item_unavailable` outcome and does not claim absence.
- Before marking implementation complete, live synthetic acceptance must prove both
  controllers, independent revocation, Viewer-only project scope, cross-project denial,
  exact-item resolution, and secret-free retained evidence.
