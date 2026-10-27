# Contract: Infisical calls made by the ownership worker

Every request identifies itself with a fixed honest `User-Agent: sandbox-secret-ownership/1`. Optional Cloudflare Access headers come only from the gateway-enabled protected bootstrap bundle. All via `providers/http.BoundedHTTPTransport` semantics: exact origin, HTTPS, no redirects, no proxies (ambient proxy env ignored), no retries, bounded response (1 MiB), per-request timeout ≤ min(timeoutSeconds, deadline). JSON decoded with the duplicate-key/constant-rejecting decoder (`infisical._decode_json`).

| Op | Request | Success classification | Labels |
|---|---|---|---|
| login | `POST /api/v1/auth/universal-auth/login` `{clientId, clientSecret}` (same as existing `_login_request_body`) | `accessToken`, `expiresIn` → tokenExpiresAt = now+expiresIn | VERIFIED (existing code) |
| exists | `GET /api/v4/secrets/{name}?projectId=&environment=&secretPath=&type=shared&viewSecretValue=false&expandSecretReferences=false&includeImports=false` | 200 + `secret.secretKey==name` + `secretValueHidden is True` + no usable value → present; 404 JSON → absent; else unknown | params VERIFIED; hidden-value shape and 404 UNPROVEN-LIVE |
| policy | none available as a value-free read-only call in verified sources → returns `null` | `null` means response-level detection only | INFERRED |
| create | `POST /api/v4/secrets/{name}` `{projectId, environment, secretPath, secretValue, secretComment, type:"shared"}` | `{"secret":…}` match → created; `{"approval":…}` → unresolved; 400 `Secret '…' already exists…` → conflict; timeout/5xx/other → acceptance_unknown; pre-connect failure (DNS/connect refused before send) → failed | route/body VERIFIED; 400 INFERRED |
| readback | `GET /api/v4/secrets/{name}?…&viewSecretValue=true&expandSecretReferences=false&includeImports=false` | `secretValue` RAM-equal (`hmac.compare_digest`) → true; differs → false; missing/malformed → null | VERIFIED shape |
| read | same as readback, per selected name, read grant | value returned as `V` frame | VERIFIED |
| revoke | `POST /api/v1/auth/token/revoke` `{accessToken}` | 200 + JSON `message == "Successfully revoked access token"` → derived access revoked; unsupported route, malformed response or other outcome → cleanup_unknown | route/body/acknowledgment source VERIFIED; runtime UNPROVEN-LIVE |

Name rule: destination names must match `^[A-Z_][A-Z0-9_]*$` (provider default uppercase rule) or classification marks them `source_unsupported`.

Before any batch create, reject input that the provider would transform
(including any ECMAScript TrimString boundary); preserve the original bytes and
report only the name/reason. Never silently trim or normalize. Gateway tokens
and native bootstrap entries remain operator-managed; completion explicitly
states their cleanup is not confirmed.

Revocation source: [v0.166.5 identity-access-token-router.ts](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/server/routes/v1/identity-access-token-router.ts#L40-L65) defines `/token/revoke` and the exact acknowledgment. [v1 registration](https://github.com/Infisical/infisical/blob/v0.166.5/backend/src/server/routes/v1/index.ts#L105-L124) mounts this router under `/auth`. This revokes only the derived access token. It does not revoke the bootstrap credential, Cloudflare token, identity membership or native-store entry. No live request was made.
