# Configuration Contract

## Backward-compatible source discrimination

Existing file sources remain valid without a `type` field. `type: file` is an optional
explicit spelling with identical behavior. `type: infisical` selects the provider adapter.
Unknown types and mixed adapter fields are invalid.

```yaml
secrets:
  sources:
    deploy-api:
      type: infisical
      endpoint: https://infisical.private.example
      projectId: project-id-placeholder
      environment: dev
      path: /
      key: API_TOKEN
      clientId: client-id-placeholder
      mcpModes: [source_info, keys, metadata, validate, use]
      timeoutSeconds: 10
  useProfiles:
    deploy-api-check:
      source: deploy-api
      key: API_TOKEN
      command: [python3, tools/check_api.py]
      env: DEPLOY_API_TOKEN
      timeoutSeconds: 30
```

All example identifiers are placeholders, not working credentials.

## Validation rules

- `endpoint` is an HTTPS origin with host and optional port only. Userinfo, query, fragment,
  non-HTTPS schemes, public fallback, and redirect targets are rejected.
- `projectId`, `environment`, `key`, and `clientId` are non-empty bounded strings without
  control characters. `path` is canonical, absolute, and bounded.
- Secret values, access tokens, and client secrets are never valid registration fields.
  Unknown fields, including credential-, token-, password-, and value-bearing field names,
  fail closed. Sandbox cannot determine whether a same-user operator has intentionally put
  secret material in an otherwise valid identifier; that remains inside the documented
  same-user trust boundary.
- `timeoutSeconds` is an integer within the repository's finite provider-operation bound.
- Unknown fields fail closed.
- `mcpModes` contains only supported provider modes. Missing modes grant no MCP authority.
- A use profile's source and key must match the registered provider source exactly. The
  caller cannot override endpoint, project, environment, path, key, command, args, or env.

## Mode matrix

| Operation | Network | Auth | Value read | Provider source |
|---|---:|---:|---:|---:|
| source info | optional `/api/status` | no | no | supported |
| keys | no | no | no | configured singleton only |
| metadata | yes | yes | exact item | only when enabled |
| validate | yes | yes | exact item | only when enabled |
| fixed use profile | yes | yes | exact item | only when enabled |
| reveal/session/arbitrary run | — | — | — | refused |
| list/search/wildcard/export | — | — | — | refused |
| write/delete/rotate/migrate/admin | — | — | — | refused |

## Credential locator

The credential-store service name is a fixed Sandbox constant. The account name is derived
from a versioned digest of stable non-secret source/project/client identity. It never embeds
the provider secret, item value, endpoint query data, or user-supplied display label.

Credential placement requires a TTY-hidden value or an equivalently protected input
channel plus explicit create/replace intent. It does not modify provider state. No
credential value or backend object identifier appears in JSON or text output.

Provider identities and credentials are provisioned outside Sandbox. A configured
`clientId` and a present local credential are prerequisites, not proof of provider role,
project exclusivity, or deployment capacity. Those properties are recorded only by the
separately authorized live acceptance gate.
