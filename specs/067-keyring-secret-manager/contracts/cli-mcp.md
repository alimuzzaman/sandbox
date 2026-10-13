# CLI and MCP Contract

## Public CLI surface

The existing `sb secrets` command remains the only secret command family. Existing file
source operations retain their current syntax and envelopes.

Provider support adds only:

- a fixed-profile use action equivalent to existing MCP fixed-use authority;
- a local credential placement action addressed by registered source, with hidden/protected
  input and explicit create versus replace intent;
- provider-aware results for existing source info, keys, metadata, and validate operations.

There is no provider CLI passthrough and no reveal, list, search, wildcard, export, write,
delete, rotate, migrate, identity-admin, permission-admin, or arbitrary-child action.

## JSON result envelope

All new CLI paths use the repository's existing JSON envelope conventions. Successful
provider results may include only approved non-secret fields such as source name/type,
configured modes, credential backend/state, reachability class, fixed profile, duration,
and audit correlation. Failures include a stable code, safe message, and bounded remedy.

They never include a value, credential, access token, provider body, request headers,
credential-store object identifier, child environment, or unredacted child output.

## MCP surface

The MCP catalog remains bounded to existing secret tools:

- `secret_source_info`
- `secret_inspect`
- `secret_validate`
- `secret_use_profile`

Provider-backed use requires both the source's explicit mode and the fixed profile's
authority. MCP cannot place credentials or create, edit, override, or delete registration.
Provider selectors are never MCP arguments.

## Authorization and denial matrix

| Caller request | Human CLI | MCP/unattended |
|---|---:|---:|
| inspect approved source info | allowed by mode | allowed by mode |
| configured singleton key | allowed by mode | allowed by mode |
| metadata/validate exact item | allowed by mode | allowed by mode |
| run registered fixed profile | allowed | allowed by mode/profile |
| place local bootstrap credential | explicit protected action | refused |
| override selector or endpoint | refused | refused |
| choose arbitrary child | refused for provider source | refused |
| reveal/session/list/search/export | refused | refused |
| provider mutation/admin/migration | refused | refused |
| registration mutation | edit authoritative config outside broker | refused |

## Audit ordering

CLI and MCP resolution call `SecretService`, which persists secret-free intent before any
credential-store callback, network request, or value parsing. If intent persistence fails,
the operation stops. Every completion writes only a stable secret-free outcome.
